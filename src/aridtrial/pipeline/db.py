"""Database helpers: engine, schema application, fast COPY loads."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ..config import default_dsn, project_root

SQL_DIR = project_root() / "sql"


def get_engine(dsn: str | None = None) -> Engine:
    return create_engine(dsn or default_dsn(), future=True, pool_pre_ping=True)


def apply_schema(engine: Engine, with_postgis: bool = False) -> None:
    files = ["01_schema.sql", "04_provenance.sql", "05_data_quality.sql", "02_views.sql", "06_results.sql"] + (["03_postgis.sql"] if with_postgis else [])
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for f in files:
            cur.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()


def reset_database(engine: Engine) -> None:
    """Drop and recreate all project schemas (development / tests only)."""
    with engine.begin() as c:
        for s in ("results", "mart", "audit", "core", "raw"):
            c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))


def copy_df(engine: Engine, df: pd.DataFrame, table: str, columns: list[str] | None = None, on_conflict_nothing: bool = False) -> int:
    """Bulk-load a DataFrame with COPY. With on_conflict_nothing, goes via a temp table so duplicates are skipped.

    Returns the number of rows actually inserted.
    """
    cols = columns or list(df.columns)
    buf = io.StringIO()
    df[cols].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        col_sql = ", ".join(cols)
        if on_conflict_nothing:
            cur.execute(f"CREATE TEMP TABLE _stage (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
            cur.copy_expert(f"COPY _stage ({col_sql}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)
            cur.execute(f"INSERT INTO {table} ({col_sql}) SELECT {col_sql} FROM _stage ON CONFLICT DO NOTHING")
            n = cur.rowcount
        else:
            cur.copy_expert(f"COPY {table} ({col_sql}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)
            n = cur.rowcount
        raw.commit()
        return int(n)
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def read_sql(engine: Engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(text(sql), c, params=params)
