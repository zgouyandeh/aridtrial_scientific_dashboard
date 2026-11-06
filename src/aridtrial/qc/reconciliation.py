"""Cross-layer data reconciliation for raw, core, audit and QC-filtered marts.

Reconciliation does not repair or delete data. It records count, lineage and
relational differences so that every discrepancy remains explainable.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import TrialConfig
from ..pipeline import audit
from ..pipeline.db import copy_df, read_sql

RESULT_COLUMNS = [
    "run_id", "dataset", "check_id", "severity", "source_count",
    "target_count", "difference", "details",
]
RULESET_VERSION = "aridtrial-reconciliation-1.0"

# Dataset names in raw.file_drop -> typed core tables.
FILE_TARGETS = {
    "plot_layout": "core.plot",
    "sensors": "core.sensor_reading",
    "weather": "core.weather_daily",
    "meters": "core.water_meter",
    "ndvi": "core.ndvi",
    "reanalysis": "core.weather_reanalysis",
    "ndvi_block": "core.ndvi_block",
}

# Kobo form -> normalized core dataset.
KOBO_TARGETS = {
    "soil_sampling_v1": ("soil_samples", "core.soil_sample"),
    "tree_survey_v1": ("tree_observations", "core.tree_obs"),
}


def _scalar(engine: Engine, sql: str, **params) -> int:
    with engine.connect() as c:
        value = c.execute(text(sql), params).scalar()
    return int(value or 0)


def _result(dataset: str, check_id: str, severity: str, source_count=None,
            target_count=None, details: dict | None = None) -> dict:
    difference = None
    if source_count is not None and target_count is not None:
        difference = int(target_count) - int(source_count)
    return {
        "dataset": dataset,
        "check_id": check_id,
        "severity": severity,
        "source_count": source_count,
        "target_count": target_count,
        "difference": difference,
        "details": details or {},
    }


def _latest_qc_run(engine: Engine) -> int | None:
    with engine.connect() as c:
        value = c.execute(text("SELECT max(run_id) FROM audit.qc_run")).scalar()
    return int(value) if value is not None else None


def _file_drop_checks(engine: Engine) -> list[dict]:
    drops = read_sql(engine, """SELECT sha256, dataset, file_name, n_rows, source_type,
                                      source_name, source_dataset
                               FROM raw.file_drop ORDER BY dataset, file_name""")
    rows: list[dict] = []
    for drop in drops.itertuples(index=False):
        table = FILE_TARGETS.get(drop.dataset)
        if table is None:
            rows.append(_result(drop.dataset, "file_drop.unmapped_dataset", "warn",
                                int(drop.n_rows), None,
                                {"file_name": drop.file_name, "sha256": drop.sha256}))
            continue

        core_count = _scalar(engine, f"SELECT count(*) FROM {table} WHERE source_sha256 = :sha",
                             sha=drop.sha256)
        action = "load_design" if drop.dataset == "plot_layout" else f"ingest_{drop.dataset}"
        with engine.connect() as c:
            log = c.execute(text("""SELECT n_rows, details FROM audit.ingest_log
                                   WHERE action = :action AND content_sha256 = :sha
                                   ORDER BY seq DESC LIMIT 1"""),
                            {"action": action, "sha": drop.sha256}).fetchone()

        if log is None:
            rows.append(_result(drop.dataset, "file_drop.ingest_log_missing", "warn",
                                int(drop.n_rows), core_count,
                                {"file_name": drop.file_name, "sha256": drop.sha256,
                                 "target_table": table}))
            continue

        details = log.details or {}
        rows_in = details.get("rows_in")
        rows_loaded = details.get("rows_loaded")
        if drop.dataset == "plot_layout":
            rows_in = int(log.n_rows or drop.n_rows)
            # Layout loading is idempotent: the log records input rows, while
            # the source-hash count measures the complete persisted layout.
            rows_loaded = int(drop.n_rows)
        if rows_in is not None:
            severity = "pass" if int(rows_in) == int(drop.n_rows) else "fail"
            rows.append(_result(drop.dataset, "file_drop.raw_count_matches_ingest", severity,
                                int(drop.n_rows), int(rows_in),
                                {"file_name": drop.file_name, "sha256": drop.sha256,
                                 "ingest_action": action}))
        if rows_loaded is not None:
            severity = "pass" if int(rows_loaded) == core_count else "fail"
            rows.append(_result(drop.dataset, "file_drop.ingest_matches_core", severity,
                                int(rows_loaded), core_count,
                                {"file_name": drop.file_name, "sha256": drop.sha256,
                                 "target_table": table,
                                 "rows_in": rows_in,
                                 "unknown_plot_rows": details.get("unknown_plot_rows", 0),
                                 "duplicate_timestamps_dropped": details.get("duplicate_timestamps_dropped", 0)}))
        else:
            rows.append(_result(drop.dataset, "file_drop.loaded_count_unavailable", "warn",
                                int(drop.n_rows), core_count,
                                {"file_name": drop.file_name, "sha256": drop.sha256,
                                 "target_table": table}))
    return rows


def _kobo_checks(engine: Engine) -> list[dict]:
    rows: list[dict] = []
    for form_id, (source_dataset, core_table) in KOBO_TARGETS.items():
        raw = read_sql(engine, """SELECT count(*) AS n_rows,
                                         count(DISTINCT payload_sha256) AS n_payloads,
                                         count(DISTINCT source_type || '|' || source_name) AS n_sources
                                  FROM raw.kobo_submission WHERE form_id = :form""", form=form_id).iloc[0]
        raw_payloads = int(raw.n_payloads)
        core_payloads = _scalar(engine, f"SELECT count(DISTINCT payload_sha256) FROM {core_table} WHERE source_dataset = :ds",
                                ds=source_dataset)
        quarantine_payloads = _scalar(engine, """SELECT count(DISTINCT source_sha256)
                                                  FROM audit.quarantine WHERE source_dataset = :ds
                                                  AND source_sha256 IS NOT NULL""", ds=source_dataset)
        represented = _scalar(engine, f"""SELECT count(DISTINCT payload_sha256) FROM {core_table}
                                          WHERE source_dataset = :ds""", ds=source_dataset)
        # Quarantined submissions may not normalize into core; union hashes to avoid double counting.
        represented_hashes = _scalar(engine, """SELECT count(*) FROM (
            SELECT DISTINCT payload_sha256 AS payload_hash FROM core.soil_sample WHERE source_dataset = :ds
            UNION
            SELECT DISTINCT payload_sha256 AS payload_hash FROM core.tree_obs WHERE source_dataset = :ds
            UNION
            SELECT DISTINCT source_sha256 AS payload_hash FROM audit.quarantine
              WHERE source_dataset = :ds AND source_sha256 IS NOT NULL
        ) x""", ds=source_dataset)
        severity = "pass" if raw_payloads == represented_hashes and int(raw.n_sources) <= 1 else "fail"
        rows.append(_result(source_dataset, "kobo.raw_submissions_represented", severity,
                            raw_payloads, represented_hashes,
                            {"form_id": form_id, "raw_rows": int(raw.n_rows),
                             "normalized_core_rows": _scalar(engine, f"SELECT count(*) FROM {core_table} WHERE source_dataset = :ds", ds=source_dataset),
                             "core_payloads": core_payloads,
                             "quarantined_payloads": quarantine_payloads,
                             "source_identities": int(raw.n_sources),
                             "grain_note": "One Kobo submission can normalize to multiple core rows."}))
    return rows


def _lineage_checks(engine: Engine) -> list[dict]:
    tables = [
        "core.plot", "core.soil_sample", "core.tree_obs", "core.sensor_reading",
        "core.weather_daily", "core.water_meter", "core.ndvi",
        "core.weather_reanalysis", "core.ndvi_block",
    ]
    rows = []
    for table in tables:
        total = _scalar(engine, f"SELECT count(*) FROM {table}")
        unknown = _scalar(engine, f"""SELECT count(*) FROM {table}
                                     WHERE source_type = 'unknown' OR source_name = 'unknown'
                                        OR source_dataset = 'unknown'
                                        OR source_type IS NULL OR source_name IS NULL
                                        OR source_dataset IS NULL
                                        OR source_type = '' OR source_name = '' OR source_dataset = ''""")
        rows.append(_result(table.split(".")[1], "lineage.identity_complete",
                            "pass" if unknown == 0 else "warn", total, total - unknown,
                            {"rows_with_unknown_source_identity": unknown, "table": table}))
    return rows


def _source_contract_checks(engine: Engine) -> list[dict]:
    """Verify that row-level lineage agrees with the immutable source registry."""
    mappings = {
        "plot_layout": "core.plot",
        "soil_samples": "core.soil_sample",
        "tree_observations": "core.tree_obs",
        "sensor_readings": "core.sensor_reading",
        "station_weather": "core.weather_daily",
        "water_meter_readings": "core.water_meter",
        "plot_level_ndvi": "core.ndvi",
        "weather_reanalysis": "core.weather_reanalysis",
        "sentinel2_block_ndvi": "core.ndvi_block",
    }
    rows = []
    for dataset, table in mappings.items():
        total = _scalar(engine, f"SELECT count(*) FROM {table}")
        missing_manifest = _scalar(engine, f"SELECT count(*) FROM {table} WHERE manifest_sha256 IS NULL")
        mismatch = _scalar(engine, f"""SELECT count(*) FROM {table} t
            WHERE t.manifest_sha256 IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM audit.dataset_source s
              WHERE s.manifest_sha256 = t.manifest_sha256
                AND s.dataset = :dataset
                AND t.source_dataset = :dataset
                AND s.source_type = t.source_type
                AND COALESCE(s.source_name, '') = COALESCE(t.source_name, '')
                AND s.contract_sha256 = t.contract_sha256
            )""", dataset=dataset)
        severity = "fail" if mismatch else ("warn" if missing_manifest else "pass")
        rows.append(_result(dataset, "lineage.matches_source_contract", severity,
                            total, total - mismatch - missing_manifest,
                            {"table": table, "missing_manifest_rows": missing_manifest,
                             "source_contract_mismatch_rows": mismatch}))

    raw_datasets = {
        "plot_layout": "plot_layout", "sensors": "sensor_readings", "weather": "station_weather",
        "meters": "water_meter_readings", "ndvi": "plot_level_ndvi",
        "reanalysis": "weather_reanalysis", "ndvi_block": "sentinel2_block_ndvi",
    }
    for raw_dataset, contract_dataset in raw_datasets.items():
        total = _scalar(engine, "SELECT count(*) FROM raw.file_drop WHERE dataset = :dataset", dataset=raw_dataset)
        mismatch = _scalar(engine, """SELECT count(*) FROM raw.file_drop r
            WHERE r.dataset = :raw_dataset AND r.manifest_sha256 IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM audit.dataset_source s
              WHERE s.manifest_sha256 = r.manifest_sha256 AND s.dataset = :contract_dataset
                AND r.source_dataset = :contract_dataset
                AND s.source_type = r.source_type
                AND COALESCE(s.source_name, '') = COALESCE(r.source_name, '')
                AND s.contract_sha256 = r.contract_sha256
            )""", raw_dataset=raw_dataset, contract_dataset=contract_dataset)
        missing = _scalar(engine, """SELECT count(*) FROM raw.file_drop
            WHERE dataset = :raw_dataset AND manifest_sha256 IS NULL""", raw_dataset=raw_dataset)
        rows.append(_result(contract_dataset, "raw_file.lineage_matches_source_contract",
                            "fail" if mismatch else ("warn" if missing else "pass"),
                            total, total - mismatch - missing,
                            {"table": "raw.file_drop", "raw_dataset": raw_dataset,
                             "missing_manifest_rows": missing, "source_contract_mismatch_rows": mismatch}))

    for form_id, (contract_dataset, _core_table) in KOBO_TARGETS.items():
        total = _scalar(engine, "SELECT count(*) FROM raw.kobo_submission WHERE form_id = :form", form=form_id)
        mismatch = _scalar(engine, """SELECT count(*) FROM raw.kobo_submission r
            WHERE r.form_id = :form AND r.manifest_sha256 IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM audit.dataset_source s
              WHERE s.manifest_sha256 = r.manifest_sha256 AND s.dataset = :dataset
                AND r.source_dataset = :dataset
                AND s.source_type = r.source_type
                AND COALESCE(s.source_name, '') = COALESCE(r.source_name, '')
                AND s.contract_sha256 = r.contract_sha256
            )""", form=form_id, dataset=contract_dataset)
        missing = _scalar(engine, """SELECT count(*) FROM raw.kobo_submission
            WHERE form_id = :form AND manifest_sha256 IS NULL""", form=form_id)
        rows.append(_result(contract_dataset, "raw_kobo.lineage_matches_source_contract",
                            "fail" if mismatch else ("warn" if missing else "pass"),
                            total, total - mismatch - missing,
                            {"table": "raw.kobo_submission", "form_id": form_id,
                             "missing_manifest_rows": missing, "source_contract_mismatch_rows": mismatch}))
    return rows


def _relational_checks(engine: Engine, cfg: TrialConfig) -> list[dict]:
    rows = []
    n_plots = _scalar(engine, "SELECT count(*) FROM core.plot")
    rows.append(_result("plot_layout", "design.expected_plot_count",
                        "pass" if n_plots == cfg.n_plots else "fail",
                        cfg.n_plots, n_plots,
                        {"expected_blocks": cfg.n_blocks, "expected_treatments_per_block": len(cfg.treatments)}))
    bad_blocks = _scalar(engine, """SELECT count(*) FROM (
        SELECT block FROM core.plot GROUP BY block
        HAVING count(*) <> :n_treatments OR count(DISTINCT treatment) <> :n_treatments
    ) x""", n_treatments=len(cfg.treatments))
    rows.append(_result("plot_layout", "design.complete_rcbd_blocks",
                        "pass" if bad_blocks == 0 else "fail", 0, bad_blocks,
                        {"blocks_with_incomplete_or_duplicate_treatments": bad_blocks}))

    expected_weather = len(cfg.dates)
    actual_weather = _scalar(engine, "SELECT count(*) FROM core.weather_daily")
    weather_missing = _scalar(engine, """SELECT count(*) FROM generate_series(
        CAST(:start_date AS date), CAST(:end_date AS date), interval '1 day'
    ) d(day) WHERE NOT EXISTS (
        SELECT 1 FROM core.weather_daily w WHERE w.date = d.day::date
    )""", start_date=cfg.trial_start, end_date=cfg.dates[-1])
    weather_outside = _scalar(engine, """SELECT count(*) FROM core.weather_daily
        WHERE date < :start_date OR date > :end_date""",
        start_date=cfg.trial_start, end_date=cfg.dates[-1])
    weather_ok = actual_weather == expected_weather and weather_missing == 0 and weather_outside == 0
    rows.append(_result("weather", "core.expected_daily_coverage",
                        "pass" if weather_ok else "warn",
                        expected_weather, actual_weather,
                        {"trial_start": cfg.trial_start.isoformat(), "trial_end": cfg.dates[-1].isoformat(),
                         "expected_days": expected_weather, "missing_days": weather_missing,
                         "days_outside_trial": weather_outside}))

    for table in ("core.soil_sample", "core.tree_obs", "core.sensor_reading", "core.water_meter", "core.ndvi"):
        orphan = _scalar(engine, f"SELECT count(*) FROM {table} x LEFT JOIN core.plot p USING (plot_id) WHERE p.plot_id IS NULL")
        rows.append(_result(table.split(".")[1], "core.plot_reference_integrity",
                            "pass" if orphan == 0 else "fail", 0, orphan,
                            {"orphan_plot_rows": orphan, "table": table}))

    known_blocks = _scalar(engine, "SELECT count(DISTINCT block) FROM core.plot")
    bad_block_ndvi = _scalar(engine, """SELECT count(*) FROM core.ndvi_block n
                                        WHERE NOT EXISTS (SELECT 1 FROM core.plot p WHERE p.block = n.block)""")
    rows.append(_result("ndvi_block", "core.block_reference_integrity",
                        "pass" if bad_block_ndvi == 0 else "fail", 0, bad_block_ndvi,
                        {"orphan_block_rows": bad_block_ndvi, "known_blocks": known_blocks}))
    return rows


def _mart_checks(engine: Engine, qc_run_id: int | None) -> list[dict]:
    if qc_run_id is None:
        return [_result("mart", "mart.qc_run_required", "warn", None, None,
                        {"message": "Run QC before mart reconciliation."})]

    checks = [
        ("soil", "core.soil_sample", "mart.soil_clean", "status = 'current'", "sample_key", True),
        ("tree", "core.tree_obs", "mart.tree_clean", "status = 'current'", "obs_key", True),
        ("sensor", "core.sensor_reading", "mart.sensor_clean", "TRUE", "plot_id, ts", False),
        ("meter", "core.water_meter", "mart.meter_clean", "TRUE", "plot_id, date", False),
        ("ndvi", "core.ndvi", "mart.ndvi_clean", "TRUE", "plot_id, date", False),
        ("weather", "core.weather_daily", "mart.weather_clean", "TRUE", "date", False),
    ]
    rows = []
    for dataset, core_table, mart_view, condition, key_cols, whole_record in checks:
        core_count = _scalar(engine, f"SELECT count(*) FROM {core_table} WHERE {condition}")
        mart_count = _scalar(engine, f"SELECT count(*) FROM {mart_view}")
        if dataset in ("soil", "tree"):
            key = "sample_key" if dataset == "soil" else "obs_key"
            excluded = _scalar(engine, f"""SELECT count(DISTINCT record_key) FROM audit.qc_result
                                           WHERE run_id = :r AND dataset = :d
                                             AND severity = 'fail' AND variable = '*'""", r=qc_run_id, d=dataset)
        elif dataset in ("sensor", "ndvi"):
            excluded = _scalar(engine, f"""SELECT count(DISTINCT (plot_id, ts)) FROM audit.qc_result
                                           WHERE run_id = :r AND dataset = :d
                                             AND severity = 'fail' AND plot_id IS NOT NULL AND ts IS NOT NULL""", r=qc_run_id, d=dataset)
        else:
            excluded = 0
        expected_mart = core_count - excluded
        severity = "pass" if expected_mart == mart_count else "fail"
        rows.append(_result(dataset, "core_to_mart.row_count", severity,
                            expected_mart, mart_count,
                            {"core_rows": core_count, "qc_excluded_records": excluded,
                             "mart_view": mart_view, "qc_run_id": qc_run_id,
                             "grain_key": key_cols,
                             "note": "QC failures may null a variable without excluding its row."}))
    return rows


def run_reconciliation(engine: Engine, cfg: TrialConfig | None = None,
                       qc_run_id: int | None = None) -> dict:
    """Reconcile registered raw drops, Kobo submissions, core tables and QC marts.

    The function is read-only with respect to raw/core data. Only append-only
    reconciliation audit records are written.
    """
    cfg = cfg or TrialConfig()
    latest_qc = _latest_qc_run(engine)
    if qc_run_id is not None and qc_run_id != latest_qc:
        raise ValueError("Reconciliation must use the latest QC run because mart views are bound to audit.qc_latest.")
    qc_run_id = latest_qc

    rows = []
    rows.extend(_file_drop_checks(engine))
    rows.extend(_kobo_checks(engine))
    rows.extend(_lineage_checks(engine))
    rows.extend(_source_contract_checks(engine))
    rows.extend(_relational_checks(engine, cfg))
    rows.extend(_mart_checks(engine, qc_run_id))

    results = pd.DataFrame(rows)
    if results.empty:
        results = pd.DataFrame(columns=RESULT_COLUMNS[1:])
    failed = int((results.severity == "fail").sum())
    warnings = int((results.severity == "warn").sum())
    summary = {
        "ruleset": RULESET_VERSION,
        "qc_run_id": qc_run_id,
        "checks": int(len(results)),
        "passed": int((results.severity == "pass").sum()),
        "failed": failed,
        "warnings": warnings,
        "status": "fail" if failed else ("warning" if warnings else "pass"),
        "by_dataset": results.groupby("dataset").severity.value_counts().unstack(fill_value=0).to_dict(orient="index")
        if len(results) else {},
    }
    with engine.begin() as c:
        run_id = c.execute(text("""INSERT INTO audit.reconciliation_run (ruleset, qc_run_id, summary)
                                   VALUES (:ruleset, :qc_run_id, CAST(:summary AS jsonb))
                                   RETURNING run_id"""),
                           {"ruleset": RULESET_VERSION, "qc_run_id": qc_run_id,
                            "summary": json.dumps(summary, default=str)}).scalar()
    results.insert(0, "run_id", int(run_id))
    results["details"] = results["details"].map(lambda x: json.dumps(x or {}, sort_keys=True, default=str))
    copy_df(engine, results[RESULT_COLUMNS], "audit.reconciliation_result", RESULT_COLUMNS)
    digest = audit.sha256_text(audit.canonical(summary))
    audit.append(engine, "reconciliation_run", n_rows=len(results), content_sha256=digest,
                 details={"run_id": int(run_id), "qc_run_id": qc_run_id,
                          "status": summary["status"], "failed": failed, "warnings": warnings})
    return {"run_id": int(run_id), "summary": summary, "results": results}
