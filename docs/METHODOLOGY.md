# aridtrial — Methodology

*What was simulated, which methods were used, what every module computes, and why.*
Written for a reader with a strong mathematical background who needs to **defend every choice** in an interview or a review.

> **Read this first.** All trial data are synthetic. The simulator knows the true treatment effects, so the project is a
> **closed-loop test bed**: it can show that the *pipeline* and the *statistics* are correct (unbiased, calibrated intervals,
> faults caught). It cannot show anything about real desert agronomy. Every effect size, variance component and fault rate is an
> **assumption of mine**, listed in §2 and §14 — not a finding.

---

## Contents

1. Logic of the whole project
2. The simulation: factors considered, equations, parameter values
3. Experimental design (RCBD, randomisation, geometry)
4. Power analysis
5. Fault injection (what can go wrong in the field)
6. Ingestion: Kobo, drops, raw → core → mart
7. Audit trail (hash chain, dataset versions)
8. Data-quality rules
9. Crop-water models: FAO-56 ET₀, water balance, calibration, AquaCrop
10. Statistical analysis
11. Closed-loop validation
12. Real external data: ERA5-Land and Sentinel-2
13. Dashboards and report
14. Limitations and honest caveats
15. What to learn (reading list) and how to present this project
16. File-by-file reference
17. Glossary

---

## 1. Logic of the whole project

The job posting asks for one person to own the data path from field to final report. The project mirrors that path:

```
design (layout, power)  ->  field data (Kobo forms, sensors, meters, satellite, weather)
   ->  database (raw -> core -> mart, audit trail)  ->  QC (flags, never edits)
   ->  models (FAO-56 water balance, calibration, AquaCrop)  ->  statistics (mixed models, survival)
   ->  dashboards + report  ->  verification-ready dataset version
```

Because the data are simulated with **known truth**, each stage can be checked against something:

| Stage | Checked against |
|---|---|
| Layout | structural RCBD test; seed + SHA-256 reproducibility |
| Power | analytic formula **and** Monte-Carlo with the production model agree |
| QC | injected faults (recall), clean data (false positives) |
| Pipeline | row counts, idempotency, quarantine, edit handling, tamper tests |
| Calibration | noise-free parameter recovery; hold-out period |
| Statistics | true effects (bias, RMSE, 95 % CI coverage over 100 simulated trials) |

**Design principle: one source of truth.** `config.py` holds all design numbers; the same mixed-model function
(`design.power.fit_rm_model`) is used for power planning *and* analysis; the same FAO-56 code produces ET₀ for the simulator, the
pipeline and the models.

---

## 2. The simulation — factors considered

Code: `simulate/weather.py`, `simulate/field.py`, `simulate/kobo.py`, `simulate/fault_injection.py`, `simulate/artifacts.py`, and `simulate/builder.py`.
The scientific generators use seeded `numpy.random.default_rng` streams. A few output metadata fields (for example UUIDs created with `uuid.uuid4()` and XLSX workbook metadata) are intentionally not byte-for-byte reproducible.

### 2.1 Why simulate with ground truth

A simulator whose true effects are known lets you ask: *"if the effect of compost+biochar on SOC really is 0.379 %-points,
does my analysis recover it, with a 95 % interval that contains it 95 % of the time?"* Real data cannot answer that.

### 2.2 Weather — real ERA5-Land from the API (primary), synthetic generator (offline fallback only)

**Primary method: real weather from an API.** The trial's weather forcing is real hourly ERA5-Land reanalysis for the site (24.24° N, 54.60° E)
and the trial period (2025-10-01 … 2026-09-30), retrieved with `aridtrial fetch-weather` from **Open-Meteo** (default) or the **Copernicus CDS**
(`--source cds`) and used with `aridtrial demo --real` (details and unit conversions: §12.1, §12.1b). From the API data the pipeline derives
Tmax/Tmin, RHmax/RHmin, 2 m wind, solar radiation (MJ m⁻² d⁻¹) and rain, and computes ET₀ with our own FAO-56 Penman–Monteith (§9.1).

Only the *on-site station* is still simulated (the fictional site has none): it is the real weather plus an assumed station discrepancy and injected
faults, so the station-vs-reanalysis QC has something to test (§12.1).

**Fallback only: the synthetic generator (`simulate_weather`).** It exists so the project, its 52 tests and the Monte-Carlo validation run
without internet or accounts. It is *not* the intended configuration and no result should be presented as based on it when real data are available.
Its structure, for completeness:

| Variable | Model | Why |
|---|---|---|
| Mean temperature | `27.5 + 8.5·cos(2π(doy−208)/365)` + AR(1) anomaly (φ = 0.7, σ = 1.1 °C) | annual cycle, day-to-day persistence |
| Diurnal range | `11 + 1·cos(…) + N(0, 0.8)`; Tmax/Tmin = Tmean ± DTR/2 | large diurnal range in arid sites |
| RHmin, RHmax | seasonal + noise, clipped; RHmax = RHmin + N(42, 6) | dry afternoons, humid nights |
| Wind (2 m) | lognormal, median 2.6 m/s, σ_log = 0.28, clipped 0.8–8 | positive, right-skewed |
| Solar radiation | `Rs = Ra·(0.75 + 2·10⁻⁵ z)·clearness`, clearness ~ N(0.93, 0.05), 5 % dust days × U(0.55, 0.85) | FAO-56 extraterrestrial radiation × clear-sky fraction |
| Rain | winter-biased Bernoulli (p = 0.02 / 0.06), Gamma(0.9, 7 mm) | rare, intermittent |
| ET₀ | FAO-56 Penman–Monteith | same function everywhere |

The closed-loop coverage study (§11) deliberately keeps using the synthetic generator: it repeats the whole trial 100 times and must not hit an API 100 times. It runs the simulator in `fast` mode, which skips the water-balance, sensor and NDVI parts, so weather does not enter the soil-chemistry responses it validates.

### 2.3 Plot-level random effects (`simulate_field`)

Each plot gets persistent properties that make plots differ even within a treatment — this is what makes the statistics non-trivial:

| Property | Distribution | Meaning |
|---|---|---|
| `u_soc` | block effect N(0, 0.040) + plot effect N(0, 0.030) | spatial structure of baseline SOC (block = gradient; plot = local) |
| `ec0` | `5.5 + 1.2·(block−1) + N(0, 0.6)` dS/m | **salinity gradient** across blocks — the reason blocks run along the gradient |
| `ph0` | 8.0 + N(0, 0.12) | alkaline desert soil |
| `bd0` | 1.55 + N(0, 0.03) g/cm³ | sandy soil bulk density |
| `fc_plot` | 0.130 + N(0, 0.004) m³/m³ | baseline field capacity |
| `u_growth` | N(0, 0.08) | plot growth vigour (log scale) |
| `frailty` | LogNormal(0, 0.30) | plot-level mortality hazard multiplier (over-dispersion) |

### 2.4 Soil sampling (5 campaigns × 2 layers × 24 plots)

Campaigns: T0 (day 0, baseline), T1 (91), T2 (182, **mid-trial**), T3 (273), T4 (364, **endpoint**). Amendment incorporated on day 7.
Layers 0–15 cm and 15–30 cm.

Response to amendment is a **saturating exponential**, `f(d, τ) = 1 − exp(−max(d−7, 0)/τ)`:

```
SOC_true = 0.25·(0.7 if deep) + u_soc·(0.6 if deep) + effect_end[trt] · f(d, 60) · (0.30 if deep)
EC_true  = ec0·(1.05 if deep) + 0.8·d/364·(0.5 if deep) + effect_end_EC[trt] · f(d, 90) · (0.5 if deep)
pH_true  = ph0 + ph_effect[trt]·f(d, 60)
BD_true  = bd0 + (0.03 if deep) + bd_effect[trt]·f(d, 60)·(0.4 if deep)
observed = true + measurement noise   (SOC 0.020, EC 0.30, pH 0.08, BD 0.03)
```

Assumed true endpoint effects (differences from control):

| Treatment | SOC (%-pts) | ECe (dS/m) | growth (relative) | mortality hazard ratio |
|---|---|---|---|---|
| CTRL | 0 | 0 (+0.8 drift/yr) | 0 | 1.00 |
| COMP | +0.15 | −1.0 | +0.18 | 0.55 |
| BIOC | +0.20 | −0.3 | +0.12 | 0.65 |
| COMP_BIOC | +0.38 | −1.4 | +0.35 | 0.30 |

Because `f(364, 60) = 0.9974`, the *true* COMP_BIOC SOC contrast at T4 is `0.38 × 0.9974 = 0.379` — the number the analysis is compared with.

*Why saturating?* Amendment effects on soil carbon build up and level off; a linear trend would be implausible over a year.
*Why deep-layer attenuation?* Amendments are incorporated in the top layer; deeper layers respond less.
*Why a control drift in EC?* Irrigation water brings salt; the control arm should get saltier, which makes the amendment effect a *difference of trends*, not a level.

### 2.5 Trees: survival and growth (10 trees per plot)

* **Survival:** each tree has an exponential death time, rate
  `λ = λ₀ · HR[trt] · frailty · exp(0.12·(ec0 − 8))`, with `λ₀ = −ln(0.78)/364` (control survival 78 % at one year at the reference salinity).
  Salinity raises the hazard (log-hazard +0.12 per dS/m above 8); frailty makes plots over-dispersed relative to a binomial.
  Deaths are only *observed* at campaigns (**interval-censoring**).
* **Growth:** `height(d) = h₀ + g_h·s(d)`, `s(d) = d/364 − 0.04·sin(2πd/364)` (near-linear with a mild seasonal wobble),
  `g_h = 38·(1 + growth_effect)·exp(−0.035(ec0 − 8.5))·exp(u_growth + N(0, 0.15))`. Salinity depresses growth; amendments raise it.
  Observation noise: height ±1 cm, diameter ±0.4 mm. Dead trees have no measurements.
* **Canopy index** (used by the water models and NDVI): mean over *planted* trees of `clip(height/300, 0, 1) · alive`.

### 2.6 Water use, soil-moisture sensors and meters

Per plot, **day by day**, the FAO-56 single-Kc root-zone balance is run with the plot's *true, time-varying* field capacity:

```
θ_FC(d) = fc_plot + fc_effect[trt]·(1 − exp(−d/30))        (amendments raise water retention)
θ_WP    = 0.05 + 0.4·(θ_FC − 0.13)                          (pedotransfer, simulator form)
TAW     = 1000·(θ_FC − θ_WP)·Z_r ,  Z_r = 0.6 m
Kc      = 0.15 + 0.40·canopy_index                          (plot-scale Kc for sparse young trees)
Drip controller: irrigate the next morning when depletion ≥ 0.2·TAW (MAD = 20 %), refill to FC; efficiency 0.9.
```

* **Meters:** `volume_l = gross depth (mm) × plot area (m²)`, cumulative register; litres rounded.
* **Soil-moisture sensor** (hourly, 24 plots × 8760 h): depletion is spread through the day with a half-sine ET profile
  (03–14 h), `θ = θ_FC − Dr/(1000·Z_r)`, then a per-plot **gain** N(1, 0.02), **offset** N(0, 0.004) and white noise 0.002.
  The gain/offset is a deliberate *calibration error* so that a perfect model could not fit perfectly.
* **Soil temperature:** air-temperature-driven diurnal wave + noise.

### 2.7 Satellite NDVI — real Sentinel-2 (primary), synthetic tree signal on top

**Primary method: real Sentinel-2 L2A NDVI from the Copernicus Data Space API.** `aridtrial fetch-ndvi --lon <lon> --lat <lat>` (needs `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET`)
retrieves cloud/shadow-masked NDVI per block zone for a bare-desert area near Al Wathba (zones printed with `--print-zones` so you can inspect them in the Copernicus Browser first).
`aridtrial demo --real` then uses that series as the plot background: `NDVI(plot, day) = real block background + 0.10·canopy + N(0, 0.007)`.

**What is real and what is not.** Real: soil/background brightness, seasonality, cloud-masking behaviour, noise level. Synthetic: the tree-canopy term (`0.10·canopy`), because
Sentinel-2 cannot resolve 12 × 20 m plots (§12.2: ≤ 1 whole 10 m pixel per plot, none after an edge buffer) and a real image of this fictional experiment does not exist.
A warning is printed if the sampled area's median NDVI ≥ 0.15 (not bare desert).

**Fallback only (no credentials / offline):** `NDVI = 0.085 + 0.10·canopy + N(0, 0.007)` at a 5-day revisit from day 2, cloud fraction ~ Beta(0.3, 4) with 4 % heavily cloudy dates, NDVI biased down 0.02–0.06 when cloud > 0.3.
This is what a run without `--real` (or without the fetched NDVI file) uses.

---

## 3. Experimental design (`design/layout.py`)

* **RCBD** (randomised complete block design): 6 blocks × 4 treatments = 24 plots, each plot 12 × 20 m (240 m²), 10 trees/plot (2 rows × 5).
* **Why blocks:** the site has a known salinity gradient; blocking removes between-block variation from the treatment error.
  Blocks run along the gradient; within each block all four treatments appear once, randomly ordered.
* **Randomisation:** `rng.permutation(treatments)` per block, seed recorded; layout hash `SHA-256(plot_id, block, position, treatment, geometry)`
  stored in the randomisation record and the audit log — you can *prove* the layout was not altered after randomisation.
* **Experimental unit = the plot.** Trees, soil layers and repeated visits are subsamples / repeated measures. Treating the 240 trees as replicates
  (pseudo-replication) is the classic error; everything downstream is built to avoid it.
* **Geometry:** UTM zone 40N (EPSG:32640), 4 m gaps between plots, 10 m between blocks; WKT polygons per plot, lon/lat of centroids via `pyproj`.
* **Controls:** one untreated plot per block (CTRL) — the baseline against which every contrast is measured.

---

## 4. Power analysis (`design/power.py`)

Question: *with 6 blocks, which differences can the trial detect?*

### 4.1 Analytic layer (single time point)

For one treatment-vs-control contrast in an RCBD with `b` blocks and `t = 4` treatments:

```
df       = (b−1)(t−1)                       (error df; 15 for b = 6)
σ        = sqrt(σ_plot² + σ_resid²)          (SD of a plot observation after removing blocks)
ncp      = Δ / (σ·sqrt(2/b))                 (noncentrality parameter)
power    = P(|T'| > t_crit(α/m, df)),  T' ~ noncentral t(df, ncp);   m = 3 comparisons, Bonferroni
```

`min_detectable_difference` inverts this with `brentq`; `blocks_needed` searches `b`. The omnibus F-test power uses `ncf` with
`λ = b·Σ(τᵢ − τ̄)²/σ²`. Numerical pitfall handled: `nct.cdf` returns NaN for very large `ncp` → lower tail replaced by 0 (true value ≈ 0).

### 4.2 Monte-Carlo layer (production model)

Simulate whole SOC trials (random block, plot and residual effects; five campaigns; saturating treatment effect), fit
`y ~ treatment * campaign + (1|block) + (1|plot)` by REML, and count rejections of the endpoint contrasts (Wald-t with RCBD df, Bonferroni).
Agreement between 4.1 and 4.2 validates both. Simulations with singular fits are excluded from the denominator and counted (`n_sim_ok`).

### 4.3 Survival power

Plot-level counts of survivors (10 trees) with logit-scale plot over-dispersion (SD 0.5) and block SD 0.3. Analysis: quasi-binomial GLM,
treatment vs control. With 6 blocks, power to detect 78 % → 93 % survival is only ≈ 47 % (≈ 14 blocks for ≈ 88 %): **survival is the weak endpoint**
because only 10 trees per plot and few plots — an important design message.

**Bug found and fixed here:** a two-column binomial `endog` with `scale="X2"` ignored the trial counts and gave ≈ 50 % type-I error; the fix is a
proportion response with `var_weights = n` (type-I error 0.065).

### 4.4 Baseline update

After T0 sampling, `baseline_variance` re-estimates σ_block, σ_resid from real baseline data, so the design check is revisited with observed variance, as a real study would.

---

## 5. Fault injection — what goes wrong in real trials

Faults are inserted *after* the clean truth is generated, and recorded in `truth/injected_faults.csv`, so QC can be scored.

| Source | Faults |
|---|---|
| Kobo soil forms | pH decimal slip (7.8 → 78), EC unit error (dS/m ↔ mS/m), missing SOC, unknown plot ID, future date, bulk-density outlier, duplicate submission, **edited** submission (same `rootUuid`, new `instanceID`) |
| Kobo tree forms | unknown plot, height outlier |
| Soil sensors | isolated spikes (0.3 % of readings; half physically impossible), flatline (stuck) 60–140 h in 3 plots, logger gaps 18–60 h in 3 plots, 40 duplicated timestamps |
| Water meters | register reset after meter replacement (2 plots) |
| Station weather | missing radiation/wind days, swapped RHmax/RHmin; *(with reanalysis)* slow pyranometer drift (×0.82 from day 200) and a 5-day Tmax offset (+6 °C) |
| Satellite NDVI | cloud contamination biases |

The faults are chosen because each needs a **different kind of rule** (range, relational, temporal, completeness, versioning) — they test the breadth of the QC design.

---

## 6. Ingestion (`pipeline/`)

### 6.1 Layers

| Layer | Content | Property |
|---|---|---|
| `raw` | Kobo payloads exactly as received; file-drop registry (SHA-256 of each file) | **append-only** (UPDATE/DELETE blocked by triggers) |
| `core` | typed, de-duplicated, versioned tables | rebuildable from raw; edits supersede, never delete |
| `audit` | hash-chained `ingest_log`, `qc_run`, `qc_result`, `dataset_version`, quarantine | tamper-evident |
| `mart` | QC-filtered views for analysis and Grafana | what any number in the report is computed from |

### 6.2 KoboToolbox API v2 quirks (`kobo_client.py`, `simulate/kobo.py`)

All values arrive as **strings**; repeat groups become lists of dicts with `group/field` keys; metadata uses `_id`, `_uuid`, `_submission_time`;
an **edit** keeps `meta/rootUuid` but gets a new `meta/instanceID`. `normalise_soil/trees` flatten and cast; `resolve_current` selects the latest instance per `rootUuid`
(earlier ones become `superseded`, labelled with a reason). The simulator reproduces these quirks so the real client logic is exercised.

### 6.3 File drops (`ingest_drop`)

Hash the file → skip if seen (**idempotent**) → check required columns (reject + audit entry if missing) → quarantine unknown plot IDs → type-cast
(`Int64` for nullable integers, `COPY` for bulk load) → record in `raw.file_drop` → append to audit chain. Meters: daily volume = `diff(register)`; a negative diff (reset) becomes NULL, never a negative volume.
Weather: ET₀ is **computed in the pipeline** (the station delivers raw variables, not ET₀).

---

## 7. Audit trail (`pipeline/audit.py`)

* **Hash chain:** `entry_hash = SHA-256(prev_hash ‖ canonical_json(entry))`, genesis = 64 zeros. Changing, deleting or re-ordering any past entry changes every later hash;
  `verify_chain` recomputes the chain. An advisory lock serialises writers so the chain cannot fork.
* **Dataset version:** freezes the QC-filtered marts — per-table row counts and SHA-256 of the canonical CSV, QC run id, chain head — into an id
  `dv-YYYYMMDD-<hash10>`. `verify_dataset_version` re-hashes; changing *one* soil value makes verification fail (tested, then rolled back).
* **Scope honesty:** this is *tamper-evident*, not a replacement for a registry's (Verra / Puro.Earth) own verification rules, which I did not review.
  Two defence lines: DB triggers (prevention) + chain (detection by an external verifier offline).

---

## 8. Data-quality rules (`qc/rules.py`, `qc/reanalysis.py`)

**Principle:** QC **flags, never edits**. Severity: `fail` (excluded from marts), `warn` (kept, reported), `info`.
Range rules use **pandera** schemas (declarative); temporal/relational rules are pandas.

| Dataset | Rule | Logic | Severity |
|---|---|---|---|
| soil | range (SOC 0.02–5 %, EC 0.1–60, pH 4–11, BD 0.8–2.0) | physical plausibility | fail |
| soil | future date; depth not a protocol layer | relational | fail |
| soil | date vs campaign > 14 d | protocol compliance | warn |
| soil | GPS > 10 m from the plot polygon | spatial (UTM distance to the rectangle) | warn |
| soil | missing expected record | completeness: plot × due campaign × layer | warn |
| tree | range; alive without height | integrity | fail |
| tree | **resurrection** (alive after death) | survival must be monotone | fail |
| tree | growth plausibility (−15 … +90 cm between campaigns) | temporal | warn |
| sensor | range; **spike** (both neighbours differ > 0.04 in the *same* direction) | robust to irrigation *steps*, which have one equal neighbour | fail |
| sensor | **flatline** ≥ 24 identical values | stuck sensor | fail |
| sensor | gap > 6 h | logger offline (rows absent) | info |
| meter | register decreased; > 60 mm/day equivalent | reset / implausible | fail |
| ndvi | cloud fraction > 0.30; |NDVI − rolling median of previous 5| > 0.03 | undeclared contamination | fail / warn |
| weather | range; RHmax < RHmin; Tmax < Tmin; Rs > 1.05·Ra | physical consistency | fail |
| weather | vs reanalysis: day residual (> 4 robust σ and absolute floor) and 15-day rolling-median drift band | station-vs-reference | **warn only** |

**Evaluation:** `evaluate_against_injected` scores recall against the known faults — 100 % for all classes except spikes (99.5 %: 627 of 630 — the three misses are most likely injected values that are only mildly implausible and sit within the 0.04 neighbour-difference threshold; I did not inspect them individually), and a test limits false positives on the soil data. Recall is *only* measurable because the data are synthetic.

---

## 9. Crop-water models (`models/`)

### 9.1 FAO-56 Penman–Monteith reference ET₀ (`fao56.py`)

Daily ET₀ (Allen et al., 1998, eq. 6):

```
ET0 = [0.408·Δ·(Rn − G) + γ·900/(T+273)·u2·(es − ea)] / [Δ + γ·(1 + 0.34·u2)]
```

Implemented pieces (equation numbers are in the code): atmospheric pressure (7), psychrometric constant (8), saturation vapour pressure (11–12), slope of the
SVP curve (13), actual vapour pressure from RHmax/RHmin (17), extraterrestrial radiation (21–25), clear-sky radiation (37), net shortwave (38), net longwave with cloudiness (39),
wind at 2 m (47). `G = 0` for daily steps. **Validated** on FAO-56 Example 18 (Brussels): 3.88 vs the published 3.9 mm/day (unit test).

### 9.2 Single-Kc root-zone water balance

```
TAW = 1000·(θ_FC − θ_WP)·Z_r ;  RAW = p·TAW ;  Ks = (TAW − Dr)/((1−p)·TAW) if Dr > RAW else 1     (eq. 82–84)
ETc = Ks · Kc · ET0 ;  Dr(t+1) = Dr(t) − P_eff + ETc ;  clipped to [0, TAW]
```

Effective rain: `0.8·(P − 2 mm)` for P > 2 mm (crude). Irrigation fires when `Dr ≥ MAD·TAW` and refills to FC; gross = net / 0.9.
`kc_young_tree`: plot-scale Kc linear in the canopy index (0.15 bare soil → 0.55 full canopy) — plot-scale because trees cover a small fraction of the ground and meter volumes are normalised per plot area.

### 9.3 Per-plot calibration (`calibration.py`)

**Goal:** recover plot-specific effective parameters from observed data — as you would with real sensors and meters.

* Parameters (3 per plot): `θ_FC` ∈ [0.08, 0.25], `Kc_min` ∈ [0, 0.40], `Kc_max` ∈ [0.10, 1.50]; `θ_WP` tied to `θ_FC` by the pedotransfer relation;
  `Z_r = 0.6`, MAD = 0.2, efficiency 0.9 fixed.
* Canopy index from **NDVI only**: `clip((NDVI − NDVI₅%)/(NDVI₉₅% − NDVI₅%), 0, 1)`, linearly interpolated to daily (soil/vegetation end-members from the 5th/95th percentiles).
* Loss = mean of squared standardised residuals of three observation types:
  1. daily mean soil moisture, σ = 0.004 m³/m³;
  2. **weekly** irrigation depth, σ = 5 mm (absorbs within-week timing mismatch);
  3. **28-day cumulative** metered volume, σ = 2 % + 3 mm (the meter is accurate, so this pins the seasonal water budget).
* Optimiser: `scipy.optimize.differential_evolution` — global and derivative-free, because the irrigation **trigger makes the loss non-smooth** (gradient methods fail on it).
* **Split:** calibrate on days 0–272, validate on days 273–364 (hold-out).
* **History:** the first version failed a noise-free recovery test (8 % irrigation-total error); adding the cumulative-volume residual fixed it. A good example of testing a calibration with a known answer.

Results (synthetic): θ_FC by treatment 0.128 / 0.142 / 0.150 / 0.164 (CTRL / BIOC / COMP / COMP_BIOC), treatment effect significant in an RCBD ANOVA;
hold-out per-plot irrigation error mean |7.1| %, 16 of 24 plots within ±10 %, worst +21 %.
**Caveat:** `θ_WP` uses the *same functional form* as the simulator, so recovery of `θ_FC` is optimistic; the hold-out is the honest test.

### 9.4 AquaCrop-OSPy (`aquacrop_model.py`)

AquaCrop is an **annual-crop** model. The trial's crop is young trees with a year-round canopy and has **no crop yield/biomass observations**, so AquaCrop is *not* calibrated here.
What the trial does measure is soil-water behaviour → used as a **scenario model**: take trial-calibrated `θ_FC` per plot, bootstrap within treatment, run a companion forage (Sorghum) season under two irrigation strategies:

* `net`: keep depletion above 70 % of TAW (full irrigation) — water need barely changes;
* `interval`: ≤ 25 mm every 10 days (deficit) — stored soil water decides stress, so yield rises with `θ_FC` (≈ 3.9 → 5.0 t/ha across treatments).

**Pitfall isolated:** AquaCrop-OSPy never returns with fewer than 12 soil compartments (4, 6, 8 hang; 12, 15, 20 run in ≈ 0.2 s). The code always uses 12 × 0.1 m and asserts it.
With crop observations, `run_forage_season` is the forward model to wrap in an optimiser.

---

## 10. Statistical analysis (`analysis/stats.py`)

### 10.1 Continuous responses (SOC, ECe, pH, BD, plot-mean height/diameter, NDVI)

```
y_ijk = μ + τ_i + γ_k + (τγ)_ik + b_j + p_ij + ε_ijk
        b_j ~ N(0, σ_b²) block ; p_ij ~ N(0, σ_p²) plot ; ε ~ N(0, σ²)
```

Implemented as `statsmodels MixedLM`: `y ~ C(trt, Treatment('CTRL')) * C(time)`, `groups = block` (random intercept), `vc_formula = plot` (variance component), REML.

* **Why a mixed model rather than repeated-measures ANOVA:** handles missing data (QC-excluded values), several variance levels, and gives variance components for design feedback.
* **Why REML:** unbiased variance components in small samples (b = 6).
* **Contrasts:** estimated marginal means and `trt − CTRL` at each campaign via explicit linear contrast vectors `L` on `β` and `cov(β)`: `est = Lβ`, `se = sqrt(LΣLᵀ)`.
* **Small-sample inference:** statsmodels' default z-tests are anticonservative with 6 blocks. I use **Wald-t with the RCBD between-plot error df (b−1)(t−1) = 15**.
  This is a pragmatic approximation; **Kenward–Roger / Satterthwaite** (lme4 + lmerTest in R) is the standard refinement for a final report — say so if asked.
* **Multiplicity:** Holm across the three treatment-vs-control contrasts within each campaign. *(The power study uses Bonferroni, which is slightly more conservative than Holm — a deliberate, stated difference.)*
* **Mid-trial vs endpoint:** the mid-trial review fits using only data available at T2 (no peeking at later campaigns).
* **Diagnostics:** Shapiro–Wilk on residuals, residual SD, max |residual|.
* **Baseline check:** at T0 an RCBD ANOVA of treatment should be non-significant (randomisation worked); no multiplicity penalty for this diagnostic.

### 10.2 Tree survival

* **Primary: Cox proportional hazards, stratified by block, plot-clustered robust SE** (`lifelines`). Stratifying by block lets each block have its own baseline hazard
  (the salinity gradient!); clustering on plot handles that trees in one plot share a frailty. Event time = first campaign at which the tree is recorded dead (**interval-censored**; trees alive at T4 censored).
* **Cross-check:** endpoint **quasi-binomial GLM** on plot-level proportions with `var_weights = n`, `scale = "X2"` (Pearson), giving odds ratios.
* **Reminder:** proportional hazards should be checked on real data (Schoenfeld residuals).

### 10.3 Irrigation

Seasonal depth per plot (`irrigation_by_plot`): days lost to QC (meter resets) are filled with the plot's *own* mean daily depth (otherwise totals are biased downward); plots with < 95 % coverage dropped.
RCBD ANOVA `irrigation ~ block + treatment` with Holm-adjusted contrasts.

### 10.4 Known limitation: survivor bias

Height and diameter are **survivor-conditional** plot means. If mortality is informative about growth (it is, in this simulator, via salinity), a joint longitudinal-survival model would be needed.

---

## 11. Closed-loop validation (`validate_coverage`)

Repeat the whole trial **100 times** (new randomisation, new random effects, same true effects), analyse each with the production model and report, per treatment: bias, RMSE, **95 % CI coverage** and Holm-adjusted power.
Results: coverage 92–95 % for SOC and 91–94 % for ECe — consistent with nominal 95 % given 100 replicates (Monte-Carlo SE of a coverage estimate ≈ 2 percentage points).
Example: COMP_BIOC SOC estimate 0.349 [0.312, 0.386] vs true 0.379 in the reported run.

**How to read it:** a coverage far below 95 % would reveal anticonservative inference (that is exactly what the z-based p-values would have shown); far above, an over-conservative method.

---

## 12. Real external data (optional)

### 12.1 Weather — Open-Meteo / ERA5-Land (`sources/openmeteo.py`)

* **API:** `archive-api.open-meteo.com/v1/archive`, model `era5_land`, hourly variables, `wind_speed_unit=ms`, local timezone. ERA5/ERA5-Land have a ~5-day publication lag → guard raises an error.
* **Hourly → daily (FAO-56 inputs):** Tmax/Tmin = max/min hourly; RHmax/RHmin = max/min hourly; wind: mean of hourly 10 m wind → 2 m by eq. 47; Rs = Σ hourly mean W/m² × 3600 → MJ/m²/day; rain = Σ; days with < 24 hourly values → NaN (never averaged silently).
* **Provenance:** request parameters, URL, SHA-256 of the response, retrieval time, grid cell metadata; responses cached by parameter hash.
* **Use:** (a) real forcing for the simulated plots; (b) reference for station QC; (c) ET₀ cross-check: our Penman–Monteith vs Open-Meteo's own `et0_fao_evapotranspiration` on identical inputs (agreement ≈ 0.2 % bias in the test).
* **Why not a substitute for a station:** 9–11 km grid, smoothed extremes, modelled radiation/wind. Disagreement is therefore a **warn** flag, never an exclusion.
* **Simulated station = reanalysis + discrepancy:** AR(1) temperature error (σ = 0.9 °C, ρ = 0.6) + multiplicative RH, wind (σ_log 0.25), radiation (5 %), rain (σ_log 0.5). **These magnitudes are assumptions.**
* **Station-vs-reanalysis QC:** residuals (additive for T and RHmin, ratio for Rs and wind); robust z with `MAD`: flag `|r − median| > 4·1.4826·MAD` *and* beyond an absolute floor; drift: 15-day centred rolling median outside a tolerance band (Rs ±10 %, T ±2 °C…). Median/MAD are used so a handful of bad days cannot hide themselves.
* In the offline test: all 5 offset days and the drift detected; clean-data false positives < 3 % of days.

#### 12.1b Weather — Copernicus CDS (`sources/cds.py`)

Same ERA5-Land product fetched at source (`reanalysis-era5-land`, monthly NetCDF requests, your `~/.cdsapirc` token, licence accepted once). Details that silently corrupt data if missed, and are handled and unit-tested:

* **Accumulated variables** (`total_precipitation` in m, `surface_solar_radiation_downwards` in J/m²) accumulate from 00 UTC; the value stamped 00 UTC is the *previous day's* 24 h total. Hourly increment = `v(t) − v(t−1h)`, except at 01 UTC where it is `v(t)`. A missing hour makes the step across the gap `NaN` instead of dumping several hours into one.
* **UTC → local day:** stamps are UTC, trial days are local (UTC+4); the request starts one day early and ends one day late so every local day is complete.
* **Relative humidity** is not stored: `RH = 100·e_s(T_dew)/e_s(T)` (FAO-56 eq. 11); RHmax/RHmin from hourly values.
* **Wind:** `√(u²+v²)` at 10 m → 2 m (eq. 47). Rain m → mm; radiation J/m² → MJ/m²/day.
* Nearest ≈ 9 km grid cell is used and recorded; file SHA-256s are saved as provenance.

Open-Meteo and CDS should agree closely (both ERA5-Land); comparing the two outputs on your machine is a good integrity check of both converters.

### 12.2 Satellite — Sentinel-2 via Copernicus Data Space (`sources/copernicus.py`)

* **NDVI** = (B08 − B04)/(B08 + B04) from L2A surface reflectance, per 10 m pixel; pixel mask from the Scene Classification Layer (keep classes 4, 5, 7; drop cloud, shadow, saturated, snow, water).
* **API:** OAuth client-credentials token → Sentinel Hub **Statistical API** (daily statistics per zone: mean, stDev, sampleCount, noDataCount → `n_valid`). Credentials from environment variables, never in code.
* **The geometry problem (`design/pixel_purity.py`):** Sentinel-2 10 m pixels sit on a grid aligned to multiples of 10 m in UTM. A 12 × 20 m plot contains **at most one whole pixel**: on this layout 6 of 24 plots contain ≥ 1 whole pixel, and **0 of 24 after a 5 m edge buffer**
  (co-registration + point-spread function). A block zone (60 × 20 m) holds ≈ 5 pixels. Conclusion: use **block/site-level NDVI as context**; for plot-level canopy use UAV imagery or larger plots.
* **What real NDVI will look like:** over a young desert plantation it is mostly bare soil (≈ 0.05–0.12) and will not show the synthetic treatment effect — that series stays simulated.
* **Status:** written from the public API documentation and tested with mocked payloads only; the first live run is a commissioning test.

---

## 13. Dashboards and report

* **Grafana:** a provisioned PostgreSQL datasource and a 14-panel + 2 optional-panel dashboard reading `mart.*` views (soil moisture by treatment, cumulative irrigation, NDVI, ET₀/rain, soil by campaign, survival, QC flags, data freshness, plot status).
  Every dashboard query is executed in the test-suite; Grafana itself was *not* started here.
* **Report (`report/build.py`):** self-contained HTML, figures embedded. Palette: four-colour categorical set validated for colour-vision deficiency; control arm additionally **dashed** (secondary encoding); direct labels de-collided (`_spread_labels`) plus tables.

---

## 14. Limitations and honest caveats

1. **Synthetic data.** Effect sizes, variance components, fault rates and the station-discrepancy size are assumptions.
2. **Calibration optimism:** θ_WP uses the same relation as the simulator; hold-out error is the credible figure.
3. **Inference:** Wald-t with RCBD df, not Kenward–Roger; statsmodels' MixedLM variance component handling is simpler than lme4's.
4. **Survivor-conditional growth** (no joint model); survival power with 6 blocks is low (≈ 47 %).
5. **AquaCrop** is not calibrated to crop data; it is a scenario model.
6. **PostGIS** SQL (`sql/03_postgis.sql`) was written but not executed (plain PostgreSQL only); the Python rule `soil.gps_in_plot` implements the same check and is tested.
7. **No live API calls** (Kobo, Open-Meteo, CDSE) were made; they are tested against mocked payloads.
8. **Audit trail** is tamper-evident but not a certified verification procedure.
9. **Exponential hazard** and the saturating-exponential effect shape are convenient, not validated, forms.
10. The `θ`-to-VWC mapping and ET profile in the sensor simulator are simplified (half-sine ET).

---

## 15. What to learn, and how to present this project

### 15.1 Topics to know well (with why they matter)

| Topic | Why | Starting point |
|---|---|---|
| RCBD, experimental unit, pseudo-replication | Core of trial design | Montgomery, *Design and Analysis of Experiments*; Hurlbert (1984) on pseudoreplication |
| Linear mixed models, REML, variance components | Primary analysis | Pinheiro & Bates; Bates et al. (2015, lme4); Kenward & Roger (1997) |
| Multiple comparisons: Bonferroni vs Holm | Three contrasts per time | Holm (1979) |
| Power: noncentral t/F, simulation-based power | Design stage | Cohen; Gelman & Hill |
| Survival: Cox PH, stratification, robust SE, interval censoring | Tree mortality | Therneau & Grambsch; `lifelines` docs |
| Quasi-binomial GLM, over-dispersion | Plot-level proportions | McCullagh & Nelder |
| FAO-56: ET₀, Kc, water balance | Irrigation modelling | Allen et al. (1998), FAO I&D Paper 56 |
| AquaCrop | Yield–water relations | Raes et al., FAO AquaCrop; `aquacrop` (OSPy) docs |
| Model calibration, identifiability, hold-out validation | Honest accuracy | Beven (GLUE); Wallach et al., *Working with Dynamic Crop Models* |
| Reanalysis (ERA5-Land) vs stations | Weather QC | Muñoz-Sabater et al. (2021) |
| Sentinel-2, NDVI, SCL mask, mixed pixels | Remote sensing limits | ESA S2 User Handbook; CDSE documentation |
| Soil carbon MRV (Verra VM0042, Puro.Earth) | Audit requirements | Methodology documents of the registries |
| Data engineering: layered architecture, idempotency, hash chains | Pipeline | Kleppmann, *Designing Data-Intensive Applications* |
| PostGIS: geometry, SRID, spatial indexes | Spatial QC | PostGIS manual |

### 15.2 Questions you should be able to answer

* *Why is the plot — not the tree — the experimental unit?* Treatments are applied to plots; trees within a plot share soil, water and treatment → they are subsamples.
* *Why block?* To remove the salinity gradient from the treatment comparison; df cost is (b−1).
* *Why does the power analysis simulate with the same model as the analysis?* So power is a property of the method actually used.
* *Why `warn`-only for reanalysis disagreement?* Reanalysis is not ground truth; QC must not overwrite the more direct measurement on weaker evidence.
* *Why differential evolution?* The irrigation trigger makes the loss non-differentiable and multimodal.
* *Why are there 12 compartments in AquaCrop?* Fewer hang the solver (found empirically).
* *How would you handle a real mid-trial result that is significant at one contrast only?* Holm-adjusted, report effect sizes with intervals, pre-specified endpoint.
* *What if the farm has a real weather station?* Use it as primary; use ERA5-Land for gap-filling and QC only.
* *What would you change with real data?* Replace assumed variances with T0 estimates; Kenward–Roger; check PH; joint growth–survival model; sensor calibration against gravimetric samples.

### 15.3 A suggested learning order (about three weeks, 1–2 h/day)

1. §3 and §4 (design + power) — re-derive the noncentral-t power by hand for b = 6.
2. §10 — write the mixed model in `lme4` (R) and compare to `statsmodels`; look at df differences.
3. §9.1–9.2 — compute FAO-56 Example 18 by hand.
4. §8 — add one new QC rule and a test.
5. §6–7 — break the hash chain deliberately and watch `verify_chain` fail.
6. §12 — run `fetch-weather` on your machine and compare station vs reanalysis.

---

## 16. File-by-file reference

| File | What it computes | Why |
|---|---|---|
| `config.py` | Trial constants, campaigns, `TrialConfig` | single source of truth for design numbers |
| `design/layout.py` | Seeded RCBD, UTM polygons, layout hash, layout figure | reproducible randomisation, audit proof |
| `design/power.py` | Analytic and Monte-Carlo power; `fit_rm_model`; survival power | design-stage evidence; shared model |
| `design/pixel_purity.py` | Whole 10 m pixels per plot/block (interval arithmetic) | quantifies the satellite resolution limit |
| `simulate/weather.py` | Synthetic weather; reanalysis → truth; simulated station | realistic forcing with known truth |
| `simulate/field.py` | Soil, trees, canopy, water balance, sensors, meters, NDVI (with truth) | known-truth data generator |
| `simulate/kobo.py` | XLSForms, API-v2-shaped submissions, injected form faults | exercise the real client logic |
| `simulate/fault_injection.py` | Injects controlled time-series and station-reference faults | exercise QC detection |
| `simulate/artifacts.py` | Writes input drops, design artifacts, and validation-only truth | clear output boundary |
| `simulate/builder.py` | Coordinates the simulation build | one readable generation entry point |
| `pipeline/db.py` | Engine, schema apply, bulk COPY | database plumbing |
| `sources/kobo.py` | Real Kobo API v2 pagination, raw page landing, retrieval metadata and SHA-256 provenance | download and preserve external submissions without changing them |
| `preparation/kobo.py` | Real Kobo payload flattening and revision labelling into separate prepared CSVs | prepare real field observations without mixing them with synthetic submissions |
| `preparation/manifest.py` | Dataset-level lineage manifest for real context inputs and generated synthetic outputs | document source type and derivation without changing data schemas |
| `pipeline/kobo_client.py` | Payload normalisation, edit/re-submission resolution; re-exports `KoboClient` for compatibility | handle Kobo quirks after raw landing |
| `pipeline/ingest.py` | Raw loading, drops, core build, idempotency, quarantine | raw → core |
| `pipeline/audit.py` | Hash chain; dataset versions; verification | tamper evidence |
| `qc/rules.py` | Pandera + temporal/relational rules | flag bad data |
| `qc/reanalysis.py` | Station-vs-reanalysis residuals, drift, ET₀ comparison | independent weather QC |
| `qc/run.py` | Runs rules, persists flags, scores recall | audit-ready QC |
| `models/fao56.py` | Penman–Monteith ET₀, water balance, Kc | standard irrigation physics |
| `models/calibration.py` | Per-plot calibration, hold-out evaluation | parameters from data |
| `models/aquacrop_model.py` | Forage-season scenarios | decision-oriented use of the calibration |
| `analysis/stats.py` | Mixed models, contrasts, Cox, GLM, ANOVA, coverage | the inference |
| `analysis/run.py` | Orchestrates the analysis | end-to-end results |
| `report/build.py` | HTML report and figures | final deliverable |
| `sources/openmeteo.py`, `sources/cds.py`, `sources/copernicus.py` | Real-data clients (Open-Meteo, CDS NetCDF, CDSE Statistical API) | external real data |
| `simulate/ndvi_background.py` | Real block NDVI → daily background per block | real soil/background signal in simulated NDVI |
| `sql/01_schema.sql` … `03_postgis.sql` | Schemas, triggers, views, PostGIS | the database |
| `grafana/` | Datasource and dashboard JSON | live monitoring |
| `tests/` | 52 tests (unit, integration, tamper, mocked APIs) | correctness evidence |

---

## 17. Glossary

* **RCBD** — randomised complete block design: every treatment appears once in every block.
* **Experimental unit** — the smallest unit to which a treatment is independently assigned (here the plot).
* **Pseudo-replication** — treating subsamples as independent replicates, inflating significance.
* **REML** — restricted maximum likelihood; unbiased variance-component estimation.
* **EMM** — estimated marginal mean (model-based treatment mean).
* **Holm** — step-down multiplicity correction, uniformly more powerful than Bonferroni.
* **Kenward–Roger** — small-sample df/variance correction for mixed models.
* **Interval censoring** — event known only to have occurred between two visits.
* **ET₀ / ETc / Kc** — reference evapotranspiration; crop evapotranspiration; crop coefficient (ETc = Kc·ET₀).
* **TAW / RAW / MAD** — total / readily available water; management allowed depletion.
* **θ_FC, θ_WP** — volumetric water content at field capacity / permanent wilting point.
* **VWC** — volumetric water content (m³/m³).
* **ECe** — electrical conductivity of the saturation extract (dS/m), a salinity index.
* **SOC** — soil organic carbon (%).
* **NDVI** — normalised difference vegetation index.
* **SCL** — Sentinel-2 Scene Classification Layer.
* **ERA5-Land** — ECMWF land reanalysis, ≈ 9 km, hourly.
* **MRV** — measurement, reporting and verification (carbon credits).
* **Hash chain** — each log entry's hash includes the previous entry's hash; tampering breaks the chain.
* **Closed-loop validation** — checking an analysis on simulated data with known truth.

## Scientific results layer and dashboard (added)

The analysis results are persisted with provenance (`results.*`, see `docs/SCIENTIFIC_DASHBOARD.md`) and shown on a second dashboard.
Two additions deserve methodological emphasis:

* **Design-based check.** Besides the model-based Wald-t contrasts (RCBD error df = 15, Holm), a *within-block randomisation test*
  (treatment labels permuted inside blocks, the actual randomisation scheme) is reported for the main responses. Where the two
  disagree on significance the result is flagged as fragile rather than resolved silently.
* **No observed power.** Power is shown prospectively only (analytic, Monte-Carlo with the production model, and under pessimistic variance).
