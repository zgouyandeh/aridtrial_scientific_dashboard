# Refactoring Notes

## Current scope

This revision first refactors the synthetic input-data generation stage, then separates real external-source access and data preparation from database ingestion. Database ingestion, data-quality rules, scientific analysis, and dashboards remain unchanged.

## What changed

- Moved the generation workflow into `simulate/builder.py`.
- Moved time-series and station-reference fault injection into `simulate/fault_injection.py`.
- Moved output-directory management and file writing into `simulate/artifacts.py`.
- Updated the CLI to call the new builder module.
- Kept `simulate/export.py` as a backward-compatible wrapper so existing scripts and tests do not break.
- Added generation documentation and focused tests for synthetic-only and real-reference input builds.
- Added `sources/kobo.py` for paginated Kobo API v2 downloads, immutable timestamped raw snapshots, and page-level SHA-256 provenance.
- Kept `pipeline/kobo_client.py` focused on payload normalization and revision resolution; the previous `KoboClient` import remains available as a compatibility re-export.
- Added `preparation/kobo.py` and the `prepare-kobo` CLI command to produce typed real soil/tree CSVs separately from synthetic Kobo submissions.
- Extended Open-Meteo, CDS, and Sentinel-2 provenance metadata. Sentinel-2 raw statistical responses can now be preserved in the external cache.
- Added `preparation/manifest.py`, which writes `input_manifest.json` with real source lineage, generated artifact hashes, and explicit real/synthetic derivation policies.
- Added `fetch-kobo` CLI command; API credentials are read from environment variables, not source files.

## What did not change

- RCBD layout generation and randomization.
- Scientific data-generating formulas in `simulate/field.py` and `simulate/weather.py`.
- FAO-56 calculations.
- Kobo form and submission structure, including injected form-entry faults.
- Existing fault types, fault rates, and random stream offsets.
- The names and schemas of the generated operational input files.
- Scientific transformations and source-specific unit conversions.
- Ground-truth separation from operational input files.
- Database schema and ingestion behavior.

## Validation performed

- Synthetic-only build: output row counts and all expected files checked.
- Real weather + real Sentinel-2 background build: optional reference drops checked.
- Stale optional reference files: confirmed removed on a later synthetic-only build.
- Old-vs-new generated CSV and scientific output comparison: matching hashes for the synthetic-only and real-reference builds. The Kobo soil payload contains UUIDs generated with `uuid.uuid4()`, and XLSX metadata may vary, so those files are not expected to be byte-identical.
- Focused external-source, preparation, simulator, and compatibility tests: `18 passed`.
- `compileall` succeeded for the package.
- Synthetic-only old-vs-new comparison: all scientific CSV hashes matched. The synthetic Kobo soil payload contains UUIDs generated with `uuid.uuid4()`, and XLSX metadata may vary; these are not expected to be byte-identical. The new `input_manifest.json` is an additional artifact.
- Real weather + Sentinel-2 old-vs-new comparison: scientific output hashes matched.
- Full test collection remains blocked in this environment because `pandera` is not installed; this is an environment dependency issue, not a reported test assertion failure.

## Boundaries and next scope

Real Kobo payloads are downloaded and normalized to separate prepared CSVs, but are not yet substituted into the database ingest path. Mixing real and synthetic submissions for the same plot/campaign/depth or plot/campaign/tree key would be unsafe without an explicit source-selection policy. Weather and Sentinel-2 context are combined only through the existing simulator interfaces: reanalysis forces the simulated field and supports QC; block NDVI supplies background to simulated plot NDVI. The next step is to design and test an explicit source-selection/merge contract before touching Data Engineering ingestion.

## Source selection contract (v1)

- Added `config/source_selection.yaml` as the explicit per-dataset source policy.
- Added `preparation/source_selection.py` to validate source choices and reject unsafe Kobo mixing.
- Upgraded `input_manifest.json` to version 2 with the contract hash and selected source map.
- `ingest_all` now requires this manifest and registers dataset-level source lineage in `audit.dataset_source`.
- Dataset-version manifests include the latest source-lineage registration.
- Existing scientific generation and transformation logic is unchanged.

## Scientific dashboard iteration

* One frontend: the CDN/Babel prototype under `dashboard/static/` was removed; Vite builds a self-contained bundle there.
* Fixed `ingest_kobo` re-ingest crash (`is_legacy` missing from the SELECT) and a stale external-data test.
* New `results` schema + `analysis/scientific.py` (persist once, read many), `/api/analysis/*` read-only endpoints, React analysis page with SVG charts.
* Dashboard tests no longer require fastapi at import time (`importorskip`).
