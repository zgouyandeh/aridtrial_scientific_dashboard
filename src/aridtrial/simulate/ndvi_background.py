"""Real Sentinel-2 block-level NDVI as the *background* of the simulated plot NDVI.

Plot NDVI = background (bare soil + whatever else is in the 10 m pixels; REAL, from Sentinel-2 L2A, cloud/shadow masked)
          + 0.10 * canopy index (SIMULATED tree signal, known truth) + noise.
The real series supplies realistic soil brightness, seasonality and noise level; only the tree contribution is synthetic.
Valid only if the sampled area is bare desert: a vegetated or water-affected area would put a non-tree signal into the background.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TrialConfig


def load_background(csv, cfg: TrialConfig, min_valid_days: int = 5) -> pd.DataFrame:
    """Daily background per block (index 0..n_days-1, columns = block numbers), linearly interpolated, ends held constant.
    Blocks without data take the across-block median series. Raises if the file has too little real data to be meaningful."""
    d = pd.read_csv(csv)
    d["date"] = pd.to_datetime(d["date"])
    d = d.dropna(subset=["ndvi_mean"])
    days = pd.DatetimeIndex(pd.to_datetime(cfg.dates))
    d = d[(d["date"] >= days[0]) & (d["date"] <= days[-1])]
    if d["date"].nunique() < min_valid_days:
        raise ValueError(f"only {d['date'].nunique()} valid Sentinel-2 dates in the trial period; need >= {min_valid_days}")
    wide = d.pivot_table(index="date", columns="block", values="ndvi_mean").reindex(days)
    med = wide.median(axis=1)
    out = {}
    for b in range(1, cfg.n_blocks + 1):
        s = wide[b] if b in wide.columns and wide[b].notna().sum() >= min_valid_days else med
        s = s.interpolate(limit_area="inside").ffill().bfill()
        if s.isna().all():
            s = med.interpolate(limit_area="inside").ffill().bfill()
        out[b] = s.to_numpy()
    return pd.DataFrame(out)


def summary(csv) -> dict:
    d = pd.read_csv(csv).dropna(subset=["ndvi_mean"])
    return dict(n=len(d), dates=int(d["date"].nunique()), median=float(d["ndvi_mean"].median()), p05=float(d["ndvi_mean"].quantile(0.05)),
                p95=float(d["ndvi_mean"].quantile(0.95)), looks_bare=bool(d["ndvi_mean"].median() < 0.15))
