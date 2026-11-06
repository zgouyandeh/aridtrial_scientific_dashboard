"""Synthetic ground truth for the trial: soil, trees, water use, sensors, NDVI.

The generator is a *closed-loop test bed*: true treatment effects are known, so the downstream
analysis can be checked for bias and interval coverage. It validates the pipeline and the
statistical machinery, not agronomic reality.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import AMENDMENT_DAY, CAMPAIGNS, SOIL_LAYERS, TrialConfig
from ..models import fao56


@dataclass
class Truth:
    soc_effect_end: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": 0.15, "BIOC": 0.20, "COMP_BIOC": 0.38})
    ec_effect_end: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": -1.0, "BIOC": -0.3, "COMP_BIOC": -1.4})
    ph_effect: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": -0.10, "BIOC": 0.05, "COMP_BIOC": -0.05})
    bd_effect: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": -0.05, "BIOC": -0.08, "COMP_BIOC": -0.12})
    fc_effect: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": 0.020, "BIOC": 0.015, "COMP_BIOC": 0.040})
    growth_effect: dict = field(default_factory=lambda: {"CTRL": 0.0, "COMP": 0.18, "BIOC": 0.12, "COMP_BIOC": 0.35})
    hazard_ratio: dict = field(default_factory=lambda: {"CTRL": 1.0, "COMP": 0.55, "BIOC": 0.65, "COMP_BIOC": 0.30})
    soc_base: float = 0.25
    soc_tau: float = 60.0
    ec_tau: float = 90.0
    ec_ctrl_drift: float = 0.8
    deep_effect_frac: float = 0.30
    sd_block: float = 0.040
    sd_plot: float = 0.030
    sd_meas: float = 0.020
    survival_ctrl_end: float = 0.78
    ec_hazard_coef: float = 0.12  # log-hazard per dS/m above 8


def _frac(day, tau):
    lag = np.maximum(np.asarray(day, float) - AMENDMENT_DAY, 0.0)
    return 1.0 - np.exp(-lag / tau)


def _growth_shape(day):
    d = np.asarray(day, float)
    return d / 364.0 - 0.04 * np.sin(2 * np.pi * d / 364.0)


def simulate_field(cfg: TrialConfig, layout: pd.DataFrame, weather: pd.DataFrame, truth: Truth | None = None, fast: bool = False,
                   seed_offset: int = 0, ndvi_background: pd.DataFrame | None = None) -> dict:
    """fast=True skips sensors/meters/NDVI (used by the closed-loop coverage study)."""
    tr = truth or Truth()
    rng = np.random.default_rng(cfg.seed + 202 + seed_offset)
    plots = layout.reset_index(drop=True).copy()
    nB = cfg.n_blocks

    # -------------------------------------------------------------------- plot-level random effects
    u_block = rng.normal(0, tr.sd_block, nB)  # SOC block effect
    plots["u_soc"] = u_block[plots["block"] - 1] + rng.normal(0, tr.sd_plot, len(plots))
    plots["ec0"] = 5.5 + 1.2 * (plots["block"] - 1) + rng.normal(0, 0.6, len(plots))  # salinity gradient
    plots["ph0"] = 8.0 + rng.normal(0, 0.12, len(plots))
    plots["bd0"] = 1.55 + rng.normal(0, 0.03, len(plots))
    plots["fc_plot"] = 0.130 + rng.normal(0, 0.004, len(plots))
    plots["u_growth"] = rng.normal(0, 0.08, len(plots))
    plots["frailty"] = np.exp(rng.normal(0, 0.30, len(plots)))

    # -------------------------------------------------------------------- soil samples
    rows = []
    for _, p in plots.iterrows():
        for cname, day in CAMPAIGNS.items():
            f_soc, f_ec = _frac(day, tr.soc_tau), _frac(day, tr.ec_tau)
            for li, (top, bot) in enumerate(SOIL_LAYERS):
                deep = li == 1
                soc_true = (tr.soc_base * (0.7 if deep else 1.0) + p.u_soc * (0.6 if deep else 1.0)
                            + tr.soc_effect_end[p.treatment] * f_soc * (tr.deep_effect_frac if deep else 1.0))
                ec_true = (p.ec0 * (1.05 if deep else 1.0) + tr.ec_ctrl_drift * day / 364.0 * (0.5 if deep else 1.0)
                           + tr.ec_effect_end[p.treatment] * f_ec * (0.5 if deep else 1.0))
                ph_true = p.ph0 + tr.ph_effect[p.treatment] * f_soc
                bd_true = p.bd0 + (0.03 if deep else 0.0) + tr.bd_effect[p.treatment] * f_soc * (0.4 if deep else 1.0)
                rows.append(dict(
                    plot_id=p.plot_id, campaign=cname, sample_date=cfg.campaign_date(cname), day=day,
                    depth_top_cm=top, depth_bottom_cm=bot,
                    soc_true=soc_true, ec_true=ec_true, ph_true=ph_true, bd_true=bd_true,
                    soc_pct=max(soc_true + rng.normal(0, tr.sd_meas), 0.02),
                    ec_dsm=max(ec_true + rng.normal(0, 0.30), 0.1),
                    ph=ph_true + rng.normal(0, 0.08),
                    bulk_density=bd_true + rng.normal(0, 0.03),
                ))
    soil = pd.DataFrame(rows)

    # -------------------------------------------------------------------- trees
    n_trees = cfg.trees_per_plot
    lam0 = -np.log(tr.survival_ctrl_end) / 364.0
    trees = []
    for _, p in plots.iterrows():
        sal = np.exp(-0.035 * (p.ec0 - 8.5))
        lam = lam0 * tr.hazard_ratio[p.treatment] * p.frailty * np.exp(tr.ec_hazard_coef * (p.ec0 - 8.0))
        for k in range(1, n_trees + 1):
            h0 = rng.normal(60, 8) + rng.normal(0, 2)
            d0 = rng.normal(9.0, 1.2)
            gfac = (1 + tr.growth_effect[p.treatment]) * sal * np.exp(p.u_growth + rng.normal(0, 0.15))
            trees.append(dict(
                plot_id=p.plot_id, tree_no=k, tree_id=f"{p.plot_id}-T{k:02d}",
                h0=h0, d0=d0, g_h=38.0 * gfac, g_d=7.0 * (1 + 0.6 * tr.growth_effect[p.treatment]) * sal * np.exp(rng.normal(0, 0.12)),
                death_day=rng.exponential(1.0 / lam),
            ))
    tree_tbl = pd.DataFrame(trees)

    obs = []
    for _, t in tree_tbl.iterrows():
        for cname, day in CAMPAIGNS.items():
            alive = bool(t.death_day > day)
            s = float(_growth_shape(day))
            obs.append(dict(
                tree_id=t.tree_id, plot_id=t.plot_id, tree_no=int(t.tree_no), campaign=cname,
                obs_date=cfg.campaign_date(cname), day=day, alive=alive,
                height_cm=(t.h0 + t.g_h * s + rng.normal(0, 1.0)) if alive else np.nan,
                stem_diam_mm=(t.d0 + t.g_d * s + rng.normal(0, 0.4)) if alive else np.nan,
            ))
    tree_obs = pd.DataFrame(obs)

    if fast:
        return dict(plots=plots, soil=soil, tree_tbl=tree_tbl, tree_obs=tree_obs, truth=tr)

    # daily canopy index per plot from the (true) height of every planted tree, dead trees count as 0
    days = np.arange(cfg.n_days)
    s_day = _growth_shape(days)
    canopy = {}
    for pid, g in tree_tbl.groupby("plot_id"):
        h = g["h0"].to_numpy()[:, None] + g["g_h"].to_numpy()[:, None] * s_day[None, :]
        alive = g["death_day"].to_numpy()[:, None] > days[None, :]
        canopy[pid] = (np.clip(h / 300.0, 0, 1) * alive).mean(axis=0)

    # -------------------------------------------------------------------- water balance, meters, sensors
    area = float(plots["area_m2"].iloc[0])
    zr = 0.60
    et0 = weather["et0_mm"].to_numpy()
    rain = weather["rain_mm"].to_numpy()
    hours = pd.date_range(pd.Timestamp(cfg.trial_start), periods=cfg.n_days * 24, freq="h", tz="UTC")
    hod = np.tile(np.arange(24), cfg.n_days)
    w_h = np.where((hod >= 3) & (hod <= 14), np.sin(np.pi * (hod - 3 + 0.5) / 12.0), 0.0)
    w_h = w_h.reshape(cfg.n_days, 24)
    w_h = w_h / w_h.sum(axis=1, keepdims=True)
    cumw = np.cumsum(w_h, axis=1)
    tmean = ((weather["tmax"] + weather["tmin"]) / 2).to_numpy()

    sensor_rows, meter_rows, daily_rows = [], [], []
    for _, p in plots.iterrows():
        fc = p.fc_plot + tr.fc_effect[p.treatment] * _frac(days, 30.0)  # amendment effect on retention
        # water balance uses plot-specific (time-varying) FC: loop with day-wise RootZone
        dr_prev = 0.0
        sensor_theta = np.zeros((cfg.n_days, 24))
        irr_gross = np.zeros(cfg.n_days)
        etc = np.zeros(cfg.n_days)
        for d in range(cfg.n_days):
            rz = fao56.RootZone(theta_fc=float(fc[d]), theta_wp=float(0.05 + 0.4 * (fc[d] - 0.13)), zr_m=zr, p=0.5)
            kc = fao56.kc_young_tree(canopy[p.plot_id][d])
            wb = fao56.water_balance([et0[d]], [rain[d]], [kc], rz, dr0=min(dr_prev, rz.taw), mad=0.2)
            r = wb.iloc[0]
            irr_gross[d] = r.irr_gross_mm
            etc[d] = r.etc_mm
            dr_pre = min(dr_prev, rz.taw)
            dr_after = dr_pre - r.irr_net_mm
            dr_end = r.dr_mm
            dr_h = np.where(np.arange(24) < 3, dr_pre, dr_after + (dr_end - dr_after) * cumw[d])
            sensor_theta[d] = rz.theta_fc - dr_h / (1000.0 * zr)
            dr_prev = dr_end
        gain, off = 1 + rng.normal(0, 0.02), rng.normal(0, 0.004)
        theta_obs = sensor_theta.reshape(-1) * gain + off + rng.normal(0, 0.002, cfg.n_days * 24)
        stemp = (np.repeat(tmean, 24) + 4.0 * np.sin(2 * np.pi * (hod - 9) / 24.0) * 0.8 + rng.normal(0, 0.2, cfg.n_days * 24))
        sensor_rows.append(pd.DataFrame(dict(ts=hours, plot_id=p.plot_id, vwc=theta_obs.round(4), soil_temp_c=stemp.round(2))))
        vol_l = np.round(irr_gross * area)
        meter_rows.append(pd.DataFrame(dict(date=weather["date"].to_numpy(), plot_id=p.plot_id,
                                            volume_l=vol_l, cum_volume_l=np.cumsum(vol_l))))
        daily_rows.append(pd.DataFrame(dict(date=weather["date"].to_numpy(), plot_id=p.plot_id, etc_mm=etc, irr_gross_mm=irr_gross,
                                            canopy_index=canopy[p.plot_id], theta_fc=fc)))
    sensors = pd.concat(sensor_rows, ignore_index=True)
    meters = pd.concat(meter_rows, ignore_index=True)
    daily_truth = pd.concat(daily_rows, ignore_index=True)

    # -------------------------------------------------------------------- NDVI (Sentinel-2, 5-day revisit)
    nd_days = np.arange(2, cfg.n_days, 5)
    nd_rows = []
    cloud = np.clip(rng.beta(0.3, 4.0, len(nd_days)), 0, 1)
    cloud[rng.random(len(nd_days)) < 0.04] = rng.uniform(0.4, 0.9)
    for j, d in enumerate(nd_days):
        for _, p in plots.iterrows():
            base = 0.085 if ndvi_background is None else float(ndvi_background[int(p.block)].iloc[d])  # real S2 background when supplied
            v = base + 0.10 * canopy[p.plot_id][d] + rng.normal(0, 0.007)
            if cloud[j] > 0.3:
                v -= rng.uniform(0.02, 0.06)  # cloud / shadow contamination biases NDVI
            nd_rows.append(dict(date=weather["date"].iloc[d], plot_id=p.plot_id, ndvi=round(v, 4),
                                cloud_frac=round(float(cloud[j]), 3), scene_id=f"S2_T40QCH_{weather['date'].iloc[d]:%Y%m%d}"))
    ndvi = pd.DataFrame(nd_rows)

    return dict(plots=plots, soil=soil, tree_tbl=tree_tbl, tree_obs=tree_obs, sensors=sensors, meters=meters,
                ndvi=ndvi, daily_truth=daily_truth, truth=tr)
