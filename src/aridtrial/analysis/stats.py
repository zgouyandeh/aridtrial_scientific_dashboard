"""Statistical analysis of the trial (mid-trial and endpoint reviews).

Design-based reasoning: treatments are randomised to plots within blocks (RCBD), so the plot is the
experimental unit. Trees, soil layers and repeated visits are *subsamples / repeated measures* inside
a plot and are never treated as independent replicates.

Models
  * continuous responses (SOC, ECe, pH, bulk density, plot-mean tree height / diameter, NDVI):
        y ~ treatment * campaign + (1 | block) + (1 | plot)          REML mixed model, one fit per response
    treatment-vs-control contrasts at each campaign with Wald-t (RCBD error df) and Holm adjustment
  * tree survival: Cox PH stratified by block with plot-clustered robust SE (primary) and a plot-level
    quasi-binomial GLM at the endpoint (cross-check)
  * seasonal irrigation depth: RCBD ANOVA
Everything reads QC-filtered marts only.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multitest import multipletests

from ..config import CAMPAIGNS, ENDPOINT, MID_TRIAL, TREATMENTS, TrialConfig
from ..design.power import fit_rm_model
from ..pipeline.db import read_sql

CTRL = "CTRL"
TRT_TERM = "C(trt, Treatment('CTRL'))"


# ----------------------------------------------------------------------------- loaders


def load_soil(engine, depth_top_cm: int = 0) -> pd.DataFrame:
    return read_sql(engine, "SELECT * FROM mart.soil_clean WHERE depth_top_cm = :d", d=depth_top_cm)


def load_tree_plot_means(engine) -> pd.DataFrame:
    """Plot-mean height / diameter of living trees per campaign (survivor-conditional)."""
    t = read_sql(engine, "SELECT * FROM mart.tree_clean")
    g = t[t["alive"]].groupby(["plot_id", "block", "treatment", "campaign"]).agg(
        height_cm=("height_cm", "mean"), stem_diam_mm=("stem_diam_mm", "mean"), n_alive=("alive", "size")).reset_index()
    return g


# ----------------------------------------------------------------------------- mixed models


@dataclass
class MixedResult:
    response: str
    result: object
    n_blocks: int
    variance: dict
    emm: pd.DataFrame
    contrasts: pd.DataFrame


def _prep(df: pd.DataFrame, y: str) -> pd.DataFrame:
    d = df.dropna(subset=[y]).copy()
    d["y"] = d[y].astype(float)
    d["trt"] = d["treatment"].astype(str)
    d["time"] = d["campaign"].astype(str)
    d["plot"] = d["plot_id"].astype(str)
    return d


def fit_response(df: pd.DataFrame, response: str, campaigns: list[str] | None = None) -> MixedResult:
    """Fit the repeated-measures mixed model and derive EMMs and treatment-vs-control contrasts."""
    d = _prep(df, response)
    if campaigns:
        d = d[d["time"].isin(campaigns)]
    res = fit_rm_model(d)
    names = list(res.fe_params.index)
    beta = res.fe_params.to_numpy()
    cov = res.cov_params().loc[names, names].to_numpy()
    n_blocks = int(d["block"].nunique())
    df_err = (n_blocks - 1) * (len(TREATMENTS) - 1)
    times = sorted(d["time"].unique())

    def L_for(trt: str, time: str) -> np.ndarray:
        L = np.zeros(len(names))
        L[names.index("Intercept")] = 1.0
        if trt != CTRL:
            L[names.index(f"{TRT_TERM}[T.{trt}]")] = 1.0
        if time != times[0]:
            L[names.index(f"C(time)[T.{time}]")] = 1.0
            if trt != CTRL:
                L[names.index(f"{TRT_TERM}[T.{trt}]:C(time)[T.{time}]")] = 1.0
        return L

    tcrit = stats.t.ppf(0.975, df_err)
    emm_rows = []
    for t in TREATMENTS:
        for tm in times:
            L = L_for(t, tm)
            est, se = float(L @ beta), float(np.sqrt(L @ cov @ L))
            emm_rows.append(dict(treatment=t, campaign=tm, emm=est, se=se, lo=est - tcrit * se, hi=est + tcrit * se))
    emm = pd.DataFrame(emm_rows)

    con_rows = []
    for tm in times:
        for t in TREATMENTS:
            if t == CTRL:
                continue
            L = L_for(t, tm) - L_for(CTRL, tm)
            est, se = float(L @ beta), float(np.sqrt(L @ cov @ L))
            tv = est / se if se > 0 else np.nan
            con_rows.append(dict(response=response, campaign=tm, contrast=f"{t} - {CTRL}", estimate=est, se=se, t=tv, df=df_err,
                                 p=float(2 * stats.t.sf(abs(tv), df_err)), lo=est - tcrit * se, hi=est + tcrit * se))
    con = pd.DataFrame(con_rows)
    con["p_holm"] = np.nan
    for tm, idx in con.groupby("campaign").groups.items():
        con.loc[idx, "p_holm"] = multipletests(con.loc[idx, "p"], method="holm")[1]

    # variance components (REML): block, plot, residual
    vc = dict(block=float(res.cov_re.iloc[0, 0]), plot=float(np.asarray(res.vcomp)[0]), resid=float(res.scale))
    vc_sd = {k: float(np.sqrt(max(v, 0.0))) for k, v in vc.items()}
    return MixedResult(response, res, n_blocks, vc_sd, emm, con)


def baseline_balance(df: pd.DataFrame, response: str) -> dict:
    """Randomisation check at T0: RCBD ANOVA of the baseline value on block + treatment."""
    d = df[df["campaign"] == "T0"].dropna(subset=[response])
    m = smf.ols(f"{response} ~ C(block) + C(treatment)", d).fit()
    a = sm.stats.anova_lm(m, typ=2)
    return dict(response=response, F=float(a.loc["C(treatment)", "F"]), p=float(a.loc["C(treatment)", "PR(>F)"]),
                means=d.groupby("treatment")[response].mean().round(4).to_dict())


def baseline_variance(df: pd.DataFrame, response: str) -> dict:
    """Variance components from T0 only; feeds back into the power analysis (design is re-checked at baseline)."""
    d = df[df["campaign"] == "T0"].dropna(subset=[response]).copy()
    d["y"] = d[response]
    m = smf.mixedlm("y ~ 1", d, groups=d["block"]).fit(reml=True)
    return dict(sd_block=float(np.sqrt(max(m.cov_re.iloc[0, 0], 0))), sd_resid=float(np.sqrt(m.scale)), n=len(d))


def residual_diagnostics(mr: MixedResult) -> dict:
    r = np.asarray(mr.result.resid)
    sw = stats.shapiro(r)
    return dict(response=mr.response, shapiro_W=float(sw.statistic), shapiro_p=float(sw.pvalue), resid_sd=float(r.std(ddof=1)),
                max_abs_resid=float(np.abs(r).max()))


# ----------------------------------------------------------------------------- survival


def tree_survival_frame(tree_clean: pd.DataFrame) -> pd.DataFrame:
    """One row per tree: duration to first campaign recorded dead (event) or end of trial (censored)."""
    end_day = CAMPAIGNS[ENDPOINT]
    rows = []
    for tid, g in tree_clean.sort_values("campaign").groupby("tree_id"):
        g = g.assign(day=g["campaign"].map(CAMPAIGNS)).sort_values("day")
        dead = g[~g["alive"]]
        ev = len(dead) > 0
        rows.append(dict(tree_id=tid, plot_id=g["plot_id"].iloc[0], block=int(g["block"].iloc[0]), treatment=g["treatment"].iloc[0],
                         duration=float(dead["day"].iloc[0]) if ev else float(end_day), event=int(ev)))
    return pd.DataFrame(rows)


def cox_survival(tree_clean: pd.DataFrame) -> dict:
    from lifelines import CoxPHFitter

    s = tree_survival_frame(tree_clean)
    X = pd.get_dummies(s["treatment"]).astype(float).drop(columns=[CTRL])
    d = pd.concat([s[["duration", "event", "block", "plot_id"]], X], axis=1)
    cph = CoxPHFitter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cph.fit(d, duration_col="duration", event_col="event", strata=["block"], cluster_col="plot_id", robust=True)
    sm_ = cph.summary
    out = pd.DataFrame(dict(contrast=[f"{t} vs {CTRL}" for t in sm_.index], HR=np.exp(sm_["coef"]).values,
                            lo=sm_["exp(coef) lower 95%"].values, hi=sm_["exp(coef) upper 95%"].values, p=sm_["p"].values))
    out["p_holm"] = multipletests(out["p"], method="holm")[1]
    return dict(table=out, model=cph, frame=s)


def endpoint_survival_glm(tree_clean: pd.DataFrame, n_trees: int = 10) -> pd.DataFrame:
    """Plot-level quasi-binomial GLM at the endpoint: survival ~ block + treatment (Pearson-scaled)."""
    e = tree_clean[tree_clean["campaign"] == ENDPOINT].groupby(["plot_id", "block", "treatment"]).agg(prop=("alive", "mean"), n=("alive", "size")).reset_index()
    e["block"] = e["block"].astype(str)
    m = smf.glm("prop ~ C(block) + C(treatment, Treatment('CTRL'))", e, family=sm.families.Binomial(), var_weights=e["n"].astype(float)).fit(scale="X2")
    rows = []
    for k in m.params.index:
        if "treatment" in k:
            t = k.split("T.")[1].rstrip("]")
            ci = m.conf_int().loc[k]
            rows.append(dict(contrast=f"{t} vs {CTRL}", odds_ratio=float(np.exp(m.params[k])), lo=float(np.exp(ci[0])), hi=float(np.exp(ci[1])), p=float(m.pvalues[k])))
    out = pd.DataFrame(rows)
    out["p_holm"] = multipletests(out["p"], method="holm")[1]
    return out


# ----------------------------------------------------------------------------- water


def irrigation_by_plot(meter_clean: pd.DataFrame, area_m2: float, min_coverage: float = 0.95) -> pd.DataFrame:
    """Seasonal irrigation depth (mm) per plot. Days lost to QC (register resets) are filled with the
    plot's own mean daily depth so the total is not biased downward; plots below `min_coverage` are dropped."""
    m = meter_clean.copy()
    g = m.groupby(["plot_id", "block", "treatment"]).agg(n_days=("date", "size"), n_valid=("volume_l", lambda x: x.notna().sum()),
                                                        vol=("volume_l", "sum"), mean_vol=("volume_l", "mean")).reset_index()
    g["coverage"] = g["n_valid"] / g["n_days"]
    g["irrigation_mm"] = (g["vol"] + (g["n_days"] - g["n_valid"]) * g["mean_vol"]) / area_m2
    return g[g["coverage"] >= min_coverage]


def rcbd_anova(df: pd.DataFrame, response: str) -> dict:
    m = smf.ols(f"{response} ~ C(block) + C(treatment, Treatment('CTRL'))", df).fit()
    a = sm.stats.anova_lm(m, typ=2)
    row = [i for i in a.index if "treatment" in i][0]
    rows = []
    for k in m.params.index:
        if "treatment" in k:
            ci = m.conf_int().loc[k]
            rows.append(dict(contrast=k.split("T.")[1].rstrip("]") + f" - {CTRL}", estimate=float(m.params[k]), lo=float(ci[0]), hi=float(ci[1]), p=float(m.pvalues[k])))
    con = pd.DataFrame(rows)
    con["p_holm"] = multipletests(con["p"], method="holm")[1]
    return dict(F=float(a.loc[row, "F"]), p=float(a.loc[row, "PR(>F)"]), contrasts=con, means=df.groupby("treatment")[response].mean().to_dict())


# ----------------------------------------------------------------------------- closed-loop validation


def validate_coverage(cfg: TrialConfig, n_rep: int = 100, response: str = "soc_pct", depth_top_cm: int = 0, seed: int = 11) -> pd.DataFrame:
    """Simulate the whole trial n_rep times with *known* effects, analyse each with the production model, and report
    bias, RMSE and 95 % CI coverage of the endpoint treatment-vs-control contrasts."""
    from ..design.layout import make_layout
    from ..simulate.field import Truth, simulate_field
    from ..simulate.weather import simulate_weather

    tr = Truth()
    truth_map = dict(soc_pct=tr.soc_effect_end, ec_dsm=tr.ec_effect_end)[response]
    w = simulate_weather(cfg)
    recs = []
    for r in range(n_rep):
        c = TrialConfig(seed=cfg.seed + 1000 + r)
        lay = make_layout(c)
        sim = simulate_field(c, lay, w, tr, fast=True, seed_offset=r)
        soil = sim["soil"].merge(lay[["plot_id", "block", "treatment"]], on="plot_id")
        soil = soil[soil["depth_top_cm"] == depth_top_cm]
        mr = fit_response(soil, response)
        con = mr.contrasts[mr.contrasts["campaign"] == ENDPOINT]
        for x in con.itertuples():
            t = x.contrast.split(" - ")[0]
            true = truth_map[t] * (1 - np.exp(-(CAMPAIGNS[ENDPOINT] - 7) / (tr.soc_tau if response == "soc_pct" else tr.ec_tau)))
            recs.append(dict(rep=r, treatment=t, est=x.estimate, true=true, covered=(x.lo <= true <= x.hi), reject=x.p_holm < 0.05))
    d = pd.DataFrame(recs)
    return d.groupby("treatment").agg(true=("true", "first"), mean_est=("est", "mean"), bias=("est", lambda s: s.mean() - d.loc[s.index, "true"].mean()),
                                      rmse=("est", lambda s: float(np.sqrt(((s - d.loc[s.index, "true"]) ** 2).mean()))),
                                      coverage95=("covered", "mean"), power_holm=("reject", "mean")).reset_index()
