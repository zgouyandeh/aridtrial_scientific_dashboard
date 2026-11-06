"""Copernicus Climate Data Store (CDS) client: hourly ERA5-Land -> FAO-56 daily weather inputs.

Same product as Open-Meteo's `era5_land` archive, but fetched at source with your own CDS account
(`~/.cdsapirc`: url https://cds.climate.copernicus.eu/api and your personal access token; accept the dataset licence once on the
dataset page). Use it when you want the authoritative file with its own provenance; Open-Meteo is quicker for small point queries.

ERA5-Land specifics handled here (each is a classic source of silent errors):
  * accumulated variables (total_precipitation [m], surface_solar_radiation_downwards [J m-2]) accumulate from 00 UTC and the
    value stamped 00 UTC is the *previous day's* 24 h total. Hourly increment = v(t) - v(t-1 h), except at 01 UTC where it is v(t).
  * time stamps are UTC; trial days are local (UTC+4 in the UAE): increments are shifted before aggregating to local days.
  * relative humidity is not a stored variable: RH = 100 * es(Td) / es(T) (FAO-56 eq. 11 saturation vapour pressure).
  * wind is 10 m u/v components -> speed -> 2 m by FAO-56 eq. 47.
  * grid cell is ~9 km: the nearest cell to (lat, lon) is used and its coordinates are recorded.

STATUS: written from the CDS documentation; tested offline with a synthetic NetCDF and a fake client. Not run against the live
service in the build environment (network blocked) - the first live run is a commissioning test.
Attribution: "Generated using Copernicus Climate Change Service information" (Munoz-Sabater et al., 2021, ERA5-Land).
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..models import fao56
from .openmeteo import DAILY_COLS

DATASET = "reanalysis-era5-land"
VARIABLES = ["2m_temperature", "2m_dewpoint_temperature", "total_precipitation", "surface_solar_radiation_downwards",
             "10m_u_component_of_wind", "10m_v_component_of_wind"]
UTC_OFFSET_H = 4  # Asia/Dubai


def build_requests(lat: float, lon: float, start: date, end: date, half_box: float = 0.1) -> list[dict]:
    """One request per calendar month (CDS cost limits), from the day *before* `start` (needed to de-accumulate), to the day after `end` (UTC->local)."""
    s, e = start - timedelta(days=1), end + timedelta(days=1)
    area = [round(lat + half_box, 2), round(lon - half_box, 2), round(lat - half_box, 2), round(lon + half_box, 2)]  # N, W, S, E
    months = pd.period_range(s, e, freq="M")
    reqs = []
    for m in months:
        d0, d1 = max(s, m.start_time.date()), min(e, m.end_time.date())
        days = [f"{d.day:02d}" for d in pd.date_range(d0, d1)]
        reqs.append(dict(variable=VARIABLES, year=f"{m.year}", month=f"{m.month:02d}", day=days, time=[f"{h:02d}:00" for h in range(24)],
                         area=area, data_format="netcdf", download_format="unarchived"))
    return reqs


def _open_nc(path: Path):
    import xarray as xr

    p = Path(path)
    if zipfile.is_zipfile(p):  # newer CDS deliveries can be zip archives of several NetCDF files
        with zipfile.ZipFile(p) as z:
            outdir = p.with_suffix("")
            outdir.mkdir(exist_ok=True)
            z.extractall(outdir)
        files = sorted(outdir.glob("*.nc"))
        return xr.merge([xr.open_dataset(f) for f in files], compat="override")
    return xr.open_dataset(p)


def _time_name(ds) -> str:
    return "valid_time" if "valid_time" in ds.coords else "time"


def nc_to_hourly(ds, lat: float, lon: float) -> tuple[pd.DataFrame, dict]:
    """Nearest grid cell -> hourly frame (UTC index) with raw ERA5-Land variables (short names)."""
    tname = _time_name(ds)
    lat_n = "latitude" if "latitude" in ds.coords else "lat"
    lon_n = "longitude" if "longitude" in ds.coords else "lon"
    cell = ds.sel({lat_n: lat, lon_n: lon}, method="nearest")
    df = pd.DataFrame({v: np.asarray(cell[v]).astype(float) for v in ("t2m", "d2m", "tp", "ssrd", "u10", "v10") if v in cell})
    df.index = pd.DatetimeIndex(np.asarray(cell[tname])).tz_localize("UTC")
    df = df[~df.index.duplicated()].sort_index()
    return df, dict(grid_lat=float(cell[lat_n]), grid_lon=float(cell[lon_n]))


def deaccumulate(v: pd.Series) -> pd.Series:
    """ERA5-Land accumulations -> hourly increments (see module docstring)."""
    inc = v.diff()
    first = v.index.hour == 1
    inc[first] = v[first]
    # a gap in the hourly index would make diff() span several hours: refuse silently-wrong increments
    dt = v.index.to_series().diff()
    inc[(dt != pd.Timedelta(hours=1)) & ~first] = np.nan
    return inc


def hourly_to_daily(h: pd.DataFrame, utc_offset_h: int = UTC_OFFSET_H, min_hours: int = 24) -> pd.DataFrame:
    x = pd.DataFrame(index=h.index)
    x["t"] = h["t2m"] - 273.15
    td = h["d2m"] - 273.15
    x["rh"] = np.clip(100.0 * fao56.svp(td) / fao56.svp(x["t"]), 0, 100)
    x["rain_mm"] = deaccumulate(h["tp"]) * 1000.0
    x["rs_mj"] = deaccumulate(h["ssrd"]) / 1e6
    x["rain_mm"] = x["rain_mm"].clip(lower=0)      # de-accumulation noise can give tiny negatives
    x["rs_mj"] = x["rs_mj"].clip(lower=0)
    x["wind10"] = np.hypot(h["u10"], h["v10"])
    x.index = x.index.tz_convert(None) + pd.Timedelta(hours=utc_offset_h)     # local clock time
    x["date"] = x.index.date
    g = x.groupby("date")
    n_ok = g["t"].count()
    out = pd.DataFrame(dict(tmax=g["t"].max(), tmin=g["t"].min(), rh_max=g["rh"].max(), rh_min=g["rh"].min(),
                            wind_2m=fao56.wind_at_2m(g["wind10"].mean().to_numpy(), 10.0),
                            rs_mj=g["rs_mj"].sum(min_count=24), rain_mm=g["rain_mm"].sum(min_count=24)))
    out["et0_openmeteo_mm"] = np.nan          # not provided by CDS (kept so the drop schema is identical)
    out.loc[n_ok < min_hours, :] = np.nan
    out = out.reset_index()
    return out[DAILY_COLS]


def fetch_daily_weather(lat: float, lon: float, start: date, end: date, cache_dir: Path, client=None, half_box: float = 0.1) -> tuple[pd.DataFrame, dict]:
    """Download (or reuse cached) monthly NetCDF files, merge, convert. `client` must offer .retrieve(dataset, request, target)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if client is None:
        import cdsapi

        client = cdsapi.Client()          # reads ~/.cdsapirc
    reqs = build_requests(lat, lon, start, end, half_box)
    frames, shas, cell, cache_hits, file_metadata = [], {}, {}, {}, {}
    for r in reqs:
        key = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16]
        target = cache_dir / f"era5land_{r['year']}{r['month']}_{key}.nc"
        cache_hits[target.name] = target.exists()
        if not target.exists():
            client.retrieve(DATASET, r, str(target))
        shas[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        sidecar = target.with_suffix(".meta.json")
        if not sidecar.exists():
            sidecar_meta = {"downloaded_at": datetime.now(timezone.utc).isoformat() if not cache_hits[target.name] else None,
                            "request_sha256": hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest(),
                            "file_sha256": shas[target.name],
                            "cache_origin": "downloaded_in_this_run" if not cache_hits[target.name] else "preexisting_without_sidecar"}
            sidecar.write_text(json.dumps(sidecar_meta, indent=2), encoding="utf-8")
        else:
            sidecar_meta = json.loads(sidecar.read_text(encoding="utf-8"))
        file_metadata[target.name] = sidecar_meta
        with _open_nc(target) as ds:
            h, cell = nc_to_hourly(ds, lat, lon)
        frames.append(h)
    h = pd.concat(frames).sort_index()
    h = h[~h.index.duplicated()]
    d = hourly_to_daily(h)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"].dt.date >= start) & (d["date"].dt.date <= end)]
    d["date"] = d["date"].dt.date
    meta = dict(source_type="real", source_name="copernicus_cds_era5_land", source="CDS reanalysis-era5-land (hourly)",
                dataset=DATASET, requested_latitude=lat, requested_longitude=lon, start_date=start.isoformat(),
                end_date=end.isoformat(), retrieved_at=datetime.now(timezone.utc).isoformat(), requests=reqs,
                request_sha256=[hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in reqs],
                file_sha256=shas, cache_hits=cache_hits, files=file_metadata, grid=cell, utc_offset_h=UTC_OFFSET_H)
    return d.reset_index(drop=True), meta
