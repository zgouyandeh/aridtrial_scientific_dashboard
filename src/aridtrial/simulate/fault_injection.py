"""Controlled data-quality faults for the synthetic input datasets.

The fault mechanisms and their random-number usage are intentionally preserved
from the original generator so existing validation expectations remain stable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _inject_timeseries_faults(cfg, sim, weather, rng):
    faults = []
    sensors = sim["sensors"].copy()
    meters = sim["meters"].copy()
    ndvi = sim["ndvi"].copy()
    w = weather.copy()
    plots = list(sim["plots"]["plot_id"])

    # sensor spikes (0.3 % of readings) -> physically impossible / spurious values
    n_spike = int(0.003 * len(sensors))
    idx = rng.choice(len(sensors), n_spike, replace=False)
    sensors.loc[idx, "vwc"] = rng.choice([0.62, -0.05, 0.95, 0.31, 0.29, 0.27], n_spike)  # half out of physical range, half merely implausible
    for i in idx:
        faults.append(dict(dataset="sensor", key=f"{sensors.at[i, 'plot_id']}|{sensors.at[i, 'ts']:%Y-%m-%dT%H}", fault="spike"))
    n_spike_all = len(idx)

    # flatline (stuck sensor) episodes
    flat_plots = rng.choice(plots, 3, replace=False)
    for pid in flat_plots:
        mask = sensors["plot_id"] == pid
        sub = sensors[mask]
        start = int(rng.integers(500, len(sub) - 400))
        length = int(rng.integers(60, 140))
        ii = sub.index[start:start + length]
        sensors.loc[ii, "vwc"] = float(sensors.loc[ii[0], "vwc"])
        faults.append(dict(dataset="sensor", key=f"{pid}|{sensors.at[ii[0], 'ts']:%Y-%m-%dT%H}", fault=f"flatline_{length}h"))

    # data gaps (logger offline): rows removed entirely
    drop = []
    for pid in rng.choice(plots, 3, replace=False):
        sub = sensors[sensors["plot_id"] == pid]
        start = int(rng.integers(200, len(sub) - 200))
        length = int(rng.integers(18, 60))
        drop.extend(sub.index[start:start + length])
        faults.append(dict(dataset="sensor", key=f"{pid}|{sub.at[sub.index[start], 'ts']:%Y-%m-%dT%H}", fault=f"gap_{length}h"))
    sensors = sensors.drop(index=drop)

    # duplicated timestamps from a double upload
    dup = sensors.sample(40, random_state=int(rng.integers(0, 10_000)))
    sensors = pd.concat([sensors, dup], ignore_index=True)
    faults.append(dict(dataset="sensor", key="*", fault="duplicate_rows_40"))

    # water meters: replacement resets the cumulative register
    for pid in rng.choice(plots, 2, replace=False):
        m = meters["plot_id"] == pid
        cut = int(rng.integers(120, 260))
        sub = meters[m].copy()
        base = sub["cum_volume_l"].iloc[cut - 1]
        sub.iloc[cut:, sub.columns.get_loc("cum_volume_l")] = sub["cum_volume_l"].iloc[cut:] - base
        meters.loc[m, "cum_volume_l"] = sub["cum_volume_l"].values
        faults.append(dict(dataset="meter", key=f"{pid}|{sub['date'].iloc[cut]}", fault="register_reset"))

    # weather: missing days and a swapped RHmax/RHmin
    miss = rng.choice(len(w), 4, replace=False)
    w.loc[miss, ["rs_mj", "wind_2m"]] = np.nan
    for i in miss:
        faults.append(dict(dataset="weather", key=str(w.at[i, "date"]), fault="missing_values"))
    j = int(rng.integers(0, len(w)))
    w.loc[j, ["rh_max", "rh_min"]] = w.loc[j, ["rh_min", "rh_max"]].values
    faults.append(dict(dataset="weather", key=str(w.at[j, "date"]), fault="rh_swapped"))

    meta = dict(n_sensor_spikes=n_spike_all)
    return sensors, meters, ndvi, w, pd.DataFrame(faults), meta


def _inject_station_drift(cfg, w):
    """Faults that only make sense when a reference exists: slow pyranometer drift and a short thermometer offset."""
    w = w.copy()
    faults = []
    rng = np.random.default_rng(cfg.seed + 404)
    d0 = 200
    w.loc[d0:, "rs_mj"] = (w.loc[d0:, "rs_mj"] * 0.82).round(2)
    faults.append(dict(dataset="weather", key=str(w.at[d0, "date"]), fault="rs_drift"))
    j = int(rng.integers(40, 150))
    w.loc[j:j + 4, "tmax"] = (w.loc[j:j + 4, "tmax"] + 6.0).round(2)
    for i in range(j, j + 5):
        faults.append(dict(dataset="weather", key=str(w.at[i, "date"]), fault="tmax_offset"))
    return w, pd.DataFrame(faults)
