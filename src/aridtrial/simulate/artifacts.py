"""Write generated input datasets and simulation-only ground truth to disk."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from ..config import TrialConfig
from ..design.layout import dump_record, plot_layout
from . import kobo


def create_output_directories(output_dir: Path) -> None:
    """Create the folders used by the current input-data contract."""
    for folder in ("kobo", "xlsform", "drops", "truth"):
        (output_dir / folder).mkdir(parents=True, exist_ok=True)


def remove_optional_files_from_previous_run(output_dir: Path) -> None:
    """Prevent optional real-data drops from leaking into a later synthetic-only run."""
    for filename in ("reanalysis_daily.csv", "ndvi_blocks_s2.csv"):
        (output_dir / "drops" / filename).unlink(missing_ok=True)


def save_layout(layout: pd.DataFrame, cfg: TrialConfig, output_dir: Path) -> None:
    """Save the plot layout and its reproducibility artifacts."""
    layout.to_csv(output_dir / "drops" / "plot_layout.csv", index=False)
    dump_record(cfg, layout, output_dir / "truth" / "randomisation_record.json")
    plot_layout(layout, output_dir / "truth" / "layout.png")


def save_kobo_inputs(layout: pd.DataFrame, cfg: TrialConfig, simulation: dict,
                     rng, output_dir: Path) -> pd.DataFrame:
    """Create XLSForms and simulated Kobo API-shaped submissions."""
    kobo.build_xlsforms(layout, output_dir / "xlsform")
    soil_submissions, tree_submissions, faults = kobo.make_submissions(cfg, simulation, rng)
    (output_dir / "kobo" / "soil_sampling.json").write_text(
        json.dumps(soil_submissions, indent=1), encoding="utf-8"
    )
    (output_dir / "kobo" / "tree_survey.json").write_text(
        json.dumps(tree_submissions, indent=1), encoding="utf-8"
    )
    return faults


def save_input_drops(sensors: pd.DataFrame, meters: pd.DataFrame, ndvi: pd.DataFrame,
                     weather: pd.DataFrame, output_dir: Path) -> None:
    """Save the operational-style CSV drops consumed by the ingestion pipeline."""
    drops_dir = output_dir / "drops"
    sensors.to_csv(drops_dir / "sensors_hourly.csv", index=False)
    # Store cumulative register readings, not the derived daily volume.
    meters[["date", "plot_id", "cum_volume_l"]].to_csv(
        drops_dir / "water_meters_daily.csv", index=False
    )
    ndvi.to_csv(drops_dir / "ndvi_sentinel2.csv", index=False)
    # ET0 is deliberately recomputed by the data-ingestion pipeline.
    weather.drop(columns=["et0_mm"]).to_csv(
        drops_dir / "weather_daily.csv", index=False
    )


def save_real_reference_drops(reanalysis: pd.DataFrame | None,
                              ndvi_blocks_csv: Path | None,
                              simulated_weather: pd.DataFrame,
                              output_dir: Path) -> None:
    """Copy optional real reference datasets into the generated input package."""
    drops_dir = output_dir / "drops"
    if reanalysis is not None:
        reference = reanalysis.assign(date=pd.to_datetime(reanalysis["date"]).dt.date)
        reference = reference[reference["date"].isin(set(simulated_weather["date"]))]
        reference.to_csv(drops_dir / "reanalysis_daily.csv", index=False)
    if ndvi_blocks_csv is not None:
        shutil.copy(ndvi_blocks_csv, drops_dir / "ndvi_blocks_s2.csv")


def save_ground_truth(simulation: dict, kobo_faults: pd.DataFrame,
                      timeseries_faults: pd.DataFrame, fault_metadata: dict,
                      output_dir: Path) -> None:
    """Persist truth and injected-fault records for validation only; never for ingestion."""
    truth_dir = output_dir / "truth"
    pd.concat([kobo_faults, timeseries_faults], ignore_index=True).to_csv(
        truth_dir / "injected_faults.csv", index=False
    )
    simulation["soil"].to_csv(truth_dir / "soil_truth.csv", index=False)
    simulation["tree_tbl"].to_csv(truth_dir / "tree_truth.csv", index=False)
    simulation["daily_truth"].to_csv(truth_dir / "daily_truth.csv", index=False)
    simulation["plots"].to_csv(truth_dir / "plot_truth.csv", index=False)

    truth = simulation["truth"]
    effects = {
        "soc_effect_end_pct": truth.soc_effect_end,
        "ec_effect_end_dsm": truth.ec_effect_end,
        "growth_effect_rel": truth.growth_effect,
        "hazard_ratio": truth.hazard_ratio,
        "fc_effect": truth.fc_effect,
        "meta": fault_metadata,
    }
    (truth_dir / "true_effects.json").write_text(
        json.dumps(effects, indent=2), encoding="utf-8"
    )
