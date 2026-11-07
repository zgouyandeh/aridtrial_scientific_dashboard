# AridTrial
### An auditable data and scientific analysis platform for agricultural field trials in arid environments

[![CI](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/ci.yml)

**[Explore the interactive dashboard demo](https://zgouyandeh.github.io/aridtrial_scientific_dashboard/)**

> **Important: All trial observations and reported experimental results in this public demonstration are based on synthetic data.**
>
> AridTrial simulates a one-year desert field experiment near Al Wathba, Abu Dhabi. Treatment effects and realistic data-quality faults are deliberately introduced so that the statistical methods and data pipeline can be tested against known conditions.
>
> This is an independent engineering and scientific-analysis demonstration, not a report of actual field measurements or evidence of treatment effectiveness in a real agricultural site.

![Dashboard preview](docs/images/dashboard-inference.png)

---

## 1. Project Overview

How can compost and biochar improve soil conditions and support tree establishment in arid environments?

AridTrial explores this question through a simulated agricultural field trial while demonstrating how to build an auditable data and analysis platform around an experimental study.

The project integrates:

- Experimental design and statistical planning.
- Field-data ingestion and data engineering.
- PostgreSQL-based data management.
- Data quality validation and provenance tracking.
- Exploratory and inferential statistical analysis.
- Soil, water, weather, and vegetation monitoring.
- Interactive dashboards and reproducible reporting.

The objective is to demonstrate an end-to-end workflow in which every analytical result can be traced back to its source data, quality-control status, dataset version, and analysis run.

## 2. Experimental Design

The simulated field consists of **24 plots**, each measuring 12 × 20 m, arranged in a Randomized Complete Block Design (RCBD).

There are six blocks, each containing four plots. Every treatment is randomly assigned to one plot within each block.

| Code | Treatment |
|---|---|
| `CTRL` | Control — no amendment |
| `COMP` | Compost |
| `BIOC` | Biochar |
| `COMP_BIOC` | Compost + biochar |

Each plot contains 10 trees.

The simulated trial follows five measurement occasions, from baseline (`T0`) to the final campaign (`T4`).

Measurements include:

- Soil organic carbon (SOC).
- Soil electrical conductivity (ECe), pH, and bulk density.
- Tree height, stem thickness, and survival.
- Daily soil-moisture sensor readings.
- Cumulative irrigation-meter readings.
- Weather observations and reference data.
- Satellite-derived vegetation indices (NDVI).

**Experimental unit:** The plot is the experimental unit. Individual trees and repeated measurements within a plot are not treated as independent experimental replicates.

### Research questions

1. Do compost and biochar treatments improve soil organic carbon?
2. Do amendments influence soil salinity, bulk density, and moisture?
3. Do treatments affect tree growth and survival?
4. How do water use and environmental conditions vary across treatments?
5. What can this experimental design reliably detect with six blocks?

## 3. End-to-End Architecture

The platform follows a modular pipeline that separates data ingestion, quality control, scientific analysis, and presentation.

```mermaid
flowchart LR
    A[Field Forms<br/>KoboToolbox]
    B[Sensors and Water Meters]
    C[Weather and Satellite Data]

    A --> D[(PostgreSQL)]
    B --> D
    C --> D

    D --> E[Raw and Core Layers]
    E --> F[Data Quality and Reconciliation]
    F --> G[Versioned Analysis Dataset]
    G --> H[Statistical and Agronomic Analysis]
    H --> I[(Analysis Results)]
    I --> J[React Dashboards]
```

### Data engineering

- Structured ingestion of field observations and environmental measurements.
- PostgreSQL schemas for raw, core, and analytical data.
- Source identification and dataset provenance.
- Validation rules and quarantine management.
- Cross-layer reconciliation.
- Versioned datasets and reproducible analysis runs.
- Audit metadata and data-integrity hashes.

### Data quality principles

The pipeline follows a non-destructive validation approach:

- Original observations are preserved.
- Invalid or suspicious records are flagged rather than silently modified.
- Quality-control findings are recorded with their rules and provenance.
- Analytical datasets apply explicit quality filters.
- Reconciliation checks help verify consistency between pipeline layers.

The architecture is designed to support traceability and audit preparation. It does not claim formal Verra or Puro.Earth certification or compliance.

## 4. Statistical and Agronomic Analysis

The scientific dashboard provides nine analytical sections.

| Section | Main capabilities |
|---|---|
| Exploratory Analysis | Distributions, trajectories, box plots, correlations |
| Experimental Design | RCBD structure, plot allocation, block effects |
| Treatment Comparison | Estimated marginal means, contrasts, confidence intervals |
| Mixed-effects Models | Treatment, campaign, interaction, random effects, model diagnostics |
| Statistical Inference | Hypothesis testing, multiple comparisons, randomization tests |
| Power & Sensitivity | Statistical power, MDE, Monte Carlo simulation |
| Agronomy & Environment | FAO-56, water balance, irrigation, soil moisture, weather |
| Survival Analysis | Tree survival and time-to-event analysis |
| Validation | Recovery of known simulated effects and methodological checks |

### Statistical methods

The project includes:

- Randomized Complete Block Design (RCBD).
- ANOVA and treatment contrasts.
- Linear mixed-effects models.
- Repeated-measures analysis.
- Estimated marginal means.
- Confidence intervals and effect sizes.
- Holm multiple-testing correction.
- Randomization-based inference respecting within-block treatment allocation.
- Survival analysis.
- Statistical power and minimum detectable effect calculations.
- Monte Carlo sensitivity analysis.

The analysis distinguishes statistical significance from practical importance. Effect sizes and uncertainty intervals are presented alongside hypothesis-testing results.

## 5. What the Simulation Demonstrates

The simulator deliberately introduces known treatment effects. The following results illustrate whether the analysis can recover those effects under the simulated conditions.

**These are simulation results, not empirical findings from a real agricultural field.**

### 5.1 Soil organic carbon

After one simulated year, the estimated differences in soil organic carbon (0–15 cm) relative to control are:

| Treatment | Difference (percentage points) | Relative difference |
|---|---:|---:|
| Compost | +0.14 | approximately +57% |
| Biochar | +0.21 | approximately +88% |
| Compost + biochar | **+0.35** | **approximately +143%** |

The simulator's planted effects were +0.15, +0.20, and +0.38 percentage points, respectively. The analysis recovered these effects within approximately 0.03 percentage points.

### 5.2 Soil salinity and pH

Simulated soil salinity (ECe) decreased by approximately:

- Compost: 0.95 dS/m.
- Biochar: 0.65 dS/m.
- Compost + biochar: 1.46 dS/m.

No clear treatment-related pH change was detected in the simulated analysis.

### 5.3 Bulk density and soil moisture

Bulk density decreased under biochar and combined amendments:

- Biochar: −0.07 g/cm³.
- Compost + biochar: −0.11 g/cm³.

The soil-moisture simulation also shows wetter conditions in amended plots.

### 5.4 Tree growth

After one simulated year, estimated height differences relative to control were:

| Treatment | Height difference |
|---|---:|
| Compost | +6.5 cm |
| Biochar | +10.4 cm |
| Compost + biochar | +13.3 cm |

Stem-thickness differences ranged from approximately 0.8 to 1.4 mm.

Some growth comparisons are borderline under the stricter validation check and should not be interpreted as equally strong evidence.

### 5.5 Tree survival

The simulation includes 60 trees per treatment.

| Treatment | Simulated deaths |
|---|---:|
| Control | 18 |
| Compost | 12 |
| Biochar | 14 |
| Compost + biochar | 2 |

The survival analysis estimates treatment-associated differences in the hazard of tree mortality. The biochar result is uncertain, while the combined treatment shows a stronger simulated survival signal.

The small number of experimental plots means survival estimates should be interpreted cautiously.

### 5.6 Irrigation water use

Simulated seasonal irrigation was approximately:

- Control: 606 mm.
- Compost + biochar: 667 mm.

The difference is approximately 61 mm. The possibility that larger, healthier trees require more irrigation is a hypothesis, not a causal conclusion established by this simulation.

### 5.7 Experimental design sensitivity

The simulation also illustrates the importance of blocking and replication.

- Approximately 85% of the initial plot-level variation is attributed to differences between blocks.
- Blocking substantially improves the precision of treatment comparisons.
- The estimated minimum detectable soil-carbon difference is approximately 0.06–0.11 percentage points, depending on the assumed variability.
- Survival analysis is less precise with six blocks, highlighting the importance of replication when survival is a primary outcome.

All these figures depend on the simulation assumptions and should not be treated as estimates of actual field variability.

## 6. Statistical Validation: Two Complementary Approaches

The main statistical model is complemented by a randomization-based analysis.

Treatment labels are reshuffled within blocks, preserving the original randomized experimental structure. The resulting reference distribution is used to assess how unusual the observed treatment differences would be under the randomization scheme.

This provides an assumption-light complementary check alongside model-based inference.

Agreement between the approaches strengthens confidence in the simulated analysis. Disagreement is reported rather than hidden.

The validation dashboard also compares recovered treatment effects with the known effects planted by the simulator.

## 7. Data Quality and Pipeline Validation

The simulator injects controlled faults to test whether the data-quality rules identify realistic operational problems.

Examples include:

- Sensor spikes and stuck readings.
- Water-meter register resets.
- Missing and invalid observations.
- Cloud-contaminated satellite scenes.
- Source and cross-layer reconciliation issues.

The public demonstration reports the following simulated quality-control pass rates:

| Dataset | Pass rate |
|---|---:|
| Soil | 95.8% |
| Trees | 99.9% |
| Sensors | 99.6% |
| Water meters | 99.98% |
| Weather | 98.6% |
| Plot-level satellite data | 89% |

The pipeline also executes 54 cross-layer reconciliation checks. In the published simulation snapshot, none failed.

The presence of QC failures is intentional: the purpose is to demonstrate detection, traceability, and controlled exclusion rather than to produce artificially perfect data.

## 8. Interactive Dashboards

The project includes two dashboards.

### Data Quality & Engineering

![Data quality dashboard](docs/images/dashboard-quality.png)

Provides visibility into:

- Ingested and processed records.
- Dataset-level source information.
- Raw, core, and analytical record counts.
- QC findings and severity.
- Quarantined records.
- Cross-layer reconciliation.
- Data lineage and pipeline status.

### Scientific & Agronomic Analysis

![Scientific analysis dashboard](docs/images/dashboard-eda.png)

Provides nine sections covering exploratory analysis, experimental design, treatment comparisons, mixed-effects models, statistical inference, power analysis, agronomic analysis, survival, and validation.

Each analysis is associated with its run identifier, dataset version, QC run, and random seed.

### Published dashboard versus local execution

The GitHub Pages demonstration is an **interactive, precomputed snapshot**. It does not run a PostgreSQL database or execute the complete pipeline when someone opens the page.

The GitHub Actions workflow can regenerate and publish the snapshot.

For a locally running application, use the installation and execution instructions below. This starts the FastAPI backend and serves the dashboard against the local database.

## 9. Run the Public Demonstration

Open the published dashboard:

**[AridTrial – Interactive Dashboard](https://zgouyandeh.github.io/aridtrial_scientific_dashboard/)**

The published snapshot uses a quick simulation run to keep the workflow practical.

To regenerate the snapshot:

1. Open the repository's **Settings → Pages**.
2. Set the deployment source to **GitHub Actions**.
3. Push to the `main` branch, or open **Actions → Dashboard on GitHub Pages → Run workflow**.
4. Select the desired run option when triggering the workflow.

The workflow creates the simulated trial, runs ingestion and quality control, performs reconciliation and statistical analysis, exports the results, and publishes the dashboard.

The full-run option uses larger simulation and validation settings.

The CI workflow also runs the automated test suite on repository updates.

## 10. Run Locally

### Requirements

- Python 3.11+
- PostgreSQL 14+ (or Docker)
- Node.js 20+ only if modifying and rebuilding the frontend

### Install

```bash
pip install -e ".[dashboard]"
```

Configure the database connection.

**Linux / macOS:**

```bash
export ARIDTRIAL_DSN=postgresql+psycopg2://USER:PASSWORD@localhost:5432/aridtrial
```

**Windows PowerShell:**

```powershell
$env:ARIDTRIAL_DSN="postgresql+psycopg2://USER:PASSWORD@localhost:5432/aridtrial"
```

### Run the complete demonstration

```bash
aridtrial demo --quick
```

This command simulates the trial, ingests the data, runs quality control and reconciliation, performs the analysis, and stores the results.

### Start the dashboard

```bash
aridtrial dashboard
```

Open:

`http://127.0.0.1:8000`

The scientific analysis dashboard is available under:

`http://127.0.0.1:8000/#/analysis/eda`

### Other commands

```bash
aridtrial analyze
aridtrial reconcile
aridtrial qc
aridtrial --help
```

For a full simulation, omit the `--quick` option when running the demo.

## 11. Run the Tests

Install development dependencies:

```bash
pip install -e ".[dev,dashboard]"
```

Run the test suite:

```bash
python -m pytest -q
```

Database integration tests require `ARIDTRIAL_TEST_DSN`. Without a configured test database, those tests are skipped.

## 12. Data Sources and Scope

The architecture supports external environmental and field-data integrations, including:

- KoboToolbox field submissions.
- Open-Meteo and Copernicus climate data.
- Sentinel-2 imagery and derived NDVI.
- Soil-moisture sensors and irrigation meters.

However, the public GitHub Actions demonstration is intentionally based on synthetic trial data. External downloads are not part of the public workflow and may require credentials or additional configuration.

The source-selection and provenance design keeps synthetic observations, external references, and operational measurements distinguishable.

## 13. Scientific and Operational Limitations

The following limitations are important when interpreting the project:

1. **Synthetic observations:** All reported trial outcomes are simulated. They do not establish real-world treatment effectiveness.
2. **Limited replication:** Six blocks provide limited precision for some outcomes, especially tree survival.
3. **Primary outcome:** Soil organic carbon at the final campaign and 0–15 cm depth is the pre-declared primary outcome. Other outcomes are secondary or exploratory; their results should be interpreted accordingly.
4. **AquaCrop scenarios:** Crop-water scenario outputs are model-based scenarios, not direct field measurements or evidence of a calibrated real-world deployment.
5. **Satellite resolution:** Sentinel-2 imagery at 10 m is coarse relative to individual 12 × 20 m plots. NDVI is therefore treated as block-level background information rather than independent plot-level ground truth.
6. **Audit and certification:** The project demonstrates data traceability and audit-oriented engineering practices. It is not a certified carbon MRV system and does not establish compliance with any specific carbon-credit methodology.
7. **Security:** The local dashboard is read-only but has no authentication. Do not expose a locally running instance directly to the public internet. The published GitHub Pages snapshot contains synthetic data only.

## 14. Repository Structure

```text
src/aridtrial/
  simulate/       Synthetic trial generation and fault injection
  preparation/    Source selection and data preparation
  pipeline/       Ingestion, provenance, audit and dataset versions
  qc/             Quality rules, quarantine and reconciliation
  analysis/       EDA, mixed models, survival and statistical inference
  models/         FAO-56 water balance and AquaCrop scenarios
  dashboard/      FastAPI backend and dashboard services

frontend/         React and Vite application
sql/              Database schemas, views and analytical tables
scripts/          Static dashboard export
docs/             Methodology, architecture and dashboard documentation
.github/workflows/
                  CI and GitHub Pages deployment
```

## 15. Technology Stack

| Area | Technologies |
|---|---|
| Programming | Python |
| Data Engineering | pandas, SQLAlchemy |
| Database | PostgreSQL, PostGIS |
| Statistical Analysis | statsmodels, SciPy, lifelines |
| Agronomic Modeling | FAO-56 and AquaCrop scenario workflows |
| API | FastAPI |
| Frontend | React, Vite, JavaScript |
| Automation | GitHub Actions |
| Testing | pytest |



**Zeinab Gouyandeh**

