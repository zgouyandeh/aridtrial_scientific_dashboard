"""Integration tests: ingestion idempotency, raw immutability, audit chain, dataset versioning, QC recall."""
import json

import pandas as pd
import pytest
from sqlalchemy import text

from aridtrial.pipeline import audit, db, ingest
from aridtrial.pipeline.kobo_client import resolve_current


def test_row_counts_after_first_ingest(loaded):
    e = loaded["engine"]
    n = lambda t: db.read_sql(e, f"SELECT count(*) AS n FROM {t}").n[0]
    assert n("core.plot") == 24
    assert n("raw.kobo_submission") == 125 + 120
    assert n("core.weather_daily") == 365
    assert n("core.water_meter") == 24 * 365
    assert n("core.ndvi") == 24 * 73
    # injected duplicated timestamps must have been dropped, not loaded twice
    assert n("core.sensor_reading") == db.read_sql(e, "SELECT count(DISTINCT (plot_id, ts)) AS n FROM core.sensor_reading").n[0]


def test_unknown_plots_are_quarantined_not_loaded(loaded):
    e = loaded["engine"]
    q = db.read_sql(e, "SELECT dataset, count(*) n FROM audit.quarantine WHERE reason='unknown_plot_id' GROUP BY 1").set_index("dataset").n
    assert q["soil"] == 2 and q["tree"] == 1
    assert db.read_sql(e, "SELECT count(*) n FROM core.soil_sample WHERE plot_id NOT IN (SELECT plot_id FROM core.plot)").n[0] == 0


def test_resubmissions_and_edits_are_versioned_not_double_counted(loaded):
    e = loaded["engine"]
    cur = db.read_sql(e, "SELECT sample_key, count(*) n FROM core.soil_sample WHERE status='current' GROUP BY 1")
    assert (cur.n == 1).all()
    sup = db.read_sql(e, "SELECT superseded_reason, count(*) n FROM core.soil_sample WHERE status='superseded' GROUP BY 1").set_index("superseded_reason").n
    assert sup["edit_revision"] == 4 and sup["resubmission"] == 6  # 2 edits x 2 layers, 3 duplicates x 2 layers


def test_ingest_is_idempotent(loaded, built):
    e = loaded["engine"]
    before = db.read_sql(e, "SELECT (SELECT count(*) FROM raw.kobo_submission) a, (SELECT count(*) FROM raw.file_drop) b, "
                            "(SELECT count(*) FROM core.sensor_reading) c, (SELECT count(*) FROM core.soil_sample) d").iloc[0].tolist()
    again = ingest.ingest_all(e, built["out"], built["cfg"])
    after = db.read_sql(e, "SELECT (SELECT count(*) FROM raw.kobo_submission) a, (SELECT count(*) FROM raw.file_drop) b, "
                           "(SELECT count(*) FROM core.sensor_reading) c, (SELECT count(*) FROM core.soil_sample) d").iloc[0].tolist()
    assert before == after
    assert again["soil_raw"]["new"] == 0 and again["tree_raw"]["new"] == 0
    assert all(again[k]["skipped"] for k in ("weather", "sensors", "meters", "ndvi"))


@pytest.mark.parametrize("sql", [
    "UPDATE raw.kobo_submission SET source='x'",
    "DELETE FROM raw.kobo_submission",
    "UPDATE audit.ingest_log SET n_rows = 0",
    "DELETE FROM audit.ingest_log",
    "UPDATE raw.file_drop SET n_rows = 0",
])
def test_append_only_tables_reject_mutation(loaded, sql):
    with pytest.raises(Exception, match="append-only"):
        with loaded["engine"].begin() as c:
            c.execute(text(sql))


def test_audit_chain_verifies(loaded):
    v = audit.verify_chain(loaded["engine"])
    assert v["ok"] and v["n"] >= 8


def test_chain_detects_tampering_with_content_order_and_deletion():
    g = audit.GENESIS
    entries = [dict(ts="2026-01-01T00:00:00+00:00", actor="p", action=a, dataset=None, source=None, n_rows=i, content_sha256=None, details={}) for i, a in enumerate("abc")]

    def chain(es):
        prev, out = g, []
        for x in es:
            h = audit.entry_hash(prev, x)
            out.append((prev, h, x))
            prev = h
        return out

    def valid(ch):
        prev = g
        for p, h, x in ch:
            if p != prev or audit.entry_hash(prev, x) != h:
                return False
            prev = h
        return True

    ch = chain(entries)
    assert valid(ch)
    tampered = [list(c) for c in ch]
    tampered[1][2] = dict(tampered[1][2], n_rows=999)
    assert not valid([tuple(c) for c in tampered])
    assert not valid([ch[0], ch[2]])  # deletion
    assert not valid([ch[1], ch[0], ch[2]])  # reorder


def test_dataset_version_detects_later_change(loaded):
    e = loaded["engine"]
    v = audit.create_dataset_version(e, "test")
    assert audit.verify_dataset_version(e, v["version_id"])["ok"]
    with e.begin() as c:  # a change to core data after freezing (rolled back at the end of the test)
        c.execute(text("UPDATE core.soil_sample SET soc_pct = soc_pct + 0.01 WHERE status='current' AND sample_key = (SELECT min(sample_key) FROM core.soil_sample WHERE status='current')"))
    try:
        assert not audit.verify_dataset_version(e, v["version_id"])["ok"]
    finally:
        with e.begin() as c:
            c.execute(text("UPDATE core.soil_sample SET soc_pct = soc_pct - 0.01 WHERE status='current' AND sample_key = (SELECT min(sample_key) FROM core.soil_sample WHERE status='current')"))
    assert audit.verify_dataset_version(e, v["version_id"])["ok"]


def test_resolve_current_latest_wins_and_labels_reason():
    df = pd.DataFrame(dict(k=["a", "a", "a", "b"], kobo_root_uuid=["r1", "r1", "r2", "r3"],
                           payload_sha256=["h1", "h2", "h3", "h4"], submitted_at=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-01"], utc=True)))
    out = resolve_current(df, "k").set_index("payload_sha256")
    assert out.loc["h3", "status"] == "current" and out.loc["h4", "status"] == "current"
    assert out.loc["h1", "superseded_reason"] == "resubmission" and out.loc["h2", "superseded_reason"] == "resubmission"
    # edit revision: same rootUuid as the winner
    df2 = df.iloc[:2].copy()
    out2 = resolve_current(df2, "k").set_index("payload_sha256")
    assert out2.loc["h1", "superseded_reason"] == "edit_revision" and out2.loc["h2", "status"] == "current"


def test_qc_catches_injected_faults(loaded):
    from aridtrial.qc.run import evaluate_against_injected

    rec = evaluate_against_injected(loaded["engine"], loaded["faults"], loaded["qc"]["run_id"])
    overall = rec["detected"].sum() / rec["n"].sum()
    assert overall >= 0.99
    # every non-spike fault class is caught completely
    assert (rec[rec.fault != "spike"]["recall"] == 1.0).all()


def test_qc_does_not_flag_clean_data_much(loaded):
    """False-positive guard: flags on soil must be explained by injected faults (+ their duplicates)."""
    q = loaded["qc"]["results"]
    soil_fail_keys = set(q[(q.dataset == "soil") & (q.severity == "fail")].record_key)
    assert len(soil_fail_keys) <= 12  # 11 injected fail-type faults at most


def test_qc_flags_not_edits_marts_exclude_failed_values(loaded):
    e = loaded["engine"]
    raw_n = db.read_sql(e, "SELECT count(*) n FROM core.soil_sample WHERE status='current'").n[0]
    mart_n = db.read_sql(e, "SELECT count(*) n FROM mart.soil_clean").n[0]
    assert mart_n < raw_n  # some failed whole-records are excluded in the mart
    # ranges respected in the mart
    r = db.read_sql(e, "SELECT min(ph) lo, max(ph) hi, max(ec_dsm) ec, max(bulk_density) bd FROM mart.soil_clean")
    assert 4 <= r.lo[0] and r.hi[0] <= 11 and r.ec[0] <= 60 and r.bd[0] <= 2.0
    # core still holds the bad values (nothing was edited)
    assert db.read_sql(e, "SELECT max(ec_dsm) m FROM core.soil_sample").m[0] > 60


def test_dashboard_queries_execute(loaded):
    dash = json.loads((pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "trial_overview.json").read_text())
    n = 0
    for p in dash["panels"]:
        for t in p["targets"]:
            sql = t["rawSql"].replace('$__timeFilter("time")', "true").replace("$__timeFilter(date::timestamp)", "true")
            assert "$__" not in sql
            df = db.read_sql(loaded["engine"], sql)
            assert len(df) > 0 or p.get("description", "").startswith("Optional:"), p["title"]
            n += 1
    assert n == len(dash["panels"])


def test_source_provenance_is_preserved_from_raw_to_core(loaded):
    e = loaded["engine"]
    for table in ("core.plot", "core.soil_sample", "core.tree_obs", "core.sensor_reading",
                  "core.weather_daily", "core.water_meter", "core.ndvi"):
        row = db.read_sql(e, f"SELECT source_type, source_name, source_dataset, manifest_sha256, contract_sha256 FROM {table} LIMIT 1").iloc[0]
        assert row.source_type != "unknown"
        assert row.source_name
        assert row.source_dataset
        assert row.manifest_sha256 and len(row.manifest_sha256) == 64
        assert row.contract_sha256 and len(row.contract_sha256) == 64

    raw = db.read_sql(e, "SELECT source_type, source_name, source_dataset, manifest_sha256, contract_sha256 FROM raw.kobo_submission LIMIT 1").iloc[0]
    assert raw.source_type == "synthetic"
    assert raw.source_dataset in {"soil_samples", "tree_observations"}
    assert raw.manifest_sha256 and raw.contract_sha256

    dropped = db.read_sql(e, "SELECT source_type, source_name, source_dataset FROM raw.file_drop WHERE dataset='plot_layout'").iloc[0]
    assert dropped.source_type == "synthetic" and dropped.source_dataset == "plot_layout"

    quarantined = db.read_sql(e, "SELECT source_type, source_name, source_sha256 FROM audit.quarantine WHERE dataset='soil' LIMIT 1").iloc[0]
    assert quarantined.source_type == "synthetic" and quarantined.source_name
    assert quarantined.source_sha256


def test_kobo_source_migration_requires_explicit_action(loaded, built):
    from aridtrial.pipeline.ingest import IngestError, ingest_kobo
    from aridtrial.pipeline.kobo_client import load_mock
    from aridtrial.simulate.kobo import SOIL_FORM_ID

    submissions = load_mock(built["out"] / "kobo" / "soil_sampling.json")[:1]
    with pytest.raises(IngestError, match="explicit source migration"):
        ingest_kobo(loaded["engine"], SOIL_FORM_ID, submissions,
                    source="real:test", source_type="real", source_name="kobo_api")


def test_dataset_version_freezes_raw_and_core_lineage(loaded):
    v = audit.create_dataset_version(loaded["engine"], "lineage-test")
    assert "raw.kobo_submission" in v["manifest"]["tables"]
    assert "raw.file_drop" in v["manifest"]["tables"]
    assert "core.weather_daily" in v["manifest"]["tables"]
    assert "core.soil_sample" in v["manifest"]["tables"]
    assert audit.verify_dataset_version(loaded["engine"], v["version_id"])["ok"]


def test_reconciliation_compares_all_layers_without_changing_core(loaded):
    from aridtrial.qc.reconciliation import run_reconciliation

    e = loaded["engine"]
    before = db.read_sql(e, "SELECT count(*) n FROM core.soil_sample").n[0]
    result = run_reconciliation(e, loaded["cfg"], loaded["qc"]["run_id"])
    after = db.read_sql(e, "SELECT count(*) n FROM core.soil_sample").n[0]

    assert before == after
    assert result["summary"]["checks"] > 20
    checks = set(result["results"].check_id)
    assert "file_drop.ingest_matches_core" in checks
    assert "kobo.raw_submissions_represented" in checks
    assert "core_to_mart.row_count" in checks
    assert db.read_sql(e, "SELECT count(*) n FROM audit.reconciliation_result WHERE run_id=:r", r=result["run_id"]).n[0] == result["summary"]["checks"]


def test_meter_mart_keeps_one_row_per_meter_key_with_multiple_qc_flags(loaded):
    e = loaded["engine"]
    meter = db.read_sql(e, "SELECT plot_id, date FROM core.water_meter ORDER BY plot_id, date LIMIT 1").iloc[0]
    ts = pd.Timestamp(meter.date).tz_localize("UTC")
    with e.begin() as c:
        for rule_id in ("meter.test_flag_a", "meter.test_flag_b"):
            c.execute(text("""INSERT INTO audit.qc_result
                              (run_id, dataset, record_key, plot_id, ts, variable, rule_id, severity, message)
                              VALUES (:run_id, 'meter', :record_key, :plot_id, :ts, 'volume_l', :rule_id, 'fail', 'test')"""),
                      {"run_id": loaded["qc"]["run_id"], "record_key": f"{meter.plot_id}|{meter.date:%Y-%m-%d}",
                       "plot_id": meter.plot_id, "ts": ts.to_pydatetime(), "rule_id": rule_id})
    n_core = db.read_sql(e, "SELECT count(*) n FROM core.water_meter").n[0]
    n_mart = db.read_sql(e, "SELECT count(*) n FROM mart.meter_clean").n[0]
    n_keys = db.read_sql(e, "SELECT count(DISTINCT (plot_id, date)) n FROM mart.meter_clean").n[0]
    assert n_core == n_mart == n_keys
