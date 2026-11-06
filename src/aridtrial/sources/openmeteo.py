"""Open-Meteo Historical Weather API client (ERA5-Land reanalysis) -> FAO-56 daily weather inputs.

Why hourly: the documented hourly variable names are exact and stable; daily RH max/min are derived here from
hourly RH instead of relying on daily aggregate names. Everything an auditor needs is kept: the request URL,
retrieval time and SHA-256 of the raw JSON are written next to the cached payload.

Reanalysis is NOT a station measurement (grid cell ~11 km, smoothed extremes, modelled radiation/wind). It is used
here (a) as real meteorological forcing and (b) as an independent reference against which an on-site station is
quality-controlled (qc.rules.check_weather_vs_reanalysis).

Licence/attribution (Open-Meteo): non-commercial use needs no key; commercial use requires an API key.
Cite: Zippenfenig, P. (2023) Open-Meteo.com Weather API, doi 10.5281/zenodo.7970649;
"Generated using Copernicus Climate Change Service information" (ERA5-Land, Munoz-Sabater et al. 2021).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..models import fao56

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "shortwave_radiation", "wind_speed_10m", "et0_fao_evapotranspiration"]
REANALYSIS_LAG_DAYS = 5  # ERA5 / ERA5-Land are published with ~5 days delay
DAILY_COLS = ["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm", "et0_openmeteo_mm"]


class ReanalysisError(RuntimeError):
    pass


def build_params(lat: float, lon: float, start: date, end: date, model: str = "era5_land", tz: str = "Asia/Dubai") -> dict:
    return dict(latitude=round(lat, 4), longitude=round(lon, 4), start_date=start.isoformat(), end_date=end.isoformat(),
                hourly=",".join(HOURLY_VARS), wind_speed_unit="ms", timezone=tz, models=model)


def fetch_hourly(lat: float, lon: float, start: date, end: date, *, model: str = "era5_land", tz: str = "Asia/Dubai",
                 session: requests.Session | None = None, cache_dir: Path | None = None, today: date | None = None, timeout: int = 90) -> dict:
    """Return {'payload', 'url', 'sha256', 'retrieved_at', 'cached'}. Identical requests are served from the cache."""
    today = today or datetime.now(timezone.utc).date()
    if end > today - timedelta(days=REANALYSIS_LAG_DAYS):
        raise ReanalysisError(f"end_date {end} is within the {REANALYSIS_LAG_DAYS}-day ERA5 publication lag (today {today}); "
                              "use a ForecastAPI source or shorten the period")
    if start > end:
        raise ValueError("start after end")
    params = build_params(lat, lon, start, end, model, tz)
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_path = Path(cache_dir) / f"openmeteo_{key}.json" if cache_dir else None
    if cache_path and cache_path.exists():
        rec = json.loads(cache_path.read_text(encoding="utf-8"))
        rec["cached"] = True
        return rec
    s = session or requests.Session()
    r = s.get(ARCHIVE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise ReanalysisError(str(payload.get("reason", payload)))
    rec = dict(payload=payload, url=getattr(r, "url", ARCHIVE_URL), sha256=hashlib.sha256(r.content if hasattr(r, "content") and r.content else json.dumps(payload).encode()).hexdigest(),
               retrieved_at=datetime.now(timezone.utc).isoformat(), params=params, cached=False)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def hourly_to_daily(payload: dict, min_hours: int = 24) -> pd.DataFrame:
    """Aggregate hourly (local time) to the FAO-56 daily inputs.

    tmax/tmin    max/min of hourly 2 m temperature (deg C)
    rh_max/min   max/min of hourly 2 m relative humidity (%)
    wind_2m      mean hourly 10 m wind (m/s, requested with wind_speed_unit=ms) scaled to 2 m with FAO-56 eq. 47
    rs_mj        sum of hourly mean shortwave radiation (W/m2) * 3600 s -> MJ/m2/day
    rain_mm      sum of hourly precipitation
    et0_openmeteo_mm  sum of hourly Open-Meteo FAO-56 ET0 (independent implementation, for cross-checking ours)
    Days with fewer than `min_hours` valid hourly values are returned as NaN rather than silently averaged.
    """
    h = payload["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"])
    df["date"] = df["time"].dt.date
    g = df.groupby("date")
    n_ok = g["temperature_2m"].count()
    out = pd.DataFrame(dict(
        tmax=g["temperature_2m"].max(), tmin=g["temperature_2m"].min(),
        rh_max=g["relative_humidity_2m"].max(), rh_min=g["relative_humidity_2m"].min(),
        wind_2m=fao56.wind_at_2m(g["wind_speed_10m"].mean().to_numpy(), 10.0),
        rs_mj=g["shortwave_radiation"].sum(min_count=1) * 3600.0 / 1e6,
        rain_mm=g["precipitation"].sum(min_count=1),
        et0_openmeteo_mm=g["et0_fao_evapotranspiration"].sum(min_count=1),
    ))
    out.loc[n_ok < min_hours, :] = np.nan
    out = out.reset_index()
    out["wind_2m"] = out["wind_2m"].astype(float)
    return out[DAILY_COLS]


def grid_metadata(payload: dict) -> dict:
    return {k: payload.get(k) for k in ("latitude", "longitude", "elevation", "timezone", "utc_offset_seconds")}


def fetch_daily_weather(lat, lon, start: date, end: date, cache_dir: Path | None = None, **kw) -> tuple[pd.DataFrame, dict]:
    rec = fetch_hourly(lat, lon, start, end, cache_dir=cache_dir, **kw)
    daily = hourly_to_daily(rec["payload"])
    meta = dict(source_type="real", source_name="open_meteo_archive", source="Open-Meteo archive (ERA5-Land)",
                dataset="ERA5-Land", model=rec["params"]["models"], requested_latitude=rec["params"]["latitude"],
                requested_longitude=rec["params"]["longitude"], start_date=start.isoformat(), end_date=end.isoformat(),
                timezone=rec["params"]["timezone"], request_params=rec["params"], url=rec["url"],
                response_sha256=rec["sha256"], retrieved_at=rec["retrieved_at"],
                grid=grid_metadata(rec["payload"]), cached=rec["cached"])
    return daily, meta
