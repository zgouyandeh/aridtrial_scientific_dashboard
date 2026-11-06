"""Scientific results layer: unit tests for JSON hygiene and helpers; integration tests (PostgreSQL) for persistence and the API."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from aridtrial.analysis import scientific as SC


def test_clean_makes_strict_json():
    obj = {"a": np.float64("nan"), "b": np.int64(3), "c": pd.DataFrame({"x": [1.0, float("inf")]}), "d": (np.bool_(True), pd.Timestamp("2025-01-02")), 5: "k"}
    out = SC.clean(obj)
    json.dumps(out, allow_nan=False)  # must not raise
    assert out["a"] is None and out["b"] == 3 and out["c"][1]["x"] is None and out["d"][0] is True and "5" in out


def test_five_number_summary_and_whiskers():
    f = SC._five(pd.Series([1, 2, 3, 4, 100.0]))
    assert f["n"] == 5 and f["median"] == 3 and f["whisker_hi"] == 4 and f["max"] == 100  # outlier outside whisker
    assert SC._five(pd.Series([], dtype=float)) == {"n": 0}


def test_wilson_interval_bounds():
    lo, hi = SC._wilson(9, 10)
    assert 0.55 < lo < 0.9 < hi <= 1.0
    assert all(math.isnan(v) for v in SC._wilson(0, 0))


def test_permutation_test_detects_large_effect_and_not_null():
    rng = np.random.default_rng(0)
    rows = []
    for b in range(1, 7):
        base = rng.normal(0, 0.05)
        for t, eff in zip(("CTRL", "COMP", "BIOC", "COMP_BIOC"), (0, 1.0, 0.0, 0.0)):
            rows.append(dict(block=b, treatment=t, y=base + eff + rng.normal(0, 0.05)))
    pt = pd.DataFrame(rows)
    res = {r["contrast"]: r for r in SC._perm_test(pt, "y", None, 2000, 1)}
    assert res["COMP - CTRL"]["p_perm_holm"] < 0.05
    assert res["BIOC - CTRL"]["p_perm_holm"] > 0.05
    assert SC._perm_test(pt.iloc[:-1], "y", None, 100, 1) == []  # incomplete blocks: refuse, do not guess


def test_run_id_validation():
    assert SC.validate_run_id("ar-20261105165932-92b4a351")
    assert not SC.validate_run_id("ar-1; DROP TABLE x")


@pytest.fixture(scope="module")
def stored(loaded):
    from aridtrial.analysis.run import run_analysis

    eng, cfg = loaded["engine"], loaded["cfg"]
    R = run_analysis(eng, cfg, quick=True)
    R["qc_summary"] = loaded["qc"]["summary"]
    run_id = SC.run_and_persist(eng, R, cfg, quick=True)
    return dict(engine=eng, run_id=run_id, cfg=cfg)


def test_all_sections_persisted_with_provenance(stored):
    from sqlalchemy import text

    with stored["engine"].connect() as c:
        run = c.execute(text("SELECT * FROM results.analysis_run WHERE run_id=:r"), {"r": stored["run_id"]}).mappings().one()
        secs = {r[0]: r[1] for r in c.execute(text("SELECT section, sha256 FROM results.analysis_artifact WHERE run_id=:r"), {"r": stored["run_id"]})}
    assert set(secs) == set(SC.SECTIONS)
    assert run["data_origin"] == "synthetic" and run["quick"] and run["seed"] == stored["cfg"].seed and run["qc_run_id"]
    assert all(len(h) == 64 for h in secs.values())
    assert "statsmodels" in run["software"]


def test_payload_hash_matches_content(stored):
    import hashlib

    from sqlalchemy import text

    with stored["engine"].connect() as c:
        row = c.execute(text("SELECT payload, sha256 FROM results.analysis_artifact WHERE run_id=:r AND section='inference'"), {"r": stored["run_id"]}).one()
    assert hashlib.sha256(SC._canonical(row[0]).encode()).hexdigest() == row[1]


def test_scientific_content_is_sound(stored):
    from aridtrial.dashboard import analysis_service as A

    eng, rid = stored["engine"], stored["run_id"]
    inf = A.get_section(eng, "inference", rid)["data"]
    primary = [c for c in inf["contrasts"] if c["is_primary"]]
    assert len(primary) == 3 and all(0 <= c["p_holm"] <= 1 for c in primary)
    assert any(r["p_perm_holm"] is not None for r in inf["randomisation_tests"])
    design = A.get_section(eng, "design", rid)["data"]
    assert design["design"]["df"]["error"] == 15 and len(design["layout"]) == 24
    assert all(v == 1 for row in design["balance_matrix"]["values"] for v in row)  # RCBD: each treatment once per block
    models = A.get_section(eng, "models", rid)["data"]["models"]
    assert {m["response"] for m in models} >= {"soc_pct", "ec_dsm"}
    for m in models:
        assert all(v >= 0 for v in m["variance"]["var"].values())
        assert 0 <= m["variance"]["icc_block"] <= 1
    power = A.get_section(eng, "power", rid)["data"]
    mdd = {(r["scenario"], r["n_blocks"]): r["mdd_80"] for r in power["mdd"]}
    assert mdd[("design assumption", 4)] > mdd[("design assumption", 8)]  # more blocks -> smaller MDD
    assert "post-hoc power is not reported" in " ".join(power["notes"]).lower()  # explicitly not reported


def test_api_contract_and_errors(stored, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from aridtrial.dashboard import app as appmod

    monkeypatch.setattr(appmod, "_engine", lambda: stored["engine"])
    c = TestClient(appmod.app)
    assert c.get("/api/analysis/runs").json()[0]["run_id"]
    r = c.get(f"/api/analysis/survival?run_id={stored['run_id']}")
    assert r.status_code == 200 and r.json()["meta"]["data_origin"] == "synthetic" and r.json()["data"]["kaplan_meier"]
    assert c.get("/api/analysis/run").json()["run_id"]
    assert c.get("/api/analysis/nonsense").status_code == 404
    assert c.get("/api/analysis/eda?run_id=ar-bad").status_code == 404
    assert c.get("/api/analysis/eda?run_id=ar-20200101000000-deadbeef").status_code == 404


def test_api_empty_state_before_first_run(engine2, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from aridtrial.dashboard import app as appmod
    from aridtrial.pipeline import db

    db.reset_database(engine2)
    db.apply_schema(engine2)
    monkeypatch.setattr(appmod, "_engine", lambda: engine2)
    r = TestClient(appmod.app).get("/api/analysis/eda")
    assert r.status_code == 404 and "no analysis run" in r.json()["detail"]


def test_static_site_export_matches_api(stored, monkeypatch, tmp_path):
    """The GitHub Pages snapshot is the real API output frozen to JSON (same files the static frontend fetches)."""
    pytest.importorskip("fastapi")
    import importlib.util
    import sys
    from pathlib import Path

    from aridtrial.dashboard import app as appmod

    monkeypatch.setattr(appmod, "_engine", lambda: stored["engine"])
    spec = importlib.util.spec_from_file_location("export_static_site", Path(__file__).parents[1] / "scripts" / "export_static_site.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_static_site"] = mod
    spec.loader.exec_module(mod)
    mod.main(["--out", str(tmp_path)])
    for sec in SC.SECTIONS:
        d = json.loads((tmp_path / "api" / "analysis" / f"{sec}.json").read_text(encoding="utf-8"))
        assert d["meta"]["data_origin"] == "synthetic" and d["data"]
    assert len(json.loads((tmp_path / "api" / "analysis" / "runs.json").read_text(encoding="utf-8"))) == 1
    assert json.loads((tmp_path / "api" / "health.json").read_text(encoding="utf-8"))["status"] == "ok"
