"""Scientific & Agronomic analysis layer: builds section payloads for the second dashboard and persists them.

Design rules
  * Everything is computed from QC-filtered marts / the already-fitted production models (``run_analysis``); no
    statistic is recomputed in an HTTP request.
  * Each analysis run is persisted append-only in ``results.analysis_run`` / ``results.analysis_artifact`` together
    with its provenance (dataset version, QC run, seed, settings, library versions, data origin).
  * Inference unit = plot (RCBD, 6 blocks x 4 treatments). Trees, soil layers and visits are subsamples / repeats.
  * Observed power is never reported; power sections are design-stage or simulation-based only.
  * Treatment x Block is not estimable with one replicate per block, so it is only shown descriptively.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from sqlalchemy import text
from statsmodels.stats.multitest import multipletests

from .. import __version__
from ..config import CAMPAIGNS, ENDPOINT, MID_TRIAL, TREATMENT_LABELS, TREATMENTS, TrialConfig
from ..design import power as P
from ..pipeline.db import read_sql

SECTIONS = ("eda", "design", "comparison", "models", "inference", "power", "agronomy", "survival", "evaluation")

RESPONSES = {
    "soc_pct": dict(label="Soil organic carbon", unit="%", source="soil"),
    "ec_dsm": dict(label="Electrical conductivity (ECe)", unit="dS/m", source="soil"),
    "ph": dict(label="pH", unit="", source="soil"),
    "bulk_density": dict(label="Bulk density", unit="g/cm³", source="soil"),
    "height_cm": dict(label="Tree height (plot mean)", unit="cm", source="tree"),
    "stem_diam_mm": dict(label="Stem diameter (plot mean)", unit="mm", source="tree"),
}
PRIMARY = "soc_pct"
CTRL = "CTRL"


# ----------------------------------------------------------------------------- helpers


def clean(o):
    """Recursively convert numpy / pandas / NaN into strict-JSON values."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return clean(o.to_dict("records"))
    if isinstance(o, pd.Series):
        return clean(o.to_dict())
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def _canonical(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _five(x: pd.Series) -> dict:
    x = x.dropna().astype(float)
    if x.empty:
        return dict(n=0)
    q1, med, q3 = x.quantile([0.25, 0.5, 0.75])
    iqr = q3 - q1
    lo_f, hi_f = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    inside = x[(x >= lo_f) & (x <= hi_f)]
    return dict(n=int(len(x)), mean=float(x.mean()), sd=float(x.std(ddof=1)) if len(x) > 1 else None, min=float(x.min()), q1=float(q1),
                median=float(med), q3=float(q3), max=float(x.max()), whisker_lo=float(inside.min()), whisker_hi=float(inside.max()))


def _plot_table(R: dict, engine) -> pd.DataFrame:
    """One row per plot: endpoint / baseline responses and covariates (plot-level analysis table)."""
    plots = R["_plots"][["plot_id", "block", "treatment"]].copy()
    soil0 = R["_soil0"]
    for resp in ("soc_pct", "ec_dsm", "ph", "bulk_density"):
        w = soil0.pivot_table(index="plot_id", columns="campaign", values=resp, aggfunc="mean")
        plots = plots.merge(w[[c for c in ("T0", ENDPOINT) if c in w]].rename(columns={"T0": f"{resp}_T0", ENDPOINT: f"{resp}_{ENDPOINT}"}), on="plot_id", how="left")
    from . import stats as S
    pm = S.load_tree_plot_means(engine)
    for resp in ("height_cm", "stem_diam_mm"):
        w = pm.pivot_table(index="plot_id", columns="campaign", values=resp, aggfunc="mean")
        plots = plots.merge(w[[c for c in ("T0", ENDPOINT) if c in w]].rename(columns={"T0": f"{resp}_T0", ENDPOINT: f"{resp}_{ENDPOINT}"}), on="plot_id", how="left")
    tr = R["_trees"]
    sv = tr[tr["campaign"] == ENDPOINT].groupby("plot_id")["alive"].mean().rename("survival_T4")
    plots = plots.merge(sv, on="plot_id", how="left")
    ir = R["irrigation_plot"][["plot_id", "irrigation_mm"]]
    plots = plots.merge(ir, on="plot_id", how="left")
    nd = read_sql(engine, "SELECT plot_id, avg(ndvi) AS ndvi_mean FROM mart.ndvi_clean GROUP BY plot_id")
    vw = read_sql(engine, "SELECT plot_id, avg(vwc) AS vwc_mean FROM mart.sensor_clean GROUP BY plot_id")
    plots = plots.merge(nd, on="plot_id", how="left").merge(vw, on="plot_id", how="left")
    return plots


# ----------------------------------------------------------------------------- 1. EDA


def section_eda(R: dict, engine) -> dict:
    soil0, pm = R["_soil0"], None
    from . import stats as S
    pm = S.load_tree_plot_means(engine)
    src = {"soil": soil0, "tree": pm}
    summary, box, missing = [], [], []
    for resp, meta in RESPONSES.items():
        d = src[meta["source"]]
        for (trt, camp), g in d.groupby(["treatment", "campaign"]):
            summary.append(dict(response=resp, treatment=trt, campaign=camp, **_five(g[resp])))
        e = d[d["campaign"] == ENDPOINT]
        for trt in TREATMENTS:
            g = e[e["treatment"] == trt]
            box.append(dict(response=resp, treatment=trt, **_five(g[resp]),
                            points=[dict(plot_id=r.plot_id, block=int(r.block), value=float(getattr(r, resp))) for r in g.dropna(subset=[resp]).itertuples()]))
        for camp, g in d.groupby("campaign"):
            n_exp = int(R["_plots"]["plot_id"].nunique())
            n_obs = int(g.dropna(subset=[resp])["plot_id"].nunique())
            missing.append(dict(response=resp, campaign=camp, expected=n_exp, observed=n_obs, excluded_by_qc=n_exp - n_obs))
    pt = _plot_table(R, engine)
    cols = {"soc_pct_T4": "SOC", "ec_dsm_T4": "ECe", "ph_T4": "pH", "bulk_density_T4": "BD", "height_cm_T4": "Height", "stem_diam_mm_T4": "Diameter",
            "survival_T4": "Survival", "irrigation_mm": "Irrigation", "ndvi_mean": "NDVI", "vwc_mean": "VWC"}
    use = [c for c in cols if c in pt and pt[c].notna().sum() > 4]
    raw = pt[use].corr()
    centered = pt[use] - pt.groupby("block")[use].transform("mean")  # remove block means: within-block association
    within = centered.corr()
    corr = dict(labels=[cols[c] for c in use], raw=raw.values.tolist(), within_block=within.values.tolist(), n=int(len(pt)),
                note="Plot-level Pearson r (n = 24). Exploratory: no multiplicity control; within-block removes block means so that block gradients do not masquerade as associations. Treatment effects also contribute to these correlations.")
    # baseline SOC distribution and campaign-wise block profile (descriptive Treatment x Block heat-map, 1 observation per cell)
    s0 = soil0[soil0["campaign"] == "T0"]["soc_pct"].dropna()
    hist_counts, edges = np.histogram(s0, bins=8)
    hm = pt.pivot_table(index="block", columns="treatment", values="soc_pct_T4")
    heat = dict(rows=[int(b) for b in hm.index], cols=list(hm.columns), values=hm.values.tolist(), response="soc_pct", campaign=ENDPOINT)
    return dict(responses={k: v for k, v in RESPONSES.items()}, summary=summary, boxplots=box, missingness=missing, correlation=corr,
                baseline_soc_hist=dict(counts=hist_counts.tolist(), edges=edges.tolist()), block_treatment_heatmap=heat,
                notes=["Summaries are descriptive and use QC-filtered data only (flagged values are excluded, never edited).",
                       "Tree responses are survivor-conditional plot means.",
                       "The block × treatment table has one observation per cell: it shows pattern, not an estimable interaction."])


# ----------------------------------------------------------------------------- 2. design


def section_design(R: dict, cfg: TrialConfig, engine) -> dict:
    plots = read_sql(engine, "SELECT plot_id, block, position, treatment, easting, northing, area_m2, n_trees FROM core.plot ORDER BY block, position")
    x0, y0 = plots["easting"].min(), plots["northing"].min()
    plots["x"], plots["y"] = plots["easting"] - x0, plots["northing"] - y0
    b, t = int(plots["block"].nunique()), len(TREATMENTS)
    pt = _plot_table(R, engine)
    bal = []
    for r in R["baseline_balance"].to_dict("records"):
        means = json.loads(r["means"]) if isinstance(r["means"], str) else r["means"]
        d0 = R["_soil0"][R["_soil0"]["campaign"] == "T0"]
        sd = float(d0[r["response"]].std(ddof=1))
        spread = (max(means.values()) - min(means.values())) / sd if sd else None
        bal.append(dict(response=r["response"], F=r["F"], p=r["p"], means=means, max_std_diff=spread))
    # relative efficiency of blocking at baseline (SOC): RE = ((b-1)MSB + b(t-1)MSE) / ((bt-1)MSE)
    d0 = pt.dropna(subset=["soc_pct_T0"])
    m = smf.ols("soc_pct_T0 ~ C(block) + C(treatment)", d0).fit()
    a = sm.stats.anova_lm(m, typ=1)
    msb, mse = a.loc["C(block)", "sum_sq"] / a.loc["C(block)", "df"], a.loc["Residual", "sum_sq"] / a.loc["Residual", "df"]
    re_ = ((b - 1) * msb + b * (t - 1) * mse) / ((b * t - 1) * mse)
    bv = R["baseline_variance"]
    icc = bv["sd_block"] ** 2 / (bv["sd_block"] ** 2 + bv["sd_resid"] ** 2)
    counts = plots.pivot_table(index="block", columns="treatment", values="plot_id", aggfunc="count").fillna(0).astype(int)
    dfs = dict(block=b - 1, treatment=t - 1, error=(b - 1) * (t - 1), total=b * t - 1)
    return dict(layout=plots, balance_matrix=dict(rows=[int(i) for i in counts.index], cols=list(counts.columns), values=counts.values.tolist()),
                design=dict(type="RCBD", blocks=b, treatments=t, plots=int(len(plots)), trees_per_plot=cfg.trees_per_plot, plot_size_m=[cfg.plot_w_m, cfg.plot_l_m],
                            experimental_unit="plot", df=dfs, treatment_labels=TREATMENT_LABELS),
                baseline_balance=bal, baseline_variance=bv,
                blocking=dict(relative_efficiency_vs_crd=float(re_), icc_block_baseline=float(icc), ms_block=float(msb), ms_error=float(mse)),
                pixel=R["pixel_summary"],
                notes=["Randomisation within blocks; balance p-values at T0 are a sanity check, not a test of 'whether randomisation worked'.",
                       "Error df = (b−1)(t−1) = 15; trees are subsamples and never counted as replicates.",
                       "Relative efficiency > 1 means the blocks removed variance that a completely randomised design would have carried."])


# ----------------------------------------------------------------------------- 3. comparison


def _ancova(pt: pd.DataFrame, resp: str, base_col: str | None, end_col: str) -> pd.DataFrame:
    d = pt.dropna(subset=[end_col] + ([base_col] if base_col else [])).copy()
    d["block"] = d["block"].astype(str)
    f = f"{end_col} ~ C(block) + C(treatment, Treatment('CTRL'))" + (f" + {base_col}" if base_col else "")
    m = smf.ols(f, d).fit()
    ci = m.conf_int()
    rows = []
    for k in m.params.index:
        if "treatment" in k:
            tname = k.split("T.")[1].rstrip("]")
            rows.append(dict(response=resp, contrast=f"{tname} - {CTRL}", estimate=float(m.params[k]), se=float(m.bse[k]), lo=float(ci.loc[k, 0]), hi=float(ci.loc[k, 1]),
                             p=float(m.pvalues[k]), df=float(m.df_resid), model="ANCOVA (block + baseline)" if base_col else "RCBD ANOVA (block + treatment)"))
    out = pd.DataFrame(rows)
    out["p_holm"] = multipletests(out["p"], method="holm")[1]
    return out


def section_comparison(R: dict, engine) -> dict:
    fits = R["_fits"]
    emm, contrasts_time, effects = [], [], []
    for resp in RESPONSES:
        mr = fits.get((resp, "endpoint"))
        if mr is None:
            continue
        e = mr.emm.assign(response=resp)
        emm.append(e)
        contrasts_time.append(mr.contrasts)
        sd_tot = float(np.sqrt(mr.variance["plot"] ** 2 + mr.variance["resid"] ** 2))
        ctrl = mr.emm[(mr.emm["treatment"] == CTRL) & (mr.emm["campaign"] == ENDPOINT)]["emm"].iloc[0]
        for c in mr.contrasts[mr.contrasts["campaign"] == ENDPOINT].itertuples():
            effects.append(dict(response=resp, contrast=c.contrast, estimate=c.estimate, lo=c.lo, hi=c.hi, p_holm=c.p_holm,
                                pct_vs_control=100 * c.estimate / ctrl if ctrl else None, std_effect=c.estimate / sd_tot if sd_tot else None,
                                std_lo=c.lo / sd_tot if sd_tot else None, std_hi=c.hi / sd_tot if sd_tot else None, sd_plot_level=sd_tot))
    pt = _plot_table(R, engine)
    anc = []
    for resp in ("soc_pct", "ec_dsm", "ph", "bulk_density", "height_cm", "stem_diam_mm"):
        end = f"{resp}_{ENDPOINT}"
        base = f"{resp}_T0" if f"{resp}_T0" in pt and pt[f"{resp}_T0"].notna().sum() > 12 else None
        if end in pt and pt[end].notna().sum() > 12:
            anc.append(_ancova(pt, resp, base, end))
    return dict(responses=RESPONSES, emm=pd.concat(emm, ignore_index=True), contrasts_time=pd.concat(contrasts_time, ignore_index=True),
                endpoint_effects=effects, ancova=pd.concat(anc, ignore_index=True) if anc else [],
                notes=["Contrasts are treatment − control from the mixed model, Wald-t with RCBD error df (15), Holm-adjusted within each response × campaign.",
                       "Standardised effect = estimate ÷ plot-level SD (√(σ²plot+σ²resid)).",
                       "The baseline-adjusted ANCOVA is a sensitivity analysis on plot values; agreement between the two supports robustness."])


# ----------------------------------------------------------------------------- 4. mixed models


def _joint_f(mr, campaign: str) -> dict:
    res = mr.result
    names = list(res.fe_params.index)
    beta, cov = res.fe_params.to_numpy(), res.cov_params().loc[names, names].to_numpy()
    times = sorted({n.split("[T.")[1].rstrip("]") for n in names if n.startswith("C(time)[T.")} | {"T0"})

    def L(trt, time):
        v = np.zeros(len(names))
        v[names.index("Intercept")] = 1
        if trt != CTRL:
            v[names.index(f"C(trt, Treatment('CTRL'))[T.{trt}]")] = 1
        if time != times[0]:
            v[names.index(f"C(time)[T.{time}]")] = 1
            if trt != CTRL:
                v[names.index(f"C(trt, Treatment('CTRL'))[T.{trt}]:C(time)[T.{time}]")] = 1
        return v

    Lm = np.array([L(t, campaign) - L(CTRL, campaign) for t in TREATMENTS if t != CTRL])
    est = Lm @ beta
    V = Lm @ cov @ Lm.T
    chi2 = float(est @ np.linalg.solve(V, est))
    k = Lm.shape[0]
    dfd = (mr.n_blocks - 1) * (len(TREATMENTS) - 1)
    return dict(campaign=campaign, F=chi2 / k, df1=k, df2=dfd, p=float(stats.f.sf(chi2 / k, k, dfd)), chi2=chi2, p_chi2=float(stats.chi2.sf(chi2, k)))


def section_models(R: dict) -> dict:
    out = []
    for resp in RESPONSES:
        mr = R["_fits"].get((resp, "endpoint"))
        if mr is None:
            continue
        res = mr.result
        names = list(res.fe_params.index)
        fe = pd.DataFrame(dict(term=names, estimate=res.fe_params.values, se=res.bse_fe.values))
        fe["z"] = fe["estimate"] / fe["se"]
        fe["p_wald_z"] = 2 * stats.norm.sf(np.abs(fe["z"]))
        inter = [i for i, n in enumerate(names) if ":" in n]
        cov = res.cov_params().loc[names, names].to_numpy()
        bi = res.fe_params.to_numpy()[inter]
        chi2 = float(bi @ np.linalg.solve(cov[np.ix_(inter, inter)], bi))
        dfd = (mr.n_blocks - 1) * (len(TREATMENTS) - 1)
        interaction = dict(chi2=chi2, df=len(inter), p_chi2=float(stats.chi2.sf(chi2, len(inter))), F=chi2 / len(inter), df2=dfd,
                           p_F=float(stats.f.sf(chi2 / len(inter), len(inter), dfd)))
        v = {k: x**2 for k, x in mr.variance.items()}
        tot = sum(v.values())
        resid = np.asarray(res.resid, float)
        fitted = np.asarray(res.fittedvalues, float)
        sd = resid.std(ddof=1)
        z = (resid - resid.mean()) / sd
        order = np.argsort(z)
        theo = stats.norm.ppf((np.arange(1, len(z) + 1) - 0.5) / len(z))
        qq = [dict(theoretical=float(a), sample=float(b)) for a, b in zip(theo, z[order])]
        rng = np.random.default_rng(1)
        idx = np.arange(len(resid)) if len(resid) <= 400 else rng.choice(len(resid), 400, replace=False)
        rvf = [dict(fitted=float(fitted[i]), resid=float(z[i])) for i in idx]
        sw = stats.shapiro(resid)
        trt_of = np.asarray(res.model.data.frame["trt"])
        groups = [resid[trt_of == t] for t in TREATMENTS if (trt_of == t).any()]
        lev = stats.levene(*groups)
        try:
            blup = {str(k): float(np.asarray(v_).ravel()[0]) for k, v_ in res.random_effects.items()}
        except Exception:
            blup = {}
        out.append(dict(response=resp, label=RESPONSES[resp]["label"], unit=RESPONSES[resp]["unit"], n_obs=int(res.nobs), n_blocks=mr.n_blocks,
                        formula="y ~ treatment × campaign + (1 | block) + (1 | plot)", method="REML", converged=bool(getattr(res, "converged", True)),
                        fixed_effects=fe, interaction_wald=interaction,
                        endpoint_joint_F=_joint_f(mr, ENDPOINT), mid_joint_F=_joint_f(mr, MID_TRIAL),
                        variance=dict(sd=mr.variance, var=v, icc_block=v["block"] / tot, icc_plot=v["plot"] / tot, icc_block_plot=(v["block"] + v["plot"]) / tot),
                        boundary=bool(mr.variance["block"] < 1e-6 or mr.variance["plot"] < 1e-6),
                        diagnostics=dict(shapiro_W=float(sw.statistic), shapiro_p=float(sw.pvalue), levene_W=float(lev.statistic), levene_p=float(lev.pvalue),
                                         qq=qq, resid_vs_fitted=rvf), block_blup=blup))
    return dict(models=out, notes=["Fixed-effect p-values in this table are asymptotic Wald z and are anti-conservative with 6 blocks; treatment contrasts use RCBD error df instead.",
                                   "Joint F tests use the 3 treatment−control contrasts at the stated campaign with den. df = 15.",
                                   "A variance component at the boundary (≈0) means the data cannot separate that source from residual: report it, do not drop it silently.",
                                   "Kenward–Roger degrees of freedom would be the refinement for unbalanced data."])


# ----------------------------------------------------------------------------- 5. inference


def _perm_test(pt: pd.DataFrame, col: str, base: str | None, B: int, seed: int) -> list[dict]:
    d = pt.dropna(subset=[col] + ([base] if base else [])).copy()
    y = (d[col] - d[base]) if base else d[col]
    d["y"] = y.astype(float)
    blocks = sorted(d["block"].unique())
    if any(len(d[d["block"] == b]) != len(TREATMENTS) for b in blocks):
        return []
    mat = np.array([[d[(d["block"] == b) & (d["treatment"] == t)]["y"].iloc[0] for t in TREATMENTS] for b in blocks])  # (b, t)
    rng = np.random.default_rng(seed)
    ci = list(TREATMENTS).index(CTRL)
    obs = mat.mean(0) - mat[:, ci].mean()
    perm = np.empty((B, len(TREATMENTS)))
    for i in range(B):
        shuffled = rng.permuted(mat, axis=1)
        perm[i] = shuffled.mean(0) - shuffled[:, ci].mean()
    rows = []
    for j, t in enumerate(TREATMENTS):
        if t == CTRL:
            continue
        p = (1 + np.sum(np.abs(perm[:, j]) >= abs(obs[j]) - 1e-12)) / (B + 1)
        rows.append(dict(contrast=f"{t} - {CTRL}", estimate=float(obs[j]), p_perm=float(p)))
    adj = multipletests([r["p_perm"] for r in rows], method="holm")[1]
    for r, a in zip(rows, adj):
        r["p_perm_holm"] = float(a)
    return rows


def section_inference(R: dict, engine, cfg: TrialConfig, B: int) -> dict:
    con = R["contrasts"].copy()
    con["is_primary"] = (con["response"] == PRIMARY) & (con["review"] == "endpoint") & (con["depth"] == "0-15 cm")
    con["significant_holm"] = con["p_holm"] < 0.05
    pt = _plot_table(R, engine)
    perm = []
    for resp, base in (("soc_pct", "soc_pct_T0"), ("ec_dsm", "ec_dsm_T0"), ("height_cm", None), ("stem_diam_mm", None)):
        end = f"{resp}_{ENDPOINT}"
        if end in pt:
            for r in _perm_test(pt, end, base if base in pt else None, B, cfg.seed + len(resp)):
                perm.append(dict(response=resp, statistic="endpoint − baseline change" if base else "endpoint value", n_perm=B, **r))
    # agreement of the model-based and design-based inference
    mod = R["contrasts"][(R["contrasts"]["review"] == "endpoint") & (R["contrasts"]["depth"].isin(["0-15 cm", "trees (plot means)"]))]
    agree = []
    for r in perm:
        m = mod[(mod["response"] == r["response"]) & (mod["contrast"] == r["contrast"])]
        if len(m):
            agree.append(dict(response=r["response"], contrast=r["contrast"], p_model_holm=float(m["p_holm"].iloc[0]), p_perm_holm=r["p_perm_holm"],
                              same_decision=bool((m["p_holm"].iloc[0] < 0.05) == (r["p_perm_holm"] < 0.05))))
    return dict(contrasts=con, randomisation_tests=perm, agreement=agree, alpha=0.05,
                multiplicity=dict(within="Holm over the 3 treatment-vs-control contrasts per response × campaign",
                                  across="No adjustment across responses; SOC at the endpoint (0–15 cm) is the single pre-specified primary outcome. All other rows are secondary or exploratory.",
                                  mid_trial="Mid-trial (T2) results are interim and exploratory; they do not replace the endpoint analysis."),
                notes=["Randomisation test: treatment labels permuted within blocks (the actual randomisation scheme), two-sided, Holm-adjusted.",
                       "With 6 blocks the smallest attainable permutation p-value is limited only by the number of permutations, but the model-based CI relies on a normality assumption: compare both.",
                       "Report effect sizes with intervals; 'not significant' means 'not distinguishable from zero with this precision', not 'no effect'."])


# ----------------------------------------------------------------------------- 6. power


def section_power(R: dict, cfg: TrialConfig) -> dict:
    sig_design = P.error_sd_single_time(cfg.assumed_sd_plot, cfg.assumed_sd_resid)
    sig_base = float(R["baseline_variance"]["sd_resid"])
    blocks = list(range(3, 13))
    scen = {"design assumption": sig_design, "baseline estimate (T0)": sig_base, "pessimistic (+50 %)": 1.5 * max(sig_design, sig_base)}
    mdd = [dict(scenario=k, sigma=s, n_blocks=b, mdd_80=P.min_detectable_difference(b, s)) for k, s in scen.items() for b in blocks]
    deltas = np.round(np.linspace(0, 0.25, 26), 3)
    curves = [dict(n_blocks=b, delta=float(d), power=P.contrast_power(b, float(d), sig_design)) for b in (4, 6, 8, 10) for d in deltas]
    needed = [dict(delta=d, scenario=k, blocks=P.blocks_needed(d, s)) for d in (0.05, 0.08, 0.10, 0.15) for k, s in scen.items()]
    return dict(sigma=scen, mdd=mdd, power_curves=curves, blocks_needed=needed, analytic=R["power_table"], monte_carlo=R["power_mc"],
                survival=R["power_survival"], assumed_effects=P.DEFAULT_EFFECTS, design_blocks=cfg.n_blocks,
                notes=["Prospective (design-stage) power only. Observed/post-hoc power is not reported because it is a function of the p-value and carries no extra information.",
                       "Analytic power: non-central t, Bonferroni for 3 comparisons, df = (b−1)(t−1). Monte-Carlo power uses the production mixed model.",
                       "Sensitivity: the minimum detectable difference scales linearly with σ, so a pessimistic variance assumption is shown next to the design and baseline-updated values.",
                       "Assumed effect sizes are planning values, not findings."])


# ----------------------------------------------------------------------------- 7. agronomy


def _weekly(df: pd.DataFrame, tcol: str, vcol: str) -> list[dict]:
    d = df.copy()
    d[tcol] = pd.to_datetime(d[tcol], utc=True).dt.tz_localize(None)
    d["week"] = d[tcol].dt.to_period("W").dt.start_time
    g = d.groupby(["week", "treatment"])[vcol].mean().reset_index()
    return [dict(date=r.week.date().isoformat(), treatment=r.treatment, value=float(getattr(r, vcol))) for r in g.itertuples()]


def section_agronomy(R: dict, engine) -> dict:
    ir = R["irrigation_plot"]
    ir_sum = ir.groupby("treatment")["irrigation_mm"].agg(["mean", "std", "count"]).reset_index()
    pt = _plot_table(R, engine)
    vwc = _weekly(read_sql(engine, "SELECT * FROM mart.v_vwc_daily_treatment"), "time", "vwc")
    ndvi = _weekly(read_sql(engine, "SELECT * FROM mart.v_ndvi_treatment"), "time", "ndvi")
    cum = read_sql(engine, "SELECT * FROM mart.v_irrigation_cum_treatment")
    cum_w = _weekly(cum, "time", "cum_irrigation_kl")
    wx = read_sql(engine, "SELECT * FROM mart.weather_clean ORDER BY date")
    wx["month"] = pd.to_datetime(wx["date"]).dt.to_period("M").astype(str)
    monthly = wx.groupby("month").agg(tmax=("tmax", "mean"), tmin=("tmin", "mean"), rain_mm=("rain_mm", "sum"), et0_mm=("et0_mm", "sum"), rs_mj=("rs_mj", "mean")).reset_index()
    cal = R["calibration_params"]
    cal_sum = cal.groupby("treatment")[["theta_fc", "kc_min", "kc_max"]].agg(["mean", "std"])
    cal_sum.columns = [f"{a}_{b}" for a, b in cal_sum.columns]
    ev = R["calibration_eval"]
    ev_sum = dict(cal_vwc_rmse=float(ev["cal_vwc_rmse"].mean()), val_vwc_rmse=float(ev["val_vwc_rmse"].mean()),
                  cal_irr_err_pct_median=float(ev["cal_irr_total_err_pct"].median()), val_irr_err_pct_median=float(ev["val_irr_total_err_pct"].median()),
                  val_irr_err_pct_abs_max=float(ev["val_irr_total_err_pct"].abs().max()))
    aq = R["aquacrop"].groupby(["treatment", "strategy"])[["irrigation_mm", "dry_yield_t_ha", "deep_perc_mm", "wp_kg_m3"]].mean().reset_index()
    scatter = [dict(plot_id=r.plot_id, treatment=r.treatment, irrigation_mm=float(r.irrigation_mm), height=float(r.height_cm_T4), soc=float(r.soc_pct_T4))
               for r in pt.dropna(subset=["irrigation_mm", "height_cm_T4", "soc_pct_T4"]).itertuples()]
    ref = None
    if R.get("reanalysis_present"):
        ref = dict(variables=R["weather_cmp_vars"], et0=R["weather_cmp_et0"], et0_totals_mm=R["weather_cmp_totals"])
    nb = R.get("ndvi_block")
    return dict(irrigation=dict(plots=ir, summary=ir_sum, anova=R["irrigation_anova"], contrasts=R["irrigation_contrasts"]),
                vwc_weekly=vwc, ndvi_weekly=ndvi, cum_irrigation_weekly=cum_w, weather_monthly=monthly,
                calibration=dict(by_treatment=cal_sum.reset_index(), evaluation=ev_sum, anova_theta_fc=dict(F=R["calibration_anova_fc"]["F"], p=R["calibration_anova_fc"]["p"])),
                aquacrop=aq, irrigation_vs_growth=scatter, reanalysis=ref,
                ndvi_block_real=(nb if nb is not None else []),
                notes=["Irrigation depth per plot: QC-gap days are filled with the plot's own mean (plots with < 95 % coverage excluded).",
                       "FAO-56 single-Kc parameters are calibrated per plot and judged on a held-out period, not on the fit.",
                       "AquaCrop rows are scenario-model outputs (not measurements): use them to compare strategies, not as yield estimates.",
                       "NDVI from Sentinel-2 at 10 m is block-level background only: plots (12×20 m) have too few pure pixels for plot-level inference."])


# ----------------------------------------------------------------------------- 8. survival


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def section_survival(R: dict) -> dict:
    from lifelines import KaplanMeierFitter
    from lifelines.statistics import multivariate_logrank_test, proportional_hazard_test

    fr = R["survival_frame"]
    km = []
    for t in TREATMENTS:
        g = fr[fr["treatment"] == t]
        k = KaplanMeierFitter().fit(g["duration"], g["event"], label=t)
        sf, ci = k.survival_function_, k.confidence_interval_
        for (d, s), (_, row) in zip(sf.iloc[:, 0].items(), ci.iterrows()):
            km.append(dict(treatment=t, day=float(d), survival=float(s), lo=float(row.iloc[0]), hi=float(row.iloc[1])))
    tr = R["_trees"]
    counts = []
    for (t, c), g in tr.groupby(["treatment", "campaign"]):
        n, k = int(len(g)), int(g["alive"].sum())
        lo, hi = _wilson(k, n)
        counts.append(dict(treatment=t, campaign=c, day=CAMPAIGNS[c], n=n, alive=k, proportion=k / n, lo=lo, hi=hi))
    events = fr.groupby("treatment").agg(trees=("event", "size"), deaths=("event", "sum")).reset_index()
    ph = None
    try:
        X = pd.get_dummies(fr["treatment"]).astype(float).drop(columns=[CTRL])
        d = pd.concat([fr[["duration", "event", "block"]], X], axis=1)
        from lifelines import CoxPHFitter
        m = CoxPHFitter().fit(d, "duration", "event", strata=["block"])
        r = proportional_hazard_test(m, d, time_transform="rank")
        ph = dict(table=r.summary.reset_index(), note="Schoenfeld-residual test (rank time) on the block-stratified model without robust SE.")
    except Exception as e:  # noqa: BLE001 - diagnostic must not break the run
        ph = dict(table=[], note=f"PH test unavailable: {type(e).__name__}")
    lr = multivariate_logrank_test(fr["duration"], fr["treatment"], fr["event"])
    return dict(kaplan_meier=km, counts=counts, events=events, cox=R["cox"], cox_mid=R["cox_mid"], glm_endpoint=R["survival_glm"],
                proportional_hazards=ph, logrank_unstratified=dict(chi2=float(lr.test_statistic), df=int(len(TREATMENTS) - 1), p=float(lr.p_value)),
                survival_power=R["power_survival"],
                notes=["Primary: Cox PH stratified by block with plot-clustered robust SE (trees within a plot are not independent). Cross-check: plot-level quasi-binomial GLM at the endpoint.",
                       "Event time is interval-censored to the campaign schedule (0, 91, 182, 273, 364 d); a tree first recorded dead at a visit is assigned that visit's day.",
                       "Survival power with 6 blocks is modest: wide intervals are expected and should be reported, not hidden.",
                       "Moisture-threshold 'survival' of soil is controller-driven by irrigation, hence circular; it is deliberately not presented as inference."])


# ----------------------------------------------------------------------------- 9. evaluation


def section_evaluation(R: dict) -> dict:
    out = dict(coverage_soc=R["coverage_soc"], coverage_ec=R["coverage_ec"], n_replicates=R["n_cov"],
               notes=["Closed-loop validation: the whole trial is re-simulated with known effects and re-analysed with the production model.",
                      "Coverage near 95 % supports the interval construction; with few replicates the Monte-Carlo error of a coverage estimate is about ±2–3 points (≥ 100 replicates).",
                      "These checks validate the machinery on synthetic data. They do not validate the assumptions on real trial data."])
    if "qc_recall" in R:
        out["qc_recall"] = R["qc_recall"]
    if "qc_summary" in R:
        out["qc_summary"] = R["qc_summary"]
    if "reconciliation_summary" in R:
        out["reconciliation_summary"] = R["reconciliation_summary"]
    return out


# ----------------------------------------------------------------------------- orchestration


def build_sections(engine, R: dict, cfg: TrialConfig, n_perm: int = 5000) -> dict:
    return {
        "eda": section_eda(R, engine),
        "design": section_design(R, cfg, engine),
        "comparison": section_comparison(R, engine),
        "models": section_models(R),
        "inference": section_inference(R, engine, cfg, n_perm),
        "power": section_power(R, cfg),
        "agronomy": section_agronomy(R, engine),
        "survival": section_survival(R),
        "evaluation": section_evaluation(R),
    }


def _software() -> dict:
    import lifelines
    import scipy

    return dict(aridtrial=__version__, python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, scipy=scipy.__version__,
                statsmodels=sm.__version__, lifelines=lifelines.__version__)


def persist(engine, sections: dict, cfg: TrialConfig, dataset_version: str | None, qc_run_id: int | None, settings: dict, quick: bool,
            data_origin: str = "synthetic") -> str:
    payloads = {k: clean(v) for k, v in sections.items()}
    digests = {k: hashlib.sha256(_canonical(v).encode()).hexdigest() for k, v in payloads.items()}
    h = hashlib.sha256(_canonical(digests).encode()).hexdigest()
    run_id = f"ar-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{h[:8]}"
    with engine.begin() as c:
        c.execute(text("""INSERT INTO results.analysis_run (run_id, dataset_version_id, qc_run_id, seed, data_origin, quick, settings, software)
                          VALUES (:r, :dv, :qc, :seed, :o, :q, CAST(:s AS jsonb), CAST(:sw AS jsonb))"""),
                  dict(r=run_id, dv=dataset_version, qc=qc_run_id, seed=cfg.seed, o=data_origin, q=quick, s=_canonical(clean(settings)), sw=_canonical(_software())))
        for k, v in payloads.items():
            c.execute(text("INSERT INTO results.analysis_artifact (run_id, section, payload, sha256) VALUES (:r, :s, CAST(:p AS jsonb), :h)"),
                      dict(r=run_id, s=k, p=_canonical(v), h=digests[k]))
    return run_id


def run_and_persist(engine, R: dict, cfg: TrialConfig, quick: bool = False) -> str:
    """Build every dashboard section from an analysis result `R` and store it with provenance."""
    n_perm = 1000 if quick else 5000
    with engine.connect() as c:
        qc_run = c.execute(text("SELECT max(run_id) FROM audit.qc_run")).scalar()
        dv_id = c.execute(text("SELECT version_id FROM audit.dataset_version ORDER BY created_at DESC LIMIT 1")).scalar()
    dv = (R.get("dataset_version") or {}).get("version_id") or dv_id
    sections = build_sections(engine, R, cfg, n_perm=n_perm)
    settings = dict(n_permutations=n_perm, n_coverage_reps=R.get("n_cov"), alpha=0.05, primary_outcome="soc_pct @ T4, 0-15 cm", mid_trial=MID_TRIAL, endpoint=ENDPOINT)
    return persist(engine, sections, cfg, dv, int(qc_run) if qc_run is not None else None, settings, quick)


def validate_run_id(run_id: str) -> bool:
    return bool(re.fullmatch(r"ar-\d{14}-[0-9a-f]{8}", run_id))
