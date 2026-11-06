-- Persisted analysis results (read by the Scientific & Agronomic dashboard).
-- Append-only: every analysis run is a new run_id; payloads are content-hashed. Nothing here feeds back into core/mart.
CREATE SCHEMA IF NOT EXISTS results;

CREATE TABLE IF NOT EXISTS results.analysis_run (
  run_id             text PRIMARY KEY,
  created_at         timestamptz NOT NULL DEFAULT now(),
  dataset_version_id text,
  qc_run_id          bigint,
  seed               bigint NOT NULL,
  data_origin        text   NOT NULL DEFAULT 'synthetic',
  quick              boolean NOT NULL DEFAULT false,
  settings           jsonb  NOT NULL DEFAULT '{}'::jsonb,
  software           jsonb  NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS results.analysis_artifact (
  run_id   text NOT NULL REFERENCES results.analysis_run(run_id) ON DELETE CASCADE,
  section  text NOT NULL,
  payload  jsonb NOT NULL,
  sha256   text NOT NULL,
  PRIMARY KEY (run_id, section)
);

CREATE OR REPLACE VIEW results.analysis_latest AS
SELECT r.* FROM results.analysis_run r ORDER BY r.created_at DESC, r.run_id DESC LIMIT 1;
