"""How many Sentinel-2 10 m pixels lie fully inside a plot (or block)?

Sentinel-2 10 m bands (B02/B03/B04/B08) are sampled on a grid aligned to multiples of 10 m in the tile's UTM system
(EPSG:32640 here). A mixed pixel (partly alley / bare soil, partly canopy) is not a measurement of the plot.
'Pure' = the whole 10 x 10 m pixel lies inside the rectangle shrunk by `buffer_m` (co-registration error + point-spread
function; 5 m is a conservative planning value - check the geolocation quality of the product baseline actually used).
Plots and block zones are axis-aligned rectangles in UTM, so the count is exact interval arithmetic (no GIS dependency).
"""
from __future__ import annotations

import math
import re

import pandas as pd

PIXEL = 10.0
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def rect_from_wkt(w: str) -> tuple[float, float, float, float]:
    v = [float(x) for x in _NUM.findall(w)]
    xs, ys = v[0::2], v[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _axis(lo: float, hi: float, pixel: float) -> int:
    return max(0, math.floor(hi / pixel + 1e-9) - math.ceil(lo / pixel - 1e-9))


def pure_pixels(rect, buffer_m: float = 0.0, pixel: float = PIXEL) -> int:
    x0, y0, x1, y1 = rect
    return _axis(x0 + buffer_m, x1 - buffer_m, pixel) * _axis(y0 + buffer_m, y1 - buffer_m, pixel)


def plot_purity(layout: pd.DataFrame, buffers=(0.0, 5.0)) -> pd.DataFrame:
    rows = []
    for r in layout.itertuples():
        rc = rect_from_wkt(r.geom_wkt)
        rows.append(dict(plot_id=r.plot_id, block=r.block, area_m2=(rc[2] - rc[0]) * (rc[3] - rc[1]),
                         **{f"pure_px_buf{int(b)}m": pure_pixels(rc, b) for b in buffers}))
    return pd.DataFrame(rows)


def block_rect(layout: pd.DataFrame, block: int):
    rs = [rect_from_wkt(w) for w in layout.loc[layout["block"] == block, "geom_wkt"]]
    return min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)


def block_purity(layout: pd.DataFrame, buffers=(0.0, 5.0)) -> pd.DataFrame:
    """Block zone = bounding rectangle of the block's four plots; it contains the alleys between plots, so this is
    block-level context, not a plot-level measurement."""
    rows = []
    for b in sorted(layout["block"].unique()):
        rc = block_rect(layout, int(b))
        rows.append(dict(block=int(b), area_m2=(rc[2] - rc[0]) * (rc[3] - rc[1]), **{f"pure_px_buf{int(x)}m": pure_pixels(rc, x) for x in buffers}))
    return pd.DataFrame(rows)


def block_zone_polygons(layout: pd.DataFrame) -> dict[int, list[list[float]]]:
    """Block zones as closed UTM rings (for the Sentinel Hub Statistical API request)."""
    out = {}
    for b in sorted(layout["block"].unique()):
        x0, y0, x1, y1 = block_rect(layout, int(b))
        out[int(b)] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return out


def summary(layout: pd.DataFrame) -> dict:
    p = plot_purity(layout)
    return dict(plots=len(p), plots_ge1_pure_px_nobuf=int((p["pure_px_buf0m"] >= 1).sum()), plots_ge1_pure_px_buf5=int((p["pure_px_buf5m"] >= 1).sum()),
                median_pure_px_nobuf=float(p["pure_px_buf0m"].median()), max_pure_px_nobuf=int(p["pure_px_buf0m"].max()))
