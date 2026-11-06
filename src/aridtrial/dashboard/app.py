"""FastAPI application serving the read-only Data Quality dashboard."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..config import default_dsn
from ..pipeline.db import get_engine
from . import analysis_service, service

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(
    title="AridTrial Data Quality Dashboard",
    description="Read-only visibility into source lineage, data layers, QC and reconciliation.",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@lru_cache(maxsize=1)
def _engine():
    return get_engine(default_dsn())


def _read(operation):
    try:
        return operation(_engine())
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable or the aridtrial schema has not been initialized. Check ARIDTRIAL_DSN and run `aridtrial init-db`.",
        ) from exc


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return _read(lambda engine: _check_connection(engine))


def _check_connection(engine):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


def _analysis(operation):
    try:
        return _read(operation)
    except analysis_service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/analysis/runs")
def analysis_runs():
    return _read(analysis_service.list_runs)


@app.get("/api/analysis/run")
def analysis_run(run_id: str | None = None):
    return _analysis(lambda e: analysis_service.get_run(e, run_id))


@app.get("/api/analysis/{section}")
def analysis_section(section: str, run_id: str | None = None):
    return _analysis(lambda e: analysis_service.get_section(e, section, run_id))


@app.get("/api/overview")
def overview():
    return _read(service.get_overview)


@app.get("/api/reconciliation")
def reconciliation():
    return _read(service.get_reconciliation)


@app.get("/api/qc-issues")
def qc_issues():
    return _read(service.get_qc_issues)
