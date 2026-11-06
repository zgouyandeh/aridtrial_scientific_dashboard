# aridtrial: can compost and biochar bring life back to desert soil?

[![CI](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/ci.yml)
[![Dashboard](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/pages.yml/badge.svg)](https://github.com/zgouyandeh/aridtrial_scientific_dashboard/actions/workflows/pages.yml)

### 👉 **[Open the live dashboard](https://<OWNER>.github.io/<REPO>/)** *(replace `<OWNER>` and `<REPO>`; one-time setup in [Run it on GitHub](#1-run-it-on-github-no-install))*

> **Please read first: every number in this project comes from simulated (synthetic) data.**
> The trial is a realistic *simulation* of a one-year desert field experiment near Al Wathba (Abu Dhabi).
> I planted known effects in it on purpose, so I can check that the analysis finds them. Nothing here is a scientific
> claim about real soil. The goal is to show a complete, auditable way of running and analysing such a trial.

![Dashboard preview](docs/images/dashboard-inference.png)

---

## The idea in one minute

Imagine a desert field divided into **24 small plots** (12 × 20 m), grouped into **6 blocks** of 4 plots.
Each block receives four treatments, one per plot, in random order:

| Code | Treatment |
|---|---|
| `CTRL` | Nothing added (the comparison group) |
| `COMP` | Compost |
| `BIOC` | Biochar (charcoal made from plant waste) |
| `COMP_BIOC` | Compost and biochar together |

For one year we measure: soil carbon, salinity, pH and density (four times), tree height, thickness and survival,
soil moisture sensors (every day), irrigation water meters, weather and satellite greenness (NDVI).

**The question:** do the amendments store carbon in the soil and help trees live and grow, and *how sure can we be*
with only 6 repeats?

**What this repository shows:** the whole chain a careful trial needs, from a field form on a phone to a final
statistical answer, with every step checkable by a third party (for example a carbon-credit auditor).

```mermaid
flowchart LR
  A[Field forms<br/>Kobo] --> B[(PostgreSQL<br/>raw → core → mart)]
  S[Sensors, meters,<br/>weather, satellite] --> B
  B --> C[Quality control<br/>flag, never edit]
  C --> D[Frozen dataset version<br/>+ audit hash chain]
  D --> E[Statistics<br/>mixed models, survival, power]
  E --> F[(results schema<br/>stored with provenance)]
  F --> G[React dashboards]
```

---

## What we found (in simple words)

*All of this is for the simulated trial. "Control" = plots with nothing added. "After one year", 0–15 cm depth.*

### 1. Both amendments raise soil carbon, and together they do the most

| Treatment | Extra soil carbon vs control | Relative to control |
|---|---|---|
| Compost | +0.14 percentage points | about +57 % |
| Biochar | +0.21 | about +88 % |
| Compost + biochar | **+0.35** | about **+143 %** |

Control plots stayed flat at about 0.25 %. All three differences are clear even after we correct for making several
comparisons at once. Deeper soil (15–30 cm) also gains, but less (+0.05 to +0.11).

*How we know it is not luck:* the simulator planted known effects, and the analysis recovered them to within about 0.03 (true values: +0.15, +0.20, +0.38).

### 2. Salt goes down

Soil salinity (ECe) dropped by about 0.95 (compost), 0.65 (biochar) and 1.46 dS/m (both), roughly 7–15 %, all clear after correction.
**pH did not change in any clear way.**

### 3. Soil gets lighter and holds more water

Bulk density fell with biochar (−0.07 g/cm³) and with both (−0.11). Compost alone was *not* clearly different.
Soil moisture sensors show visibly wetter soil in amended plots.

### 4. Trees grow taller and thicker

After a year trees were taller by 6.5 cm (compost), 10.4 cm (biochar) and 13.3 cm (both).
Stem thickness gained 0.8–1.4 mm. Two of these results (compost height, biochar thickness) were **borderline**:
they passed the main test but not a second, stricter check (see "two ways to check" below). Treat them as hints, not conclusions.

### 5. Trees survive better, especially with both amendments

Of 60 trees per treatment, these died: **control 18, compost 12, biochar 14, both 2.**
Compared with control, the risk of dying was about 38 % lower with compost (clear), 26 % lower with biochar (**not**
clear: the range includes "no effect"), and about 91 % lower with both (very clear, but the range is wide).
With only 6 plots per treatment, survival is the least certain result of the project.

### 6. Water use

Seasonal irrigation was about 606 mm for control and 667 mm for compost + biochar (+61 mm, clear). The other
treatments were not clearly different. A plausible reason is that bigger, healthier trees drink more. That is a
hypothesis, not something this project tested.

### 7. What the design can and cannot see

* **Blocking paid off.** About 85 % of the starting differences between plots were differences *between blocks*
  (the field is not uniform). Comparing plots inside a block removes that noise: it was worth roughly **6 times as many
  plots** in a design without blocks.
* **Smallest reliable difference** with 6 blocks: about **0.06–0.11 percentage points** of soil carbon, depending on how
  noisy the real soil turns out to be. Effects smaller than that can hide in the noise.
* **Survival needs more repeats.** With 6 blocks, the chance to detect a big survival benefit is only about **50 %**; about 10 blocks give roughly 78 %.

### 8. The data pipeline itself is trustworthy

* QC checked every record. Pass rates: soil 95.8 %, trees 99.9 %, sensors 99.6 %, water meters 99.98 %, weather 98.6 %,
  plot-level satellite greenness 89 %. Bad values are **flagged and left out of the analysis, never edited**: the original value stays on file.
* 54 cross-layer reconciliation checks (do counts match between raw, standard and analysis tables?): **0 failed**.
* The dashboard's "QC failures" counter is high on purpose: the simulator injects realistic faults (stuck sensors, spikes, register resets) to prove the rules catch them.

> The snapshot on GitHub Pages is a **quick run** (fewer simulations for power and validation), and a badge on the page says so.
> The treatment effects above do not depend on that. Trigger the workflow with "full run" for full-size simulations.

---

## Two ways to check an answer

Beyond the main statistical model, the dashboard shows a **second, assumption-light check**: we re-shuffle which plot got
which treatment *inside each block* (exactly how the real randomisation worked) thousands of times and ask how often chance alone looks as extreme as what we saw.
When both methods agree the result is solid. When they disagree, the dashboard says so, which is why two results above are called "borderline".

---

## The two dashboards

| Data quality | Scientific & agronomic analysis |
|---|---|
| ![](docs/images/dashboard-quality.png) | ![](docs/images/dashboard-eda.png) |
| What arrived, what passed QC, what was reconciled | The statistical evidence, in 9 tabs |

The analysis dashboard has nine tabs:

1. **Exploratory**: plain pictures of the data before any model (trajectories, box plots, correlations).
2. **Experimental design**: the field map, balance, how much the blocks helped.
3. **Treatment comparison**: how big is each effect, with its uncertainty range.
4. **Mixed-effects models**: the main statistical model, and checks that it fits.
5. **Statistical inference**: which differences are clear, and the second "re-shuffle" check.
6. **Power & sensitivity**: what this design can and cannot detect.
7. **Agronomy & environment**: irrigation, soil moisture, weather, crop-water modelling.
8. **Survival**: how long trees lived under each treatment.
9. **Validation**: proof the method works, using simulated truth we know.

Every page shows where its numbers came from: the run id, the frozen dataset version, the QC run and the random seed.

---

## Glossary for non-statisticians

| Term | Meaning |
|---|---|
| **Block** | A group of similar plots. Every treatment appears once per block, so comparisons are fair. |
| **Experimental unit = plot** | Treatments go on whole plots, so the plot, not the tree, is one independent repeat. Counting 10 trees as 10 repeats would make us look far more certain than we are. |
| **Mixed model** | A regression that knows plots sit inside blocks and are measured repeatedly over time. |
| **95 % interval** | A range that, over many repeats of the whole study, would contain the true value 95 % of the time. A wide range means little precision. |
| **Holm correction** | When we compare 3 treatments to control, chance alone could make one look special. Holm makes the bar stricter to compensate. |
| **p-value** | How surprising the data would be if the treatment did nothing. Small = surprising. It is not the chance that the treatment works. |
| **Power** | The chance a design detects an effect of a given size. We only report it *before* seeing results; "observed power" is misleading and deliberately omitted. |
| **Hazard ratio (HR)** | Risk of dying relative to control. 0.5 means half the risk; 1 means no difference. |
| **Synthetic data** | Computer-generated data that behave like real ones, with effects we chose. |

---

## Run it

### 1. Run it on GitHub (no install)

1. Put this project in a GitHub repository (branch `main`).
2. In the repository: **Settings → Pages → Build and deployment → Source: "GitHub Actions"**.
3. Push to `main` (or open **Actions → "Dashboard on GitHub Pages" → Run workflow**).
   The workflow starts a PostgreSQL database, builds the synthetic trial, runs quality control and the analysis, freezes the
   results into JSON and publishes the dashboard. It takes about 5 minutes (quick run).
4. Open `https://<OWNER>.github.io/<REPO>/`.

The published page is a **frozen snapshot** (same screens and numbers as the live app, no server needed). The
**Run workflow** button has a *full run* option for full-size simulations. The `CI` workflow runs the full test suite on every push.

### 2. Run it on your computer (live dashboard)

Requirements: Python 3.11+, PostgreSQL 14+ (or Docker, see `docker-compose.yml`), Node 20 only if you change the frontend.

```bash
pip install -e ".[dashboard]"
export ARIDTRIAL_DSN=postgresql+psycopg2://USER:PASSWORD@localhost:5432/aridtrial   # Windows: set ARIDTRIAL_DSN=...
aridtrial demo --quick        # simulate, ingest, QC, reconcile, analyse, store results (about 3-5 min)
aridtrial dashboard           # open http://127.0.0.1:8000  then #/analysis/eda for the second dashboard
```

Without `--quick`, the simulations are full size (slower, tighter validation numbers).
Other commands: `aridtrial analyze` (new analysis run on current data), `aridtrial reconcile`, `aridtrial qc`. Run `aridtrial --help`.

### 3. Run the tests

```bash
pip install -e ".[dev,dashboard]"
python -m pytest -q        # 88 tests; database tests need ARIDTRIAL_TEST_DSN, otherwise they are skipped
```

---

## Honest limits

* **Everything is simulated.** Real soil will be messier. The point is the method and the audit trail.
* **Six blocks is few.** Intervals are wide, and "not clearly different" never means "no effect".
* **One primary question.** Soil carbon at the end of the year (0–15 cm) is the pre-declared main outcome. All other results are secondary or exploratory and are not corrected across outcomes.
* **Crop-water (AquaCrop) tables are model scenarios, not measurements.**
* **Satellite NDVI** at 10 m is too coarse for 12 × 20 m plots: it is used only as block-level background.
* The dashboard has **no login**. Do not expose a live instance to the public internet. The GitHub Pages snapshot contains only synthetic data.
* The live weather and satellite downloads (Open-Meteo, Copernicus CDS, Sentinel-2) are implemented but not part of the GitHub workflow: they need keys, and the public demo is intentionally 100 % synthetic.

---

## What is in the repository

```
src/aridtrial/
  simulate/      synthetic trial with planted effects and injected faults
  pipeline/      ingestion, audit hash chain, dataset versions
  qc/            quality rules (flag, never edit) and reconciliation
  analysis/      mixed models, survival, power; scientific.py stores results with provenance
  models/        FAO-56 water balance, calibration, AquaCrop scenarios
  dashboard/     read-only FastAPI (+ analysis_service) serving the React build
frontend/        React + Vite source (SVG charts, no chart library)
sql/             schema, views, provenance, results tables
scripts/         export_static_site.py (GitHub Pages snapshot)
docs/            methodology, data engineering, dashboards (start with SCIENTIFIC_DASHBOARD.md)
.github/workflows/   ci.yml, pages.yml
```

More detail: [Scientific dashboard](docs/SCIENTIFIC_DASHBOARD.md) · [Methodology](docs/METHODOLOGY.md) ·
[Data quality & reconciliation](docs/DATA_QUALITY_AND_RECONCILIATION.md) · [Technical README (original)](docs/TECHNICAL_README.md)

## Built with

Python (pandas, statsmodels, SciPy, lifelines), PostgreSQL, FastAPI, React + Vite, GitHub Actions.

## Author

Zeinab Gouyandeh: senior data engineer, PhD in applied mathematics.
