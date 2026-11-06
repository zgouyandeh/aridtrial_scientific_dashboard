"""Central trial configuration.

Everything that defines the *design* of the trial lives here so that the design,
the simulator, the pipeline and the analysis all read the same numbers.
All data in this repository are synthetic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

TREATMENTS: tuple[str, ...] = ("CTRL", "COMP", "BIOC", "COMP_BIOC")
TREATMENT_LABELS = {
    "CTRL": "Control (no amendment)",
    "COMP": "Compost",
    "BIOC": "Biochar",
    "COMP_BIOC": "Compost + biochar",
}

# Sampling campaigns: name -> days after trial start. T2 is the mid-trial review, T4 the endpoint.
CAMPAIGNS: dict[str, int] = {"T0": 0, "T1": 91, "T2": 182, "T3": 273, "T4": 364}
MID_TRIAL = "T2"
ENDPOINT = "T4"
AMENDMENT_DAY = 7  # amendments incorporated one week after baseline sampling
SOIL_LAYERS: tuple[tuple[int, int], ...] = ((0, 15), (15, 30))  # cm


@dataclass(frozen=True)
class TrialConfig:
    seed: int = 20261027
    site_name: str = "Al Wathba trial site (synthetic)"
    site_lon: float = 54.60
    site_lat: float = 24.24
    site_elevation_m: float = 40.0
    utm_epsg: int = 32640  # WGS 84 / UTM zone 40N

    n_blocks: int = 6
    treatments: tuple[str, ...] = TREATMENTS
    plot_w_m: float = 12.0  # east-west
    plot_l_m: float = 20.0  # north-south
    plot_gap_m: float = 4.0
    block_gap_m: float = 10.0
    trees_per_plot: int = 10  # 2 rows x 5

    trial_start: date = date(2025, 10, 1)
    n_days: int = 365

    # Design-stage variance components for soil organic carbon (% points, 0-15 cm).
    # Replaced by baseline estimates once T0 data are in (see analysis.stats.baseline_variance).
    assumed_sd_block: float = 0.040
    assumed_sd_plot: float = 0.030
    assumed_sd_resid: float = 0.020

    @property
    def n_plots(self) -> int:
        return self.n_blocks * len(self.treatments)

    def campaign_date(self, name: str) -> date:
        return self.trial_start + timedelta(days=CAMPAIGNS[name])

    @property
    def dates(self) -> list[date]:
        return [self.trial_start + timedelta(days=i) for i in range(self.n_days)]


def project_root() -> Path:
    return Path(os.environ.get("ARIDTRIAL_HOME", Path(__file__).resolve().parents[2]))


def data_dir() -> Path:
    p = project_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def outputs_dir() -> Path:
    p = project_root() / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_dsn() -> str:
    return os.environ.get(
        "ARIDTRIAL_DSN", "postgresql+psycopg2://aridtrial:aridtrial@localhost:5432/aridtrial"
    )
