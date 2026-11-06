"""Create a compact, auditable lineage manifest for a generated trial input package.

The manifest describes lineage without changing CSV schemas or blending source labels
into scientific columns. Real weather and satellite context remain distinguishable
from synthetic station, plot, and ground-truth data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import TrialConfig
from .source_selection import load_source_contract, validate_source_selection


def _file_record(path: Path, source_name: str, provenance_path: Path | None = None) -> dict:
    frame = pd.read_csv(path)
    record = {
        "source_type": "real", "source_name": source_name,
        "file": path.name, "source_path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "row_count": len(frame), "columns": list(frame.columns),
    }
    for column in ("date", "sample_date", "obs_date"):
        if column in frame.columns:
            dates = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not dates.empty:
                record["date_min"] = dates.min().date().isoformat()
                record["date_max"] = dates.max().date().isoformat()
                break
    if provenance_path and provenance_path.exists():
        record["source_provenance"] = json.loads(provenance_path.read_text(encoding="utf-8"))
    return record


def write_input_manifest(output_dir: Path, cfg: TrialConfig,
                         reanalysis_csv: Path | None = None,
                         ndvi_blocks_csv: Path | None = None,
                         source_contract_path: str | Path | None = None) -> Path:
    """Write dataset-level lineage for synthetic outputs and optional real context."""
    output_dir = Path(output_dir)
    contract, contract_sha256, contract_path = load_source_contract(source_contract_path)
    selected_sources = validate_source_selection(
        contract, reanalysis_csv=reanalysis_csv, ndvi_blocks_csv=ndvi_blocks_csv
    )
    real_inputs = {}
    if reanalysis_csv is not None:
        p = Path(reanalysis_csv)
        real_inputs["weather_reanalysis"] = _file_record(
            p, "era5_land_reanalysis", p.parent / "reanalysis_provenance.json")
    if ndvi_blocks_csv is not None:
        p = Path(ndvi_blocks_csv)
        real_inputs["sentinel2_block_ndvi"] = _file_record(
            p, "sentinel_2_l2a_block_ndvi", p.parent / "ndvi_provenance.json")

    kobo_root = output_dir / "external" / "kobo"
    kobo_snapshots = []
    if kobo_root.exists():
        for provenance_file in sorted(kobo_root.glob("*/**/provenance.json")):
            snapshot = json.loads(provenance_file.read_text(encoding="utf-8"))
            kobo_snapshots.append({"provenance_file": str(provenance_file.relative_to(output_dir)),
                                   "asset_uid": snapshot.get("asset_uid"),
                                   "form_id": snapshot.get("form_id"),
                                   "retrieved_at": snapshot.get("retrieved_at"),
                                   "submission_count": snapshot.get("submission_count")})
    if kobo_snapshots:
        real_inputs["kobo_submissions"] = kobo_snapshots

    generated_artifacts = []
    for artifact in sorted(output_dir.glob("drops/*")) + sorted(output_dir.glob("kobo/*")) + sorted(output_dir.glob("truth/*")):
        if artifact.is_file():
            item = {"file": str(artifact.relative_to(output_dir)),
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "size_bytes": artifact.stat().st_size}
            if artifact.suffix.lower() == ".csv":
                frame = pd.read_csv(artifact)
                item["row_count"] = len(frame)
                item["columns"] = list(frame.columns)
            generated_artifacts.append(item)

    manifest = {
        "manifest_version": 2,
        "source_selection": {
            "contract_version": contract["contract_version"],
            "contract_file": str(contract_path),
            "contract_sha256": contract_sha256,
            "run_profile": contract.get("run_profile"),
            "selected_sources": selected_sources,
            "contract": contract,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trial": {"site_name": cfg.site_name, "site_lat": cfg.site_lat, "site_lon": cfg.site_lon,
                  "trial_start": cfg.trial_start.isoformat(), "n_days": cfg.n_days,
                  "n_blocks": cfg.n_blocks, "n_plots": cfg.n_plots, "seed": cfg.seed},
        "real_context_inputs": real_inputs,
        "generated_artifacts": generated_artifacts,
        "generated_datasets": {
            "plot_layout": {"source_type": "synthetic", "generator": "design.layout.make_layout"},
            "soil_and_tree_kobo_submissions": {"source_type": "synthetic", "generator": "simulate.kobo.make_submissions"},
            "station_weather": {"source_type": "synthetic", "generator": "simulate.weather" if reanalysis_csv is None else "simulate.weather.station_from_reanalysis",
                                "derived_from": ["weather_reanalysis"] if reanalysis_csv is not None else []},
            "sensor_readings": {"source_type": "synthetic", "generator": "simulate.field + simulate.fault_injection"},
            "water_meter_readings": {"source_type": "synthetic", "generator": "simulate.field + simulate.fault_injection"},
            "plot_level_ndvi": {"source_type": "synthetic", "generator": "simulate.field",
                                "derived_from": ["sentinel2_block_ndvi"] if ndvi_blocks_csv is not None else []},
            "ground_truth": {"source_type": "synthetic_validation_only", "ingest": False},
        },
        "combination_policy": {
            "contract_sha256": contract_sha256,
            "weather": "Real ERA5-Land is meteorological forcing and an independent QC reference; station observations remain simulated.",
            "sentinel2": "Real block-level NDVI is background context; plot-level canopy contribution and noise remain simulated.",
            "kobo": "Real Kobo submissions are downloaded separately under data/external/kobo and are not merged with synthetic submissions by this build step.",
            "ground_truth": "Validation-only; never load into operational database tables.",
        },
    }
    path = output_dir / "input_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
