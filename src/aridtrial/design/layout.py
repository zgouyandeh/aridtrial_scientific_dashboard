"""Randomised complete block design (RCBD) layout, plot geometry and randomisation record.

Blocks run north-south across the expected salinity gradient, so block absorbs the
dominant nuisance variation. Treatments are randomised independently within each block
with a recorded seed; the layout hash goes into the audit log so the randomisation
can be reproduced and shown unaltered at verification.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from pyproj import Transformer

from ..config import TREATMENTS, TrialConfig


def _origin_utm(cfg: TrialConfig) -> tuple[float, float]:
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{cfg.utm_epsg}", always_xy=True)
    return t.transform(cfg.site_lon, cfg.site_lat)


def make_layout(cfg: TrialConfig) -> pd.DataFrame:
    """One row per plot. Columns: plot_id, block, treatment, row, col, geometry in UTM metres."""
    rng = np.random.default_rng(cfg.seed)
    e0, n0 = _origin_utm(cfg)
    tr = Transformer.from_crs(f"EPSG:{cfg.utm_epsg}", "EPSG:4326", always_xy=True)
    rows = []
    n_t = len(cfg.treatments)
    for b in range(1, cfg.n_blocks + 1):
        order = rng.permutation(list(cfg.treatments))
        for k, trt in enumerate(order):
            x0 = e0 + k * (cfg.plot_w_m + cfg.plot_gap_m)
            y0 = n0 + (b - 1) * (cfg.plot_l_m + cfg.block_gap_m)
            x1, y1 = x0 + cfg.plot_w_m, y0 + cfg.plot_l_m
            wkt = f"POLYGON(({x0:.2f} {y0:.2f},{x1:.2f} {y0:.2f},{x1:.2f} {y1:.2f},{x0:.2f} {y1:.2f},{x0:.2f} {y0:.2f}))"
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            lon, lat = tr.transform(cx, cy)
            rows.append(
                dict(
                    plot_id=f"B{b}-P{k + 1}",
                    block=b,
                    position=k + 1,
                    treatment=str(trt),
                    easting=cx,
                    northing=cy,
                    lon=lon,
                    lat=lat,
                    area_m2=cfg.plot_w_m * cfg.plot_l_m,
                    n_trees=cfg.trees_per_plot,
                    geom_wkt=wkt,
                    srid=cfg.utm_epsg,
                )
            )
    df = pd.DataFrame(rows)
    assert len(df) == cfg.n_blocks * n_t
    return df


def check_rcbd(layout: pd.DataFrame, treatments=TREATMENTS) -> dict:
    """Structural checks; raises AssertionError if the layout is not a valid RCBD."""
    g = layout.groupby("block")["treatment"].apply(lambda s: sorted(s))
    for b, trts in g.items():
        assert trts == sorted(treatments), f"block {b} is not complete: {trts}"
    assert layout["plot_id"].is_unique
    # treatment balance across positions: report, do not require (randomisation can be unbalanced)
    pos_tab = pd.crosstab(layout["treatment"], layout["position"])
    return {"n_blocks": int(layout["block"].nunique()), "position_table": pos_tab}


def layout_hash(layout: pd.DataFrame) -> str:
    cols = ["plot_id", "block", "position", "treatment", "geom_wkt"]
    payload = layout[cols].sort_values("plot_id").to_json(orient="records")
    return hashlib.sha256(payload.encode()).hexdigest()


def randomisation_record(cfg: TrialConfig, layout: pd.DataFrame) -> dict:
    return {
        "design": "RCBD",
        "n_blocks": cfg.n_blocks,
        "treatments": list(cfg.treatments),
        "seed": cfg.seed,
        "rng": "numpy.random.default_rng (PCG64), permutation per block",
        "layout_sha256": layout_hash(layout),
        "crs": f"EPSG:{cfg.utm_epsg}",
    }


def _short(trt: str) -> str:
    return "COMP+\nBIOC" if trt == "COMP_BIOC" else trt


def plot_layout(layout: pd.DataFrame, path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    colors = {"CTRL": "#9aa5b1", "COMP": "#2a9d8f", "BIOC": "#6d4c41", "COMP_BIOC": "#e9a03b"}
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    x_min = layout["easting"].min() - 6
    for _, r in layout.iterrows():
        ax.add_patch(Rectangle((r.easting - 6 - x_min, r.northing - 10 - layout.northing.min()), 12, 20,
                               fc=colors[r.treatment], ec="white", lw=1.5))
        ax.text(r.easting - x_min, r.northing - layout.northing.min(), f"{r.plot_id}\n{_short(r.treatment)}",
                ha="center", va="center", fontsize=6.5, color="white" if r.treatment != "CTRL" else "black")
    ax.set_xlim(-2, layout.easting.max() - x_min + 8)
    ax.set_ylim(-12, layout.northing.max() - layout.northing.min() + 14)
    ax.set_aspect("equal")
    ax.set_xlabel("Easting offset (m)")
    ax.set_ylabel("Northing offset (m)  →  expected salinity gradient")
    ax.set_title("RCBD layout: 6 blocks × 4 treatments (synthetic site)")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def dump_record(cfg, layout, path) -> None:
    rec = randomisation_record(cfg, layout)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
