# aridtrial

An end-to-end, runnable data and analysis stack for a **desert soil-amendment field trial**, built as a
demonstration of the work described in a "trial data and analysis lead" job posting: experimental design,
field-form ingestion, a PostgreSQL/PostGIS database, live dashboards, audit-ready data quality, crop-water
models, and the mid-trial / endpoint statistical analysis.

> **The default trial data are synthetic.** Optional real sources (KoboToolbox, ERA5-Land and Sentinel-2)
> are downloaded and prepared with separate provenance. The simulator still knows the true treatment effects,
> which turns the default project into a closed-loop test bed. Synthetic outputs are not agronomic evidence
> about any real site.

> **Methodology:** `docs/METHODOLOGY.md` explains every simulation factor, method, equation and design choice in detail.

## Requirement-to-code map

| Posting requirement | Where it lives | How it is verified |
|---|---|---|
| Finalise the experimental design: layout, controls, replication, statistical plan | `design/layout.py` (seeded RCBD, UTM geometry, layout hash), `design/power.py` (noncentral-t, mixed-model Monte-Carlo, survival simulation) | layout validity/reproducibility tests; analytic power checked against simulation; type-I error of the mixed-model test |
| Pipeline from KoboToolbox forms, soil and weather sensors, water meters, satellite NDVI into PostgreSQL/PostGIS | `simulate/kobo.py` (XLSForms + API-v2-shaped payloads), `sources/kobo.py` (real API download + raw provenance), `preparation/kobo.py` (real Kobo preparation), `preparation/manifest.py` (source lineage), `pipeline/kobo_client.py` (normalisation + revision resolution), `pipeline/ingest.py`, `sql/01_schema.sql`, `sql/03_postgis.sql` | integration tests against a real PostgreSQL 16 server (row counts, idempotency, quarantine, edit/resubmission handling) |
| Real external data: weather reanalysis as forcing and QC reference; satellite NDVI | `sources/openmeteo.py`, `sources/copernicus.py`, `qc/reanalysis.py`, `design/pixel_purity.py`, `core.weather_reanalysis`, `core.ndvi_block` | unit conversions, API-lag guard, cache, QC recall/false-positive tests, mocked CDSE response parsing (all offline) |
| Live dashboards (Grafana) | `grafana/` (provisioned datasource + 14-panel dashboard on `mart.*` views) | every dashboard query is executed against the database in the test-suite; Grafana itself was **not** run |
| Data-quality checks, audit-ready for carbon-credit verification | `qc/rules.py` (Pandera ranges + temporal rules), `qc/reconciliation.py` (raw-to-core and core-to-mart reconciliation), `pipeline/audit.py` (hash-chained log, frozen dataset versions), append-only triggers | injected-fault recall; row-count, lineage, RCBD, referential-integrity and mart reconciliation checks |
| Explicit source selection and row-level provenance | `config/source_selection.yaml`, `preparation/source_selection.py`, `preparation/manifest.py`, `pipeline/ingest.py`, `sql/04_provenance.sql`, `docs/DATA_ENGINEERING_PROVENANCE.md` | source exclusivity, raw-to-core lineage, contract/manifest hashes, dataset-version verification |
| Crop-water and irrigation models (FAO-56, AquaCrop-OSPy) using trial data | `models/fao56.py` (Penman-Monteith, water balance; reproduces the FAO-56 worked example), `models/calibration.py` (per-plot calibration, hold-out validation), `models/aquacrop_model.py` | worked-example test; noise-free parameter recovery; hold-out RMSE in the report |
| Mid-trial and endpoint analysis, final report | `analysis/stats.py`, `analysis/run.py`, `report/build.py` | closed-loop coverage study; diagnostics; report (`outputs/report.html`) |

## Run it

```bash
pip install -e .[dev]

# a database: either the bundled throw-away cluster (needs PostgreSQL server binaries) ...
scripts/pg_local.sh start                       # prints the DSN, port 5433
export ARIDTRIAL_DSN=postgresql+psycopg2://aridtrial:aridtrial@127.0.0.1:5433/aridtrial
# ... or the full stack with PostGIS and Grafana
docker compose up -d                            # db on 5432, Grafana on :3000 (admin/admin)
export ARIDTRIAL_DSN=postgresql+psycopg2://aridtrial:aridtrial@localhost:5432/aridtrial

aridtrial demo                  # simulate -> DB -> ingest -> QC -> reconcile -> freeze version -> analysis -> outputs/report.html
aridtrial demo --postgis        # same, also applies sql/03_postgis.sql (needs the PostGIS image)
aridtrial demo --quick          # ~2 min instead of ~7 (fewer Monte-Carlo replicates)
aridtrial reconcile             # rerun reconciliation against the latest QC run
pytest                          # offline tests plus PostgreSQL integration tests when the driver/database are available
```

### Real external data (recommended; needs internet on *your* machine)

```bash
# 1. weather: ERA5-Land hourly -> FAO-56 daily. Either source gives the same product:
aridtrial fetch-weather                    # Open-Meteo archive API (no account)
aridtrial fetch-weather --source cds       # Copernicus CDS (needs ~/.cdsapirc with your token + accepted ERA5-Land licence)

# 2. NDVI: choose a BARE-DESERT area near Al Wathba, check it, then fetch
aridtrial fetch-ndvi --lon 54.62 --lat 24.10 --print-zones     # prints the 6 block-zone corners (lon, lat) - look at them in the Copernicus Browser
export CDSE_CLIENT_ID=...  CDSE_CLIENT_SECRET=...              # OAuth client from the CDSE dashboard (never commit)
aridtrial fetch-ndvi --lon 54.62 --lat 24.10

# 3. run everything on the real inputs
aridtrial demo --real                      # real ERA5-Land forcing + real Sentinel-2 NDVI background for the plots
```
`--reanalysis` / `--real-ndvi` select one of the two. Missing files are an error, never a silent fall-back to synthetic data.
What becomes real: the ERA5-Land meteorological forcing/reference and block-level Sentinel-2 background values. What stays
synthetic: treatments, tree growth/survival, soil chemistry, plot-level canopy contribution, on-site station readings (simulated from
reanalysis), sensors and meters. Real Kobo submissions can be downloaded and prepared separately; they are not yet substituted into
`aridtrial demo` or merged with synthetic Kobo records in the database pipeline.
The trial period (2025-10-01 .. 2026-09-30) is in the past, so both archives cover it. Outputs land in `data/external/`.

## Design decisions worth knowing

* **The plot is the experimental unit.** 6 blocks × 4 treatments (control, compost, biochar, compost + biochar), 10 trees
  per plot. Trees, soil layers and repeated visits are subsamples or repeated measures, never replicates.
* **One model, used for planning and analysis.** `design.power.fit_rm_model` (`y ~ treatment * campaign + (1|block) + (1|plot)`, REML)
  is the same function the analysis calls, so the power study tests the model that will actually be run.
* **Small-sample inference.** Treatment contrasts use a Wald *t* with the RCBD between-plot error df, (b−1)(t−1) = 15, and Holm
  adjustment. statsmodels' default z-based p-values are anticonservative with 6 blocks. The simulated type-I error of this choice
  is below nominal (conservative). Kenward–Roger (lme4/lmerTest) is the natural refinement for a final report.
* **Raw is immutable, QC only flags.** Kobo payloads are stored as received (`raw.kobo_submission`, append-only). Edits (same
  `rootUuid`) and re-submissions are versioned in `core`, never deleted. The marts that analysis and dashboards read apply the QC flags.
* **Audit trail.** Every ingest, QC run and export is a link in a SHA-256 hash chain (`audit.ingest_log`); the endpoint dataset
  is frozen as a hash-addressed version with per-table digests. Editing any frozen value is detected by `verify_dataset_version`.
  Tables are also protected against UPDATE/DELETE by triggers. This is *tamper-evident*, not a substitute for the registry's own
  verification procedures (Verra / Puro.Earth requirements were not reviewed here).
* **AquaCrop-OSPy scope.** It is an annual-crop model and this trial has year-round tree canopies and no annual-crop yield data. So the
  FAO-56 single-Kc model is the one calibrated against sensor and meter data; AquaCrop is used for a companion-forage *scenario* that
  consumes the trial-calibrated soil water-holding capacity, with uncertainty from the plot-to-plot calibration scatter. With crop
  observations, `run_forage_season` is the forward model to wrap in an optimiser.
* **AquaCrop-OSPy pitfall (isolated while building this):** it never returns when the soil has fewer than 12 compartments (4, 6 and 8
  tested; 12, 15 and 20 run in about 0.2 s regardless of total depth). `build_soil` always uses 12 × 0.1 m and asserts it.

## Limitations (read before quoting any number)

* Effect sizes and variance components are assumptions; real baseline data must replace them in the power analysis.
* Calibration ties θ<sub>WP</sub> to θ<sub>FC</sub> through a relation of the same form as the simulator's, so recovery of field capacity
  is optimistic. The hold-out period (days 273–364) is the honest accuracy test; per-plot hold-out irrigation errors are wide.
* Tree height and diameter are survivor-conditional plot means; mortality is analysed separately. A joint model is needed if mortality
  is informative about growth. Mortality is interval-censored at campaign visits; check proportional hazards with real data.
* `sql/03_postgis.sql` (geometry column, GPS-in-plot check, plot-overlap check) was written but **not executed** in the build
  environment, which had plain PostgreSQL only. The Python QC rule `soil.gps_in_plot` implements the same check and is tested.
* **No live API call was made** (the build environment's network allow-list blocked Open-Meteo and Copernicus). The Kobo, Open-Meteo and
  CDSE clients are tested against mocked payloads written from the public documentation; the first live run is a commissioning test.
  Request/response details most likely to need adjustment: Open-Meteo hourly variable names, the Statistical API response shape, SCL classes kept.
* **Reanalysis is not station data.** ERA5-Land (~9-11 km) smooths extremes and models radiation/wind. Here it is (a) the weather forcing of the simulated
  plots and (b) the independent reference for station QC. The on-site station, soil sensors, meters and plot-level NDVI remain simulated; the
  station-vs-reanalysis discrepancy size and the injected drift/offset faults are assumptions. Reanalysis disagreement raises `warn` flags only.
* **Sentinel-2 cannot resolve these plots.** A 12 x 20 m plot holds at most one whole 10 m pixel (6 of 24 plots) and none after a 5 m edge buffer
  (`design/pixel_purity.py`, report section 3b). Real NDVI is therefore block/site-level context. Real NDVI over a young desert plantation is mostly bare soil,
  so it cannot carry the synthetic treatment effect; plot-level canopy needs UAV imagery or larger plots.
* Attribution if used: "Generated using Copernicus Climate Change Service information" (ERA5-Land via Open-Meteo, CC BY 4.0; commercial use needs an Open-Meteo key);
  "Contains modified Copernicus Sentinel data, processed by Sentinel Hub / CDSE".
* Grafana provisioning was not started here; its queries are tested for correctness against the database.

## External source commands

```bash
# Download raw Kobo API v2 snapshots (token from KOBOTOOLBOX_TOKEN)
aridtrial fetch-kobo --soil-asset <SOIL_ASSET_UID> --tree-asset <TREE_ASSET_UID>

# Prepare real Kobo payloads without mixing them into synthetic submissions
aridtrial prepare-kobo --soil-payload <SOIL_SUBMISSIONS_JSON> --tree-payload <TREE_SUBMISSIONS_JSON>

# Retrieve real weather and Sentinel-2 block context
aridtrial fetch-weather --source openmeteo   # or --source cds
aridtrial fetch-ndvi
```

## Layout

```text
src/aridtrial/
  design/       layout.py power.py pixel_purity.py
  sources/      kobo.py openmeteo.py cds.py copernicus.py
  preparation/  source_selection.py manifest.py kobo.py
  simulate/     weather.py field.py kobo.py builder.py artifacts.py fault_injection.py
  pipeline/     db.py kobo_client.py ingest.py audit.py
  qc/           rules.py reanalysis.py run.py reconciliation.py
  models/       fao56.py calibration.py aquacrop_model.py
  analysis/     stats.py run.py
  report/       build.py
sql/            01_schema.sql 02_views.sql 03_postgis.sql 04_provenance.sql 05_data_quality.sql
grafana/        provisioning/ dashboards/
frontend/       Vite + React sources (build -> src/aridtrial/dashboard/static/)
tests/          offline and database integration tests
```


## Data source policy

See [`docs/SOURCE_SELECTION_AND_DATA_PREPARATION.md`](SOURCE_SELECTION_AND_DATA_PREPARATION.md) for the per-dataset source-selection contract and permitted real/synthetic combinations, and [`docs/DATA_ENGINEERING_PROVENANCE.md`](DATA_ENGINEERING_PROVENANCE.md) for row-level lineage, database registration, and migration rules. The active policy is [`config/source_selection.yaml`](../config/source_selection.yaml). Run `aridtrial init-db` to apply the additive provenance migration to an existing database; do not use `--reset` unless you intend to drop the project schemas.


## Data quality and reconciliation

`aridtrial qc` evaluates record-level agronomic and sensor rules and appends flags to `audit.qc_result`.
`aridtrial reconcile` then checks whether the registered inputs, ingestion log, typed core tables, source lineage, and QC-filtered marts agree. The full `aridtrial demo` runs both stages in sequence.

Reconciliation is intentionally non-destructive: it never edits raw or core records. It writes a run summary and row-level check results to `audit.reconciliation_run` and `audit.reconciliation_result`. A result is `fail` when an invariant or count comparison is inconsistent, `warn` when evidence is incomplete (for example, a legacy ingest event is missing), and `pass` when the check agrees. Kobo checks compare distinct payload hashes rather than raw row counts because one soil submission can expand into multiple layer records. File-drop checks compare the ingest log's accepted row count with core rows carrying that exact file hash.

Useful queries:

```sql
-- Latest reconciliation outcome
SELECT * FROM mart.v_reconciliation_summary ORDER BY severity;

-- Checks by dataset and severity
SELECT * FROM mart.v_reconciliation_by_dataset ORDER BY dataset, severity;

-- Inspect failed or warning checks
SELECT dataset, check_id, severity, source_count, target_count, difference, details
FROM audit.reconciliation_latest
WHERE severity IN ('fail', 'warn')
ORDER BY severity, dataset, check_id;
```

Apply the additive migration with `aridtrial init-db` on an existing database. Reconciliation requires the latest QC run because the clean marts are intentionally bound to `audit.qc_latest`.

## Data Quality Dashboard

A read-only FastAPI dashboard summarizes source identity, raw/core/final record counts, quarantine, QC findings and the latest cross-layer reconciliation run. Install the optional dependencies and start it with:

```bash
pip install -e ".[dashboard]"
aridtrial dashboard
```

Then open `http://127.0.0.1:8000`. See [Data Quality Dashboard](DATA_QUALITY_DASHBOARD.md) for API endpoints, count semantics, database configuration and deployment safety notes. The dashboard is read-only and has no built-in authentication; do not expose it directly to the public internet.


## React Data Quality Dashboard

The dashboard has two pages: Data quality and **Scientific & Agronomic Analysis** (EDA, design, mixed models, inference, power, agronomy, survival, validation; see [`docs/SCIENTIFIC_DASHBOARD.md`](SCIENTIFIC_DASHBOARD.md)). It uses a responsive React frontend while retaining the existing read-only FastAPI and PostgreSQL API. See [`docs/REACT_DASHBOARD.md`](REACT_DASHBOARD.md) for run and build instructions.
