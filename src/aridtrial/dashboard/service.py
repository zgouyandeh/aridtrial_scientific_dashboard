"""Read-only query services for the Data Quality dashboard.

The dashboard never repairs or mutates raw/core data. It only reads pipeline,
QC, quarantine and reconciliation audit tables.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


# contract dataset -> raw landing, typed core and QC-filtered final layer.
DATASETS: tuple[dict[str, str], ...] = (
    {"dataset": "plot_layout", "label": "Plot layout", "raw_kind": "file", "raw_key": "plot_layout", "core_table": "core.plot", "final_table": "core.plot", "final_label": "Core"},
    {"dataset": "soil_samples", "label": "Soil samples", "raw_kind": "kobo", "raw_key": "soil_sampling_v1", "core_table": "core.soil_sample", "final_table": "mart.soil_clean", "final_label": "Clean mart"},
    {"dataset": "tree_observations", "label": "Tree observations", "raw_kind": "kobo", "raw_key": "tree_survey_v1", "core_table": "core.tree_obs", "final_table": "mart.tree_clean", "final_label": "Clean mart"},
    {"dataset": "sensor_readings", "label": "Sensor readings", "raw_kind": "file", "raw_key": "sensors", "core_table": "core.sensor_reading", "final_table": "mart.sensor_clean", "final_label": "Clean mart"},
    {"dataset": "station_weather", "label": "Station weather", "raw_kind": "file", "raw_key": "weather", "core_table": "core.weather_daily", "final_table": "mart.weather_clean", "final_label": "Clean mart"},
    {"dataset": "water_meter_readings", "label": "Water meter readings", "raw_kind": "file", "raw_key": "meters", "core_table": "core.water_meter", "final_table": "mart.meter_clean", "final_label": "Clean mart"},
    {"dataset": "plot_level_ndvi", "label": "Plot-level NDVI", "raw_kind": "file", "raw_key": "ndvi", "core_table": "core.ndvi", "final_table": "mart.ndvi_clean", "final_label": "Clean mart"},
    {"dataset": "weather_reanalysis", "label": "Weather reanalysis", "raw_kind": "file", "raw_key": "reanalysis", "core_table": "core.weather_reanalysis", "final_table": "core.weather_reanalysis", "final_label": "Core reference"},
    {"dataset": "sentinel2_block_ndvi", "label": "Sentinel-2 block NDVI", "raw_kind": "file", "raw_key": "ndvi_block", "core_table": "core.ndvi_block", "final_table": "core.ndvi_block", "final_label": "Core reference"},
)


def _rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(text(sql), params).fetchall()]


def _scalar(engine: Engine, sql: str, **params: Any) -> int:
    with engine.connect() as connection:
        value = connection.execute(text(sql), params).scalar()
    return int(value or 0)


def _count(engine: Engine, table: str) -> int:
    # Table names are constants from DATASETS, never request parameters.
    return _scalar(engine, f"SELECT count(*) FROM {table}")


def _dataset_counts(engine: Engine, item: dict[str, str]) -> dict[str, Any]:
    if item["raw_kind"] == "file":
        raw = _rows(engine, """SELECT count(*) AS raw_batches, COALESCE(sum(n_rows), 0) AS raw_rows,
                                      max(ingested_at) AS last_ingested
                               FROM raw.file_drop WHERE dataset = :key""", key=item["raw_key"])[0]
        raw_batches, raw_rows = int(raw["raw_batches"]), int(raw["raw_rows"])
        quarantine = _scalar(engine, """SELECT count(*) FROM audit.quarantine
                                         WHERE dataset = :dataset OR source_dataset = :dataset""",
                             dataset=item["dataset"])
    else:
        raw = _rows(engine, """SELECT count(*) AS raw_batches, count(*) AS raw_rows,
                                      max(ingested_at) AS last_ingested
                               FROM raw.kobo_submission WHERE form_id = :key""", key=item["raw_key"])[0]
        raw_batches, raw_rows = int(raw["raw_batches"]), int(raw["raw_rows"])
        quarantine = _scalar(engine, """SELECT count(*) FROM audit.quarantine
                                         WHERE dataset = :dataset OR source_dataset = :dataset""",
                             dataset=item["dataset"])

    core_rows = _count(engine, item["core_table"])
    final_rows = _count(engine, item["final_table"])
    return {
        "dataset": item["dataset"], "label": item["label"],
        "raw_batches": raw_batches, "raw_rows": raw_rows,
        "core_rows": core_rows, "final_rows": final_rows,
        "final_label": item["final_label"], "quarantined_rows": quarantine,
        "last_ingested": raw["last_ingested"].isoformat() if raw["last_ingested"] else None,
    }


def get_overview(engine: Engine) -> dict[str, Any]:
    datasets = [_dataset_counts(engine, item) for item in DATASETS]
    source_rows = _rows(engine, """SELECT DISTINCT ON (dataset)
        dataset, source_type, COALESCE(source_name, 'not specified') AS source_name,
        COALESCE(role, '') AS role, COALESCE(ingest_target, '') AS ingest_target,
        registered_at, manifest_sha256
        FROM audit.dataset_source ORDER BY dataset, registered_at DESC""")
    sources = {row["dataset"]: {**row, "registered_at": row["registered_at"].isoformat() if row["registered_at"] else None}
               for row in source_rows}
    for item in datasets:
        item["source"] = sources.get(item["dataset"], {
            "source_type": "not registered", "source_name": "—", "role": "", "ingest_target": ""})

    qc = _rows(engine, """SELECT dataset, severity, count(*) AS issue_count
                          FROM audit.qc_latest GROUP BY dataset, severity
                          ORDER BY dataset, severity""")
    quarantine_by_dataset = _rows(engine, """SELECT COALESCE(source_dataset, dataset) AS dataset,
        count(*) AS quarantined_rows, count(DISTINCT reason) AS distinct_reasons,
        max(ts) AS last_quarantined
        FROM audit.quarantine GROUP BY COALESCE(source_dataset, dataset)
        ORDER BY quarantined_rows DESC""")
    reconciliation_by_dataset = _rows(engine, """SELECT dataset,
        count(*) FILTER (WHERE severity = 'fail') AS failed_checks,
        count(*) FILTER (WHERE severity = 'warn') AS warning_checks
        FROM audit.reconciliation_latest GROUP BY dataset""")
    reconciliation_index = {row["dataset"]: row for row in reconciliation_by_dataset}
    qc_index: dict[str, dict[str, int]] = {}
    for row in qc:
        qc_index.setdefault(row["dataset"], {"failures": 0, "warnings": 0})
        if row["severity"] == "fail":
            qc_index[row["dataset"]]["failures"] = int(row["issue_count"])
        elif row["severity"] == "warn":
            qc_index[row["dataset"]]["warnings"] = int(row["issue_count"])
    for item in datasets:
        item["reconciliation"] = reconciliation_index.get(item["dataset"], {"failed_checks": 0, "warning_checks": 0})
        item["qc"] = qc_index.get(item["dataset"], {"failures": 0, "warnings": 0})
    latest_reconciliation = _rows(engine, """SELECT run_id, started_at, ruleset, qc_run_id, summary
        FROM audit.reconciliation_run ORDER BY run_id DESC LIMIT 1""")
    latest_qc = _rows(engine, """SELECT run_id, started_at, ruleset, summary
        FROM audit.qc_run ORDER BY run_id DESC LIMIT 1""")
    totals = {
        "raw_rows": sum(x["raw_rows"] for x in datasets),
        "core_rows": sum(x["core_rows"] for x in datasets),
        "final_rows": sum(x["final_rows"] for x in datasets),
        "quarantined_rows": sum(x["quarantined_rows"] for x in datasets),
        "qc_failures": sum(int(x["issue_count"]) for x in qc if x["severity"] == "fail"),
        "qc_warnings": sum(int(x["issue_count"]) for x in qc if x["severity"] == "warn"),
    }
    return {
        "totals": totals, "datasets": datasets, "qc_issues": qc,
        "quarantine": [{**x, "last_quarantined": x["last_quarantined"].isoformat() if x["last_quarantined"] else None}
                       for x in quarantine_by_dataset],
        "latest_reconciliation": ({**latest_reconciliation[0],
            "started_at": latest_reconciliation[0]["started_at"].isoformat() if latest_reconciliation[0]["started_at"] else None}
            if latest_reconciliation else None),
        "latest_qc": ({**latest_qc[0],
            "started_at": latest_qc[0]["started_at"].isoformat() if latest_qc[0]["started_at"] else None}
            if latest_qc else None),
    }


def get_reconciliation(engine: Engine) -> list[dict[str, Any]]:
    rows = _rows(engine, """SELECT run_id, dataset, check_id, severity, source_count,
        target_count, difference, details
        FROM audit.reconciliation_latest
        ORDER BY CASE severity WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                 dataset, check_id""")
    return rows


def get_qc_issues(engine: Engine) -> list[dict[str, Any]]:
    return _rows(engine, """SELECT dataset, record_key, plot_id, ts, variable,
        rule_id, severity, message, value
        FROM audit.qc_latest
        ORDER BY CASE severity WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                 dataset, ts DESC NULLS LAST LIMIT 500""")
