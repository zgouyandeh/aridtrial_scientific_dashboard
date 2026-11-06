# Scientific & Agronomic Analysis dashboard

Second dashboard of the project (the first is the Data Quality dashboard). It presents the persisted results of the
trial analysis: *what the data say, how sure we are, and what the design could and could not detect.*
**All data in this repository are synthetic**; every page carries a "Synthetic data" badge and the run provenance.

Open `aridtrial dashboard`, then use **Scientific & agronomic** in the sidebar (`#/analysis/<section>`).

## Architecture (results are computed once, stored, then only read)

```
aridtrial analyze | demo
   └─ analysis.run.run_analysis      production models (mixed models, Cox, power, calibration, validation)
        └─ analysis.scientific.run_and_persist
             └─ results.analysis_run       run id, dataset version, QC run, seed, settings, library versions, data origin
             └─ results.analysis_artifact  one JSON payload per section, SHA-256 per payload   (append-only)
FastAPI (read-only)  /api/analysis/runs | /run | /{section}?run_id=
React (Vite, SVG charts, no chart library)  frontend/src/AnalysisPage.jsx, charts.jsx
```

* No statistic is computed in the browser or in an HTTP request. A page is a view of a stored, hash-addressed run.
* Each run is tied to a frozen dataset version and QC run, so a figure can be traced back to raw submissions.
* The results schema (`sql/06_results.sql`) never feeds back into `core` / `mart`.

## Sections

| Tab | Content | Statistical notes |
|---|---|---|
| Exploratory | trajectories, endpoint box/strip plots, within-block and raw correlation, baseline histogram, block×treatment pattern, QC exclusions | descriptive; correlation is exploratory and shown *within block* to avoid block-gradient artefacts |
| Experimental design | layout map, balance matrix, df accounting, baseline balance, blocking efficiency (RE vs CRD), ICC, pixel purity | plot is the experimental unit; Treatment×Block not estimable (1 rep) → descriptive only |
| Treatment comparison | EMMs with CI, endpoint effects (absolute, % vs control, standardised), baseline-adjusted ANCOVA sensitivity | contrasts: Wald-t with RCBD error df, Holm |
| Mixed models | `y ~ treatment*campaign + (1|block) + (1|plot)` (REML): fixed effects, interaction test, joint F at T2/T4, variance components & ICC, Q–Q, residuals vs fitted, Shapiro/Levene, block BLUPs | fixed-effect z p-values flagged anti-conservative; boundary variance flagged |
| Inference | contrasts per response/review, primary-outcome flag, multiplicity rules, **within-block randomisation test** vs model decision | agreement/disagreement is shown, not hidden |
| Power & sensitivity | MDD vs blocks under design / baseline / pessimistic σ, power curves, blocks needed, Monte-Carlo power, survival power | prospective only — **observed power is deliberately not reported** |
| Agronomy & environment | irrigation depth + ANOVA, VWC, NDVI, cumulative irrigation, weather, FAO-56 calibration (hold-out), AquaCrop scenarios, optional real ERA5-Land / Sentinel-2 block NDVI | AquaCrop = scenario output, not measurement; Sentinel-2 is block-level context only |
| Survival | Kaplan–Meier with bands, Wilson intervals, block-stratified Cox (plot-clustered robust SE), quasi-binomial GLM cross-check, PH test, log-rank | moisture-threshold "survival" is controller-driven (circular) and is not presented as inference |
| Validation | closed-loop coverage/bias/RMSE/power (known truth), QC recall on injected faults, QC pass rates, reconciliation | validates the machinery on synthetic data, not real-data assumptions |

## Honest limits
* With 6 blocks, error df = 15. Intervals are wide; "not significant" ≠ "no effect".
* SOC at the endpoint (0–15 cm) is the single primary outcome; everything else is secondary/exploratory and not adjusted across responses.
* `--quick` runs use fewer simulations (badge shown on the page). Run without `--quick` for the final numbers.
* The dashboard has no authentication; do not expose it publicly.

## Run
```bash
aridtrial demo            # full run incl. results persistence
aridtrial analyze         # re-run analysis on the current database; adds a new run_id
aridtrial dashboard       # http://127.0.0.1:8000
cd frontend && npm install && npm run build   # only after editing frontend/src
```
