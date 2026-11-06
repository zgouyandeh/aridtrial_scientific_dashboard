"""FAO-56 reference evapotranspiration (Penman-Monteith) and single-crop-coefficient water balance.

Equation numbers refer to Allen et al. (1998), FAO Irrigation and Drainage Paper 56.
Unit tests reproduce the worked daily example (Brussels, 6 July) from the paper.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

GSC = 0.0820  # solar constant, MJ m-2 min-1
SIGMA = 4.903e-9  # Stefan-Boltzmann, MJ K-4 m-2 d-1


def atm_pressure(z: float) -> float:  # eq. 7, kPa
    return 101.3 * ((293.0 - 0.0065 * z) / 293.0) ** 5.26


def psychrometric_constant(p_kpa: float) -> float:  # eq. 8
    return 0.000665 * p_kpa


def svp(t_c):  # eq. 11, kPa
    t_c = np.asarray(t_c, float)
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def svp_slope(t_c):  # eq. 13
    t_c = np.asarray(t_c, float)
    return 4098.0 * svp(t_c) / (t_c + 237.3) ** 2


def wind_at_2m(uz, z_m: float = 10.0):  # eq. 47
    return np.asarray(uz, float) * 4.87 / np.log(67.8 * z_m - 5.42)


def solar_declination(doy):  # eq. 24
    return 0.409 * np.sin(2 * np.pi * np.asarray(doy) / 365.0 - 1.39)


def inverse_rel_distance(doy):  # eq. 23
    return 1.0 + 0.033 * np.cos(2 * np.pi * np.asarray(doy) / 365.0)


def sunset_hour_angle(lat_rad: float, delta):  # eq. 25
    return np.arccos(np.clip(-np.tan(lat_rad) * np.tan(delta), -1.0, 1.0))


def extraterrestrial_radiation(lat_deg: float, doy):  # eq. 21, MJ m-2 d-1
    phi = np.deg2rad(lat_deg)
    d = solar_declination(doy)
    ws = sunset_hour_angle(phi, d)
    dr = inverse_rel_distance(doy)
    return (24 * 60 / np.pi) * GSC * dr * (ws * np.sin(phi) * np.sin(d) + np.cos(phi) * np.cos(d) * np.sin(ws))


def daylight_hours(lat_deg: float, doy):  # eq. 34
    phi = np.deg2rad(lat_deg)
    return 24.0 / np.pi * sunset_hour_angle(phi, solar_declination(doy))


def rs_from_sunshine(n_hours, lat_deg, doy, a_s=0.25, b_s=0.50):  # eq. 35 (Angstrom)
    ra = extraterrestrial_radiation(lat_deg, doy)
    big_n = daylight_hours(lat_deg, doy)
    return (a_s + b_s * np.asarray(n_hours) / big_n) * ra


def eto_pm_daily(tmax, tmin, rh_max, rh_min, u2, rs, lat_deg: float, z_m: float, doy, g: float = 0.0) -> np.ndarray:
    """Daily FAO-56 Penman-Monteith ET0 (mm/day) from Tmax/Tmin, RHmax/RHmin, u2 and Rs."""
    tmax, tmin = np.asarray(tmax, float), np.asarray(tmin, float)
    rh_max, rh_min = np.asarray(rh_max, float), np.asarray(rh_min, float)
    u2, rs = np.asarray(u2, float), np.asarray(rs, float)
    tmean = (tmax + tmin) / 2.0
    p = atm_pressure(z_m)
    gamma = psychrometric_constant(p)
    delta = svp_slope(tmean)
    es = (svp(tmax) + svp(tmin)) / 2.0  # eq. 12
    ea = (svp(tmin) * rh_max / 100.0 + svp(tmax) * rh_min / 100.0) / 2.0  # eq. 17
    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = (0.75 + 2e-5 * z_m) * ra  # eq. 37
    rns = (1 - 0.23) * rs  # eq. 38
    rel = np.clip(rs / rso, 0.3, 1.0)  # eq. 39 guard
    rnl = SIGMA * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2.0) * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * rel - 0.35)  # eq. 39
    rn = rns - rnl  # eq. 40
    num = 0.408 * delta * (rn - g) + gamma * 900.0 / (tmean + 273.0) * u2 * (es - ea)
    den = delta + gamma * (1.0 + 0.34 * u2)
    return num / den  # eq. 6


def eto_from_frame(w: pd.DataFrame, lat_deg: float, z_m: float) -> pd.Series:
    """Compute ET0 for a weather frame with columns date,tmax,tmin,rh_max,rh_min,wind_2m,rs_mj."""
    doy = pd.to_datetime(w["date"]).dt.dayofyear.to_numpy()
    et0 = eto_pm_daily(w["tmax"], w["tmin"], w["rh_max"], w["rh_min"], w["wind_2m"], w["rs_mj"], lat_deg, z_m, doy)
    return pd.Series(np.clip(et0, 0.0, None), index=w.index, name="et0_mm")


# ----------------------------------------------------------------------------- water balance


@dataclass(frozen=True)
class RootZone:
    theta_fc: float  # m3/m3
    theta_wp: float
    zr_m: float  # effective root depth, m
    p: float = 0.50  # depletion fraction for no stress

    @property
    def taw(self) -> float:  # eq. 82, mm
        return 1000.0 * (self.theta_fc - self.theta_wp) * self.zr_m

    @property
    def raw(self) -> float:  # eq. 83
        return self.p * self.taw


def stress_coefficient(dr: float, rz: RootZone) -> float:  # eq. 84
    if dr <= rz.raw:
        return 1.0
    return max(0.0, (rz.taw - dr) / ((1.0 - rz.p) * rz.taw))


def water_balance(et0, rain, kc, rz: RootZone, dr0: float | None = None, irrigate: bool = True,
                  mad: float | None = None, efficiency: float = 0.90, irr_time_lag: int = 0):
    """Daily root-zone depletion model with an optional refill-to-field-capacity drip schedule.

    Returns a DataFrame with columns: etc_mm, ks, dr_mm, irr_net_mm, irr_gross_mm, drain_mm.
    Irrigation is triggered on the morning after depletion exceeds mad*TAW (default: RAW) and refills
    to field capacity; gross depth = net / efficiency.
    """
    et0, rain, kc = (np.asarray(x, float) for x in (et0, rain, kc))
    n = len(et0)
    mad_mm = (mad if mad is not None else rz.p) * rz.taw
    dr = rz.raw * 0.5 if dr0 is None else dr0
    out = np.zeros((n, 6))
    for i in range(n):
        irr_net = 0.0
        if irrigate and dr >= mad_mm:
            irr_net = dr
            dr = 0.0
        ks = stress_coefficient(dr, rz)
        etc = ks * kc[i] * et0[i]
        eff_rain = max(rain[i] - 2.0, 0.0) * 0.8 if rain[i] > 2.0 else 0.0  # crude effective rainfall
        dr_new = dr - eff_rain + etc
        drain = max(-dr_new, 0.0)
        dr = min(max(dr_new, 0.0), rz.taw)
        out[i] = (etc, ks, dr, irr_net, irr_net / efficiency, drain)
    return pd.DataFrame(out, columns=["etc_mm", "ks", "dr_mm", "irr_net_mm", "irr_gross_mm", "drain_mm"])


def kc_young_tree(canopy_index, kc_min: float = 0.15, kc_max: float = 0.55):
    """Plot-scale single Kc for sparse young trees: linear in an (0-1) canopy index.

    Plot-scale because trees cover a small fraction of the ground; the Kc is the effective value
    for the whole plot area, which is what the meter volumes are normalised to.
    """
    return kc_min + (kc_max - kc_min) * np.clip(np.asarray(canopy_index, float), 0.0, 1.0)
