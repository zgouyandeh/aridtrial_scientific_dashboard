"""KoboToolbox payload normalisation and revision resolution.

Real API access and raw downloads live in :mod:`aridtrial.sources.kobo`.
This module handles API-shaped payloads after landing: values arrive as strings; repeat
groups arrive as lists of dicts with group-prefixed keys; edited submissions get a
new instanceID (_uuid) but keep ``meta/rootUuid``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .audit import canonical, sha256_text


from ..sources.kobo import KoboClient  # compatibility re-export; API access lives in sources.kobo


def load_mock(path: str | Path) -> list[dict]:
    """Read a saved API payload (used by the demo and tests in place of the live server)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))["results"]


# ----------------------------------------------------------------------------- envelope


def envelope(sub: dict, form_id: str) -> dict:
    root = str(sub.get("meta/rootUuid") or sub.get("meta/instanceID") or sub["_uuid"]).replace("uuid:", "")
    submitted = pd.Timestamp(sub["_submission_time"])
    submitted = submitted.tz_localize("UTC") if submitted.tzinfo is None else submitted.tz_convert("UTC")
    return dict(
        form_id=form_id,
        root_uuid=root,
        instance_uuid=str(sub["_uuid"]),
        payload_sha256=sha256_text(canonical(sub)),
        submitted_at=submitted,
    )


def _num(x):
    if x is None or (isinstance(x, str) and x.strip() == ""):
        return np.nan
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _gps(sub: dict):
    g = sub.get("_geolocation") or [None, None]
    lat, lon = (g + [None, None])[:2]
    return (lon, lat)


def normalise_soil(rows: list[dict]) -> pd.DataFrame:
    """One output row per soil layer per submission revision."""
    out = []
    for r in rows:  # r: dict(payload=..., root_uuid=..., payload_sha256=..., submitted_at=...)
        p = r["payload"]
        lon, lat = _gps(p)
        for i, layer in enumerate(p.get("layers", [])):
            top = _num(layer.get("layers/depth_top_cm"))
            plot, camp = p.get("plot_id"), p.get("campaign")
            out.append(dict(
                form_id=r["form_id"], kobo_root_uuid=r["root_uuid"], payload_sha256=r["payload_sha256"], submitted_at=r["submitted_at"],
                source_type=r.get("source_type", "unknown"), source_name=r.get("source_name", "unknown"),
                source_dataset=r.get("source_dataset", "unknown"), manifest_sha256=r.get("manifest_sha256"),
                contract_sha256=r.get("contract_sha256"),
                layer_idx=i, plot_id=plot, campaign=camp, sample_date=pd.to_datetime(p.get("sample_date"), errors="coerce"),
                depth_top_cm=int(top) if not np.isnan(top) else None, depth_bottom_cm=_num(layer.get("layers/depth_bottom_cm")),
                soc_pct=_num(layer.get("layers/soc_pct")), ec_dsm=_num(layer.get("layers/ec_dsm")), ph=_num(layer.get("layers/ph")),
                bulk_density=_num(layer.get("layers/bulk_density")), collector=p.get("collector"), gps_lon=lon, gps_lat=lat,
            ))
    return pd.DataFrame(out)


def normalise_trees(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        p = r["payload"]
        lon, lat = _gps(p)
        for i, t in enumerate(p.get("trees", [])):
            no = _num(t.get("trees/tree_no"))
            alive = str(t.get("trees/alive", "")).strip().lower() == "yes"
            out.append(dict(
                form_id=r["form_id"], kobo_root_uuid=r["root_uuid"], payload_sha256=r["payload_sha256"], submitted_at=r["submitted_at"],
                source_type=r.get("source_type", "unknown"), source_name=r.get("source_name", "unknown"),
                source_dataset=r.get("source_dataset", "unknown"), manifest_sha256=r.get("manifest_sha256"),
                contract_sha256=r.get("contract_sha256"),
                layer_idx=i, plot_id=p.get("plot_id"), campaign=p.get("campaign"), obs_date=pd.to_datetime(p.get("obs_date"), errors="coerce"),
                tree_no=int(no) if not np.isnan(no) else None, alive=alive,
                height_cm=_num(t.get("trees/height_cm")) if alive else np.nan,
                stem_diam_mm=_num(t.get("trees/stem_diam_mm")) if alive else np.nan, gps_lon=lon, gps_lat=lat,
            ))
    return pd.DataFrame(out)


def resolve_current(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """Mark exactly one row per business key as 'current'.

    Latest submission wins. Same rootUuid => the earlier row is an *edit revision*; different rootUuid =>
    a *re-submission*. Nothing is deleted: superseded rows stay in core with the reason, raw stays untouched.
    """
    df = df.sort_values(["submitted_at", "payload_sha256"]).copy()
    df["status"] = "superseded"
    df["superseded_reason"] = None
    last = df.groupby(key_col).tail(1).index
    df.loc[last, "status"] = "current"
    for k, g in df.groupby(key_col):
        if len(g) < 2:
            continue
        cur_root = g.iloc[-1]["kobo_root_uuid"]
        for idx, row in g.iloc[:-1].iterrows():
            df.at[idx, "superseded_reason"] = "edit_revision" if row["kobo_root_uuid"] == cur_root else "resubmission"
    return df
