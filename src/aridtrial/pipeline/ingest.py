"""Ingestion orchestration: design -> raw landing -> core build -> QC -> dataset version.

Idempotency contract: running any step twice with the same inputs changes nothing and logs
the skipped work. Raw is append-only; core is a deterministic function of raw + the loaded drops.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import TrialConfig
from ..models import fao56
from . import audit
from .db import copy_df, read_sql
from .kobo_client import envelope, load_mock, normalise_soil, normalise_trees, resolve_current
from ..simulate.kobo import SOIL_FORM_ID, TREE_FORM_ID


class IngestError(RuntimeError):
    pass



def register_source_contract(engine: Engine, data_dir: Path) -> dict:
    """Validate the generated input manifest and persist its source contract in audit."""
    manifest_path = Path(data_dir) / "input_manifest.json"
    if not manifest_path.exists():
        raise IngestError("input_manifest.json is required; regenerate inputs with the source-selection contract")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selection = manifest.get("source_selection")
    if manifest.get("manifest_version", 0) < 2 or not selection:
        raise IngestError("input manifest is missing source-selection contract metadata; regenerate inputs")
    contract = selection.get("contract", {})
    contract_sha = selection.get("contract_sha256")
    selected = selection.get("selected_sources", {})
    if not contract_sha or not selected:
        raise IngestError("source-selection contract metadata is incomplete")
    if set(selected) != set(contract.get("datasets", {})):
        raise IngestError("source-selection manifest and contract define different dataset sets")
    for dataset, source_type in selected.items():
        allowed = contract["datasets"][dataset].get("allowed_sources", [])
        if source_type not in allowed:
            raise IngestError(f"selected source {source_type!r} is not allowed for {dataset}")
    ground_truth_spec = contract.get("datasets", {}).get("ground_truth", {})
    if selected.get("ground_truth") != "synthetic_validation_only" or ground_truth_spec.get("ingest") is not False or ground_truth_spec.get("ingest_target") is not None:
        raise IngestError("ground_truth must be validation-only and have no ingestion target")
    if selected.get("soil_samples") == "mixed" or selected.get("tree_observations") == "mixed":
        raise IngestError("mixed real/synthetic Kobo sources are not allowed")
    manifest_sha = audit.sha256_text(audit.canonical(manifest))
    specs = contract.get("datasets", {})
    generated = manifest.get("generated_datasets", {})
    real_inputs = manifest.get("real_context_inputs", {})
    registered = 0
    with engine.begin() as conn:
        for dataset, source_type in sorted(selected.items()):
            spec = specs.get(dataset, {})
            generated_key = "soil_and_tree_kobo_submissions" if dataset in {"soil_samples", "tree_observations"} else dataset
            generated_record = generated.get(generated_key, {})
            real_record = real_inputs.get(dataset, {})
            source_name = (real_record.get("source_name") or generated_record.get("generator")
                           or spec.get("selected_source") or dataset)
            if source_type == "absent":
                source_name = "absent"
            details = {
                "run_profile": selection.get("run_profile"),
                "manifest_version": manifest.get("manifest_version"),
                "manifest_file": manifest_path.name,
                "selected_source": source_type,
            }
            result = conn.execute(text("""
                INSERT INTO audit.dataset_source
                    (manifest_sha256, dataset, source_type, source_name, contract_sha256, ingest_target, role, details)
                VALUES (:manifest, :dataset, :source_type, :source_name, :contract, :target, :role, CAST(:details AS jsonb))
                ON CONFLICT (manifest_sha256, dataset) DO NOTHING
            """), dict(manifest=manifest_sha, dataset=dataset, source_type=source_type,
                      source_name=source_name, contract=contract_sha,
                      target=spec.get("ingest_target"), role=spec.get("role"),
                      details=audit.canonical(details)))
            registered += result.rowcount
    audit.append(engine, "register_source_contract", dataset="source_selection",
                 source=manifest_path.name, content_sha256=manifest_sha,
                 details={"contract_sha256": contract_sha, "datasets": len(selected),
                          "new_registrations": registered})
    return {"manifest_sha256": manifest_sha, "contract_sha256": contract_sha,
            "datasets": len(selected), "registered": registered,
            "selected_sources": selected, "dataset_specs": specs,
            "generated_datasets": generated, "real_context_inputs": real_inputs}

# ----------------------------------------------------------------------------- design


def load_design(engine: Engine, layout_csv: Path, record_json: Path, *, source_type: str = "synthetic",
                source_name: str = "design.layout.make_layout", source_dataset: str = "plot_layout",
                manifest_sha256: str | None = None, contract_sha256: str | None = None) -> int:
    layout_csv = Path(layout_csv)
    layout = pd.read_csv(layout_csv)
    record = json.loads(Path(record_json).read_text(encoding="utf-8"))
    source_sha256 = audit.sha256_bytes(layout_csv.read_bytes())
    layout["source_sha256"] = source_sha256
    layout["source_type"] = source_type
    layout["source_name"] = source_name
    layout["source_dataset"] = source_dataset
    layout["manifest_sha256"] = manifest_sha256
    layout["contract_sha256"] = contract_sha256
    cols = ["plot_id", "block", "position", "treatment", "easting", "northing", "lon", "lat", "area_m2", "n_trees", "geom_wkt", "srid",
            "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"]
    n = copy_df(engine, layout, "core.plot", cols, on_conflict_nothing=True)
    with engine.begin() as c:
        c.execute(text("INSERT INTO audit.randomisation (layout_sha256, record) VALUES (:h, CAST(:r AS jsonb)) ON CONFLICT DO NOTHING"),
                  dict(h=record["layout_sha256"], r=json.dumps(record)))
        c.execute(text("""INSERT INTO raw.file_drop
                          (sha256, dataset, file_name, n_rows, source_type, source_name, source_dataset, manifest_sha256, contract_sha256)
                          VALUES (:sha, 'plot_layout', :name, :n, :st, :sn, :sd, :mh, :ch) ON CONFLICT DO NOTHING"""),
                  dict(sha=source_sha256, name=layout_csv.name, n=len(layout), st=source_type,
                       sn=source_name, sd=source_dataset, mh=manifest_sha256, ch=contract_sha256))
    audit.append(engine, "load_design", dataset="design", source=source_name, n_rows=len(layout), content_sha256=source_sha256,
                 details=dict(new_rows=n, seed=record["seed"], randomisation_sha256=record["layout_sha256"],
                              source_type=source_type, source_dataset=source_dataset,
                              manifest_sha256=manifest_sha256, contract_sha256=contract_sha256))
    return n


# ----------------------------------------------------------------------------- Kobo


def ingest_kobo(engine: Engine, form_id: str, submissions: list[dict], source: str = "api", *,
                source_type: str = "real", source_name: str | None = None, source_dataset: str | None = None,
                manifest_sha256: str | None = None, contract_sha256: str | None = None) -> dict:
    """Land immutable Kobo payloads with explicit source lineage."""
    source_name = source_name or source
    source_dataset = source_dataset or form_id
    existing_sources = read_sql(engine, """SELECT DISTINCT
        CASE WHEN source_type = 'unknown' AND source LIKE 'mock:%' THEN 'synthetic' ELSE source_type END AS source_type,
        CASE WHEN source_type = 'unknown' AND source LIKE 'mock:%' THEN 'simulate.kobo.make_submissions' ELSE source_name END AS source_name,
        (source_type = 'unknown') AS is_legacy
        FROM raw.kobo_submission WHERE form_id = :f""", f=form_id)
    if not existing_sources.empty:
        for prior in existing_sources.itertuples():
            if prior.source_type != source_type or (not prior.is_legacy and prior.source_name != source_name):
                raise IngestError(f"Kobo form {form_id} already contains another source identity; explicit source migration is required")
    env = [dict(envelope(s, form_id), payload=s) for s in submissions]
    new = 0
    with engine.begin() as c:
        for e in env:
            res = c.execute(text("""INSERT INTO raw.kobo_submission
                                    (form_id, root_uuid, instance_uuid, payload, payload_sha256, submitted_at, source,
                                     source_type, source_name, source_dataset, manifest_sha256, contract_sha256)
                                    VALUES (:form_id, :root_uuid, :instance_uuid, CAST(:payload AS jsonb), :payload_sha256,
                                            :submitted_at, :source, :source_type, :source_name, :source_dataset, :manifest_sha256, :contract_sha256)
                                    ON CONFLICT DO NOTHING"""),
                            dict(form_id=e["form_id"], root_uuid=e["root_uuid"], instance_uuid=e["instance_uuid"],
                                 payload=audit.canonical(e["payload"]), payload_sha256=e["payload_sha256"],
                                 submitted_at=e["submitted_at"].to_pydatetime(), source=source,
                                 source_type=source_type, source_name=source_name, source_dataset=source_dataset,
                                 manifest_sha256=manifest_sha256, contract_sha256=contract_sha256))
            new += res.rowcount
    digest = audit.sha256_text("".join(sorted(e["payload_sha256"] for e in env)))
    audit.append(engine, "ingest_kobo", dataset=form_id, source=source, n_rows=new, content_sha256=digest,
                 details=dict(received=len(env), new=new, already_present=len(env) - new,
                              source_type=source_type, source_name=source_name,
                              source_dataset=source_dataset, manifest_sha256=manifest_sha256,
                              contract_sha256=contract_sha256))
    return dict(received=len(env), new=new)


def _raw_rows(engine: Engine, form_id: str) -> list[dict]:
    df = read_sql(engine, """SELECT form_id, root_uuid, payload_sha256, submitted_at, payload,
                                CASE WHEN source_type = 'unknown' AND source LIKE 'mock:%' THEN 'synthetic' ELSE source_type END AS source_type,
                                CASE WHEN source_type = 'unknown' AND source LIKE 'mock:%' THEN 'simulate.kobo.make_submissions' ELSE source_name END AS source_name,
                                CASE WHEN source_dataset = 'unknown' THEN CASE WHEN form_id = :f THEN
                                  CASE WHEN form_id LIKE 'soil%' THEN 'soil_samples' ELSE 'tree_observations' END END
                                  ELSE source_dataset END AS source_dataset,
                                manifest_sha256, contract_sha256
                         FROM raw.kobo_submission WHERE form_id = :f""", f=form_id)
    return [dict(form_id=r.form_id, root_uuid=r.root_uuid, payload_sha256=r.payload_sha256,
                 submitted_at=r.submitted_at, payload=r.payload, source_type=r.source_type,
                 source_name=r.source_name, source_dataset=r.source_dataset,
                 manifest_sha256=r.manifest_sha256, contract_sha256=r.contract_sha256) for r in df.itertuples()]


def _quarantine(engine: Engine, dataset: str, rows: pd.DataFrame, key_fn, reason: str):
    if rows.empty:
        return 0
    with engine.begin() as c:
        for r in rows.itertuples():
            c.execute(text("""INSERT INTO audit.quarantine
                              (dataset, record_key, reason, payload, source_type, source_name, source_dataset,
                               source_sha256, manifest_sha256, contract_sha256)
                              VALUES (:d, :k, :r, CAST(:p AS jsonb), :st, :sn, :sd, :sh, :mh, :ch)
                              ON CONFLICT DO NOTHING"""),
                      dict(d=dataset, k=key_fn(r), r=reason,
                           p=json.dumps({"plot_id": r.plot_id, "campaign": getattr(r, "campaign", None)}),
                           st=getattr(r, "source_type", None), sn=getattr(r, "source_name", None),
                           sd=getattr(r, "source_dataset", None),
                           sh=getattr(r, "source_sha256", getattr(r, "payload_sha256", None)),
                           mh=getattr(r, "manifest_sha256", None), ch=getattr(r, "contract_sha256", None)))
    return len(rows)


def build_core_from_raw(engine: Engine) -> dict:
    """Rebuild core.soil_sample / core.tree_obs from raw (deterministic, idempotent)."""
    known = set(read_sql(engine, "SELECT plot_id FROM core.plot")["plot_id"])
    summary = {}

    soil_raw = _raw_rows(engine, SOIL_FORM_ID)
    soil_sources = {(r.get("source_type"), r.get("source_name")) for r in soil_raw}
    if len(soil_sources) > 1:
        raise IngestError("multiple Kobo source identities found for soil form; source migration requires an explicit raw/core migration")
    soil = normalise_soil(soil_raw)
    if not soil.empty:
        soil["source_type"] = soil_raw[0].get("source_type") or "unknown"
        soil["source_name"] = soil_raw[0].get("source_name") or "unknown"
        soil["source_dataset"] = soil["source_dataset"].fillna("soil_samples")
    bad = soil[~soil["plot_id"].isin(known)].drop_duplicates(["kobo_root_uuid", "payload_sha256"])
    summary["soil_quarantined"] = _quarantine(engine, "soil", bad, lambda r: f"{r.kobo_root_uuid}:{r.payload_sha256[:12]}", "unknown_plot_id")
    soil = soil[soil["plot_id"].isin(known)].copy()
    soil["sample_key"] = soil["plot_id"] + "|" + soil["campaign"] + "|" + soil["depth_top_cm"].astype(int).astype(str)
    soil = resolve_current(soil, "sample_key")
    soil["record_id"] = [audit.sha256_text(f"{f}|{r}|{p}|{i}") for f, r, p, i in zip(soil.form_id, soil.kobo_root_uuid, soil.payload_sha256, soil.layer_idx)]

    tree_raw = _raw_rows(engine, TREE_FORM_ID)
    tree_sources = {(r.get("source_type"), r.get("source_name")) for r in tree_raw}
    if len(tree_sources) > 1:
        raise IngestError("multiple Kobo source identities found for tree form; source migration requires an explicit raw/core migration")
    trees = normalise_trees(tree_raw)
    if not trees.empty:
        trees["source_type"] = tree_raw[0].get("source_type") or "unknown"
        trees["source_name"] = tree_raw[0].get("source_name") or "unknown"
        trees["source_dataset"] = trees["source_dataset"].fillna("tree_observations")
    badt = trees[~trees["plot_id"].isin(known)].drop_duplicates(["kobo_root_uuid", "payload_sha256"])
    summary["tree_quarantined"] = _quarantine(engine, "tree", badt, lambda r: f"{r.kobo_root_uuid}:{r.payload_sha256[:12]}", "unknown_plot_id")
    trees = trees[trees["plot_id"].isin(known)].copy()
    trees["obs_key"] = trees["plot_id"] + "|" + trees["campaign"] + "|" + trees["tree_no"].astype(int).astype(str)
    trees["tree_id"] = trees["plot_id"] + "-T" + trees["tree_no"].astype(int).map("{:02d}".format)
    trees = resolve_current(trees, "obs_key")
    trees["record_id"] = [audit.sha256_text(f"{f}|{r}|{p}|{i}") for f, r, p, i in zip(trees.form_id, trees.kobo_root_uuid, trees.payload_sha256, trees.layer_idx)]

    with engine.begin() as c:
        c.execute(text("TRUNCATE core.soil_sample, core.tree_obs"))
    soil_cols = ["record_id", "sample_key", "plot_id", "campaign", "sample_date", "depth_top_cm", "depth_bottom_cm", "soc_pct", "ec_dsm", "ph",
                 "bulk_density", "collector", "gps_lon", "gps_lat", "status", "superseded_reason", "kobo_root_uuid", "payload_sha256", "submitted_at", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"]
    tree_cols = ["record_id", "obs_key", "tree_id", "plot_id", "campaign", "obs_date", "tree_no", "alive", "height_cm", "stem_diam_mm", "gps_lon",
                 "gps_lat", "status", "kobo_root_uuid", "payload_sha256", "submitted_at", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"]
    soil["depth_top_cm"] = soil["depth_top_cm"].astype(int)
    soil["depth_bottom_cm"] = soil["depth_bottom_cm"].astype("Int64")  # integer column: avoid "15.0" in COPY
    trees["tree_no"] = trees["tree_no"].astype("Int64")
    summary["soil_rows"] = copy_df(engine, soil, "core.soil_sample", soil_cols)
    summary["soil_superseded"] = int((soil["status"] == "superseded").sum())
    summary["tree_rows"] = copy_df(engine, trees, "core.tree_obs", tree_cols)
    summary["tree_superseded"] = int((trees["status"] == "superseded").sum())
    audit.append(engine, "build_core", dataset="kobo", n_rows=summary["soil_rows"] + summary["tree_rows"], details=summary)
    return summary


# ----------------------------------------------------------------------------- file drops

REQUIRED = {
    "sensors": ["ts", "plot_id", "vwc", "soil_temp_c"],
    "weather": ["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm"],
    "meters": ["date", "plot_id", "cum_volume_l"],
    "ndvi": ["date", "plot_id", "ndvi", "cloud_frac", "scene_id"],
    "reanalysis": ["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm", "et0_openmeteo_mm"],
    "ndvi_block": ["block", "date", "ndvi_mean", "ndvi_std", "n_valid", "n_total"],
}


def ingest_drop(engine: Engine, dataset: str, path: Path, cfg: TrialConfig | None = None, *,
                source_type: str = "synthetic", source_name: str | None = None, source_dataset: str | None = None,
                manifest_sha256: str | None = None, contract_sha256: str | None = None) -> dict:
    cfg = cfg or TrialConfig()
    source_name = source_name or path.name
    source_dataset = source_dataset or dataset
    path = Path(path)
    sha = audit.sha256_bytes(path.read_bytes())
    with engine.connect() as c:
        prior = c.execute(text("""SELECT CASE WHEN source_type = 'unknown' THEN
                    CASE WHEN dataset IN ('reanalysis', 'ndvi_block') THEN 'real' WHEN dataset = 'weather' THEN 'synthetic_station' WHEN dataset = 'ndvi' THEN 'synthetic_plot' ELSE 'synthetic' END
                    ELSE source_type END AS source_type,
                    CASE WHEN source_type = 'unknown' THEN file_name ELSE source_name END AS source_name,
                    (source_type = 'unknown') AS is_legacy
                    FROM raw.file_drop WHERE sha256 = :s AND dataset = :d"""),
                          dict(s=sha, d=dataset)).fetchone()
        if prior:
            if prior.source_type != source_type or (not prior.is_legacy and prior.source_name != source_name):
                raise IngestError(f"{dataset} file hash already belongs to another source identity; explicit source migration is required")
            audit.append(engine, "skip_duplicate_file", dataset=dataset, source=source_name, content_sha256=sha,
                         details={"source_type": source_type, "source_name": source_name})
            return dict(skipped=True, rows=0, source_type=source_type, source_name=source_name,
                        source_dataset=source_dataset, manifest_sha256=manifest_sha256, contract_sha256=contract_sha256)
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED[dataset] if c not in df.columns]
    if missing:
        audit.append(engine, "reject_file", dataset=dataset, source=path.name, content_sha256=sha, details=dict(missing_columns=missing))
        raise IngestError(f"{path.name}: missing columns {missing}")
    n_in = len(df)
    df["source_sha256"] = sha
    df["source_type"] = source_type
    df["source_name"] = source_name
    df["source_dataset"] = source_dataset
    df["manifest_sha256"] = manifest_sha256
    df["contract_sha256"] = contract_sha256
    known = set(read_sql(engine, "SELECT plot_id FROM core.plot")["plot_id"])
    details = dict(rows_in=n_in)
    if "plot_id" in df.columns:
        unk = df[~df["plot_id"].isin(known)]
        if len(unk):
            _quarantine(engine, dataset, unk.drop_duplicates("plot_id"), lambda r: f"{sha[:12]}:{r.plot_id}", "unknown_plot_id")
        df = df[df["plot_id"].isin(known)].copy()
        details["unknown_plot_rows"] = int(len(unk))
    if dataset == "sensors":
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        before = len(df)
        df = df.drop_duplicates(["plot_id", "ts"], keep="first")
        details["duplicate_timestamps_dropped"] = before - len(df)
        n = copy_df(engine, df, "core.sensor_reading", ["plot_id", "ts", "vwc", "soil_temp_c", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    elif dataset == "weather":
        df["date"] = pd.to_datetime(df["date"])
        df["et0_mm"] = fao56.eto_from_frame(df.assign(date=df["date"]), cfg.site_lat, cfg.site_elevation_m).round(3)
        n = copy_df(engine, df, "core.weather_daily", ["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm", "et0_mm", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    elif dataset == "meters":
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["plot_id", "date"])
        df["volume_l"] = df.groupby("plot_id")["cum_volume_l"].diff()
        df.loc[df["volume_l"] < 0, "volume_l"] = np.nan  # register reset / meter replacement: daily volume unknown, never negative
        n = copy_df(engine, df, "core.water_meter", ["plot_id", "date", "cum_volume_l", "volume_l", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    elif dataset == "ndvi":
        df["date"] = pd.to_datetime(df["date"])
        n = copy_df(engine, df, "core.ndvi", ["plot_id", "date", "ndvi", "cloud_frac", "scene_id", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    elif dataset == "reanalysis":
        df["date"] = pd.to_datetime(df["date"])
        n = copy_df(engine, df, "core.weather_reanalysis", ["date", "tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj", "rain_mm", "et0_openmeteo_mm", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    elif dataset == "ndvi_block":
        df["date"] = pd.to_datetime(df["date"])
        for c_ in ("block", "n_valid", "n_total"):
            df[c_] = df[c_].astype("Int64")
        n = copy_df(engine, df, "core.ndvi_block", ["block", "date", "ndvi_mean", "ndvi_std", "n_valid", "n_total", "source_sha256", "source_type", "source_name", "source_dataset", "manifest_sha256", "contract_sha256"])
    else:
        raise IngestError(f"unknown dataset {dataset}")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO raw.file_drop (sha256, dataset, file_name, n_rows, source_type, source_name, source_dataset,
                                                   manifest_sha256, contract_sha256)
                          VALUES (:s,:d,:f,:n,:st,:sn,:sd,:mh,:ch) ON CONFLICT DO NOTHING"""),
                  dict(s=sha, d=dataset, f=path.name, n=n_in, st=source_type, sn=source_name,
                       sd=source_dataset, mh=manifest_sha256, ch=contract_sha256))
    details["rows_loaded"] = n
    details.update(source_type=source_type, source_name=source_name, source_dataset=source_dataset,
                   manifest_sha256=manifest_sha256, contract_sha256=contract_sha256)
    audit.append(engine, f"ingest_{dataset}", dataset=dataset, source=source_name, n_rows=n,
                 content_sha256=sha, details=details)
    return dict(skipped=False, rows=n, **details)


def ingest_all(engine: Engine, data_dir: Path, cfg: TrialConfig | None = None) -> dict:
    data_dir = Path(data_dir)
    source_contract = register_source_contract(engine, data_dir)
    out = {"source_contract": source_contract}
    selected = source_contract["selected_sources"]
    specs = source_contract["dataset_specs"]
    generated = source_contract.get("generated_datasets", {})
    real_inputs = source_contract.get("real_context_inputs", {})
    manifest_sha = source_contract["manifest_sha256"]
    contract_sha = source_contract["contract_sha256"]

    def lineage(dataset_key: str) -> dict:
        spec = specs.get(dataset_key, {})
        real_record = real_inputs.get(dataset_key, {})
        generated_key = "soil_and_tree_kobo_submissions" if dataset_key in {"soil_samples", "tree_observations"} else dataset_key
        generated_record = generated.get(generated_key, {})
        source_name = (real_record.get("source_name") or generated_record.get("generator")
                       or spec.get("selected_source") or dataset_key)
        if selected.get(dataset_key) == "absent":
            source_name = "absent"
        return {"source_type": selected.get(dataset_key, "unknown"),
                "source_name": source_name, "source_dataset": dataset_key,
                "manifest_sha256": manifest_sha, "contract_sha256": contract_sha}

    out["design"] = load_design(engine, data_dir / "drops" / "plot_layout.csv", data_dir / "truth" / "randomisation_record.json",
                               **lineage("plot_layout"))
    out["soil_raw"] = ingest_kobo(engine, SOIL_FORM_ID, load_mock(data_dir / "kobo" / "soil_sampling.json"),
                                  source="mock:soil_sampling.json", **lineage("soil_samples"))
    out["tree_raw"] = ingest_kobo(engine, TREE_FORM_ID, load_mock(data_dir / "kobo" / "tree_survey.json"),
                                  source="mock:tree_survey.json", **lineage("tree_observations"))
    for ds, f, key in [("weather", "weather_daily.csv", "station_weather"),
                       ("sensors", "sensors_hourly.csv", "sensor_readings"),
                       ("meters", "water_meters_daily.csv", "water_meter_readings"),
                       ("ndvi", "ndvi_sentinel2.csv", "plot_level_ndvi")]:
        out[ds] = ingest_drop(engine, ds, data_dir / "drops" / f, cfg, **lineage(key))
    for ds, f, key in [("reanalysis", "reanalysis_daily.csv", "weather_reanalysis"),
                       ("ndvi_block", "ndvi_blocks_s2.csv", "sentinel2_block_ndvi")]:
        optional_path = data_dir / "drops" / f
        selected_source = selected.get(key)
        if selected_source == "real" and not optional_path.is_file():
            raise IngestError(f"{key} is selected as real but its prepared file is missing: {optional_path}")
        if selected_source == "absent" and optional_path.exists():
            raise IngestError(f"{key} is selected as absent but a stale file exists: {optional_path}; regenerate the input package")
        if selected_source == "real":
            out[ds] = ingest_drop(engine, ds, optional_path, cfg, **lineage(key))
    out["core"] = build_core_from_raw(engine)
    return out
