import numpy as np
import pytest

from aridtrial.config import TrialConfig
from aridtrial.design import layout as L
from aridtrial.design import power as P


def test_rcbd_layout_is_valid_and_reproducible():
    cfg = TrialConfig()
    a, b = L.make_layout(cfg), L.make_layout(cfg)
    assert L.layout_hash(a) == L.layout_hash(b)
    L.check_rcbd(a)
    assert len(a) == cfg.n_plots
    assert (a.groupby("block")["treatment"].nunique() == 4).all()


def test_different_seed_changes_randomisation():
    a = L.make_layout(TrialConfig(seed=1))
    b = L.make_layout(TrialConfig(seed=2))
    assert L.layout_hash(a) != L.layout_hash(b)


def test_plots_do_not_overlap():
    import re

    lay = L.make_layout(TrialConfig())
    boxes = []
    for w in lay.geom_wkt:
        xy = np.array(re.findall(r"-?\d+\.?\d*", w), float).reshape(-1, 2)
        boxes.append((xy[:, 0].min(), xy[:, 0].max(), xy[:, 1].min(), xy[:, 1].max()))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            assert a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2]


def test_power_monotone_in_blocks_and_mdd_is_consistent():
    sig = 0.036
    pw = [P.contrast_power(b, 0.08, sig) for b in range(3, 10)]
    assert all(x <= y + 1e-12 for x, y in zip(pw, pw[1:]))
    mdd = P.min_detectable_difference(6, sig)
    assert P.contrast_power(6, mdd, sig) == pytest.approx(0.8, abs=1e-3)


def test_analytic_power_matches_simulation_for_single_time_contrast():
    """Monte-Carlo check of the noncentral-t formula (RCBD, one time point)."""
    from scipy import stats

    rng = np.random.default_rng(0)
    b, t, sigma, delta, m = 6, 4, 0.036, 0.08, 3
    hits, n = 0, 3000
    df = (b - 1) * (t - 1)
    for _ in range(n):
        blk = rng.normal(0, 0.05, b)[:, None]
        y = blk + rng.normal(0, sigma, (b, t))
        y[:, 1] += delta  # treatment 1 vs control (col 0)
        # RCBD residual
        r = y - y.mean(1, keepdims=True) - y.mean(0, keepdims=True) + y.mean()
        mse = (r ** 2).sum() / df
        d = y[:, 1].mean() - y[:, 0].mean()
        tv = d / np.sqrt(2 * mse / b)
        hits += 2 * stats.t.sf(abs(tv), df) < 0.05 / m
    assert hits / n == pytest.approx(P.contrast_power(b, delta, sigma), abs=0.04)


def test_mixed_model_power_study_has_controlled_type_i_error():
    sc = P.SocScenario({k: 0.0 for k in P.DEFAULT_EFFECTS})
    r = P.power_soc_mixed(6, sc, n_sim=80, seed=3)
    assert (r["power"] <= 0.05 / 3 + 0.04).all()
