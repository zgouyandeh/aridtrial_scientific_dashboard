import numpy as np
import pandas as pd
import pytest

from aridtrial.analysis import stats as S
from aridtrial.config import ENDPOINT, TrialConfig
from aridtrial.models import aquacrop_model as AQ
from aridtrial.models import calibration as C


def test_endpoint_effects_cover_truth_on_loaded_data(loaded):
    e = loaded["engine"]
    soil = S.load_soil(e, 0)
    mr = S.fit_response(soil, "soc_pct")
    c = mr.contrasts[mr.contrasts.campaign == ENDPOINT].set_index("contrast")
    truth = {"COMP - CTRL": 0.15, "BIOC - CTRL": 0.20, "COMP_BIOC - CTRL": 0.38}
    for k, v in truth.items():
        assert c.loc[k, "lo"] <= v <= c.loc[k, "hi"], k
    assert (c["p_holm"] < 0.001).all()


def test_randomisation_check_at_baseline_does_not_reject(loaded):
    soil = S.load_soil(loaded["engine"], 0)
    assert S.baseline_balance(soil, "soc_pct")["p"] > 0.05


def test_survival_cox_direction_and_ordering(loaded):
    trees = pd.read_sql("SELECT * FROM mart.tree_clean", loaded["engine"])
    t = S.cox_survival(trees)["table"].set_index("contrast")
    assert (t["HR"] < 1).all()
    assert t.loc["COMP_BIOC vs CTRL", "HR"] < t.loc["BIOC vs CTRL", "HR"]
    assert t.loc["COMP_BIOC vs CTRL", "p_holm"] < 0.01


def test_closed_loop_coverage_is_near_nominal():
    d = S.validate_coverage(TrialConfig(), n_rep=40, response="soc_pct")
    assert (d["coverage95"] >= 0.85).all()
    assert d["bias"].abs().max() < 0.01  # |bias| << effect sizes (0.15-0.38)


def test_calibration_recovers_known_parameters_noise_free():
    rng = np.random.default_rng(0)
    n = 365
    et0 = 4 + 4 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 0.2, n)
    rain = np.zeros(n)
    canopy = np.clip(np.linspace(0.05, 1, n), 0, 1)
    true = (0.16, 0.20, 0.45)
    vwc, irr = C.simulate_plot(true, et0, rain, canopy)
    s = C.PlotSeries("X", et0, rain, canopy, vwc, irr)
    p = C.calibrate_plot(s, slice(0, 365), seed=1, maxiter=80)
    assert p["theta_fc"] == pytest.approx(true[0], abs=0.006)
    ev = C.evaluate(s, (p["theta_fc"], p["kc_min"], p["kc_max"]), slice(0, 365))
    assert abs(ev["irr_total_err_pct"]) < 3.0


def test_aquacrop_runs_and_yield_responds_to_field_capacity_under_deficit(loaded):
    from aridtrial.pipeline.db import read_sql

    w = read_sql(loaded["engine"], "SELECT * FROM mart.weather_clean ORDER BY date")
    aw = AQ.aquacrop_weather(w)
    lo = AQ.run_forage_season(0.11, aw, "2025/10/01", "2026/09/30", "03/01", strategy="interval")
    hi = AQ.run_forage_season(0.20, aw, "2025/10/01", "2026/09/30", "03/01", strategy="interval")
    assert np.isfinite(lo["dry_yield_t_ha"]) and hi["dry_yield_t_ha"] > lo["dry_yield_t_ha"] * 1.2
    full = AQ.run_forage_season(0.11, aw, "2025/10/01", "2026/09/30", "03/01", strategy="net")
    assert full["irrigation_mm"] > 2 * lo["irrigation_mm"]
