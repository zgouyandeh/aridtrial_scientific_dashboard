# aridtrial — Learning Guide: from zero to mastery

How to learn this code step by step: where to start, what to do in each step, why that order, and how to know you have understood it.
Companion to `METHODOLOGY.md` (which explains *what and why scientifically*). This file explains **how to study the code**.

**Rules of the method**
1. Never read a file you have not first *run something from*. Run → read → change → break → fix.
2. Every step ends with a **checkpoint**: something you can do or explain without looking. If you cannot, repeat the step.
3. Keep a notebook `my_notes.md`. After each step write 5 lines in your own words. This is where learning happens.
4. Budget: about 4–5 weeks at 1–2 h/day. Steps 0–4 give you a working understanding; steps 5–9 give mastery.

**Windows setup (once)** — in `cmd`, in the project folder (the one with `pyproject.toml`):
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
aridtrial --help
```
The database steps (6 onwards) also need PostgreSQL: `docker compose up -d db`, or a local install. Steps 0–5 need no database.

---

## The map (memorise this picture first)

```
config.py ──────────────────────────────────────────────── all design numbers (single source of truth)
   │
design/      layout.py  power.py  pixel_purity.py          STEP 2   the experiment on paper
simulate/    weather.py field.py kobo.py export.py          STEP 3   fake-but-realistic data with KNOWN truth, plus faults
sources/     openmeteo.py cds.py copernicus.py              STEP 3b  REAL weather / satellite data
   │   files written to data/drops, data/kobo, data/truth
pipeline/    db.py kobo_client.py ingest.py audit.py        STEP 6   files -> PostgreSQL (raw -> core), audit chain
sql/         01_schema.sql 02_views.sql 03_postgis.sql      STEP 6   tables, triggers, QC-filtered views
qc/          rules.py reanalysis.py run.py                  STEP 7   flags bad data (never edits)
models/      fao56.py calibration.py aquacrop_model.py      STEP 4/8 crop-water physics and fitting
analysis/    stats.py run.py                                STEP 5/9 mixed models, survival, validation
report/      build.py       grafana/                        STEP 9   outputs
tests/                                                      every step: tests are executable documentation
```
Data flow: **design → simulate/fetch → files → database → QC → marts → models + statistics → report**.

---

## STEP 0 — Orientation (½ day)

**Do**
1. Read `README.md` (requirement map) and the top of `METHODOLOGY.md` §1.
2. Run `aridtrial demo --quick` (needs PostgreSQL; if you have none yet, skip and do it at Step 6). Open `outputs\report.html` in a browser and read the headings.
3. Run `pytest -q` and look at the list of test files.

**Why first:** you must see the finished product before the parts make sense, like seeing a car before studying the engine.

**Checkpoint:** draw the data-flow line above from memory and say in one sentence what each arrow carries.

---

## STEP 1 — Python and tools you need (3–5 days, skip what you know)

You have a PhD and data-engineering experience, so only the project-specific idioms:

| Idiom | Where it appears | Learn it by |
|---|---|---|
| `dataclass(frozen=True)` | `config.TrialConfig` | change `n_blocks`, rerun, see what changes |
| `numpy.random.default_rng(seed)` | everywhere in `simulate/`, `design/` | run `rng = default_rng(1); rng.normal(size=3)` twice; why identical? |
| pandas `groupby`, `pivot_table`, `merge`, `shift`, `rolling` | `qc/rules.py`, `models/calibration.py` | rewrite one rule in your own words |
| SQLAlchemy `text()`, parameters | `pipeline/db.py`, `audit.py` | run a `SELECT` through `db.read_sql` |
| statsmodels formula API (`smf.mixedlm`) | `design/power.py`, `analysis/stats.py` | fit a toy mixed model on 20 rows |
| pytest fixtures | `tests/conftest.py` | read `engine`, `built`, `loaded`: which depends on which? |
| pandera schemas | `qc/rules.py` | break a range on purpose, read the error |

**Checkpoint:** explain why `default_rng(seed + 202)` (not just `seed`) is used for different parts of the simulator (independent streams, yet fully reproducible).

---

## STEP 2 — The experiment on paper: `config.py`, `design/` (3–4 days)

**Why now:** every later file assumes you understand blocks, plots, treatments and the experimental unit.

**2.1 `config.py`** — read all of it (87 lines). Notice: nothing is computed, only declared. `CAMPAIGNS` maps T0…T4 to days; `MID_TRIAL`/`ENDPOINT` name the two reviews.

**2.2 `design/layout.py`**
- `make_layout`: converts the site lon/lat to UTM (EPSG:32640), then for each block shuffles the four treatments (`rng.permutation`) and places 12×20 m rectangles with 4 m gaps; blocks are 10 m apart.
- `check_rcbd`: asserts every block contains each treatment exactly once.
- `layout_hash` / `randomisation_record`: SHA-256 of the layout so you can later prove it was not changed.
- **Do:** run
  ```python
  from aridtrial.config import TrialConfig
  from aridtrial.design.layout import make_layout, check_rcbd, layout_hash
  L = make_layout(TrialConfig()); print(L.head(8)); print(check_rcbd(L)); print(layout_hash(L))
  ```
  Change the seed, see a different layout and a different hash.

**2.3 `design/power.py`** — read in this order:
1. `contrast_power`: noncentral-t power of one contrast. Derive the formula on paper first (`ncp = Δ/(σ√(2/b))`, `df=(b−1)(t−1)`).
2. `min_detectable_difference`: inverts it with `brentq`.
3. `_simulate_soc` + `fit_rm_model` + `power_soc_mixed`: Monte-Carlo with the *same* model as the analysis.
4. `power_survival`: why proportion + `var_weights` (see the bug note in METHODOLOGY §4.3).
- **Do:** compute power for b = 4, 6, 8 analytically, then with `power_soc_mixed(n_sim=100)`; compare.

**2.4 `design/pixel_purity.py`** — 70 lines of interval arithmetic: how many whole 10 m grid cells fit in a rectangle. Run `plot_purity(L)`; understand why the answer is 0 or 1.

**Checkpoint:** (a) why is the plot, not the tree, the experimental unit? (b) What happens to power if you analyse 240 trees as replicates? (c) Why do blocks run along the salinity gradient?
**Tests to read:** `tests/test_design_power.py`.

---

## STEP 3 — Where the data come from: `simulate/` and `sources/` (5–6 days)

This is the part you asked about most. Read it in the order of data creation.

**3.1 `simulate/weather.py`**
- `simulate_weather`: offline fallback generator (temperature = annual cosine + AR(1); radiation = extraterrestrial × clear-sky × clearness; etc.). Then ET₀ via FAO-56.
- `weather_from_reanalysis`: converts the **real** daily frame into the "truth" weather (fills ≤ 3-day gaps, refuses longer ones, computes ET₀).
- `station_from_reanalysis`: station = real weather + AR(1) temperature error + multiplicative RH/wind/radiation errors. **Why:** the fictional site has no station, but the QC needs something to compare with the reanalysis.

**3.2 `sources/openmeteo.py` and `sources/cds.py` (REAL weather)**
- `openmeteo.fetch_hourly`: builds the request, refuses dates inside the 5-day ERA5 lag, caches by parameter hash, stores URL + SHA-256 + time (provenance).
- `openmeteo.hourly_to_daily`: hourly → daily. Know every conversion: W/m² mean × 3600 → MJ/m²/day; 10 m → 2 m wind (eq. 47); incomplete days → NaN.
- `cds.deaccumulate`: ERA5-Land accumulations → hourly increments. This is the subtle function: the value stamped 00 UTC is the previous day's total, 01 UTC starts a new accumulation.
- **Do (you have internet):** `aridtrial fetch-weather` and `aridtrial fetch-weather --source cds`; compare the two CSVs day by day. Differences in rain/radiation point to converter errors.

**3.3 `sources/copernicus.py` and `simulate/ndvi_background.py` (REAL NDVI)**
- `stats_request` builds the Sentinel Hub Statistical API request: a polygon in EPSG:32640, daily aggregation, an *evalscript* computing NDVI and a mask from the Scene Classification Layer (keep classes 4, 5, 7).
- `parse_statistics`: JSON → rows (`n_valid = sampleCount − noDataCount`).
- `ndvi_background.load_background`: real block series → daily background per block (interpolated).
- **Do:** `aridtrial fetch-ndvi --lon … --lat … --print-zones`, inspect the polygons in the Copernicus Browser, then fetch. Plot `ndvi_mean` per block in pandas.

**3.4 `simulate/field.py`** — the longest file; read in sections, run each in a notebook:
1. `Truth`: **the true effects**. This is the answer key for the whole project.
2. Random effects per plot (block, plot, salinity gradient, frailty).
3. Soil loop: the saturating curve `1−exp(−(d−7)/τ)` and noise → `soil` table with `*_true` and observed columns.
4. Trees: exponential death times with hazard ratios and frailty; growth with salinity penalty; observations only at campaigns.
5. Water loop (per plot, per day): FAO-56 balance with time-varying field capacity → irrigation volumes (meters) and hourly soil moisture (sensors) with gain/offset/noise.
6. NDVI: background + `0.10·canopy` + noise (+ cloud bias).
- **Do:** call `simulate_field(cfg, layout, weather)`; plot true vs observed SOC for one plot over the five campaigns; change `soc_effect_end["COMP_BIOC"]` to 0 and see the effect vanish.

**3.5 `simulate/kobo.py`, `simulate/fault_injection.py`, `simulate/artifacts.py`, and `simulate/builder.py`**
- `kobo.py`: builds XLSForms and API-v2-shaped JSON (strings, repeat groups, `rootUuid` vs `instanceID`) and injects data-entry faults. **Purpose:** exercise the real client code on realistic mess.
- `builder.py::build`: coordinates the generation steps; it contains no scientific generation formulas.
- `fault_injection.py`: adds spikes, flatlines, gaps, duplicates, meter resets, missing weather values, and (when real reanalysis is supplied) station drift and temperature offsets.
- `artifacts.py`: writes `data/drops`, `data/kobo`, `data/xlsform`, and `data/truth`. Every injected fault is logged in `truth/injected_faults.csv` — the answer key for QC.
- **Do:** run `aridtrial simulate`, open each CSV in `data/drops`, find an injected fault by hand (e.g. a flat run in `sensors_hourly.csv`).

**Checkpoint:** (a) which numbers in the output come from real APIs and which are synthetic (answer with a table)? (b) Why does the `truth/` folder never get loaded into the database? (c) Explain interval-censoring of tree deaths.
**Tests to read:** `tests/test_external.py`, `tests/test_real_sources.py`.

---

## STEP 4 — The physics: `models/fao56.py` (3–4 days)

**Why before the pipeline:** it is pure math, no infrastructure, and the simulator, the pipeline and the calibration all call it.

- Read `eto_pm_daily` next to FAO-56 (Allen et al. 1998), chapters 2–4, matching each line to its equation number (they are in the comments).
- **Do:** reproduce FAO-56 Example 18 by hand and with `tests/test_fao56.py`. Then compute ET₀ for one real day from your `reanalysis_daily.csv`.
- `RootZone`, `stress_coefficient`, `water_balance`: write the daily loop on paper (`Dr`, `TAW`, `RAW`, `Ks`, trigger at `MAD·TAW`, refill, efficiency 0.9). Then run it for 30 days with constant ET₀ = 8 mm and see irrigation events.
- `kc_young_tree`: why a *plot-scale* Kc.

**Checkpoint:** from `T, RH, wind, Rs` compute ET₀ without looking at code; explain what changes when wind doubles.

---

## STEP 5 — Statistics: `analysis/stats.py` (6–8 days; the heart of the job)

Read in this order, always with the model equation (METHODOLOGY §10) next to the code.

1. `fit_rm_model` (in `design/power.py`): `y ~ trt*time + (1|block) + (1|plot)`, REML.
2. `fit_response`: builds contrast vectors `L`; `est = Lβ`, `se = √(LΣLᵀ)`; t-test with df = 15; Holm within campaign; variance components.
3. `baseline_balance`, `baseline_variance`: T0 randomisation check and re-estimation of variance.
4. `tree_survival_frame`, `cox_survival`: interval-censored event time; Cox stratified by block, clustered SE.
5. `endpoint_survival_glm`: quasi-binomial cross-check.
6. `irrigation_by_plot`, `rcbd_anova`.
7. `validate_coverage`: the closed-loop study.

**Do (essential exercises)**
- Fit the same model in R (`lme4::lmer` + `lmerTest` with Kenward–Roger) on the exported soil CSV and compare SEs and p-values with Python's. This teaches you exactly where the shortcut (Wald-t, df=15) differs.
- Remove the plot random effect and watch the SE shrink: that is pseudo-replication.
- Run `validate_coverage(cfg, n_rep=30)`; check coverage near 95 %.

**Checkpoint:** explain (a) why REML, (b) why Holm and not Bonferroni in the analysis, (c) what the PH assumption is and how you would test it, (d) why mortality and growth need a joint model in principle.
**Tests to read:** `tests/test_analysis_models.py`.

---

## External data sources and preparation

- Weather: `sources/openmeteo.py` and `sources/cds.py` retrieve hourly ERA5-Land and convert it to daily FAO-56 inputs. Open-Meteo is the lightweight point-query route; CDS is the direct Copernicus route. These are two access paths to the same reanalysis family, not independent weather observations.
- Satellite: `sources/copernicus.py` requests Sentinel-2 L2A B04/B08 NDVI statistics at 10 m over block polygons in EPSG:32640. The output is block-level context, not plot-level measurement.
- Preparation: `preparation/manifest.py` writes `input_manifest.json`, identifying real source files, hashes, retrieval provenance and synthetic outputs derived from them.
- Combination policy: real ERA5-Land forces the simulator and is retained as a QC reference; the on-site station series remains synthetic. Real Sentinel-2 block NDVI supplies background context while the plot canopy contribution remains synthetic. Real Kobo measurements are prepared in a separate output area and must not be mixed with synthetic submissions for the same business keys.

## STEP 6 — Database and pipeline: `sql/`, `pipeline/` (5–6 days)

1. Start PostgreSQL. Read `sql/01_schema.sql` top to bottom: schemas `raw`, `core`, `audit`, `mart`; the append-only triggers (`audit.deny_mutation`); constraints and primary keys.
2. `pipeline/db.py`: engine, `apply_schema`, `reset_database`, `copy_df` (fast COPY), `read_sql`.
3. `sources/kobo.py`: downloads real API v2 submissions page by page and stores each raw response under immutable timestamped snapshots in `data/external/kobo/<asset_uid>/<retrieval_id>/pages/`, plus a combined API-shaped payload and `provenance.json` (retrieval time, asset/form IDs, page URLs, counts, and SHA-256 hashes). Run `aridtrial fetch-kobo --soil-asset <ASSET_UID> --tree-asset <ASSET_UID>`; provide `KOBOTOOLBOX_TOKEN` through the environment.
4. `preparation/kobo.py`: reads downloaded payloads, applies the existing soil/tree normalisers and revision policy, and writes `soil_samples_real.csv`, `tree_observations_real.csv`, and a provenance manifest under `data/prepared/kobo`. Run `aridtrial prepare-kobo --soil-payload <PATH> --tree-payload <PATH>`. Real observations remain separate from synthetic Kobo files and are not loaded into PostgreSQL by this command.
5. `pipeline/kobo_client.py`: `normalise_soil/trees` (strings → typed), `resolve_current` (latest instance per `rootUuid`; earlier ones become superseded with a reason). The old `KoboClient` import remains as a compatibility re-export.
6. `pipeline/ingest.py`: `ingest_kobo` → `raw.kobo_submission`; `build_core_from_raw` → typed core tables; `ingest_drop` (hash → skip duplicates → required columns → quarantine unknown plots → type cast → COPY → log).
7. `pipeline/audit.py`: `append` (hash chain with advisory lock), `verify_chain`, `create_dataset_version`, `verify_dataset_version`.
8. `sql/02_views.sql`: the marts — each is `core` minus rows with a `fail` flag in `audit.qc_latest`. This is the mechanism by which QC "flags, never edits".

**Do**
- `aridtrial demo --quick`, then in `psql` run `SELECT count(*) FROM core.soil_sample;` and `... FROM mart.soil_clean;` — explain the difference.
- Try `UPDATE raw.file_drop SET n_rows = 0;` and read the error (trigger).
- Run `aridtrial ingest` twice; confirm nothing is duplicated (idempotency).
- Change one value in `core.soil_sample` (as superuser, after disabling nothing — core has no trigger) and run `verify_dataset_version`; see it fail; restore.

**Checkpoint:** (a) why three layers? (b) what does the hash chain prove and what does it *not* prove? (c) why quarantine unknown plot IDs instead of dropping them?
**Tests to read:** `tests/test_pipeline.py` (especially tamper tests).

---

## STEP 7 — Data quality: `qc/` (4 days)

- `rules.py`: pandera schemas for ranges (declarative); `check_soil` (future date, depth layer, GPS distance to plot, completeness); `check_trees` (resurrection, growth plausibility); `check_sensors` (spike, flatline, gap); `check_meters` (register reset); `check_ndvi` (cloud, temporal jump); `check_weather`.
- `reanalysis.py`: residuals station − reanalysis; robust z with MAD; 15-day rolling median drift band; `compare_weather`.
- `run.py`: loads tables, runs rules, writes `audit.qc_run/qc_result`, and `evaluate_against_injected` scores recall.

**Do:** add one new rule (e.g. "soil SOC decreasing > 0.3 between campaigns" → warn), write a test with a hand-made DataFrame, run QC, see the new rule id in `audit.qc_latest`.
**Checkpoint:** why is a spike rule "both neighbours in the same direction"? Why is reanalysis disagreement only a *warn*?

---

## STEP 8 — Fitting models to data: `models/calibration.py`, `aquacrop_model.py` (4–5 days)

- `simulate_plot`: the same water balance in a fast loop, parameters `(θ_FC, Kc_min, Kc_max)`.
- `canopy_from_ndvi`: end-member scaling from the 5th/95th percentile.
- `_residuals`: three residual types and their σ; why the cumulative one matters.
- `calibrate_plot`: `differential_evolution` and why not gradient descent (non-smooth trigger).
- `calibrate_all`, `evaluate`: calibration vs hold-out.
- `aquacrop_model.py`: soil from calibrated θ_FC (12 compartments!), two irrigation strategies, bootstrap over plots.

**Do:** calibrate one plot; plot observed vs simulated VWC on the hold-out days; run it on noise-free simulated data and confirm parameter recovery (`tests/test_analysis_models.py`).
**Checkpoint:** explain identifiability: which parameters does soil moisture pin down, and which only the meters?

---

## STEP 9 — Orchestration and outputs: `analysis/run.py`, `report/build.py`, `cli.py`, `grafana/` (3 days)

- `analysis/run.py::run_analysis`: reads marts, runs everything in order, returns a dict `R`.
- `report/build.py`: figures (direct labels, validated palette) and HTML assembly.
- `cli.py`: subcommands; `_external_inputs` resolves `--real`.
- `grafana/dashboards/trial_overview.json`: panels = SQL on `mart.v_*` views; the test executes each query.

**Checkpoint:** run `aridtrial demo --real` end to end and explain, for any number in the report, which table it comes from and which function computed it.

---

## STEP 10 — Mastery: change, break, extend (ongoing)

**Break it (understand failure modes)**
1. Set the plot random effect to zero in the simulator and in the model; watch the type-I error in `validate_coverage`.
2. Feed `fit_response` a dataset with 30 % of T4 missing; compare estimates.
3. Corrupt one CDS accumulation (shift by one hour); see the daily rain error.
4. Make the calibration use only VWC residuals; watch irrigation totals drift.

**Extend (real-job skills)**
1. Replace Wald-t by Kenward–Roger (call R via `rpy2`, or implement Satterthwaite).
2. Add soil-sensor calibration against gravimetric samples.
3. Add a second, joint growth–survival model.
4. Run `sql/03_postgis.sql` on a PostGIS image and add the spatial QC in SQL.
5. Run the Grafana stack (`docker compose up`) and add a QC panel.
6. Replace assumed variances with T0 estimates from a real baseline sample.

**Teach it:** explain Steps 2→9 aloud in 20 minutes without notes. If you can, you can defend it in an interview.

---

## Weekly plan

| Week | Steps | Output you keep |
|---|---|---|
| 1 | 0, 1, 2 | notes + power table you derived by hand |
| 2 | 3, 4 | fetched real weather/NDVI + FAO-56 by hand |
| 3 | 5 | R vs Python mixed-model comparison |
| 4 | 6, 7 | a new QC rule + a tamper experiment |
| 5 | 8, 9, 10 | calibration notebook + end-to-end `demo --real` + 20-minute talk |

## How to read any file (repeat for each)

1. Read the module docstring (it states purpose and caveats).
2. List the function names only; guess the pipeline.
3. Read the test for the function *first* — it shows the intended inputs and outputs.
4. Run the function on a tiny example in a notebook.
5. Read the body line by line; for each line write *what* and *why*.
6. Change one constant, predict the effect, run, compare.

## Honest pointers

- This is a demonstration project on synthetic data (with real weather/NDVI backgrounds when you fetch them). It proves method and engineering, not agronomy.
- Four places are explicitly simplified and worth your own improvement: Wald-t instead of Kenward–Roger, survivor-conditional growth, θ_WP pedotransfer sharing the simulator's form, and AquaCrop being a scenario model.
- Live API clients (Open-Meteo, CDS, Copernicus) were tested only against mocked responses; your first live run is part of your learning.
