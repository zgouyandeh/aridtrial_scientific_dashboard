"""Run the QC ruleset against the database and persist the flags (audit.qc_run / audit.qc_result)."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import TrialConfig
from ..pipeline import audit
from ..pipeline.db import copy_df, read_sql
from . import reanalysis as rean_qc
from . import rules


def run_qc(engine: Engine, cfg: TrialConfig | None = None, as_of: date | None = None) -> dict:
    cfg = cfg or TrialConfig()
    as_of = as_of or date.today()
    plots = read_sql(engine, "SELECT * FROM core.plot")
    soil = read_sql(engine, "SELECT * FROM core.soil_sample WHERE status = 'current'")
    trees = read_sql(engine, "SELECT * FROM core.tree_obs WHERE status = 'current'")
    sensors = read_sql(engine, "SELECT plot_id, ts, vwc, soil_temp_c FROM core.sensor_reading")
    meters = read_sql(engine, "SELECT plot_id, date, cum_volume_l FROM core.water_meter")
    ndvi = read_sql(engine, "SELECT plot_id, date, ndvi, cloud_frac FROM core.ndvi")
    weather = read_sql(engine, "SELECT * FROM core.weather_daily")
    rean = read_sql(engine, "SELECT * FROM core.weather_reanalysis")

    parts = [
        rules.check_soil(soil, plots, cfg, as_of),
        rules.check_trees(trees, plots, cfg, as_of),
        rules.check_sensors(sensors),
        rules.check_meters(meters, float(plots["area_m2"].iloc[0])),
        rules.check_ndvi(ndvi),
        rules.check_weather(weather, cfg),
        rean_qc.check_weather_vs_reanalysis(weather, rean),
    ]
    res = pd.concat([p for p in parts if len(p)], ignore_index=True)
    res["value"] = pd.to_numeric(res["value"], errors="coerce")

    n_rows = dict(soil=len(soil), tree=len(trees), sensor=len(sensors), meter=len(meters), ndvi=len(ndvi), weather=len(weather))
    fails = res[res["severity"] == "fail"].groupby("dataset")["record_key"].nunique().to_dict()
    pass_rate = {d: round(1 - fails.get(d, 0) / n, 5) for d, n in n_rows.items()}
    by_rule = res.groupby(["dataset", "rule_id", "severity"]).size().reset_index(name="n")
    summary = dict(as_of=as_of.isoformat(), rows_checked=n_rows, pass_rate=pass_rate, flags=int(len(res)),
                   by_rule=by_rule.to_dict(orient="records"))

    with engine.begin() as c:
        run_id = c.execute(text("INSERT INTO audit.qc_run (ruleset, summary) VALUES (:r, CAST(:s AS jsonb)) RETURNING run_id"),
                           dict(r=rules.RULESET_VERSION, s=json.dumps(summary))).scalar()
    res.insert(0, "run_id", run_id)
    copy_df(engine, res, "audit.qc_result", ["run_id", "dataset", "record_key", "plot_id", "ts", "variable", "rule_id", "severity", "message", "value"])
    audit.append(engine, "qc_run", n_rows=int(len(res)), content_sha256=audit.sha256_text(audit.canonical(summary)),
                 details=dict(run_id=int(run_id), ruleset=rules.RULESET_VERSION, pass_rate=pass_rate))
    return dict(run_id=int(run_id), summary=summary, results=res)


def evaluate_against_injected(engine: Engine, faults: pd.DataFrame, run_id: int | None = None) -> pd.DataFrame:
    """Recall of the QC against the known injected faults (only possible because the data are synthetic)."""
    q = read_sql(engine, "SELECT dataset, record_key, plot_id, ts, variable, rule_id, severity FROM audit.qc_latest" if run_id is None else
                 "SELECT dataset, record_key, plot_id, ts, variable, rule_id, severity FROM audit.qc_result WHERE run_id = :r", **({} if run_id is None else {"r": run_id}))
    q_soil = set(q[q.dataset == "soil"]["record_key"])
    q_tree = set(q[q.dataset == "tree"]["record_key"])
    q_sensor = set(q[q.dataset == "sensor"]["record_key"])
    q_meter_plots = set(q[(q.dataset == "meter")]["plot_id"])
    q_weather = set(q[q.dataset == "weather"]["record_key"])
    rows = []
    for f in faults.itertuples():
        det, note = None, ""
        if f.dataset == "soil":
            if f.fault in ("duplicate_submission", "edited_submission"):
                det, note = True, "handled by versioning (superseded), not a flag"
            elif f.fault == "unknown_plot":
                det, note = True, "quarantined at ingest"
            else:
                det = f.key in q_soil if not f.key.endswith("|*") else any(k.startswith(f.key[:-1]) for k in q_soil)
        elif f.dataset == "tree":
            det = True if f.fault == "unknown_plot" else (f.key in q_tree)
        elif f.dataset == "sensor":
            if f.fault.startswith("gap_"):
                det = f.key in q_sensor
            elif f.fault == "duplicate_rows_40":
                det, note = True, "dropped at ingest"
            else:
                det = f.key in q_sensor
        elif f.dataset == "meter":
            det = f.key.split("|")[0] in q_meter_plots
        elif f.dataset == "weather" and f.fault == "rs_drift":
            d_ = q[(q.dataset == "weather") & (q.rule_id == "weather.vs_reanalysis_drift") & (q.variable == "rs_mj")]["record_key"]
            det, note = bool((d_ >= f.key).any()), "persistent offset vs reanalysis (warn)"
        elif f.dataset == "weather":
            det = f.key in q_weather
        rows.append(dict(dataset=f.dataset, fault=f.fault.split("_")[0] if f.fault.startswith(("flatline", "gap")) else f.fault, detected=bool(det), note=note))
    out = pd.DataFrame(rows)
    return out.groupby(["dataset", "fault"]).agg(n=("detected", "size"), detected=("detected", "sum")).reset_index().assign(recall=lambda d: d["detected"] / d["n"])
