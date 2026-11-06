"""Tamper-evident audit trail.

Every ingest / QC / export event is appended to audit.ingest_log as a hash chain:

    entry_hash = SHA-256( prev_hash || canonical_json(entry without hashes) )

Altering, deleting or re-ordering any past entry breaks every later hash, which `verify_chain`
detects. UPDATE/DELETE on the table are also blocked by a trigger (sql/01_schema.sql); the chain
is the second line of defence, and is what an external verifier can re-check offline.

Dataset versions freeze exactly which rows fed an analysis: a manifest of per-table row counts and
content hashes plus the chain head at that moment.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entry_hash(prev_hash: str, entry: dict) -> str:
    return sha256_text(prev_hash + canonical(entry))


def append(engine: Engine, action: str, dataset: str | None = None, source: str | None = None, n_rows: int | None = None,
           content_sha256: str | None = None, details: dict | None = None, actor: str = "pipeline", ts: datetime | None = None) -> str:
    """Append one entry; serialised with an advisory lock so concurrent writers cannot fork the chain."""
    ts = ts or datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(727001)"))
        prev = c.execute(text("SELECT entry_hash FROM audit.ingest_log ORDER BY seq DESC LIMIT 1")).scalar() or GENESIS
        entry = dict(ts=ts.isoformat(), actor=actor, action=action, dataset=dataset, source=source, n_rows=n_rows,
                     content_sha256=content_sha256, details=details or {})
        h = entry_hash(prev, entry)
        c.execute(
            text("""INSERT INTO audit.ingest_log (ts, actor, action, dataset, source, n_rows, content_sha256, details, prev_hash, entry_hash)
                    VALUES (:ts, :actor, :action, :dataset, :source, :n_rows, :content_sha256, CAST(:details AS jsonb), :prev, :h)"""),
            dict(ts=ts, actor=actor, action=action, dataset=dataset, source=source, n_rows=n_rows, content_sha256=content_sha256,
                 details=canonical(details or {}), prev=prev, h=h),
        )
    return h


def verify_chain(engine: Engine) -> dict:
    """Recompute the chain from the stored entries. Returns {'ok': bool, 'n': int, 'first_bad_seq': int|None}."""
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT seq, ts, actor, action, dataset, source, n_rows, content_sha256, details, prev_hash, entry_hash "
            "FROM audit.ingest_log ORDER BY seq")).fetchall()
    prev = GENESIS
    for r in rows:
        entry = dict(ts=pd.Timestamp(r.ts).to_pydatetime().astimezone(timezone.utc).isoformat(), actor=r.actor, action=r.action,
                     dataset=r.dataset, source=r.source, n_rows=r.n_rows, content_sha256=r.content_sha256, details=r.details)
        if r.prev_hash != prev or entry_hash(prev, entry) != r.entry_hash:
            return dict(ok=False, n=len(rows), first_bad_seq=int(r.seq))
        prev = r.entry_hash
    return dict(ok=True, n=len(rows), first_bad_seq=None, head=prev)


# ----------------------------------------------------------------------------- dataset versions

# table -> (ordering columns, columns hashed)
VERSIONED = {
    "mart.soil_clean": ["sample_key"],
    "mart.tree_clean": ["obs_key"],
    "mart.sensor_clean": ["plot_id", "ts"],
    "mart.meter_clean": ["plot_id", "date"],
    "mart.ndvi_clean": ["plot_id", "date"],
    "mart.weather_clean": ["date"],
    "core.plot": ["plot_id"],
    "core.soil_sample": ["record_id"],
    "core.tree_obs": ["record_id"],
    "core.sensor_reading": ["plot_id", "ts"],
    "core.weather_daily": ["date"],
    "core.water_meter": ["plot_id", "date"],
    "core.ndvi": ["plot_id", "date"],
    "core.weather_reanalysis": ["date"],
    "core.ndvi_block": ["block", "date"],
    "raw.kobo_submission": ["form_id", "root_uuid", "payload_sha256"],
    "raw.file_drop": ["sha256"],
}


def table_digest(engine: Engine, table: str, order_by: list[str]) -> tuple[int, str]:
    with engine.connect() as c:
        df = pd.read_sql(text(f"SELECT * FROM {table} ORDER BY {', '.join(order_by)}"), c)
    payload = df.to_csv(index=False, float_format="%.10g", date_format="%Y-%m-%dT%H:%M:%S%z")
    return len(df), sha256_text(payload)


def create_dataset_version(engine: Engine, label: str | None = None) -> dict:
    """Freeze the current QC-filtered marts into an immutable, hash-addressed dataset version."""
    now = datetime.now(timezone.utc)
    with engine.connect() as c:
        qc_run = c.execute(text("SELECT max(run_id) FROM audit.qc_run")).scalar()
        head = c.execute(text("SELECT entry_hash FROM audit.ingest_log ORDER BY seq DESC LIMIT 1")).scalar() or GENESIS
        source_rows = c.execute(text("""
            SELECT manifest_sha256, dataset, source_type, source_name, contract_sha256, ingest_target, role
            FROM audit.dataset_source
            WHERE manifest_sha256 = (SELECT manifest_sha256 FROM audit.dataset_source ORDER BY registered_at DESC LIMIT 1)
            ORDER BY dataset
        """)).mappings().all()
    source_lineage = {
        "manifest_sha256": source_rows[0]["manifest_sha256"] if source_rows else None,
        "datasets": [dict(row) for row in source_rows],
    }
    tables = {}
    for t, order in VERSIONED.items():
        n, d = table_digest(engine, t, order)
        tables[t] = dict(rows=n, sha256=d)
    manifest = dict(created_at=now.isoformat(), label=label, qc_run_id=qc_run, chain_head=head, tables=tables, source_lineage=source_lineage)
    msha = sha256_text(canonical(manifest))
    version_id = f"dv-{now:%Y%m%d}-{msha[:10]}"
    with engine.begin() as c:
        c.execute(text("""INSERT INTO audit.dataset_version (version_id, created_at, manifest, manifest_sha256, chain_head, qc_run_id)
                          VALUES (:v, :t, CAST(:m AS jsonb), :s, :h, :q) ON CONFLICT (version_id) DO NOTHING"""),
                  dict(v=version_id, t=now, m=canonical(manifest), s=msha, h=head, q=qc_run))
    append(engine, "dataset_version", details=dict(version_id=version_id, manifest_sha256=msha), content_sha256=msha)
    return dict(version_id=version_id, manifest=manifest, manifest_sha256=msha)


def verify_dataset_version(engine: Engine, version_id: str) -> dict:
    """Recompute table digests and compare with the stored manifest (detects any change since freezing)."""
    with engine.connect() as c:
        row = c.execute(text("SELECT manifest, manifest_sha256 FROM audit.dataset_version WHERE version_id=:v"), dict(v=version_id)).fetchone()
    if row is None:
        return dict(ok=False, reason="unknown version")
    manifest = row.manifest
    if sha256_text(canonical(manifest)) != row.manifest_sha256:
        return dict(ok=False, reason="manifest altered")
    diffs = []
    for t, order in VERSIONED.items():
        n, d = table_digest(engine, t, order)
        if d != manifest["tables"][t]["sha256"]:
            diffs.append(t)
    return dict(ok=not diffs, changed_tables=diffs)
