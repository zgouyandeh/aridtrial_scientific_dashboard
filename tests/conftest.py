"""Shared fixtures. Integration tests need a PostgreSQL database (scripts/pg_local.sh start, or docker compose up db)
and are skipped when none is reachable. They run in a separate database (aridtrial_test) because they reset it."""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

TEST_DSN = os.environ.get("ARIDTRIAL_TEST_DSN", "postgresql+psycopg2://aridtrial:aridtrial@127.0.0.1:5433/aridtrial_test")
AS_OF = date(2026, 10, 27)


@pytest.fixture(scope="session")
def engine():
    from aridtrial.pipeline.db import get_engine

    try:
        eng = get_engine(TEST_DSN)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # pragma: no cover
        pytest.skip(f"PostgreSQL integration environment unavailable: {type(e).__name__}")
    return eng


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Synthetic dataset written to a temp dir (never touches the project's data/)."""
    from aridtrial.config import TrialConfig
    from aridtrial.simulate.export import build

    out = tmp_path_factory.mktemp("data")
    cfg = TrialConfig()
    r = build(cfg, out)
    return dict(r, cfg=cfg, out=out)


@pytest.fixture(scope="session")
def loaded(engine, built):
    """Fresh schema -> ingest -> QC. Session-scoped: tests read it; tests that mutate use their own transaction."""
    from aridtrial.pipeline import db, ingest
    from aridtrial.qc.run import run_qc

    db.reset_database(engine)
    db.apply_schema(engine)
    first = ingest.ingest_all(engine, built["out"], built["cfg"])
    qc = run_qc(engine, built["cfg"], as_of=AS_OF)
    faults = pd.read_csv(built["out"] / "truth" / "injected_faults.csv")
    return dict(first=first, qc=qc, faults=faults, engine=engine, cfg=built["cfg"], out=built["out"])


TEST_DSN2 = os.environ.get("ARIDTRIAL_TEST_DSN2", "postgresql+psycopg2://aridtrial:aridtrial@127.0.0.1:5433/aridtrial_test2")


@pytest.fixture(scope="session")
def engine2():
    """Second database for tests that reset the schema with different data (keeps the `loaded` fixture intact)."""
    from aridtrial.pipeline.db import get_engine

    try:
        eng = get_engine(TEST_DSN2)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Second PostgreSQL integration environment unavailable: {type(e).__name__}")
    return eng


def fake_reanalysis(cfg, seed=7):
    """A stand-in for a real ERA5-Land daily frame (TEST ONLY): synthetic weather, perturbed so it differs from any station."""
    import numpy as np

    from aridtrial.simulate.weather import simulate_weather

    w = simulate_weather(cfg)
    rng = np.random.default_rng(seed)
    r = w[["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm"]].copy()
    r["et0_openmeteo_mm"] = w["et0_mm"] * rng.normal(1.0, 0.02, len(w))
    return r
