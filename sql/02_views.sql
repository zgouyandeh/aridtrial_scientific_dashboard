-- QC-filtered marts and dashboard views. Plain PostgreSQL (no PostGIS needed).
-- Principle: raw data are never edited. QC writes flags to audit.qc_result; these views apply them.

CREATE OR REPLACE VIEW audit.qc_latest AS
SELECT * FROM audit.qc_result
WHERE run_id = (SELECT max(run_id) FROM audit.qc_run);

-- ---------------------------------------------------------------- soil
CREATE OR REPLACE VIEW mart.soil_clean AS
SELECT s.sample_key, s.plot_id, p.block, p.treatment, s.campaign, s.sample_date,
       s.depth_top_cm, s.depth_bottom_cm,
       CASE WHEN 'soc_pct'      = ANY(f.vars) THEN NULL ELSE s.soc_pct      END AS soc_pct,
       CASE WHEN 'ec_dsm'       = ANY(f.vars) THEN NULL ELSE s.ec_dsm       END AS ec_dsm,
       CASE WHEN 'ph'           = ANY(f.vars) THEN NULL ELSE s.ph           END AS ph,
       CASE WHEN 'bulk_density' = ANY(f.vars) THEN NULL ELSE s.bulk_density END AS bulk_density,
       s.kobo_root_uuid, s.payload_sha256
FROM core.soil_sample s
JOIN core.plot p USING (plot_id)
LEFT JOIN LATERAL (
  SELECT array_agg(q.variable) AS vars FROM audit.qc_latest q
  WHERE q.dataset = 'soil' AND q.record_key = s.sample_key AND q.severity = 'fail'
) f ON true
WHERE s.status = 'current' AND NOT ('*' = ANY(COALESCE(f.vars, ARRAY[]::text[])));

-- ---------------------------------------------------------------- trees
CREATE OR REPLACE VIEW mart.tree_clean AS
SELECT t.obs_key, t.tree_id, t.plot_id, p.block, p.treatment, t.campaign, t.obs_date, t.tree_no, t.alive,
       CASE WHEN 'height_cm'    = ANY(f.vars) THEN NULL ELSE t.height_cm    END AS height_cm,
       CASE WHEN 'stem_diam_mm' = ANY(f.vars) THEN NULL ELSE t.stem_diam_mm END AS stem_diam_mm
FROM core.tree_obs t
JOIN core.plot p USING (plot_id)
LEFT JOIN LATERAL (
  SELECT array_agg(q.variable) AS vars FROM audit.qc_latest q
  WHERE q.dataset = 'tree' AND q.record_key = t.obs_key AND q.severity = 'fail'
) f ON true
WHERE t.status = 'current' AND NOT ('*' = ANY(COALESCE(f.vars, ARRAY[]::text[])));

-- ---------------------------------------------------------------- time series
CREATE OR REPLACE VIEW mart.sensor_clean AS
SELECT r.plot_id, p.treatment, p.block, r.ts, r.vwc, r.soil_temp_c
FROM core.sensor_reading r
JOIN core.plot p USING (plot_id)
WHERE NOT EXISTS (
  SELECT 1 FROM audit.qc_latest q
  WHERE q.dataset = 'sensor' AND q.severity = 'fail' AND q.plot_id = r.plot_id AND q.ts = r.ts
);

CREATE OR REPLACE VIEW mart.meter_clean AS
SELECT m.plot_id, p.treatment, p.block, m.date, m.cum_volume_l,
       CASE WHEN EXISTS (
         SELECT 1 FROM audit.qc_latest q
         WHERE q.dataset = 'meter' AND q.severity = 'fail'
           AND q.plot_id = m.plot_id AND q.ts = ((m.date)::timestamp AT TIME ZONE 'UTC')
       ) THEN NULL ELSE m.volume_l END AS volume_l
FROM core.water_meter m
JOIN core.plot p USING (plot_id);

CREATE OR REPLACE VIEW mart.ndvi_clean AS
SELECT n.plot_id, p.treatment, p.block, n.date, n.ndvi, n.cloud_frac
FROM core.ndvi n
JOIN core.plot p USING (plot_id)
WHERE NOT EXISTS (
  SELECT 1 FROM audit.qc_latest q
  WHERE q.dataset = 'ndvi' AND q.severity = 'fail' AND q.plot_id = n.plot_id AND q.ts = ((n.date)::timestamp AT TIME ZONE 'UTC')
);

CREATE OR REPLACE VIEW mart.weather_clean AS
SELECT w.date, w.tmax, w.tmin, w.rh_max, w.rh_min, w.wind_2m, w.rs_mj, w.rain_mm,
       CASE WHEN q.id IS NULL THEN w.et0_mm END AS et0_mm
FROM core.weather_daily w
LEFT JOIN (SELECT DISTINCT ON (ts) * FROM audit.qc_latest WHERE dataset = 'weather' AND severity = 'fail' ORDER BY ts, id) q
  ON q.ts = ((w.date)::timestamp AT TIME ZONE 'UTC');

-- ---------------------------------------------------------------- dashboard views (Grafana)
CREATE OR REPLACE VIEW mart.v_vwc_daily_treatment AS
SELECT date_trunc('day', ts) AS "time", treatment, avg(vwc) AS vwc
FROM mart.sensor_clean GROUP BY 1, 2;

CREATE OR REPLACE VIEW mart.v_irrigation_cum_treatment AS
SELECT ((date)::timestamp AT TIME ZONE 'UTC') AS "time", treatment, avg(cum_l) / 1000.0 AS cum_irrigation_kl
FROM (
  SELECT plot_id, treatment, date, sum(COALESCE(volume_l, 0)) OVER (PARTITION BY plot_id ORDER BY date) AS cum_l
  FROM mart.meter_clean
) x GROUP BY 1, 2;

CREATE OR REPLACE VIEW mart.v_ndvi_treatment AS
SELECT ((date)::timestamp AT TIME ZONE 'UTC') AS "time", treatment, avg(ndvi) AS ndvi
FROM mart.ndvi_clean GROUP BY 1, 2;

CREATE OR REPLACE VIEW mart.v_soil_campaign_treatment AS
SELECT campaign, ((min(sample_date))::timestamp AT TIME ZONE 'UTC') AS "time", treatment,
       avg(soc_pct) FILTER (WHERE depth_top_cm = 0) AS soc_0_15_pct,
       avg(ec_dsm)  FILTER (WHERE depth_top_cm = 0) AS ec_0_15_dsm
FROM mart.soil_clean GROUP BY campaign, treatment;

CREATE OR REPLACE VIEW mart.v_tree_survival AS
SELECT campaign, ((min(obs_date))::timestamp AT TIME ZONE 'UTC') AS "time", treatment,
       avg(alive::int) AS survival, avg(height_cm) FILTER (WHERE alive) AS mean_height_cm
FROM mart.tree_clean GROUP BY campaign, treatment;

CREATE OR REPLACE VIEW mart.v_qc_summary AS
SELECT dataset, severity, rule_id, count(*) AS n FROM audit.qc_latest GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW mart.v_data_freshness AS
SELECT 'sensor'  AS dataset, max(ts)::timestamptz AS latest, count(*) AS n_rows FROM core.sensor_reading
UNION ALL SELECT 'meter',   ((max(date))::timestamp AT TIME ZONE 'UTC'), count(*) FROM core.water_meter
UNION ALL SELECT 'ndvi',    ((max(date))::timestamp AT TIME ZONE 'UTC'), count(*) FROM core.ndvi
UNION ALL SELECT 'weather', ((max(date))::timestamp AT TIME ZONE 'UTC'), count(*) FROM core.weather_daily
UNION ALL SELECT 'soil',    ((max(sample_date))::timestamp AT TIME ZONE 'UTC'), count(*) FROM mart.soil_clean
UNION ALL SELECT 'tree',    ((max(obs_date))::timestamp AT TIME ZONE 'UTC'), count(*) FROM mart.tree_clean;

CREATE OR REPLACE VIEW mart.v_plot_latest AS
SELECT p.plot_id, p.block, p.treatment, p.lat, p.lon,
       (SELECT ndvi FROM mart.ndvi_clean n WHERE n.plot_id = p.plot_id ORDER BY date DESC LIMIT 1) AS ndvi_latest,
       (SELECT vwc  FROM mart.sensor_clean s WHERE s.plot_id = p.plot_id ORDER BY ts DESC LIMIT 1)  AS vwc_latest,
       (SELECT avg(alive::int) FROM mart.tree_clean t
         WHERE t.plot_id = p.plot_id AND t.campaign = (SELECT max(campaign) FROM mart.tree_clean)) AS survival_latest
FROM core.plot p;

-- ---------------------------------------------------------------- real external data (reference views)
CREATE OR REPLACE VIEW mart.v_weather_vs_reanalysis AS
SELECT ((w.date)::timestamp AT TIME ZONE 'UTC') AS "time",
       w.tmax AS tmax_station, r.tmax AS tmax_reanalysis,
       w.rs_mj AS rs_station, r.rs_mj AS rs_reanalysis,
       w.et0_mm AS et0_station, r.et0_openmeteo_mm AS et0_openmeteo
FROM core.weather_daily w JOIN core.weather_reanalysis r USING (date);

CREATE OR REPLACE VIEW mart.v_ndvi_block AS
SELECT ((date)::timestamp AT TIME ZONE 'UTC') AS "time", 'block ' || block AS block, ndvi_mean AS ndvi
FROM core.ndvi_block WHERE n_valid >= 5;


-- ---------------------------------------------------------------- reconciliation dashboard views
CREATE OR REPLACE VIEW audit.reconciliation_latest AS
SELECT * FROM audit.reconciliation_result
WHERE run_id = (SELECT max(run_id) FROM audit.reconciliation_run);

CREATE OR REPLACE VIEW mart.v_reconciliation_summary AS
SELECT severity, count(*) AS checks
FROM audit.reconciliation_latest
GROUP BY severity;

CREATE OR REPLACE VIEW mart.v_reconciliation_by_dataset AS
SELECT dataset, severity, count(*) AS checks,
       count(*) FILTER (WHERE difference <> 0) AS count_mismatches
FROM audit.reconciliation_latest
GROUP BY dataset, severity;
