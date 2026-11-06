"""Command line: `aridtrial demo` runs the whole stack; individual stages are available for debugging."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .config import TrialConfig, data_dir, default_dsn, outputs_dir


def _engine(args):
    from .pipeline.db import get_engine

    return get_engine(args.dsn or default_dsn())


def cmd_simulate(args):
    from .simulate.builder import build

    rean, ndvi = _external_inputs(args)
    r = build(TrialConfig(), reanalysis_csv=rean, ndvi_blocks_csv=ndvi,
              source_contract_path=getattr(args, "source_contract", None))
    print(f"synthetic data written to {r['out']}  (real weather: {bool(rean)}, real NDVI background: {bool(ndvi)})")


def _external_dir():
    d = data_dir() / "external"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _external_inputs(args):
    """Resolve explicit real-context inputs and validate them against the source contract."""
    def pick(val, default_name, label):
        if val in (None, False):
            return None
        p = _external_dir() / default_name if val is True else Path(val)
        if not p.exists():
            raise SystemExit(f"{label} file {p} not found - fetch it first (needs internet / credentials)")
        return p

    real = getattr(args, "real", False)
    rean = pick(True if real else getattr(args, "reanalysis", None), "reanalysis_daily.csv", "weather")
    ndvi = pick(True if real else getattr(args, "real_ndvi", None), "ndvi_blocks_s2.csv", "NDVI")
    from .preparation.source_selection import load_source_contract, validate_source_selection

    contract, _, _ = load_source_contract(getattr(args, "source_contract", None))
    selected = validate_source_selection(contract, reanalysis_csv=rean, ndvi_blocks_csv=ndvi)
    print("source selection:", json.dumps(selected, sort_keys=True))
    if ndvi:
        from .simulate.ndvi_background import summary

        sm = summary(ndvi)
        print(f"real NDVI: {sm['dates']} dates, median {sm['median']:.3f} (5-95 %: {sm['p05']:.3f}-{sm['p95']:.3f})")
        if not sm["looks_bare"]:
            print("WARNING: median NDVI >= 0.15 - the sampled area is probably not bare desert (vegetation/crops/water). "
                  "Choose another area (aridtrial fetch-ndvi --lon --lat) before using it as the plot background.")
    return rean, ndvi


def cmd_fetch_kobo(args):
    """Download real KoboToolbox submissions without modifying synthetic inputs."""
    import os

    from .sources.kobo import KoboClient
    from .simulate.kobo import SOIL_FORM_ID, TREE_FORM_ID

    soil_asset = args.soil_asset or os.environ.get("KOBOTOOLBOX_SOIL_ASSET")
    tree_asset = args.tree_asset or os.environ.get("KOBOTOOLBOX_TREE_ASSET")
    if not soil_asset or not tree_asset:
        raise SystemExit("provide --soil-asset and --tree-asset or set KOBOTOOLBOX_SOIL_ASSET / KOBOTOOLBOX_TREE_ASSET")
    client = KoboClient(server=args.server, timeout=args.timeout)
    root = _external_dir() / "kobo"
    for asset_uid, form_id in ((soil_asset, SOIL_FORM_ID), (tree_asset, TREE_FORM_ID)):
        result = client.download_asset(asset_uid, form_id, root, page_size=args.page_size)
        print(f"[{form_id}] {result['submission_count']} submissions in {result['page_count']} pages -> {result['directory']}")


def cmd_prepare_kobo(args):
    """Normalise downloaded real Kobo payloads into separate prepared CSV files."""
    from .preparation.kobo import prepare_kobo_downloads

    result = prepare_kobo_downloads(args.soil_payload, args.tree_payload, args.output_dir or data_dir() / "prepared" / "kobo")
    print(json.dumps(result, indent=2))


def cmd_fetch_weather(args):
    """Download ERA5-Land daily weather for the trial period (needs internet; cached; provenance saved).
    --source openmeteo (default, no account) or cds (Copernicus Climate Data Store, needs ~/.cdsapirc)."""
    cfg = TrialConfig()
    dates = pd.to_datetime(cfg.dates)
    start, end = dates.min().date(), dates.max().date()
    if args.source == "cds":
        from .sources import cds

        daily, meta = cds.fetch_daily_weather(cfg.site_lat, cfg.site_lon, start, end, cache_dir=_external_dir() / "cache" / "cds")
    else:
        from .sources import openmeteo

        daily, meta = openmeteo.fetch_daily_weather(cfg.site_lat, cfg.site_lon, start, end, cache_dir=_external_dir() / "cache")
    f = _external_dir() / "reanalysis_daily.csv"
    daily.to_csv(f, index=False)
    (_external_dir() / "reanalysis_provenance.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    na = int(daily[["tmax", "tmin", "rh_max", "rh_min", "wind_2m", "rs_mj"]].isna().any(axis=1).sum())
    print(f"[{args.source}] {len(daily)} days ({na} with missing values) -> {f}  grid {meta.get('grid')}")


def _zones_wgs84(rings, epsg):
    from pyproj import Transformer

    t = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    return {b: [tuple(round(v, 5) for v in t.transform(x, y)) for x, y in r[:4]] for b, r in rings.items()}


def cmd_fetch_ndvi(args):
    """Block-level Sentinel-2 NDVI (CDSE, needs CDSE_CLIENT_ID / CDSE_CLIENT_SECRET). The six block zones are laid out at
    --lon/--lat (default: the trial site). Pick bare desert: check the printed corners in the Copernicus Browser first."""
    import dataclasses

    from .design.layout import make_layout
    from .design.pixel_purity import block_zone_polygons
    from .sources import copernicus

    cfg = TrialConfig()
    if args.lon is not None and args.lat is not None:
        cfg = dataclasses.replace(cfg, site_lon=args.lon, site_lat=args.lat)
    rings = block_zone_polygons(make_layout(cfg))
    print(f"area centre lon/lat {cfg.site_lon}, {cfg.site_lat}; block zone corners (lon, lat):")
    for b, c in _zones_wgs84(rings, cfg.utm_epsg).items():
        print(f"  block {b}: {c}")
    if args.print_zones:
        return
    dates = pd.to_datetime(cfg.dates)
    df, meta = copernicus.fetch_block_ndvi(rings, dates.min().date(), dates.max().date(), cache_dir=_external_dir() / "cache" / "sentinel2")
    f = _external_dir() / "ndvi_blocks_s2.csv"
    df.to_csv(f, index=False)
    meta["area_centre"] = [cfg.site_lon, cfg.site_lat]
    (_external_dir() / "ndvi_provenance.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    print(f"{len(df)} block-days with >= {meta['min_valid']} valid pixels -> {f}")


def cmd_initdb(args):
    from .pipeline import db

    eng = _engine(args)
    if args.reset:
        db.reset_database(eng)
    db.apply_schema(eng, with_postgis=args.postgis)
    print("schema applied" + (" (with PostGIS)" if args.postgis else ""))


def cmd_ingest(args):
    from .pipeline import ingest

    out = ingest.ingest_all(_engine(args), data_dir(), TrialConfig())
    print(json.dumps(out, indent=1, default=str))


def cmd_qc(args):
    from .qc.run import evaluate_against_injected, run_qc

    eng = _engine(args)
    r = run_qc(eng, TrialConfig(), as_of=date.fromisoformat(args.as_of) if args.as_of else None)
    print("pass rate:", r["summary"]["pass_rate"])
    f = data_dir() / "truth" / "injected_faults.csv"
    if f.exists():
        print(evaluate_against_injected(eng, pd.read_csv(f), r["run_id"]).to_string(index=False))


def cmd_reconcile(args):
    from .qc.reconciliation import run_reconciliation

    result = run_reconciliation(_engine(args), TrialConfig())
    print(json.dumps({"run_id": result["run_id"], **result["summary"]}, indent=2, default=str))
    if len(result["results"]):
        cols = ["dataset", "check_id", "severity", "source_count", "target_count", "difference"]
        print(result["results"][cols].to_string(index=False))


def cmd_dashboard(args):
    """Serve the read-only Data Quality dashboard and JSON API."""
    if args.dsn:
        import os
        os.environ["ARIDTRIAL_DSN"] = args.dsn
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Dashboard dependencies are missing. Install with: pip install -e '.[dashboard]'") from exc
    uvicorn.run("aridtrial.dashboard.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_version(args):
    from .pipeline import audit

    eng = _engine(args)
    v = audit.create_dataset_version(eng, args.label)
    print(v["version_id"], audit.verify_dataset_version(eng, v["version_id"]))
    print("audit chain:", audit.verify_chain(eng))


def cmd_analyze(args):
    from .analysis.run import run_analysis
    from .report.build import build_report

    eng = _engine(args)
    R = run_analysis(eng, TrialConfig(), quick=args.quick)
    from .analysis.scientific import run_and_persist
    print("scientific results run:", run_and_persist(eng, R, TrialConfig(), quick=args.quick))
    path = build_report(eng, R, TrialConfig(), outputs_dir())
    print("report:", path)


def cmd_demo(args):
    """Full run from nothing: simulate -> DB -> ingest -> QC -> freeze version -> analysis -> report."""
    from .analysis.run import run_analysis
    from .pipeline import audit, db, ingest
    from .qc.run import evaluate_against_injected, run_qc
    from .qc.reconciliation import run_reconciliation
    from .report.build import build_report
    from .simulate.builder import build

    cfg = TrialConfig()
    print("[1/8] simulating synthetic trial data ...")
    rean, ndvi = _external_inputs(args)
    build(cfg, reanalysis_csv=rean, ndvi_blocks_csv=ndvi,
          source_contract_path=getattr(args, "source_contract", None))
    eng = _engine(args)
    print("[2/7] resetting database and applying schema ...")
    db.reset_database(eng)
    db.apply_schema(eng, with_postgis=args.postgis)
    print("[3/7] ingesting ...")
    ingest.ingest_all(eng, data_dir(), cfg)
    print("[4/8] QC ...")
    qc = run_qc(eng, cfg, as_of=date.fromisoformat(args.as_of))
    print("[5/8] data reconciliation ...")
    reconciliation = run_reconciliation(eng, cfg, qc["run_id"])
    print(f"      reconciliation: {reconciliation['summary']['status']} "
          f"({reconciliation['summary']['failed']} failed, {reconciliation['summary']['warnings']} warnings)")
    recall = evaluate_against_injected(eng, pd.read_csv(data_dir() / "truth" / "injected_faults.csv"), qc["run_id"])
    print(f"      pass rates: {qc['summary']['pass_rate']}")
    print("[6/8] freezing dataset version ...")
    v = audit.create_dataset_version(eng, "endpoint-review")
    ok = audit.verify_dataset_version(eng, v["version_id"])
    print(f"      {v['version_id']} verify={ok['ok']}  chain={audit.verify_chain(eng)['ok']}")
    print("[7/8] analysis ...")
    R = run_analysis(eng, cfg, quick=args.quick)
    R["qc_recall"] = recall
    R["qc_summary"] = qc["summary"]
    R["reconciliation_summary"] = reconciliation["summary"]
    R["dataset_version"] = v
    from .analysis.scientific import run_and_persist
    print("      scientific results persisted:", run_and_persist(eng, R, cfg, quick=args.quick))
    print("[8/8] report ...")
    path = build_report(eng, R, cfg, outputs_dir())
    print("done ->", path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aridtrial", description=__doc__)
    ap.add_argument("--dsn", default=None, help="SQLAlchemy URL (default: $ARIDTRIAL_DSN)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("simulate"); p.add_argument("--source-contract", default=None, help="YAML source-selection contract"); p.add_argument("--real", action="store_true", help="use data/external/reanalysis_daily.csv and ndvi_blocks_s2.csv"); p.add_argument("--reanalysis", nargs="?", const=True, default=None); p.add_argument("--real-ndvi", nargs="?", const=True, default=None); p.set_defaults(fn=cmd_simulate)
    p = sub.add_parser("fetch-kobo"); p.add_argument("--server", default="https://kf.kobotoolbox.org"); p.add_argument("--soil-asset", default=None); p.add_argument("--tree-asset", default=None); p.add_argument("--page-size", type=int, default=1000); p.add_argument("--timeout", type=int, default=60); p.set_defaults(fn=cmd_fetch_kobo)
    p = sub.add_parser("prepare-kobo"); p.add_argument("--soil-payload", required=True); p.add_argument("--tree-payload", required=True); p.add_argument("--output-dir", default=None); p.set_defaults(fn=cmd_prepare_kobo)
    p = sub.add_parser("fetch-weather"); p.add_argument("--source", choices=["openmeteo", "cds"], default="openmeteo"); p.set_defaults(fn=cmd_fetch_weather)
    p = sub.add_parser("fetch-ndvi"); p.add_argument("--lon", type=float, default=None); p.add_argument("--lat", type=float, default=None); p.add_argument("--print-zones", action="store_true"); p.set_defaults(fn=cmd_fetch_ndvi)
    p = sub.add_parser("init-db"); p.add_argument("--reset", action="store_true"); p.add_argument("--postgis", action="store_true"); p.set_defaults(fn=cmd_initdb)
    sub.add_parser("ingest").set_defaults(fn=cmd_ingest)
    p = sub.add_parser("qc"); p.add_argument("--as-of", default=None); p.set_defaults(fn=cmd_qc)
    sub.add_parser("reconcile", help="reconcile raw, core and QC-filtered data layers").set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("dashboard", help="serve the read-only Data Quality dashboard"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.add_argument("--reload", action="store_true"); p.set_defaults(fn=cmd_dashboard)
    p = sub.add_parser("version"); p.add_argument("--label", default=None); p.set_defaults(fn=cmd_version)
    p = sub.add_parser("analyze"); p.add_argument("--quick", action="store_true"); p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("demo"); p.add_argument("--source-contract", default=None, help="YAML source-selection contract"); p.add_argument("--quick", action="store_true"); p.add_argument("--real", action="store_true"); p.add_argument("--reanalysis", nargs="?", const=True, default=None); p.add_argument("--real-ndvi", nargs="?", const=True, default=None); p.add_argument("--postgis", action="store_true")
    p.add_argument("--as-of", default="2026-10-27", help="run date for QC (trial endpoint must be before it)"); p.set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
