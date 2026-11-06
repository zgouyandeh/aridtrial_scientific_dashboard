"""Station-vs-reanalysis comparison and QC.

Reanalysis (ERA5-Land, ~9-11 km grid) is NOT ground truth: it smooths extremes and models radiation/wind. Disagreement is
therefore a *flag for investigation* (severity 'warn'), never a reason to overwrite or exclude station data.

Two complementary rules, both on robust statistics so that a handful of bad days cannot hide themselves:
  weather.vs_reanalysis_residual  day-level outlier: |residual - median| > K * 1.4826 * MAD  and above an absolute floor
  weather.vs_reanalysis_drift     persistent offset: 15-day rolling median of the residual outside a tolerance band
                                  (catches slow sensor drift, e.g. a soiled / mis-levelled pyranometer)
Residual definitions: additive for tmax, tmin (deg C), rh_min (% points); multiplicative (station/reanalysis) for rs_mj, wind_2m.
Tolerances are configuration, to be set from a commissioning period of the real station.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TrialConfig
from ..models import fao56
from . import rules

ADDITIVE = {"tmax": 3.0, "tmin": 3.0, "rh_min": 20.0}      # absolute floors for the day-level rule
RATIO = {"rs_mj": 0.25, "wind_2m": 0.8}                    # |ratio-1| floors
DRIFT_BAND = {"tmax": 2.0, "tmin": 2.0, "rh_min": 10.0, "rs_mj": 0.10, "wind_2m": 0.35}
K_MAD = 4.0
ROLL_DAYS = 15
MIN_ROLL = 10


def _residual(st: pd.Series, re: pd.Series, var: str) -> pd.Series:
    if var in RATIO:
        return st / re.where(re.abs() > 0.05)
    return st - re


def residual_frame(weather: pd.DataFrame, rean: pd.DataFrame) -> pd.DataFrame:
    s = weather.assign(date=pd.to_datetime(weather["date"])).set_index("date").sort_index()
    r = rean.assign(date=pd.to_datetime(rean["date"])).set_index("date").sort_index()
    idx = s.index.intersection(r.index)
    return pd.DataFrame({v: _residual(s.loc[idx, v], r.loc[idx, v], v) for v in list(ADDITIVE) + list(RATIO)})


def check_weather_vs_reanalysis(weather: pd.DataFrame, rean: pd.DataFrame) -> pd.DataFrame:
    if rean is None or len(rean) == 0:
        return rules.empty()
    res = residual_frame(weather, rean)
    out = []
    for v in res.columns:
        x = res[v]
        centre = 1.0 if v in RATIO else 0.0
        med = x.median()
        sig = 1.4826 * (x - med).abs().median()
        floor = RATIO.get(v, ADDITIVE.get(v))
        dev = (x - (med if v not in RATIO else med)).abs()
        flag = x[(dev > K_MAD * max(sig, 1e-9)) & ((x - centre).abs() > floor)]
        if len(flag):
            df = pd.DataFrame(dict(date=flag.index, plot_id=None, v=flag.values))
            out.append(rules._mk("weather", df, lambda r_: f"{pd.Timestamp(r_.date):%Y-%m-%d}", v, "weather.vs_reanalysis_residual", "warn",
                                 f"{v} differs from reanalysis by more than {K_MAD:g} robust sigma and the absolute floor", value=df["v"].values, ts="date", plot_id="plot_id"))
        roll = x.rolling(ROLL_DAYS, min_periods=MIN_ROLL, center=True).median()
        band = DRIFT_BAND[v]
        bad = roll[(roll - centre).abs() > band]
        if len(bad):
            df = pd.DataFrame(dict(date=bad.index, plot_id=None, v=bad.values))
            out.append(rules._mk("weather", df, lambda r_: f"{pd.Timestamp(r_.date):%Y-%m-%d}", v, "weather.vs_reanalysis_drift", "warn",
                                 f"{ROLL_DAYS}-day median of {v} residual outside tolerance band (+/-{band:g}) vs reanalysis", value=df["v"].values, ts="date", plot_id="plot_id"))
    out = [o for o in out if len(o)]
    return pd.concat(out, ignore_index=True) if out else rules.empty()


def compare_weather(weather: pd.DataFrame, rean: pd.DataFrame, cfg: TrialConfig) -> dict:
    """Agreement statistics for the report: per-variable bias / RMSE / r, and three ET0 estimates side by side."""
    s = weather.assign(date=pd.to_datetime(weather["date"])).set_index("date").sort_index()
    r = rean.assign(date=pd.to_datetime(rean["date"])).set_index("date").sort_index()
    idx = s.index.intersection(r.index)
    rows = []
    for v in ["tmax", "tmin", "rh_min", "rh_max", "wind_2m", "rs_mj", "rain_mm"]:
        a, b = s.loc[idx, v], r.loc[idx, v]
        ok = a.notna() & b.notna()
        d = (a[ok] - b[ok])
        rows.append(dict(variable=v, n=int(ok.sum()), bias=float(d.mean()), rmse=float(np.sqrt((d ** 2).mean())),
                         r=float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 2 and a[ok].std() > 0 and b[ok].std() > 0 else np.nan))
    et_station = s.loc[idx, "et0_mm"] if "et0_mm" in s else pd.Series(fao56.eto_from_frame(s.loc[idx].reset_index(), cfg.site_lat, cfg.site_elevation_m).values, index=idx)
    rr = r.loc[idx].reset_index()
    et_rean_pm = pd.Series(fao56.eto_from_frame(rr, cfg.site_lat, cfg.site_elevation_m).values, index=idx)
    et_om = r.loc[idx, "et0_openmeteo_mm"] if "et0_openmeteo_mm" in r else pd.Series(np.nan, index=idx)
    def st(a, b):
        ok = a.notna() & b.notna()
        d = a[ok] - b[ok]
        return dict(n=int(ok.sum()), bias_mm_d=float(d.mean()), rmse_mm_d=float(np.sqrt((d ** 2).mean())), bias_pct=float(100 * d.mean() / b[ok].mean()) if ok.any() else np.nan)
    return dict(variables=pd.DataFrame(rows),
                et0=dict(station_vs_reanalysisPM=st(et_station, et_rean_pm), reanalysisPM_vs_openmeteo=st(et_rean_pm, et_om), station_vs_openmeteo=st(et_station, et_om)),
                et0_totals_mm=dict(station=float(et_station.sum()), reanalysis_pm=float(et_rean_pm.sum()), openmeteo=float(et_om.sum(skipna=True))))
