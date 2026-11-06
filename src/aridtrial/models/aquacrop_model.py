"""AquaCrop-OSPy integration: companion-forage scenario driven by *trial-calibrated* soil water properties.

Scope, stated plainly
  The trial's primary crop is young trees with a year-round canopy; AquaCrop-OSPy is an annual-crop model
  (its season terminates at crop maturity) and the trial has no annual-crop canopy/biomass/yield observations.
  So AquaCrop is NOT calibrated against crop data here. What the trial *does* measure is soil water
  behaviour; the FAO-56 calibration (models.calibration) turns that into per-plot field capacity, and this
  module asks a decision question with it: how would the amended soils change irrigation need and
  yield of a companion forage crop (Sorghum), and how uncertain is that given plot-to-plot calibration scatter?
  With crop observations, `run_forage_season` is the forward model to wrap in an optimiser.

Known pitfall (found and isolated while building this, aquacrop 3.x): AquaCrop-OSPy never returns when the soil has
fewer than 12 compartments. Tested: 4, 6 and 8 compartments hang; 12, 15 and 20 run in ~0.2 s, independent of
total depth (12 x 0.05 m = 0.6 m runs). `build_soil` always uses 12 x 0.1 m and `run_forage_season` asserts it.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .calibration import wp_from_fc

N_COMPARTMENTS = 12  # must be >= 12, see module docstring
PROFILE_DEPTH_M = 1.2
THETA_S = 0.38
KSAT_MM_DAY = 1500.0  # coarse desert sand; fixed (not identifiable from VWC data here)


def aquacrop_weather(weather: pd.DataFrame) -> pd.DataFrame:
    w = weather.assign(date=pd.to_datetime(weather["date"])).sort_values("date")
    w[["tmax", "tmin", "rain_mm", "et0_mm"]] = w[["tmax", "tmin", "rain_mm", "et0_mm"]].interpolate(limit_direction="both")
    return pd.DataFrame(dict(MinTemp=w["tmin"].to_numpy(), MaxTemp=w["tmax"].to_numpy(), Precipitation=w["rain_mm"].to_numpy(),
                             ReferenceET=w["et0_mm"].to_numpy(), Date=w["date"].to_numpy()))


def build_soil(theta_fc: float):
    from aquacrop import Soil

    soil = Soil("custom", dz=[0.1] * N_COMPARTMENTS)
    soil.add_layer(thickness=PROFILE_DEPTH_M, thWP=wp_from_fc(theta_fc), thFC=theta_fc, thS=THETA_S, Ksat=KSAT_MM_DAY, penetrability=100)
    return soil


def run_forage_season(theta_fc: float, weather_aq: pd.DataFrame, start: str, end: str, planting: str = "03/01", crop_name: str = "Sorghum",
                      net_irrigation_smt: float = 70.0, strategy: str = "net", interval_days: int = 10, max_depth_mm: float = 25.0) -> dict:
    """strategy 'net': keep depletion above `net_irrigation_smt` % TAW (full irrigation).
    strategy 'interval': fixed schedule, at most `max_depth_mm` every `interval_days` days (deficit irrigation:
    here stored soil water, i.e. the amendment effect, decides how much stress the crop sees)."""
    from aquacrop import AquaCropModel, Crop, InitialWaterContent, IrrigationManagement

    crop = Crop(crop_name, planting_date=planting)
    assert N_COMPARTMENTS >= 12, "AquaCrop-OSPy hangs with fewer than 12 soil compartments"
    if strategy == "net":
        irr = IrrigationManagement(irrigation_method=4, NetIrrSMT=net_irrigation_smt, MaxIrr=25.0)
    elif strategy == "interval":
        irr = IrrigationManagement(irrigation_method=2, IrrInterval=interval_days, MaxIrr=max_depth_mm)
    else:
        raise ValueError(strategy)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = AquaCropModel(start, end, weather_aq, build_soil(theta_fc), crop, InitialWaterContent(value=["FC"]), irrigation_management=irr)
        m.run_model(till_termination=True)
    fs = m._outputs.final_stats.iloc[0]
    flux = m.get_water_flux()
    irr_mm = float(fs["Seasonal irrigation (mm)"])
    y = float(fs["Dry yield (tonne/ha)"])
    return dict(theta_fc=theta_fc, irrigation_mm=irr_mm, dry_yield_t_ha=y, deep_perc_mm=float(flux["DeepPerc"].sum()),
                evaporation_mm=float(flux["Es"].sum()), transpiration_mm=float(flux["Tr"].sum()),
                wp_kg_m3=(y * 1000.0 / (irr_mm * 10.0)) if irr_mm > 0 else np.nan)


def scenario_by_treatment(params: pd.DataFrame, plots: pd.DataFrame, weather: pd.DataFrame, start: str, end: str, planting: str = "03/01",
                          n_draws: int = 30, seed: int = 5, strategies: tuple = ("net", "interval")) -> pd.DataFrame:
    """Bootstrap the per-plot calibrated field capacities within each treatment, run AquaCrop per draw."""
    rng = np.random.default_rng(seed)
    w = aquacrop_weather(weather)
    p = params if "treatment" in params.columns else params.merge(plots[["plot_id", "treatment"]], on="plot_id")
    rows = []
    for trt, g in p.groupby("treatment"):
        fcs = g["theta_fc"].to_numpy()
        for d in range(n_draws):
            fc = float(rng.choice(fcs))
            for st in strategies:
                rows.append(dict(treatment=trt, draw=d, strategy=st, **run_forage_season(fc, w, start, end, planting, strategy=st)))
    return pd.DataFrame(rows)
