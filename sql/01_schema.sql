-- aridtrial schema (plain PostgreSQL 14+; spatial extras live in 03_postgis.sql)
--
-- Layering
--   raw    immutable landing zone: Kobo payloads exactly as received + file-drop registry
--   core   typed, de-duplicated, lineage-carrying tables (rebuildable from raw)
--   audit  hash-chained ingest log, QC results, quarantine, dataset versions
--   mart   views for dashboards and analysis (QC-filtered)

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS mart;

-- ---------------------------------------------------------------- append-only guard
CREATE OR REPLACE FUNCTION audit.deny_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% on %.% is not allowed: table is append-only (audit requirement)', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- design
CREATE TABLE IF NOT EXISTS core.plot (
  plot_id    text PRIMARY KEY,
  block      int  NOT NULL,
  position   int  NOT NULL,
  treatment  text NOT NULL CHECK (treatment IN ('CTRL','COMP','BIOC','COMP_BIOC')),
  easting    double precision NOT NULL,
  northing   double precision NOT NULL,
  lon        double precision NOT NULL,
  lat        double precision NOT NULL,
  area_m2    double precision NOT NULL,
  n_trees    int  NOT NULL,
  geom_wkt   text NOT NULL,
  srid       int  NOT NULL DEFAULT 32640,
  UNIQUE (block, position),
  UNIQUE (block, treatment)           -- RCBD: each treatment exactly once per block
);

-- ---------------------------------------------------------------- raw (append-only)
CREATE TABLE IF NOT EXISTS raw.kobo_submission (
  form_id        text NOT NULL,
  root_uuid      text NOT NULL,       -- stable across edits (meta/rootUuid)
  instance_uuid  text NOT NULL,       -- changes on every edit (_uuid)
  payload        jsonb NOT NULL,
  payload_sha256 text NOT NULL,
  submitted_at   timestamptz,
  source         text,
  ingested_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (form_id, root_uuid, payload_sha256)
);
CREATE TABLE IF NOT EXISTS raw.file_drop (
  sha256      text PRIMARY KEY,
  dataset     text NOT NULL,
  file_name   text NOT NULL,
  n_rows      int  NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS no_mutation ON raw.kobo_submission;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON raw.kobo_submission
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();
DROP TRIGGER IF EXISTS no_mutation ON raw.file_drop;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON raw.file_drop
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();

-- ---------------------------------------------------------------- audit
CREATE TABLE IF NOT EXISTS audit.ingest_log (
  seq            bigserial PRIMARY KEY,
  ts             timestamptz NOT NULL,
  actor          text NOT NULL,
  action         text NOT NULL,
  dataset        text,
  source         text,
  n_rows         int,
  content_sha256 text,
  details        jsonb NOT NULL DEFAULT '{}'::jsonb,
  prev_hash      text NOT NULL,
  entry_hash     text NOT NULL UNIQUE
);
DROP TRIGGER IF EXISTS no_mutation ON audit.ingest_log;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON audit.ingest_log
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();

CREATE TABLE IF NOT EXISTS audit.dataset_source (
  manifest_sha256 text NOT NULL,
  dataset         text NOT NULL,
  source_type     text NOT NULL,
  source_name     text,
  contract_sha256 text NOT NULL,
  ingest_target   text,
  role            text,
  details         jsonb NOT NULL DEFAULT '{}'::jsonb,
  registered_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (manifest_sha256, dataset)
);
DROP TRIGGER IF EXISTS no_mutation ON audit.dataset_source;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON audit.dataset_source
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();

CREATE TABLE IF NOT EXISTS audit.randomisation (
  id            serial PRIMARY KEY,
  recorded_at   timestamptz NOT NULL DEFAULT now(),
  layout_sha256 text NOT NULL UNIQUE,
  record        jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS audit.quarantine (
  id          bigserial PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT now(),
  dataset     text NOT NULL,
  record_key  text,
  reason      text NOT NULL,
  payload     jsonb,
  UNIQUE (dataset, record_key, reason)          -- rebuilds are idempotent
);

CREATE TABLE IF NOT EXISTS audit.qc_run (
  run_id        bigserial PRIMARY KEY,
  started_at    timestamptz NOT NULL DEFAULT now(),
  ruleset       text NOT NULL,
  summary       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS audit.qc_result (
  id          bigserial PRIMARY KEY,
  run_id      bigint NOT NULL REFERENCES audit.qc_run(run_id),
  dataset     text NOT NULL,
  record_key  text NOT NULL,
  plot_id     text,
  ts          timestamptz,
  variable    text NOT NULL DEFAULT '*',               -- column the rule applies to; '*' = whole record
  rule_id     text NOT NULL,
  severity    text NOT NULL CHECK (severity IN ('info','warn','fail')),
  message     text,
  value       double precision
);
CREATE INDEX IF NOT EXISTS qc_result_lookup ON audit.qc_result (run_id, dataset, severity, plot_id, ts);

CREATE TABLE IF NOT EXISTS audit.dataset_version (
  version_id      text PRIMARY KEY,
  created_at      timestamptz NOT NULL,
  manifest        jsonb NOT NULL,
  manifest_sha256 text NOT NULL,
  chain_head      text NOT NULL,
  qc_run_id       bigint REFERENCES audit.qc_run(run_id)
);
DROP TRIGGER IF EXISTS no_mutation ON audit.dataset_version;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON audit.dataset_version
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();

-- ---------------------------------------------------------------- core (rebuildable from raw)
CREATE TABLE IF NOT EXISTS core.soil_sample (
  record_id      text PRIMARY KEY,                     -- sha256(form|root|payload|layer)
  sample_key     text NOT NULL,                        -- plot|campaign|depth_top
  plot_id        text NOT NULL REFERENCES core.plot(plot_id),
  campaign       text NOT NULL,
  sample_date    date,
  depth_top_cm   int  NOT NULL,
  depth_bottom_cm int,
  soc_pct        double precision,
  ec_dsm         double precision,
  ph             double precision,
  bulk_density   double precision,
  collector      text,
  gps_lon        double precision,
  gps_lat        double precision,
  status         text NOT NULL CHECK (status IN ('current','superseded')),
  superseded_reason text,
  kobo_root_uuid text NOT NULL,
  payload_sha256 text NOT NULL,
  submitted_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS soil_current_uq ON core.soil_sample (sample_key) WHERE status = 'current';

CREATE TABLE IF NOT EXISTS core.tree_obs (
  record_id      text PRIMARY KEY,
  obs_key        text NOT NULL,                        -- plot|campaign|tree_no
  tree_id        text NOT NULL,
  plot_id        text NOT NULL REFERENCES core.plot(plot_id),
  campaign       text NOT NULL,
  obs_date       date,
  tree_no        int  NOT NULL,
  alive          boolean NOT NULL,
  height_cm      double precision,
  stem_diam_mm   double precision,
  gps_lon        double precision,
  gps_lat        double precision,
  status         text NOT NULL CHECK (status IN ('current','superseded')),
  kobo_root_uuid text NOT NULL,
  payload_sha256 text NOT NULL,
  submitted_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS tree_current_uq ON core.tree_obs (obs_key) WHERE status = 'current';

CREATE TABLE IF NOT EXISTS core.sensor_reading (
  plot_id      text NOT NULL REFERENCES core.plot(plot_id),
  ts           timestamptz NOT NULL,
  vwc          double precision,                       -- m3/m3
  soil_temp_c  double precision,
  source_sha256 text NOT NULL,
  PRIMARY KEY (plot_id, ts)
);
CREATE TABLE IF NOT EXISTS core.weather_daily (
  date     date PRIMARY KEY,
  tmax double precision, tmin double precision, rh_max double precision, rh_min double precision,
  wind_2m double precision, rs_mj double precision, rain_mm double precision,
  et0_mm double precision,                             -- FAO-56 Penman-Monteith, computed in pipeline
  source_sha256 text NOT NULL
);
CREATE TABLE IF NOT EXISTS core.water_meter (
  plot_id       text NOT NULL REFERENCES core.plot(plot_id),
  date          date NOT NULL,
  cum_volume_l  double precision,                      -- register reading
  volume_l      double precision,                      -- derived daily volume (NULL on reset / first day)
  source_sha256 text NOT NULL,
  PRIMARY KEY (plot_id, date)
);
CREATE TABLE IF NOT EXISTS core.ndvi (
  plot_id    text NOT NULL REFERENCES core.plot(plot_id),
  date       date NOT NULL,
  ndvi       double precision,
  cloud_frac double precision,
  scene_id   text,
  source_sha256 text NOT NULL,
  PRIMARY KEY (plot_id, date)
);

-- Real external data (reference only: never replaces on-site measurements; see qc/reanalysis.py)
CREATE TABLE IF NOT EXISTS core.weather_reanalysis (
  date     date PRIMARY KEY,
  tmax double precision, tmin double precision, rh_max double precision, rh_min double precision,
  wind_2m double precision, rs_mj double precision, rain_mm double precision,
  et0_openmeteo_mm double precision,                   -- Open-Meteo's own FAO-56 ET0 (independent implementation)
  source_sha256 text NOT NULL                          -- sha256 of the delivered drop; request URL + payload hash in raw.file_drop / audit log
);
CREATE TABLE IF NOT EXISTS core.ndvi_block (          -- Sentinel-2 L2A, block-level zonal statistics (cloud/shadow-masked via SCL)
  block      int  NOT NULL,
  date       date NOT NULL,
  ndvi_mean  double precision,
  ndvi_std   double precision,
  n_valid    int,                                      -- valid (unmasked) 10 m pixels
  n_total    int,                                      -- pixels in the zone
  source_sha256 text NOT NULL,
  PRIMARY KEY (block, date)
);
