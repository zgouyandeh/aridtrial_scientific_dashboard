"""Load and validate the explicit source-selection contract for a trial run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONTRACT = Path(__file__).resolve().parents[3] / "config" / "source_selection.yaml"


class SourceSelectionError(ValueError):
    """Raised when selected sources violate the project data contract."""


def load_source_contract(path: str | Path | None = None) -> tuple[dict[str, Any], str, Path]:
    contract_path = Path(path) if path is not None else DEFAULT_CONTRACT
    if not contract_path.exists():
        raise SourceSelectionError(f"source-selection contract not found: {contract_path}")
    raw = contract_path.read_bytes()
    contract = yaml.safe_load(raw) or {}
    if contract.get("contract_version") != 1:
        raise SourceSelectionError("unsupported or missing contract_version")
    if not isinstance(contract.get("datasets"), dict):
        raise SourceSelectionError("contract must define a datasets mapping")
    return contract, hashlib.sha256(raw).hexdigest(), contract_path


def validate_source_selection(
    contract: dict[str, Any],
    *,
    reanalysis_csv: str | Path | None,
    ndvi_blocks_csv: str | Path | None,
) -> dict[str, str]:
    """Validate available inputs against the configured selection, without fallback."""
    datasets = contract["datasets"]
    selected = {name: spec.get("selected_source") for name, spec in datasets.items()}
    if selected.get("soil_samples") != "synthetic" or selected.get("tree_observations") != "synthetic":
        raise SourceSelectionError("contract v1 requires a single synthetic Kobo source; real Kobo remains prepared separately")
    if selected.get("station_weather") != "synthetic_station":
        raise SourceSelectionError("station_weather must remain synthetic_station in contract v1")
    if selected.get("plot_level_ndvi") != "synthetic_plot":
        raise SourceSelectionError("plot_level_ndvi must remain synthetic_plot in contract v1")
    if selected.get("ground_truth") != "synthetic_validation_only":
        raise SourceSelectionError("ground_truth must remain validation-only")
    if contract.get("combination_rules", {}).get("kobo", {}).get("allow_real_synthetic_union") is not False:
        raise SourceSelectionError("real and synthetic Kobo records cannot be unioned under contract v1")

    weather_present = reanalysis_csv is not None
    ndvi_present = ndvi_blocks_csv is not None
    if weather_present and not Path(reanalysis_csv).is_file():
        raise SourceSelectionError(f"selected weather reference file does not exist: {reanalysis_csv}")
    if ndvi_present and not Path(ndvi_blocks_csv).is_file():
        raise SourceSelectionError(f"selected Sentinel-2 reference file does not exist: {ndvi_blocks_csv}")

    weather_spec = datasets.get("weather_reanalysis", {})
    ndvi_spec = datasets.get("sentinel2_block_ndvi", {})
    if weather_present and "real" not in weather_spec.get("allowed_sources", []):
        raise SourceSelectionError("real weather input is not allowed by the source contract")
    if ndvi_present and "real" not in ndvi_spec.get("allowed_sources", []):
        raise SourceSelectionError("real Sentinel-2 input is not allowed by the source contract")
    if not weather_present and "absent" not in weather_spec.get("allowed_sources", []):
        raise SourceSelectionError("weather reference is required by the source contract but was not supplied")
    if not ndvi_present and "absent" not in ndvi_spec.get("allowed_sources", []):
        raise SourceSelectionError("Sentinel-2 reference is required by the source contract but was not supplied")

    return {
        "plot_layout": "synthetic",
        "weather_reanalysis": "real" if weather_present else "absent",
        "sentinel2_block_ndvi": "real" if ndvi_present else "absent",
        "soil_samples": "synthetic",
        "tree_observations": "synthetic",
        "sensor_readings": "synthetic",
        "water_meter_readings": "synthetic",
        "station_weather": "synthetic_station",
        "plot_level_ndvi": "synthetic_plot",
        "ground_truth": "synthetic_validation_only",
    }


def canonical_contract_json(contract: dict[str, Any]) -> str:
    return json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
