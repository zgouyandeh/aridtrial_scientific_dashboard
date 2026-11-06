"""Copernicus Data Space Ecosystem (CDSE): Sentinel-2 L2A block-level NDVI via the Sentinel Hub Statistical API.

STATUS: written from the public API documentation and unit-tested against a mocked response only. It was NOT run against the
live service in the build environment (network blocked) - treat the first live run as a commissioning test.

Credentials: an OAuth client (id + secret) created in the CDSE dashboard (https://shapps.dataspace.copernicus.eu/dashboard);
read from CDSE_CLIENT_ID / CDSE_CLIENT_SECRET, never stored in the repository. Processing units are quota-limited.

Method: NDVI = (B08 - B04) / (B08 + B04) from L2A surface reflectance, evaluated per 10 m pixel, with a pixel mask from the
Scene Classification Layer (SCL): kept classes 4 (vegetation), 5 (not vegetated), 7 (unclassified) - cloud (8,9,10), cloud
shadow (3), saturated/defective (1), no-data (0), snow (11) and water (6) are excluded. Statistics are returned per day and
zone; days with fewer than `min_valid` unmasked pixels are discarded downstream. The zone is the block rectangle
(design.pixel_purity), i.e. block-level context - NOT plot-level (see pixel_purity: a 12 x 20 m plot holds at most one whole
10 m pixel).

Attribution: "Contains modified Copernicus Sentinel data [year], processed by Sentinel Hub / CDSE" (Sentinel data licence: free, full, open).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
STAC_URL = "https://stac.dataspace.copernicus.eu/v1/search"
CRS_UTM40N = "http://www.opengis.net/def/crs/EPSG/0/32640"
KEEP_SCL = (4, 5, 7)

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: [{ id: "ndvi", bands: 1, sampleType: "FLOAT32" }, { id: "dataMask", bands: 1 }]
  };
}
function evaluatePixel(s) {
  var keep = (s.SCL == 4 || s.SCL == 5 || s.SCL == 7) && s.dataMask == 1 && (s.B08 + s.B04) > 0;
  return { ndvi: [(s.B08 - s.B04) / (s.B08 + s.B04)], dataMask: [keep ? 1 : 0] };
}
"""


class CopernicusError(RuntimeError):
    pass


def get_token(session: requests.Session | None = None, client_id: str | None = None, client_secret: str | None = None) -> str:
    cid, sec = client_id or os.environ.get("CDSE_CLIENT_ID"), client_secret or os.environ.get("CDSE_CLIENT_SECRET")
    if not cid or not sec:
        raise CopernicusError("set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET (OAuth client from the CDSE dashboard)")
    r = (session or requests).post(TOKEN_URL, data=dict(grant_type="client_credentials", client_id=cid, client_secret=sec), timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def stats_request(ring_utm: list[list[float]], start: date, end: date, interval: str = "P1D", max_cloud: int = 80) -> dict:
    """Request body for one zone (closed ring in EPSG:32640 metres)."""
    return {
        "input": {
            "bounds": {"geometry": {"type": "Polygon", "coordinates": [ring_utm]}, "properties": {"crs": CRS_UTM40N}},
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": max_cloud, "mosaickingOrder": "mostRecent"}}],
        },
        "aggregation": {
            "timeRange": {"from": f"{start.isoformat()}T00:00:00Z", "to": f"{end.isoformat()}T23:59:59Z"},
            "aggregationInterval": {"of": interval},
            "evalscript": EVALSCRIPT, "resx": 10, "resy": 10,
        },
        "calculations": {"default": {}},
    }


def parse_statistics(resp: dict, block: int) -> pd.DataFrame:
    """Statistical API JSON -> rows (block, date, ndvi_mean, ndvi_std, n_valid, n_total)."""
    if resp.get("status") not in (None, "OK"):
        raise CopernicusError(f"statistics request failed: {resp}")
    rows = []
    for it in resp.get("data", []):
        st = it["outputs"]["ndvi"]["bands"]["B0"]["stats"]
        n_tot, n_nd = int(st.get("sampleCount", 0)), int(st.get("noDataCount", 0))
        mean = st.get("mean")
        ok = mean is not None and str(mean).lower() != "nan" and n_tot - n_nd > 0
        rows.append(dict(block=block, date=pd.Timestamp(it["interval"]["from"]).date(),
                         ndvi_mean=float(mean) if ok else float("nan"), ndvi_std=float(st["stDev"]) if ok and st.get("stDev") is not None else float("nan"),
                         n_valid=n_tot - n_nd, n_total=n_tot))
    return pd.DataFrame(rows, columns=["block", "date", "ndvi_mean", "ndvi_std", "n_valid", "n_total"])


def fetch_block_ndvi(rings: dict[int, list[list[float]]], start: date, end: date, *, session: requests.Session | None = None, token: str | None = None,
                     min_valid: int = 3, cache_dir: Path | None = None, max_cloud: int = 80) -> tuple[pd.DataFrame, dict]:
    """Fetch block statistics and optionally preserve each raw response under a request-hash filename."""
    s = session or requests.Session()
    tok = token or get_token(s)
    frames, shas, request_hashes, raw_files, geometry_hashes = [], {}, {}, {}, {}
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for b, ring in rings.items():
        body = stats_request(ring, start, end, max_cloud=max_cloud)
        request_sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        request_hashes[b] = request_sha
        geometry_hashes[b] = hashlib.sha256(json.dumps(ring, sort_keys=True).encode()).hexdigest()
        r = s.post(STATS_URL, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=180)
        r.raise_for_status()
        raw = r.content if hasattr(r, "content") and r.content else json.dumps(r.json()).encode("utf-8")
        shas[b] = hashlib.sha256(raw).hexdigest()
        if cache_dir is not None:
            raw_path = Path(cache_dir) / f"block_{b}_{request_sha[:16]}_{shas[b][:16]}.json"
            raw_path.write_bytes(raw)
            raw_files[b] = raw_path.name
        frames.append(parse_statistics(r.json(), b))
    df = pd.concat(frames, ignore_index=True)
    df = df[df["n_valid"] >= min_valid].reset_index(drop=True)
    meta = dict(source_type="real", source_name="cdse_sentinel_2_l2a", source="CDSE Sentinel Hub Statistical API, sentinel-2-l2a",
                product="sentinel-2-l2a", url=STATS_URL, start_date=start.isoformat(), end_date=end.isoformat(),
                retrieved_at=retrieved_at, crs="EPSG:32640", resolution_m=10, max_cloud_coverage_pct=max_cloud,
                response_sha256=shas, request_sha256=request_hashes, geometry_sha256=geometry_hashes,
                block_geometries_utm={str(b): ring for b, ring in rings.items()},
                raw_response_files=raw_files, min_valid=min_valid, scl_kept=list(KEEP_SCL),
                evalscript_sha256=hashlib.sha256(EVALSCRIPT.encode()).hexdigest())
    return df, meta


def stac_search(bbox_wgs84: list[float], start: date, end: date, max_cloud: float = 30.0, session: requests.Session | None = None) -> list[dict]:
    """Optional: list the scenes behind the statistics (provenance for the audit trail)."""
    body = {"collections": ["sentinel-2-l2a"], "bbox": bbox_wgs84, "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "limit": 200, "query": {"eo:cloud_cover": {"lte": max_cloud}}}
    r = (session or requests).post(STAC_URL, json=body, timeout=90)
    r.raise_for_status()
    return [dict(id=f["id"], datetime=f["properties"].get("datetime"), cloud=f["properties"].get("eo:cloud_cover")) for f in r.json().get("features", [])]
