"""Power and minimum-detectable-difference analysis for the trial.

Two layers, deliberately redundant so each can validate the other:

* analytic (noncentral F / noncentral t) for a single-time RCBD comparison;
* Monte-Carlo with the *same mixed model the analysis will use* (repeated measures,
  block random intercept + plot variance component) and, for tree survival, a
  plot-level binomial GLM with 10 trees per plot.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from scipy.optimize import brentq

from ..config import AMENDMENT_DAY, CAMPAIGNS, TREATMENTS

# ----------------------------------------------------------------------------- analytic


def error_sd_single_time(sd_plot: float, sd_resid: float, n_sub: int = 1) -> float:
    """SD of an individual plot observation after removing block effects."""
    return float(np.sqrt(sd_plot**2 + sd_resid**2 / n_sub))


def rcbd_f_power(n_blocks: int, effects: np.ndarray, sigma: float, alpha: float = 0.05) -> float:
    """Power of the omnibus treatment F-test in an RCBD (fixed blocks).

    lambda = b * sum_i (tau_i - mean(tau))^2 / sigma^2 ; df1 = t-1 ; df2 = (b-1)(t-1).
    """
    effects = np.asarray(effects, float)
    t = len(effects)
    df1, df2 = t - 1, (n_blocks - 1) * (t - 1)
    lam = n_blocks * np.sum((effects - effects.mean()) ** 2) / sigma**2
    fcrit = stats.f.ppf(1 - alpha, df1, df2)
    return float(stats.ncf.sf(fcrit, df1, df2, lam))


def contrast_power(n_blocks: int, delta: float, sigma: float, alpha: float = 0.05, m: int = 3) -> float:
    """Power of one treatment-vs-control comparison, Bonferroni-adjusted for m comparisons."""
    t = len(TREATMENTS)
    df = (n_blocks - 1) * (t - 1)
    a = alpha / m
    tcrit = stats.t.ppf(1 - a / 2, df)
    ncp = delta / (sigma * np.sqrt(2.0 / n_blocks))
    lower = np.nan_to_num(stats.nct.cdf(-tcrit, df, ncp), nan=0.0)  # nct.cdf is NaN-prone for large ncp; true value ~0
    return float(stats.nct.sf(tcrit, df, ncp) + lower)


def min_detectable_difference(n_blocks: int, sigma: float, power: float = 0.8, alpha: float = 0.05, m: int = 3) -> float:
    f = lambda d: contrast_power(n_blocks, d, sigma, alpha, m) - power
    hi = 9.0 * sigma * np.sqrt(2.0 / n_blocks)  # noncentrality ~9: power ~ 1
    return float(brentq(f, 1e-9, hi))


def blocks_needed(delta: float, sigma: float, power: float = 0.8, alpha: float = 0.05, m: int = 3, max_blocks: int = 40) -> int:
    for b in range(2, max_blocks + 1):
        if contrast_power(b, delta, sigma, alpha, m) >= power:
            return b
    return max_blocks


# ----------------------------------------------------------------------------- Monte-Carlo (mixed model)


@dataclass(frozen=True)
class SocScenario:
    effects_end: dict  # treatment -> true difference from control at endpoint (% points SOC)
    sd_block: float = 0.040
    sd_plot: float = 0.030
    sd_resid: float = 0.020
    tau_days: float = 60.0  # approach to the endpoint effect after amendment incorporation


DEFAULT_EFFECTS = {"CTRL": 0.0, "COMP": 0.10, "BIOC": 0.12, "COMP_BIOC": 0.22}


def _simulate_soc(n_blocks: int, sc: SocScenario, rng: np.random.Generator) -> pd.DataFrame:
    trts = list(TREATMENTS)
    rows = []
    u = rng.normal(0, sc.sd_block, n_blocks)
    for b in range(n_blocks):
        order = rng.permutation(trts)
        for trt in order:
            v = rng.normal(0, sc.sd_plot)
            for tname, day in CAMPAIGNS.items():
                lag = max(day - AMENDMENT_DAY, 0)
                eff = sc.effects_end[trt] * (1 - np.exp(-lag / sc.tau_days)) / (1 - np.exp(-(364 - AMENDMENT_DAY) / sc.tau_days))
                y = 0.25 + u[b] + v + eff + rng.normal(0, sc.sd_resid)
                rows.append((b + 1, f"B{b + 1}-{trt}", trt, tname, y))
    return pd.DataFrame(rows, columns=["block", "plot", "trt", "time", "y"])


def fit_rm_model(df: pd.DataFrame):
    """Repeated-measures mixed model used by both the power study and the analysis (single source of truth)."""
    m = smf.mixedlm(
        "y ~ C(trt, Treatment('CTRL')) * C(time)",
        df,
        groups=df["block"],
        re_formula="1",
        vc_formula={"plot": "0 + C(plot)"},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return m.fit(reml=True, method="lbfgs")


def endpoint_contrast_pvalues(res, n_blocks: int, endpoint: str = "T4") -> dict:
    """Two-sided p-values for (trt - CTRL) at the endpoint.

    Wald t with the RCBD between-plot error df, (b-1)(t-1). statsmodels' own z-based p-values are
    anticonservative with 6 blocks; Kenward-Roger would be the refinement (see README, limitations).
    """
    names = list(res.fe_params.index)
    cov = res.cov_params().loc[names, names].to_numpy()
    beta = res.fe_params.to_numpy()
    df_err = (n_blocks - 1) * (len(TREATMENTS) - 1)
    out = {}
    for t in TREATMENTS:
        if t == "CTRL":
            continue
        L = np.zeros(len(names))
        L[names.index(f"C(trt, Treatment('CTRL'))[T.{t}]")] = 1.0
        L[names.index(f"C(trt, Treatment('CTRL'))[T.{t}]:C(time)[T.{endpoint}]")] = 1.0
        est, se = float(L @ beta), float(np.sqrt(L @ cov @ L))
        out[t] = float(2 * stats.t.sf(abs(est / se), df_err))
    return out


def power_soc_mixed(n_blocks: int, sc: SocScenario | None = None, n_sim: int = 200, alpha: float = 0.05,
                    seed: int = 1, m: int = 3) -> pd.DataFrame:
    """Monte-Carlo power for each treatment-vs-control endpoint contrast (Bonferroni over m)."""
    sc = sc or SocScenario(DEFAULT_EFFECTS)
    rng = np.random.default_rng(seed)
    hits = {t: 0 for t in TREATMENTS if t != "CTRL"}
    ok = 0
    for _ in range(n_sim):
        df = _simulate_soc(n_blocks, sc, rng)
        try:
            pv = endpoint_contrast_pvalues(fit_rm_model(df), n_blocks)
            for t in hits:
                if pv[t] < alpha / m:
                    hits[t] += 1
            ok += 1
        except Exception:  # singular fit etc.; count as non-converged, excluded from the denominator
            continue
    out = pd.DataFrame({"treatment": list(hits), "power": [h / max(ok, 1) for h in hits.values()]})
    out["n_blocks"], out["n_sim_ok"] = n_blocks, ok
    return out


# ----------------------------------------------------------------------------- Monte-Carlo (survival)


def power_survival(n_blocks: int, s_ctrl: float = 0.78, s_trt: float = 0.93, trees_per_plot: int = 10,
                   sd_plot_logit: float = 0.5, n_sim: int = 500, alpha: float = 0.05, seed: int = 2) -> float:
    """Power to detect s_trt vs s_ctrl at the endpoint with plot-level over-dispersion.

    Analysis: quasi-binomial GLM (logit, Pearson-scaled covariance) on plot-level counts,
    treatment vs control Wald test, one comparison (no multiplicity). Block is left out of the
    working model: with 2 plots per block a block fixed effect saturates the fit and produces
    perfect-separation artefacts that inflate power, so the conservative form is used.
    """
    rng = np.random.default_rng(seed)
    logit = lambda p: np.log(p / (1 - p))
    inv = lambda x: 1 / (1 + np.exp(-x))
    rejects, ok = 0, 0
    for _ in range(n_sim):
        rows = []
        ub = rng.normal(0, 0.3, n_blocks)
        for b in range(n_blocks):
            for arm, s in (("ctrl", s_ctrl), ("trt", s_trt)):
                p = inv(logit(s) + ub[b] + rng.normal(0, sd_plot_logit))
                rows.append((b, arm, rng.binomial(trees_per_plot, p)))
        d = pd.DataFrame(rows, columns=["block", "arm", "alive"])
        d["prop"] = d["alive"] / trees_per_plot
        d["n"] = float(trees_per_plot)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # proportion response + var_weights = trials. (A two-column endog with scale="X2"
                # ignores the trial counts in this statsmodels version and gives a ~50% type I error.)
                res = smf.glm("prop ~ C(arm)", d, family=sm.families.Binomial(), var_weights=d["n"]).fit(scale="X2")
            if res.pvalues["C(arm)[T.trt]"] < alpha:
                rejects += 1
            ok += 1
        except Exception:
            continue
    return rejects / max(ok, 1)


def analytic_power_table(sigma: float, delta: float, blocks=range(3, 11)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "n_blocks": list(blocks),
            "power_contrast": [contrast_power(b, delta, sigma) for b in blocks],
            "mdd_80": [min_detectable_difference(b, sigma) for b in blocks],
        }
    )
