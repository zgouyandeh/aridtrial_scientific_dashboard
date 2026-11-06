"""Synthetic daily weather for an inland UAE site (hot desert, BWh), seeded and reproducible."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TrialConfig
from ..models import fao56


def simulate_weather(cfg: TrialConfig) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.seed + 101)
    dates = pd.to_datetime(cfg.dates)
    doy = dates.dayofyear.to_numpy()
    n = len(dates)

    # annual cycle peaking late July / early August; AR(1) synoptic anomalies
    tmean_clim = 27.5 + 8.5 * np.cos(2 * np.pi * (doy - 208) / 365.0)
    anom = np.zeros(n)
    for i in range(1, n):
        anom[i] = 0.7 * anom[i - 1] + rng.normal(0, 1.1)
    tmean = tmean_clim + anom
    dtr = 11.0 + 1.0 * np.cos(2 * np.pi * (doy - 120) / 365.0) + rng.normal(0, 0.8, n)
    tmax = tmean + dtr / 2
    tmin = tmean - dtr / 2

    rh_min = np.clip(rng.normal(30, 6, n) - 4 * np.cos(2 * np.pi * (doy - 208) / 365.0) + 4 * np.cos(2 * np.pi * (doy - 10) / 365.0), 10, 60)
    rh_max = np.clip(rh_min + rng.normal(42, 6, n), 45, 100)

    wind = np.clip(rng.lognormal(mean=np.log(2.6), sigma=0.28, size=n), 0.8, 8.0)

    ra = fao56.extraterrestrial_radiation(cfg.site_lat, doy)
    clear = (0.75 + 2e-5 * cfg.site_elevation_m)
    clearness = np.clip(rng.normal(0.93, 0.05, n), 0.45, 1.0)
    dusty = rng.random(n) < 0.05  # dust / cloud days
    clearness[dusty] *= rng.uniform(0.55, 0.85, dusty.sum())
    rs = ra * clear * clearness

    # winter-biased rain events (annual total of order 60-100 mm is typical for the region)
    p_rain = 0.02 + 0.04 * (np.cos(2 * np.pi * (doy - 20) / 365.0) > 0.3)
    wet = rng.random(n) < p_rain
    rain = np.where(wet, rng.gamma(0.9, 7.0, n), 0.0)

    w = pd.DataFrame(
        dict(
            date=dates.date,
            tmax=tmax.round(2),
            tmin=tmin.round(2),
            rh_max=rh_max.round(1),
            rh_min=rh_min.round(1),
            wind_2m=wind.round(2),
            rs_mj=rs.round(2),
            rain_mm=rain.round(1),
        )
    )
    w["et0_mm"] = fao56.eto_from_frame(w, cfg.site_lat, cfg.site_elevation_m).round(2)
    return w


# ----------------------------------------------------------------------------- real reanalysis as forcing

CORE_VARS = ["tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj"]


def weather_from_reanalysis(cfg: TrialConfig, rean: pd.DataFrame, max_gap_days: int = 3) -> pd.DataFrame:
    """Turn a real reanalysis daily frame (sources.openmeteo.hourly_to_daily) into the 'true' weather the plots experienced.

    The frame must cover every trial day; gaps up to `max_gap_days` are linearly interpolated, longer gaps are an error
    (the caller should shorten the trial window or choose another source). ET0 is computed with our own FAO-56 code so
    that it is comparable with what the pipeline computes from the station.
    """
    r = rean.copy()
    r["date"] = pd.to_datetime(r["date"])
    r = r.set_index("date").reindex(pd.to_datetime(cfg.dates))
    if r[CORE_VARS].isna().all(axis=1).mean() > 0.0 and r[CORE_VARS].isna().all(axis=1).sum() > max_gap_days * 3:
        raise ValueError("reanalysis does not cover the trial period")
    for c in CORE_VARS + ["rain_mm"]:
        r[c] = r[c].interpolate(limit=max_gap_days, limit_area="inside")
    if r[CORE_VARS].isna().any().any():
        bad = r.index[r[CORE_VARS].isna().any(axis=1)][:5]
        raise ValueError(f"reanalysis gaps longer than {max_gap_days} days or outside the series, e.g. {list(bad.date)}")
    r["rain_mm"] = r["rain_mm"].fillna(0.0)
    r["rh_min"], r["rh_max"] = np.minimum(r["rh_min"], r["rh_max"]), np.maximum(r["rh_min"], r["rh_max"])
    w = r.reset_index()[["index", *CORE_VARS, "rain_mm"]].rename(columns={"index": "date"})
    w["date"] = w["date"].dt.date
    w = w.round(dict(tmax=2, tmin=2, rh_max=1, rh_min=1, wind_2m=2, rs_mj=2, rain_mm=1))
    w["et0_mm"] = fao56.eto_from_frame(w, cfg.site_lat, cfg.site_elevation_m).round(2)
    return w


# Assumed reanalysis-vs-station discrepancy used when the 'on-site station' is simulated from real reanalysis.
# These are modelling assumptions (typical ERA5-Land vs station scatter in arid sites), not measured values.
STATION_DISCREPANCY = dict(t_sd=0.9, t_rho=0.6, rh_rel_sd=0.06, wind_log_sd=0.25, rs_rel_sd=0.05, rain_log_sd=0.5)


def station_from_reanalysis(cfg: TrialConfig, truth: pd.DataFrame, rng: np.random.Generator, d: dict | None = None) -> pd.DataFrame:
    """Simulated on-site station = the real weather the plots saw + station-specific discrepancy (AR(1) temperature error,
    multiplicative RH/wind/radiation errors). Gives the station-vs-reanalysis QC something realistic to chew on."""
    d = d or STATION_DISCREPANCY
    w = truth.copy()
    n = len(w)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = d["t_rho"] * e[i - 1] + rng.normal(0, d["t_sd"] * np.sqrt(1 - d["t_rho"] ** 2))
    e[0] = rng.normal(0, d["t_sd"])
    w["tmax"] = (w["tmax"] + e + rng.normal(0, 0.3, n)).round(2)
    w["tmin"] = (w["tmin"] + 0.5 * e + rng.normal(0, 0.3, n)).round(2)
    w["tmax"] = np.maximum(w["tmax"], w["tmin"] + 1.0)
    rh_f = 1 + rng.normal(0, d["rh_rel_sd"], n)
    w["rh_max"] = np.clip(w["rh_max"] * rh_f, 5, 100).round(1)
    w["rh_min"] = np.clip(w["rh_min"] * (1 + rng.normal(0, d["rh_rel_sd"], n)), 3, None).round(1)
    w["rh_min"] = np.minimum(w["rh_min"], w["rh_max"] - 1.0)
    w["wind_2m"] = np.clip(w["wind_2m"] * rng.lognormal(0, d["wind_log_sd"], n), 0.2, None).round(2)
    doy = pd.to_datetime(w["date"]).dt.dayofyear.to_numpy()
    ra = fao56.extraterrestrial_radiation(cfg.site_lat, doy)
    w["rs_mj"] = np.minimum(w["rs_mj"] * (1 + rng.normal(0, d["rs_rel_sd"], n)), 0.98 * ra).round(2)
    w["rain_mm"] = (w["rain_mm"] * rng.lognormal(0, d["rain_log_sd"], n)).round(1)
    w["et0_mm"] = fao56.eto_from_frame(w, cfg.site_lat, cfg.site_elevation_m).round(2)
    return w
