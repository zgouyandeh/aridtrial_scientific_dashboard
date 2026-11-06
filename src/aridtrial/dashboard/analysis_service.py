"""Read-only access to persisted scientific results (results.* schema). Nothing is computed here."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..analysis.scientific import SECTIONS, validate_run_id


class NotFound(LookupError):
    pass


def _run_row(engine: Engine, run_id: str | None) -> dict[str, Any]:
    if run_id is not None and not validate_run_id(run_id):
        raise NotFound(f"invalid run id: {run_id!r}")
    sql = "SELECT * FROM results.analysis_run " + ("WHERE run_id = :r" if run_id else "ORDER BY created_at DESC, run_id DESC LIMIT 1")
    with engine.connect() as c:
        row = c.execute(text(sql), {"r": run_id}).mappings().first()
    if row is None:
        raise NotFound("no analysis run available" if run_id is None else f"analysis run not found: {run_id}")
    return dict(row)


def list_runs(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    with engine.connect() as c:
        rows = c.execute(text("""SELECT run_id, created_at, dataset_version_id, qc_run_id, seed, data_origin, quick
                                 FROM results.analysis_run ORDER BY created_at DESC, run_id DESC LIMIT :n"""), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def get_run(engine: Engine, run_id: str | None = None) -> dict[str, Any]:
    run = _run_row(engine, run_id)
    with engine.connect() as c:
        arts = c.execute(text("SELECT section, sha256 FROM results.analysis_artifact WHERE run_id = :r ORDER BY section"), {"r": run["run_id"]}).mappings().all()
    run["sections"] = {a["section"]: a["sha256"] for a in arts}
    return run


def get_section(engine: Engine, section: str, run_id: str | None = None) -> dict[str, Any]:
    if section not in SECTIONS:
        raise NotFound(f"unknown section: {section}")
    run = _run_row(engine, run_id)
    with engine.connect() as c:
        row = c.execute(text("SELECT payload, sha256 FROM results.analysis_artifact WHERE run_id = :r AND section = :s"),
                        {"r": run["run_id"], "s": section}).mappings().first()
    if row is None:
        raise NotFound(f"section {section!r} not stored for run {run['run_id']}")
    meta = {k: run[k] for k in ("run_id", "created_at", "dataset_version_id", "qc_run_id", "seed", "data_origin", "quick")}
    return dict(meta=meta, sha256=row["sha256"], data=row["payload"])
