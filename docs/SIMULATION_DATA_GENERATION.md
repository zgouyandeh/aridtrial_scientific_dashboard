# Simulation Data Generation

## Purpose

The simulation package creates the input datasets used by the AridTrial data-engineering pipeline. It also creates separate ground-truth files used to validate data-quality checks and statistical analyses. Ground truth is not operational input and must never be ingested into the application database.

## Entry point

Use `aridtrial.simulate.builder.build()` to generate a complete dataset package. The CLI `aridtrial simulate` command calls this function.

The older import path `aridtrial.simulate.export.build` remains available as a compatibility wrapper for existing tests and scripts. New code should import from `aridtrial.simulate.builder`.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `design/layout.py` | Creates the randomized complete block design (RCBD), plot geometry, and randomization record. |
| `simulate/weather.py` | Generates synthetic daily weather, prepares real reanalysis as trial weather forcing, and simulates on-site station observations from real reanalysis. |
| `simulate/field.py` | Generates plot-level, soil, tree, water-balance, sensor, meter, and plot-level NDVI datasets. |
| `simulate/kobo.py` | Creates XLSForm definitions and synthetic KoboToolbox API-shaped submissions, including data-entry faults. |
| `simulate/ndvi_background.py` | Loads real Sentinel-2 block-level NDVI and prepares the background series used by the synthetic plot-level NDVI generator. |
| `simulate/fault_injection.py` | Applies controlled faults to generated time-series inputs and, when real reanalysis is used, injects station-vs-reference drift. |
| `simulate/artifacts.py` | Writes generated input files, design artifacts, optional reference drops, and validation-only ground truth. |
| `simulate/builder.py` | Coordinates the generation steps and writes the input lineage manifest. It contains no scientific generation formulas. |

## Build sequence

1. Create the output folders and remove optional reference files left by a previous run.
2. Generate the RCBD plot layout.
3. Prepare the weather forcing:
   - Without real reanalysis, generate synthetic weather.
   - With real reanalysis, align the real daily series to the trial dates, calculate FAO-56 ET0, and simulate an on-site station series with the existing discrepancy model.
4. Optionally prepare real Sentinel-2 block-level NDVI as the background for plot-level NDVI simulation.
5. Generate the synthetic field datasets using the layout and prepared environmental inputs.
6. Write the plot layout and randomization record; generate XLSForms and Kobo-shaped submissions.
7. Inject the existing controlled data-quality faults.
8. Write operational-style input drops, optional real reference drops, and separate ground-truth files.

## Output contract

```text
data/
├── kobo/
│   ├── soil_sampling.json
│   └── tree_survey.json
├── xlsform/
│   ├── soil_sampling.xlsx
│   └── tree_survey.xlsx
├── drops/
│   ├── plot_layout.csv
│   ├── sensors_hourly.csv
│   ├── water_meters_daily.csv
│   ├── ndvi_sentinel2.csv
│   ├── weather_daily.csv
│   ├── reanalysis_daily.csv       # only when real reanalysis is supplied
│   └── ndvi_blocks_s2.csv         # only when real block NDVI is supplied
├── input_manifest.json       # source lineage and generated artifact hashes
└── truth/
    ├── randomisation_record.json
    ├── layout.png
    ├── injected_faults.csv
    ├── soil_truth.csv
    ├── tree_truth.csv
    ├── daily_truth.csv
    ├── plot_truth.csv
    └── true_effects.json
```

`weather_daily.csv` deliberately excludes `et0_mm`; the data-ingestion stage recalculates ET0 using the project's FAO-56 implementation. `water_meters_daily.csv` contains cumulative register readings, not precomputed daily volumes.

## Real and synthetic data boundaries

- Real weather reanalysis is used as the environmental forcing for the simulated field. The on-site station series remains simulated and includes the existing discrepancy model.
- Real Sentinel-2 block-level NDVI supplies a background series. Plot-level NDVI still includes the simulated canopy contribution and noise.
- Synthetic Kobo submissions mimic the KoboToolbox API v2 payload shape. They are not downloaded from a live Kobo project. Real Kobo payloads are downloaded under `data/external/kobo/` and prepared separately under `data/prepared/kobo/`; they are not merged into these synthetic submissions.
- `input_manifest.json` records real context file hashes and their provenance, generated artifact hashes, and which synthetic datasets were derived from real weather or satellite context.
- Ground truth and injected-fault labels are validation artifacts. They must remain separate from operational input drops.

## Reproducibility and compatibility

The scientific generator functions and their random-number streams are retained. The refactor separates orchestration, fault injection, and file writing without intentionally changing the scientific data-generating process. UUIDs created with `uuid.uuid4()` and XLSX workbook metadata are inherently non-deterministic; compare the generated scientific tables and CSV inputs rather than expecting every output byte to be identical across runs.
