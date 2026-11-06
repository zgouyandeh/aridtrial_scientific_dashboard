# Data Engineering Provenance Contract

## Purpose

The ingestion layer preserves the identity of each dataset from the selected input package through raw landing, core transformation, audit registration, and dataset versioning. Scientific calculations and experimental design are unchanged.

## Source identity carried by records

Every ingested file-drop dataset stores:

- `source_type`: for example `synthetic`, `real`, or `synthetic_station`;
- `source_name`: provider or generator identity;
- `source_dataset`: dataset key from `config/source_selection.yaml`;
- `source_sha256`: SHA-256 of the delivered file;
- `manifest_sha256`: canonical hash of the input manifest;
- `contract_sha256`: hash of the source-selection contract file.

The same source identity fields are stored in `raw.file_drop` and the corresponding `core` table. Kobo submissions carry the same contract and source fields in `raw.kobo_submission`; normalised soil and tree records retain them in `core.soil_sample` and `core.tree_obs`.

The plot layout is synthetic and carries the same lineage fields plus its delivered CSV hash in `core.plot`; its raw file registration is also stored in `raw.file_drop`. Ground Truth remains outside operational tables.

## Ingestion rules

1. `ingest_all` requires `input_manifest.json` version 2 or later.
2. Every selected source must be present in the dataset's `allowed_sources` list.
3. Ground Truth must have `ingest: false` and `ingest_target: null`.
4. Soil and tree Kobo forms are source-exclusive. If raw records for a form already belong to a different source identity, ingestion stops and asks for an explicit migration. It never unions real and synthetic submissions implicitly.
5. Optional real reference files must agree with the manifest: a selected `real` source requires its prepared file; a selected `absent` source must not have a stale file in the input package.
6. File hashes and source identity are checked before a duplicate file is skipped. A hash already associated with another source identity is an error.
7. Primary-key conflicts are not overwritten. A changed file that overlaps existing business keys requires a deliberate rebuild or migration.

## Database layout

- `raw.kobo_submission`: immutable Kobo payloads and source metadata.
- `raw.file_drop`: content hash, dataset key, and source metadata for delivered files.
- `audit.dataset_source`: one immutable source-contract registration per manifest and dataset.
- `core.*`: typed records with row-level source identity, manifest hash, contract hash, and original file/payload hash.
- `audit.ingest_log`: hash-chained events containing source metadata and content hashes.
- `audit.dataset_version`: frozen hashes for marts, core tables, raw landing tables, and source-lineage registration.

`sql/04_provenance.sql` is an additive migration. `db.apply_schema()` applies it automatically after the base schema and before views. Existing legacy records are classified as synthetic when their old raw source labels support that conclusion; their unavailable historical manifest hashes remain NULL rather than being invented.

## Rebuild and migration policy

Changing the operational source for an existing dataset is not an ordinary rerun. It requires an explicit migration plan covering raw retention, business-key overlap, current/superseded resolution, QC baselines, and dataset-version history. For Kobo, the current source-exclusive guard deliberately prevents a mixed-source rebuild.

## Verification

The test suite checks source identity preservation, explicit Kobo source-migration failure, and inclusion of raw/core lineage in dataset-version hashes. PostgreSQL integration tests require a PostgreSQL driver and database; they must be run in the project's normal development environment.

For cross-layer row-count, payload-hash, lineage and QC-mart checks, see
[`DATA_QUALITY_AND_RECONCILIATION.md`](DATA_QUALITY_AND_RECONCILIATION.md).
