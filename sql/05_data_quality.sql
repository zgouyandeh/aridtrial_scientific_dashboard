-- Additive Data Quality & Reconciliation ledger. Raw and core data remain immutable/read-only here.
CREATE TABLE IF NOT EXISTS audit.reconciliation_run (
  run_id      bigserial PRIMARY KEY,
  started_at  timestamptz NOT NULL DEFAULT now(),
  ruleset     text NOT NULL,
  qc_run_id   bigint REFERENCES audit.qc_run(run_id),
  summary     jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS audit.reconciliation_result (
  id            bigserial PRIMARY KEY,
  run_id        bigint NOT NULL REFERENCES audit.reconciliation_run(run_id),
  dataset       text NOT NULL,
  check_id      text NOT NULL,
  severity      text NOT NULL CHECK (severity IN ('pass','warn','fail')),
  source_count  bigint,
  target_count  bigint,
  difference    bigint,
  details       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS reconciliation_result_lookup
  ON audit.reconciliation_result (run_id, severity, dataset);

DROP TRIGGER IF EXISTS no_mutation ON audit.reconciliation_run;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON audit.reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();
DROP TRIGGER IF EXISTS no_mutation ON audit.reconciliation_result;
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON audit.reconciliation_result
  FOR EACH ROW EXECUTE FUNCTION audit.deny_mutation();
