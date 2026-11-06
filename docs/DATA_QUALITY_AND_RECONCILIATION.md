# Data Quality Framework & Reconciliation

## Purpose

The QC rules answer: **does an individual observation look valid?** Reconciliation answers a different question: **did the same information move consistently through the data layers?** Both are needed. A perfectly plausible value can still be lost during ingestion, attributed to the wrong source, duplicated during a join, or omitted from a downstream view.

This module is a control and evidence layer. It does not repair records, rewrite source payloads, or change scientific calculations. Findings are persisted so that discrepancies can be investigated and corrected through an explicit upstream process.

## Data path and checks

| Layer | Reconciliation checks | Evidence |
|---|---|---|
| Raw file drops | Registered input row count and SHA-256 identity | `raw.file_drop` |
| Ingestion log | Input count and accepted/loaded count for the exact file hash | `audit.ingest_log` |
| Kobo raw submissions | Every distinct payload hash is represented in normalized core data or quarantine; one source identity per form | `raw.kobo_submission`, `core.soil_sample`, `core.tree_obs`, `audit.quarantine` |
| Core | Source identity completeness, plot/block references, expected RCBD size and daily weather coverage | `core.*` |
| QC-filtered marts | Expected row counts after the latest QC run match the published mart row counts | `mart.*_clean`, `audit.qc_result` |

### Why counts must respect data grain

A direct `raw row count = core row count` rule is not valid for every dataset. A soil Kobo submission may contain two depth layers and therefore create two normalized soil records. Edited submissions can also create current and superseded core versions. Reconciliation therefore compares distinct payload hashes represented in core or quarantine, while reporting normalized row counts separately.

For file-based datasets, the ingestion log is the bridge between raw input and core output. The comparison is:

- `raw.file_drop.n_rows` versus `audit.ingest_log.details.rows_in`;
- `audit.ingest_log.details.rows_loaded` versus the number of core rows carrying the same `source_sha256`.

This accounts for deliberate filtering and duplicate handling without pretending that every source row must become a core row.

### Core-to-mart rules

The reconciliation uses the latest QC run because `audit.qc_latest` and the clean marts are defined against the latest run. For soil and tree records, whole-record failures (`variable = '*'`) are excluded from clean marts; variable-level failures are represented as nulls and do not remove the row. Sensor and plot-level NDVI rows with failing QC flags are excluded. Meter and weather marts preserve their row grain while masking affected derived values.

The meter view uses `EXISTS` rather than a direct join to QC flags. This guarantees one output row per `(plot_id, date)` even if multiple QC rules flag the same meter observation.

## Severity

- `pass`: the invariant or count comparison agrees.
- `warn`: the check could not be fully verified or the issue is informational (for example, a missing historical ingest-log event).
- `fail`: an expected count, source representation, design invariant, or referential-integrity check does not agree.

The reconciliation severity is not a replacement for the record-level QC severity. A dataset can contain QC failures and still reconcile correctly: reconciliation is checking whether those failures were handled consistently in the downstream mart.

## Run commands

```bash
# Apply additive schema migrations, including the reconciliation ledger
aridtrial init-db

# Run individual stages after ingestion
aridtrial qc --as-of 2026-10-27
aridtrial reconcile

# Full workflow (reconciliation runs after QC and before dataset freeze)
aridtrial demo --quick
```

`aridtrial reconcile` requires a QC run and only accepts the latest run, since the marts are bound to `audit.qc_latest`. It appends results to `audit.reconciliation_run` and `audit.reconciliation_result`, and adds a hash-chained event to `audit.ingest_log`. It does not update raw or core tables.

## Inspect results

```sql
SELECT * FROM mart.v_reconciliation_summary ORDER BY severity;
SELECT * FROM mart.v_reconciliation_by_dataset ORDER BY dataset, severity;

SELECT dataset, check_id, severity, source_count, target_count, difference, details
FROM audit.reconciliation_latest
WHERE severity IN ('fail', 'warn')
ORDER BY severity, dataset, check_id;
```

## Current scope and limitations

- Reconciliation currently covers registered CSV file drops, the two Kobo forms, core lineage, RCBD structure, plot/block references, weather row coverage, and the six QC-filtered core marts.
- It does not claim that the external source itself is scientifically correct. For example, station-versus-reanalysis disagreement remains a separate QC/reference question.
- Real Kobo downloads are prepared separately in the current project contract; the database pipeline currently ingests the selected synthetic Kobo payloads.
- The reconciliation ledger is append-only by convention (new run IDs and results are inserted); raw and core tables remain unchanged.
- A `pass` means the programmed invariant passed. It is not a certification of agronomic validity, sensor calibration, satellite pixel purity, or compliance with a carbon-credit methodology.
