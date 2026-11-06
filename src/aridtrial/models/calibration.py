"""Calibration of the FAO-56 single-Kc plot water balance against sensor and meter data.

Per plot, three effective parameters are fitted:
    theta_fc   field capacity (m3/m3)            <- level of the soil-moisture record
    kc_min     plot-scale Kc at zero canopy      <- bare-soil evaporation under drip
    kc_max     plot-scale Kc at full canopy index <- seasonal irrigation demand
Fixed / known: root depth (0.6 m), MAD (the controller set-point), application efficiency (0.9),
theta_wp tied to theta_fc by a pedotransfer relation (stated limitation: same functional form as the
simulator, so recovery of theta_fc is optimistic; the *hold-out* check is the honest test).

Canopy index comes from NDVI alone: c = clip((NDVI - NDVI_soil) / (NDVI_ref - NDVI_soil), 0, 1) with
NDVI_soil = 5th and NDVI_ref = 95th percentile of the QC-passed trial NDVI, linearly interpolated to daily.
Fitting residuals are in observation units: VWC (sd 0.004 m3/m3), weekly irrigation depth (sd 5 mm; absorbs
within-week timing mismatch) and 28-day cumulative irrigation (2 % + 3 mm; pins the seasonal water budget).
Optimiser: scipy differential_evolution (the irrigation trigger makes the loss non-smooth).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution

ZR = 0.60
MAD = 0.20
EFF = 0.90
BOUNDS = [(0.08, 0.25), (0.00, 0.40), (0.10, 1.50)]  # theta_fc, kc_min, kc_max
SD_VWC = 0.004
SD_IRR_WEEK = 5.0
METER_REL_ERR = 0.02  # metered cumulative volume accuracy (2 %)
METER_ABS_ERR_MM = 3.0
CUM_STEP_DAYS = 28


def wp_from_fc(fc: float) -> float:
    return 0.05 + 0.4 * (fc - 0.13)


def simulate_plot(params, et0: np.ndarray, rain: np.ndarray, canopy: np.ndarray, dr0: float = 0.0):
    """FAO-56 single-Kc daily root-zone balance with a refill-to-FC drip controller.

    Returns (vwc_daily_mean, irrigation_gross_mm).
    """
    fc, kc_min, kc_max = params
    wp = wp_from_fc(fc)
    taw = 1000.0 * (fc - wp) * ZR
    raw = 0.5 * taw
    mad_mm = MAD * taw
    n = len(et0)
    vwc = np.empty(n)
    irr = np.zeros(n)
    dr = min(dr0, taw)
    for i in range(n):
        net = 0.0
        if dr >= mad_mm:
            net, dr = dr, 0.0
        dr_after = dr
        ks = 1.0 if dr <= raw else max(0.0, (taw - dr) / (0.5 * taw))
        etc = ks * (kc_min + (kc_max - kc_min) * canopy[i]) * et0[i]
        eff_rain = 0.8 * (rain[i] - 2.0) if rain[i] > 2.0 else 0.0
        dr = min(max(dr - eff_rain + etc, 0.0), taw)
        vwc[i] = fc - 0.5 * (dr_after + dr) / (1000.0 * ZR)
        irr[i] = net / EFF
    return vwc, irr


def canopy_from_ndvi(ndvi: pd.DataFrame, dates: pd.DatetimeIndex, soil_q: float = 0.05, ref_q: float = 0.95) -> pd.DataFrame:
    """ndvi: columns plot_id, date, ndvi (QC-passed). Returns daily canopy index per plot (columns = plot_id)."""
    lo, hi = ndvi["ndvi"].quantile(soil_q), ndvi["ndvi"].quantile(ref_q)
    piv = ndvi.assign(date=pd.to_datetime(ndvi["date"])).pivot_table(index="date", columns="plot_id", values="ndvi")
    piv = piv.reindex(dates).interpolate(limit_direction="both")
    return ((piv - lo) / (hi - lo)).clip(0, 1)


@dataclass
class PlotSeries:
    plot_id: str
    et0: np.ndarray
    rain: np.ndarray
    canopy: np.ndarray
    vwc: np.ndarray  # daily mean, NaN where missing
    irr_mm: np.ndarray  # daily gross depth, NaN where QC-excluded


def _weekly(x: np.ndarray) -> np.ndarray:
    n = len(x) // 7 * 7
    return np.array([np.nan if np.isnan(x[i:i + 7]).any() else np.nansum(x[i:i + 7]) for i in range(0, n, 7)])


def _residuals(params, s: PlotSeries, days: slice):
    vwc, irr = simulate_plot(params, s.et0, s.rain, s.canopy)
    r1 = (vwc[days] - s.vwc[days]) / SD_VWC
    wo, wm = _weekly(s.irr_mm[days]), _weekly(irr[days])
    r2 = (wm - wo) / SD_IRR_WEEK
    # cumulative-volume constraint: timing-insensitive, tight (meters are accurate), pins the seasonal water budget.
    io, im = s.irr_mm[days], irr[days]
    ok = ~np.isnan(io)
    co, cm = np.cumsum(np.where(ok, io, 0.0)), np.cumsum(np.where(ok, im, 0.0))
    idx = np.arange(CUM_STEP_DAYS - 1, len(io), CUM_STEP_DAYS)
    r3 = (cm[idx] - co[idx]) / (METER_REL_ERR * np.maximum(co[idx], 1.0) + METER_ABS_ERR_MM)
    r = np.concatenate([r1[~np.isnan(r1)], r2[~np.isnan(r2)], r3])
    return r


def calibrate_plot(s: PlotSeries, calib_days: slice, seed: int = 0, maxiter: int = 60) -> dict:
    def loss(p):
        if p[1] >= p[2]:
            return 1e9
        r = _residuals(p, s, calib_days)
        return float(np.mean(r ** 2))

    res = differential_evolution(loss, BOUNDS, seed=seed, maxiter=maxiter, popsize=12, tol=1e-6, polish=False, updating="deferred")
    return dict(plot_id=s.plot_id, theta_fc=res.x[0], kc_min=res.x[1], kc_max=res.x[2], loss=res.fun)


def evaluate(s: PlotSeries, params, days: slice) -> dict:
    vwc, irr = simulate_plot(params, s.et0, s.rain, s.canopy)
    o, m = s.vwc[days], vwc[days]
    ok = ~np.isnan(o)
    rmse = float(np.sqrt(np.mean((m[ok] - o[ok]) ** 2)))
    bias = float(np.mean(m[ok] - o[ok]))
    wo, wm = _weekly(s.irr_mm[days]), _weekly(irr[days])
    k = ~np.isnan(wo)
    tot_o, tot_m = float(np.nansum(s.irr_mm[days])), float(irr[days][~np.isnan(s.irr_mm[days])].sum())
    return dict(vwc_rmse=rmse, vwc_bias=bias, irr_total_obs_mm=tot_o, irr_total_mod_mm=tot_m,
                irr_total_err_pct=100 * (tot_m - tot_o) / tot_o if tot_o else np.nan,
                weekly_irr_rmse_mm=float(np.sqrt(np.nanmean((wm[k] - wo[k]) ** 2))))


def build_series(weather: pd.DataFrame, ndvi: pd.DataFrame, sensors_daily: pd.DataFrame, meters: pd.DataFrame, area_m2: float) -> dict[str, PlotSeries]:
    w = weather.assign(date=pd.to_datetime(weather["date"])).set_index("date").sort_index()
    dates = w.index
    can = canopy_from_ndvi(ndvi, dates)
    vwc = sensors_daily.assign(date=pd.to_datetime(sensors_daily["date"])).pivot_table(index="date", columns="plot_id", values="vwc").reindex(dates)
    m = meters.assign(date=pd.to_datetime(meters["date"])).pivot_table(index="date", columns="plot_id", values="volume_l").reindex(dates) / area_m2
    et0 = w["et0_mm"].interpolate(limit_direction="both").to_numpy()
    rain = w["rain_mm"].fillna(0.0).to_numpy()
    out = {}
    for pid in can.columns:
        out[pid] = PlotSeries(pid, et0, rain, can[pid].to_numpy(), vwc[pid].to_numpy(), m[pid].to_numpy())
    return out


def calibrate_all(series: dict[str, PlotSeries], split_day: int = 273, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calibrate on [0, split_day), validate on [split_day, end). Returns (parameters, evaluation)."""
    n = len(next(iter(series.values())).et0)
    cal, val = slice(0, split_day), slice(split_day, n)
    pars, evals = [], []
    for i, (pid, s) in enumerate(series.items()):
        p = calibrate_plot(s, cal, seed=seed + i)
        pars.append(p)
        x = (p["theta_fc"], p["kc_min"], p["kc_max"])
        e_cal, e_val = evaluate(s, x, cal), evaluate(s, x, val)
        evals.append(dict(plot_id=pid, **{f"cal_{k}": v for k, v in e_cal.items()}, **{f"val_{k}": v for k, v in e_val.items()}))
    return pd.DataFrame(pars), pd.DataFrame(evals)
