# External Data Sources and Preparation

This document defines how real source data enter the AridTrial project and where they are allowed to interact with synthetic data. The objective is traceability and a clear scientific interpretation, not to make the whole trial dataset appear real.

## 1. KoboToolbox

### Download

`aridtrial fetch-kobo` uses the KoboToolbox API v2 endpoint:

`GET /api/v2/assets/{asset_uid}/data/?format=json&limit={page_size}`

The client follows the API's `next` link until all pages are retrieved. The token is read from `KOBOTOOLBOX_TOKEN`; it must not be committed to the repository or written into provenance files.

```bash
aridtrial fetch-kobo \
  --soil-asset YOUR_SOIL_ASSET_UID \
  --tree-asset YOUR_TREE_ASSET_UID
```

The raw landing area is immutable and timestamped:

```text
data/external/kobo/
  <asset_uid>/
    <retrieval_id>/
      pages/page-0001.json
      pages/page-0002.json
      submissions.json
      provenance.json
```

Each page file preserves the API response. The provenance file records the server, asset and logical form IDs, retrieval time, page URLs, page and submission counts, submission-time range, and SHA-256 hashes. Re-running a download creates a new snapshot rather than replacing an earlier one.

### Prepare

```bash
aridtrial prepare-kobo \
  --soil-payload path/to/soil/snapshot/submissions.json \
  --tree-payload path/to/tree/snapshot/submissions.json
```

The preparation stage reuses the existing normalisers and revision logic. It writes:

- `data/prepared/kobo/soil_samples_real.csv`
- `data/prepared/kobo/tree_observations_real.csv`
- `data/prepared/kobo/provenance.json`

Rows retain the real source label, asset UID, Kobo root UUID, payload hash, and submission timestamp. The latest revision for each business key is marked `current`; prior revisions remain `superseded` with a reason.

**Important boundary:** these real prepared CSVs are not currently loaded by `aridtrial ingest` or `aridtrial demo`. The existing database demo continues to use synthetic Kobo-shaped submissions. Do not load real and synthetic submissions together for the same business keys: the current `build_core_from_raw` routine resolves all submissions for a form together, and does not yet apply an explicit source-priority policy. That policy must be designed and tested before real Kobo data are connected to database ingestion.

## 2. Weather: Open-Meteo and CDS

Both clients retrieve ERA5-Land reanalysis. They are two access routes to the same reanalysis family, not two independent observations.

```bash
aridtrial fetch-weather --source openmeteo
aridtrial fetch-weather --source cds
```

- Open-Meteo is convenient for a point query and stores its raw JSON response in a request-keyed cache.
- CDS downloads monthly NetCDF files directly. It records request hashes, file hashes, grid-cell coordinates, cache status, and per-file sidecar metadata. It requires a configured `cdsapi` credential and accepted dataset licence.

Both produce `data/external/reanalysis_daily.csv` using the daily weather input contract. The provenance JSON records source type/name, requested coordinates, date range, product/model, retrieval metadata, grid coordinates, and hashes.

The project converts hourly data to local daily values before the simulator consumes them. Open-Meteo uses the `Asia/Dubai` timezone; the CDS conversion applies the existing UTC+4 local-day shift. CDS accumulated precipitation and radiation are de-accumulated before daily aggregation. The current FAO-56 implementation calculates the project's ET0 used by the simulator; Open-Meteo's ET0 field is retained as a comparison value when available.

### How real weather is combined

When a real reanalysis CSV is supplied to `simulate.builder.build`:

1. The real daily reanalysis is aligned to the trial dates and converted into the simulator's weather forcing using the existing `weather_from_reanalysis` function.
2. The field and water-balance simulation uses that forcing.
3. A synthetic on-site station series is generated from the real forcing using the existing discrepancy model and random stream.
4. The real reanalysis is retained as an independent reference for station QC.

Therefore, **the weather forcing is real reanalysis, but the on-site station observations, sensor readings, soil response, and water-meter readings remain synthetic**. This is a controlled simulation conditioned on real weather, not a record of measured field outcomes.

## 3. Sentinel-2 L2A

```bash
# Inspect the proposed block polygons first
aridtrial fetch-ndvi --lon 54.62 --lat 24.10 --print-zones

# Configure CDSE OAuth credentials in environment variables, then fetch
aridtrial fetch-ndvi --lon 54.62 --lat 24.10
```

The client uses the Sentinel Hub Statistical API over Sentinel-2 L2A, calculates NDVI from B08 and B04 at 10 m, and applies the existing Scene Classification Layer mask (classes 4, 5 and 7). It requests statistics for each block polygon in EPSG:32640. Raw response JSON, request/response hashes, exact UTM geometries, product, time range, cloud filter, mask classes, and evalscript hash are retained in the external cache and provenance JSON.

The output is `data/external/ndvi_blocks_s2.csv` with block/date statistics. It is **not plot-level satellite measurement**. The plots are 12 × 20 m and cannot reliably contain enough pure 10 m pixels; block polygons provide the more defensible spatial context for this demonstration.

### How real NDVI is combined

The existing `load_background` function aligns block-level NDVI to the trial's daily timeline, interpolates within the observed period, holds endpoint values constant, and uses the across-block median for blocks with insufficient observations. The field simulator then adds its existing synthetic canopy contribution and noise to that background to produce plot-level NDVI.

Thus, **the satellite background is real, but the plot-level canopy signal and final plot NDVI series are synthetic**. The current mask, interpolation, fallback, and minimum-observation rules are preserved by this refactor.

## 4. Input manifest and provenance

Every `simulate` build writes `data/input_manifest.json`. It records:

- Trial configuration relevant to the generated package.
- Real weather and Sentinel-2 input file hashes, row counts, date ranges, and adjacent source provenance JSON when available.
- Kobo download snapshots found under `data/external/kobo/`.
- Hashes, byte sizes, row counts, and columns for generated CSV/JSON artifacts.
- Dataset-level source labels and explicit `derived_from` relationships.
- The policy that ground truth is synthetic validation-only and must never be ingested.

The manifest complements (it does not replace) the raw source files, source-specific provenance files, and the database audit chain.

## 5. Source combination matrix

| Dataset | Source | Role in this project | Real or synthetic in current demo? |
|---|---|---|---|
| Kobo soil/tree submissions | Kobo API v2 | Field observations; prepared as typed records | Real downloads are separate; demo ingestion remains synthetic |
| Weather reanalysis | ERA5-Land via Open-Meteo or CDS | Field-simulation forcing and station-QC reference | Real when supplied |
| On-site station weather | Simulation conditioned on reanalysis | Station-vs-reference QC exercise | Synthetic |
| Sentinel-2 block NDVI | Sentinel-2 L2A via CDSE | Spatial background context for NDVI simulation | Real when supplied |
| Plot-level NDVI | Field simulator | Background + simulated canopy contribution + noise | Synthetic |
| Soil chemistry, tree growth/survival | Field simulator | Known-truth trial outcomes | Synthetic |
| Sensors and water meters | Field simulator + fault injection | Data-engineering and QC exercise | Synthetic |
| Ground truth and injected-fault labels | Simulator | Validation only | Synthetic; never ingest |

## 6. What is deliberately not changed

This stage does not alter RCBD randomisation, field-data generation formulas, FAO-56 equations, weather discrepancy assumptions, NDVI background interpolation, fault rates, random-number streams, operational CSV schemas, database schemas, or statistical analysis. It adds source clients, raw provenance, preparation outputs, and a lineage manifest. Any scientific concern should be reviewed separately before changing those rules.
