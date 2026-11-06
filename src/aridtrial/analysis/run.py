"""End-to-end analysis run: reads QC-filtered marts, produces every table used by the report."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import CAMPAIGNS, ENDPOINT, MID_TRIAL, TREATMENTS, TrialConfig, outputs_dir
from ..design import power as P
from ..models import aquacrop_model as AQ
from ..models import calibration as CAL
from ..pipeline.db import read_sql
from . import stats as S

SOIL_RESPONSES = {"soc_pct": "SOC (%)", "ec_dsm": "ECe (dS/m)", "ph": "pH", "bulk_density": "Bulk density (g/cm3)"}


def _mid_campaigns() -> list[str]:
    keys = list(CAMPAIGNS)
    return keys[: keys.index(MID_TRIAL) + 1]


def run_analysis(engine, cfg: TrialConfig | None = None, out: Path | None = None, n_coverage_reps: int = 100, n_power_sims: int = 200,
                 n_scenario_draws: int = 20, quick: bool = False) -> dict:
    cfg = cfg or TrialConfig()
    out = Path(out) if out else outputs_dir()
    tdir = out / "tables"
    tdir.mkdir(parents=True, exist_ok=True)
    if quick:
        n_coverage_reps, n_power_sims, n_scenario_draws = 20, 60, 6
    R: dict = {"n_cov": n_coverage_reps}

    # ---------------------------------------------------------------- data
    soil0, soil1 = S.load_soil(engine, 0), S.load_soil(engine, 15)
    trees = read_sql(engine, "SELECT * FROM mart.tree_clean")
    plot_means = S.load_tree_plot_means(engine)
    plots = read_sql(engine, "SELECT * FROM core.plot")

    # ---------------------------------------------------------------- baseline: randomisation check and variance feedback
    R["baseline_balance"] = pd.DataFrame([S.baseline_balance(soil0, r) | {"means": json.dumps(S.baseline_balance(soil0, r)["means"])} for r in SOIL_RESPONSES])
    bv = S.baseline_variance(soil0, "soc_pct")
    R["baseline_variance"] = bv

    # ---------------------------------------------------------------- soil mixed models: mid-trial and endpoint
    fits, contrasts, emms, diags = {}, [], [], []
    for resp in SOIL_RESPONSES:
        for review, camps in (("mid-trial", _mid_campaigns()), ("endpoint", None)):
            mr = S.fit_response(soil0, resp, camps)
            fits[(resp, review)] = mr
            c = mr.contrasts.assign(review=review, depth="0-15 cm")
            contrasts.append(c[c["campaign"] == (MID_TRIAL if review == "mid-trial" else ENDPOINT)])
            if review == "endpoint":
                emms.append(mr.emm.assign(response=resp, depth="0-15 cm"))
                diags.append(S.residual_diagnostics(mr) | {"sd_block": mr.variance["block"], "sd_plot": mr.variance["plot"], "sd_resid": mr.variance["resid"]})
    mr15 = S.fit_response(soil1, "soc_pct")
    contrasts.append(mr15.contrasts.assign(review="endpoint", depth="15-30 cm").query(f"campaign == '{ENDPOINT}'"))
    emms.append(mr15.emm.assign(response="soc_pct", depth="15-30 cm"))
    for resp in ("height_cm", "stem_diam_mm"):
        for review, camps in (("mid-trial", _mid_campaigns()), ("endpoint", None)):
            mr = S.fit_response(plot_means, resp, camps)
            contrasts.append(mr.contrasts.assign(review=review, depth="trees (plot means)").query(f"campaign == '{MID_TRIAL if review == 'mid-trial' else ENDPOINT}'"))
            if review == "endpoint":
                emms.append(mr.emm.assign(response=resp, depth="trees (plot means)"))
                diags.append(S.residual_diagnostics(mr) | {"sd_block": mr.variance["block"], "sd_plot": mr.variance["plot"], "sd_resid": mr.variance["resid"]})
    R["contrasts"] = pd.concat(contrasts, ignore_index=True)
    R["emm"] = pd.concat(emms, ignore_index=True)
    R["diagnostics"] = pd.DataFrame(diags)

    # ---------------------------------------------------------------- survival
    cox = S.cox_survival(trees)
    R["cox"] = cox["table"]
    R["survival_frame"] = cox["frame"]
    R["survival_glm"] = S.endpoint_survival_glm(trees, cfg.trees_per_plot)
    mid_trees = trees[trees["campaign"].isin(_mid_campaigns())]
    R["cox_mid"] = S.cox_survival(mid_trees)["table"]
    R["survival_by_trt"] = trees[trees["campaign"] == ENDPOINT].groupby("treatment")["alive"].mean().rename("survival_endpoint").reset_index()

    # ---------------------------------------------------------------- water use
    meters = read_sql(engine, "SELECT * FROM mart.meter_clean")
    area = float(plots["area_m2"].iloc[0])
    ir = S.irrigation_by_plot(meters, area)
    R["irrigation_plot"] = ir
    an = S.rcbd_anova(ir, "irrigation_mm")
    R["irrigation_anova"] = dict(F=an["F"], p=an["p"], means=an["means"])
    R["irrigation_contrasts"] = an["contrasts"]

    # ---------------------------------------------------------------- external reference data (real reanalysis / Sentinel-2) and pixel geometry
    from ..design import pixel_purity as PX
    from ..qc import reanalysis as RQ

    layout_df = read_sql(engine, "SELECT plot_id, block, geom_wkt FROM core.plot")
    R["pixel_plot"], R["pixel_block"] = PX.plot_purity(layout_df), PX.block_purity(layout_df)
    R["pixel_summary"] = PX.summary(layout_df)
    st_w = read_sql(engine, "SELECT * FROM core.weather_daily ORDER BY date")
    rean_w = read_sql(engine, "SELECT * FROM core.weather_reanalysis ORDER BY date")
    R["reanalysis_present"] = bool(len(rean_w))
    if len(rean_w):
        cmp_ = RQ.compare_weather(st_w, rean_w, cfg)
        R["weather_cmp_vars"], R["weather_cmp_et0"], R["weather_cmp_totals"] = cmp_["variables"], cmp_["et0"], cmp_["et0_totals_mm"]
        R["_weather_pair"] = (st_w, rean_w)
    nb = read_sql(engine, "SELECT * FROM core.ndvi_block ORDER BY block, date")
    R["ndvi_block_present"] = bool(len(nb))
    if len(nb):
        R["ndvi_block"] = nb

    # ---------------------------------------------------------------- calibration (FAO-56) and AquaCrop scenario
    weather = read_sql(engine, "SELECT * FROM mart.weather_clean ORDER BY date")
    ndvi = read_sql(engine, "SELECT plot_id, date, ndvi FROM mart.ndvi_clean")
    sens = read_sql(engine, "SELECT plot_id, (ts AT TIME ZONE 'UTC')::date AS date, avg(vwc) AS vwc FROM mart.sensor_clean GROUP BY 1, 2")
    mt = read_sql(engine, "SELECT plot_id, date, volume_l FROM mart.meter_clean")
    series = CAL.build_series(weather, ndvi, sens, mt, area)
    par, ev = CAL.calibrate_all(series, split_day=273)
    par = par.merge(plots[["plot_id", "block", "treatment"]], on="plot_id")
    R["calibration_params"], R["calibration_eval"] = par, ev.merge(plots[["plot_id", "treatment"]], on="plot_id")
    R["calibration_anova_fc"] = S.rcbd_anova(par.rename(columns={"theta_fc": "y"}), "y")
    start, end = pd.Timestamp(cfg.trial_start).strftime("%Y/%m/%d"), (pd.Timestamp(cfg.trial_start) + pd.Timedelta(days=cfg.n_days - 1)).strftime("%Y/%m/%d")
    R["aquacrop"] = AQ.scenario_by_treatment(par, plots, weather, start, end, planting="03/01", n_draws=n_scenario_draws)

    # ---------------------------------------------------------------- design-stage and baseline-updated power
    sig0 = P.error_sd_single_time(cfg.assumed_sd_plot, cfg.assumed_sd_resid)
    R["power_table"] = P.analytic_power_table(sig0, delta=0.10)
    sc = P.SocScenario(P.DEFAULT_EFFECTS, sd_block=cfg.assumed_sd_block, sd_plot=cfg.assumed_sd_plot, sd_resid=cfg.assumed_sd_resid)
    R["power_mc"] = pd.concat([P.power_soc_mixed(b, sc, n_sim=n_power_sims, seed=b) for b in (4, 6, 8)], ignore_index=True)
    R["power_survival"] = pd.DataFrame([dict(n_blocks=b, power=P.power_survival(b, n_sim=max(n_power_sims * 3, 200))) for b in (6, 10, 14, 20)])

    # ---------------------------------------------------------------- closed-loop validation (known truth)
    R["coverage_soc"] = S.validate_coverage(cfg, n_rep=n_coverage_reps, response="soc_pct")
    R["coverage_ec"] = S.validate_coverage(cfg, n_rep=n_coverage_reps, response="ec_dsm")

    for k, v in R.items():
        if isinstance(v, pd.DataFrame):
            v.to_csv(tdir / f"{k}.csv", index=False)
    (tdir / "baseline_variance.json").write_text(json.dumps(bv, indent=2), encoding="utf-8")
    R["_series"], R["_fits"], R["_cox_model"] = series, fits, cox["model"]
    R["_trees"], R["_plots"], R["_soil0"] = trees, plots, soil0
    return R
