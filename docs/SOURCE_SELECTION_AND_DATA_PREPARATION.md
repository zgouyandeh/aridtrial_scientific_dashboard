# Source Selection & Data Preparation Contract

## Purpose

This contract makes source choice explicit for every dataset in a trial run. It prevents silent fallback, accidental real/synthetic unions, and loss of source lineage before PostgreSQL ingestion.

The active contract is `config/source_selection.yaml`. A run records the contract content hash and selected source for each dataset in `data/input_manifest.json`.

## Dataset source decisions (contract v1)

| Dataset | Selected source | Use of real data | Database target |
|---|---|---|---|
| Plot layout | Synthetic | Not allowed | `core.plot` |
| Soil samples | Synthetic Kobo | Real Kobo is prepared separately; no union | `raw.kobo_submission` |
| Tree observations | Synthetic Kobo | Real Kobo is prepared separately; no union | `raw.kobo_submission` |
| Station weather | Synthetic station | May be generated using ERA5-Land forcing | `core.weather_daily` |
| Weather reanalysis | Optional real reference | Independent comparison/QC reference; never replaces station observations | `core.weather_reanalysis` |
| Sensor readings | Synthetic | Not allowed in v1 | `core.sensor_reading` |
| Water-meter readings | Synthetic | Not allowed in v1 | `core.water_meter` |
| Plot-level NDVI | Synthetic plot signal | May use real block NDVI as background context | `core.ndvi` |
| Sentinel-2 block NDVI | Optional real reference | Block-level contextual data only | `core.ndvi_block` |
| Ground truth | Synthetic validation-only | Never operational | No target; `ingest: false` |

## Combination rules

### Weather

When real ERA5-Land data is supplied, it is used as meteorological forcing for the synthetic field simulation. A synthetic station series is then generated with the existing discrepancy model and fault injection. The station series remains the operational-style dataset; reanalysis is stored independently for reference and QC. Real reanalysis must not overwrite `core.weather_daily`.

### Sentinel-2

Real Sentinel-2 L2A statistics are aggregated at block level. The existing simulation uses these values as spatial background context while retaining the simulated plot-level canopy contribution and noise. Block-level satellite values must not be copied into plot-level records.

### KoboToolbox

Real Kobo submissions are downloaded and normalized into separate prepared files. Contract v1 selects synthetic Kobo submissions for the current demo ingestion path. A real/synthetic union is forbidden because the revision resolver uses business keys such as `plot_id + campaign + depth` and could incorrectly supersede records across independent data-generation domains. Switching to real Kobo requires a future explicit source migration and a defined campaign/identity reconciliation policy.

### Ground truth

Ground-truth outputs are validation artifacts only. They must never be loaded into operational raw/core tables or exposed as operational observations.

## Execution

Default contract:

```bash
aridtrial simulate
```

Use both available real reference datasets:

```bash
aridtrial simulate --real
```

Use one reference input or a custom contract:

```bash
aridtrial simulate --reanalysis data/external/reanalysis_daily.csv
aridtrial simulate --real-ndvi data/external/ndvi_blocks_s2.csv
aridtrial simulate --source-contract config/source_selection.yaml
```

The CLI validates selected files and contract rules before simulation. It does not silently fall back to another source.

## Provenance lifecycle

1. Source clients retain raw API responses/cache files and source-specific provenance.
2. Preparation writes normalized files separately and does not overwrite raw payloads.
3. Simulation writes `input_manifest.json`, including selected sources, contract hash, input hashes, source metadata, generated artifact hashes, and declared combination rules.
4. `ingest_all` requires a manifest with source-selection metadata and registers one immutable row per dataset in `audit.dataset_source`.
5. Raw, core, and quarantine records retain source identity and the hashes that connect them to the manifest and contract.
6. The ingest audit hash chain records registration of the manifest. Dataset-version manifests include source lineage and hashes for mart, core, and raw tables.

## Database migration

`sql/01_schema.sql` creates `audit.dataset_source`; `sql/04_provenance.sql` adds row-level lineage columns and backfills recoverable legacy core metadata. Apply the schema before ingestion:

```bash
aridtrial init-db
aridtrial ingest
```

The table is append-only. A new input manifest creates a new manifest hash and therefore a new lineage registration, while re-ingesting the same manifest is idempotent.

## Version 1 boundaries

This contract formalizes current safe behavior; it does not yet enable choosing real Kobo as the operational source or ingesting real station/sensor/meter data. Those choices require explicit mapping, identity reconciliation, and schema decisions. No scientific formulas, random streams, RCBD design, FAO-56 calculations, NDVI calculations, or model parameters are changed by this contract.
