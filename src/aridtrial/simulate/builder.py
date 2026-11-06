"""High-level orchestration for building the trial's input datasets.

This module coordinates the existing scientific generators. It does not contain
scientific formulas or data-quality fault logic; those remain in their dedicated
modules. The public ``build`` signature is kept compatible with the CLI and tests.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import TrialConfig, data_dir
from ..design.layout import make_layout
from ..preparation.manifest import write_input_manifest
from .artifacts import (
    create_output_directories,
    remove_optional_files_from_previous_run,
    save_ground_truth,
    save_input_drops,
    save_kobo_inputs,
    save_layout,
    save_real_reference_drops,
)
from .fault_injection import _inject_station_drift, _inject_timeseries_faults
from .field import simulate_field
from .ndvi_background import load_background
from .weather import simulate_weather, station_from_reanalysis, weather_from_reanalysis


def _load_real_weather(reanalysis_csv: Path, cfg: TrialConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (weather forcing, simulated station observations) from real reanalysis."""
    reanalysis = pd.read_csv(reanalysis_csv)
    weather_forcing = weather_from_reanalysis(cfg, reanalysis)
    station_observations = station_from_reanalysis(
        cfg, weather_forcing, np.random.default_rng(cfg.seed + 202)
    )
    return reanalysis, weather_forcing, station_observations


def _prepare_weather(cfg: TrialConfig, reanalysis_csv: Path | None):
    if reanalysis_csv is None:
        weather = simulate_weather(cfg)
        return None, weather, weather
    reanalysis, weather, station = _load_real_weather(reanalysis_csv, cfg)
    return reanalysis, weather, station


def _prepare_ndvi_background(ndvi_blocks_csv: Path | None, cfg: TrialConfig):
    if ndvi_blocks_csv is None:
        return None
    return load_background(ndvi_blocks_csv, cfg)


def build(
    cfg: TrialConfig | None = None,
    out: Path | None = None,
    reanalysis_csv: Path | None = None,
    ndvi_blocks_csv: Path | None = None,
    source_contract_path: Path | None = None,
) -> dict:
    """Generate a complete set of input files for the data-engineering pipeline.

    Optional real ERA5-Land data is used as the weather forcing while a synthetic
    on-site station series is generated from it. Optional real Sentinel-2 block
    NDVI is used as the background for synthetic plot-level NDVI. Ground-truth
    files are written separately and must never be ingested as operational data.
    """
    cfg = cfg or TrialConfig()
    output_dir = Path(out) if out is not None else data_dir()
    reanalysis_csv = Path(reanalysis_csv) if reanalysis_csv is not None else None
    ndvi_blocks_csv = Path(ndvi_blocks_csv) if ndvi_blocks_csv is not None else None

    # 1. Prepare a clean output location and deterministic random streams.
    create_output_directories(output_dir)
    remove_optional_files_from_previous_run(output_dir)
    rng = np.random.default_rng(cfg.seed + 303)

    # 2. Create the RCBD layout and prepare weather / satellite context.
    layout = make_layout(cfg)
    reanalysis, weather_forcing, station_weather = _prepare_weather(cfg, reanalysis_csv)
    ndvi_background = _prepare_ndvi_background(ndvi_blocks_csv, cfg)

    # 3. Generate the synthetic field datasets using the prepared context.
    simulation = simulate_field(
        cfg, layout, weather_forcing, ndvi_background=ndvi_background
    )

    # 4. Save design artifacts and create Kobo-compatible forms and submissions.
    save_layout(layout, cfg, output_dir)
    kobo_faults = save_kobo_inputs(layout, cfg, simulation, rng, output_dir)

    # 5. Inject controlled faults into the operational-style time-series inputs.
    sensors, meters, ndvi, station_weather, timeseries_faults, fault_metadata = (
        _inject_timeseries_faults(cfg, simulation, station_weather, rng)
    )
    if reanalysis is not None:
        station_weather, station_drift_faults = _inject_station_drift(cfg, station_weather)
        timeseries_faults = pd.concat(
            [timeseries_faults, station_drift_faults], ignore_index=True
        )

    # 6. Write input drops, optional real reference data and validation-only truth.
    save_input_drops(sensors, meters, ndvi, station_weather, output_dir)
    save_real_reference_drops(reanalysis, ndvi_blocks_csv, weather_forcing, output_dir)
    save_ground_truth(
        simulation, kobo_faults, timeseries_faults, fault_metadata, output_dir
    )
    write_input_manifest(
        output_dir, cfg, reanalysis_csv, ndvi_blocks_csv,
        source_contract_path=source_contract_path,
    )

    return {
        "layout": layout,
        "weather": station_weather,
        "sim": simulation,
        "out": output_dir,
    }
