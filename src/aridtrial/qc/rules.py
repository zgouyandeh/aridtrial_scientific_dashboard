"""Data-quality rules. Pure functions over DataFrames; each returns a result frame:

    dataset, record_key, plot_id, ts, variable, rule_id, severity, message, value

Severity semantics
    fail  value is excluded from the QC-filtered marts that analysis and dashboards read
    warn  kept, but reported (and visible on the QC dashboard)
    info  informational (e.g. a logger gap that is already visible as missing rows)

Raw and core data are never modified: QC only *flags*. That is what makes the dataset auditable.
Range/null rules are declared with pandera; temporal and relational rules are plain pandas.
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd
from pyproj import Transformer

try:  # pandera >= 0.24 exposes the pandas API here
    import pandera.pandas as pa
except ImportError:  # pragma: no cover
    import pandera as pa

from ..config import CAMPAIGNS, SOIL_LAYERS, TrialConfig
from ..models import fao56

RESULT_COLS = ["dataset", "record_key", "plot_id", "ts", "variable", "rule_id", "severity", "message", "value"]
RULESET_VERSION = "aridtrial-qc-1.1"


def empty() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLS)


def _mk(dataset, df, key, variable, rule_id, severity, message, value=None, plot_id="plot_id", ts=None) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return empty()
    out = pd.DataFrame(
        dict(
            dataset=dataset,
            record_key=key if not callable(key) else [key(r) for r in df.itertuples()],
            plot_id=df[plot_id].values if plot_id in df else None,
            ts=pd.to_datetime(df[ts].values, utc=True) if ts else pd.NaT,
            variable=variable,
            rule_id=rule_id,
            severity=severity,
            message=message,
            value=(df[value].values if isinstance(value, str) else value),
        )
    )
    return out[RESULT_COLS]


# ----------------------------------------------------------------------------- pandera adapter


def _from_pandera(dataset: str, df: pd.DataFrame, schema: "pa.DataFrameSchema", key_fn, ts_col: str | None = None, null_rule: str | None = None) -> pd.DataFrame:
    try:
        schema.validate(df, lazy=True)
        return empty()
    except pa.errors.SchemaErrors as e:
        fc = e.failure_cases
    fc = fc[fc["index"].notna()].copy()
    rows = []
    for r in fc.itertuples():
        rec = df.loc[r.index]
        rule = null_rule if str(r.check) == "not_nullable" else (f"{dataset}.range_{r.column}" if str(r.check).startswith("in_range") else str(r.check))
        rows.append(dict(
            dataset=dataset, record_key=key_fn(rec), plot_id=rec.get("plot_id"),
            ts=pd.Timestamp(rec[ts_col], tz="UTC") if ts_col and pd.notna(rec[ts_col]) and pd.Timestamp(rec[ts_col]).tzinfo is None
            else (pd.Timestamp(rec[ts_col]) if ts_col else pd.NaT),
            variable=r.column, rule_id=f"{dataset}.{rule.split('.')[-1]}" if "." not in rule else rule, severity="fail",
            message=f"{r.column}={r.failure_case!r} violates {rule}", value=float(r.failure_case) if isinstance(r.failure_case, (int, float, np.floating)) else np.nan,
        ))
    return pd.DataFrame(rows, columns=RESULT_COLS)


def _col(lo, hi, name, nullable=False):
    return pa.Column(float, checks=pa.Check.in_range(lo, hi, name=name), nullable=nullable, coerce=True)


SOIL_SCHEMA = pa.DataFrameSchema({
    "soc_pct": _col(0.02, 5.0, "soil.range_soc_pct"),
    "ec_dsm": _col(0.1, 60.0, "soil.range_ec_dsm"),
    "ph": _col(4.0, 11.0, "soil.range_ph"),
    "bulk_density": _col(0.8, 2.0, "soil.range_bulk_density"),
}, strict=False)

TREE_SCHEMA = pa.DataFrameSchema({
    "height_cm": _col(5.0, 600.0, "tree.range_height_cm", nullable=True),
    "stem_diam_mm": _col(1.0, 150.0, "tree.range_stem_diam_mm", nullable=True),
}, strict=False)

SENSOR_SCHEMA = pa.DataFrameSchema({
    "vwc": _col(0.02, 0.55, "sensor.range_vwc"),
    "soil_temp_c": _col(0.0, 70.0, "sensor.range_soil_temp_c"),
}, strict=False)

NDVI_SCHEMA = pa.DataFrameSchema({"ndvi": _col(-0.1, 1.0, "ndvi.range_ndvi")}, strict=False)

WEATHER_SCHEMA = pa.DataFrameSchema({
    "tmax": _col(-5.0, 55.0, "weather.range_tmax"),
    "tmin": _col(-5.0, 45.0, "weather.range_tmin"),
    "rh_max": _col(0.0, 100.0, "weather.range_rh_max"),
    "rh_min": _col(0.0, 100.0, "weather.range_rh_min"),
    "wind_2m": _col(0.0, 30.0, "weather.range_wind_2m"),
    "rs_mj": _col(0.0, 40.0, "weather.range_rs_mj"),
    "rain_mm": _col(0.0, 300.0, "weather.range_rain_mm"),
}, strict=False)


# ----------------------------------------------------------------------------- soil


def _plot_bounds(plots: pd.DataFrame) -> pd.DataFrame:
    num = plots["geom_wkt"].apply(lambda w: np.array(re.findall(r"-?\d+\.?\d*", w), float).reshape(-1, 2))
    return pd.DataFrame(dict(plot_id=plots["plot_id"], xmin=num.apply(lambda a: a[:, 0].min()), xmax=num.apply(lambda a: a[:, 0].max()),
                             ymin=num.apply(lambda a: a[:, 1].min()), ymax=num.apply(lambda a: a[:, 1].max()))).set_index("plot_id")


def _distance_to_plot(df: pd.DataFrame, plots: pd.DataFrame, epsg: int) -> pd.Series:
    b = _plot_bounds(plots)
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = t.transform(df["gps_lon"].to_numpy(float), df["gps_lat"].to_numpy(float))
    bb = b.loc[df["plot_id"]]
    dx = np.maximum.reduce([bb["xmin"].to_numpy() - x, np.zeros(len(df)), x - bb["xmax"].to_numpy()])
    dy = np.maximum.reduce([bb["ymin"].to_numpy() - y, np.zeros(len(df)), y - bb["ymax"].to_numpy()])
    return pd.Series(np.hypot(dx, dy), index=df.index)


def check_soil(soil: pd.DataFrame, plots: pd.DataFrame, cfg: TrialConfig, as_of: date, due_campaigns: list[str] | None = None) -> pd.DataFrame:
    """soil: current rows of core.soil_sample."""
    s = soil.copy()
    key = lambda r: r["sample_key"] if isinstance(r, (pd.Series, dict)) else r.sample_key
    res = [_from_pandera("soil", s, SOIL_SCHEMA, lambda r: r["sample_key"], null_rule="soil.missing_value")]
    s["sample_date"] = pd.to_datetime(s["sample_date"])

    fut = s[s["sample_date"].dt.date > as_of]
    res.append(_mk("soil", fut, lambda r: r.sample_key, "*", "soil.future_date", "fail", "sample_date is after the run date"))
    nominal = s["campaign"].map(lambda c: pd.Timestamp(cfg.campaign_date(c)))
    off = s[(s["sample_date"] - nominal).abs() > pd.Timedelta(days=14)]
    res.append(_mk("soil", off, lambda r: r.sample_key, "sample_date", "soil.date_vs_campaign", "warn", "sample_date more than 14 days from the campaign date"))
    ok_layers = {(a, b) for a, b in SOIL_LAYERS}
    bad_depth = s[[(t, b) not in ok_layers for t, b in zip(s["depth_top_cm"], s["depth_bottom_cm"])]]
    res.append(_mk("soil", bad_depth, lambda r: r.sample_key, "*", "soil.depth_layer", "fail", "depth interval is not a protocol layer"))
    g = s.dropna(subset=["gps_lon", "gps_lat"])
    if len(g):
        d = _distance_to_plot(g, plots, cfg.utm_epsg)
        far = g[d > 10.0].assign(dist_m=d[d > 10.0])
        res.append(_mk("soil", far, lambda r: r.sample_key, "*", "soil.gps_in_plot", "warn", "GPS fix more than 10 m from the plot polygon", value="dist_m"))
    # completeness: every plot x due campaign x layer present
    due = due_campaigns if due_campaigns is not None else [c for c in CAMPAIGNS if cfg.campaign_date(c) <= as_of]
    expected = {f"{p}|{c}|{t}" for p in plots["plot_id"] for c in due for t, _ in SOIL_LAYERS}
    missing = sorted(expected - set(s["sample_key"]))
    if missing:
        m = pd.DataFrame(dict(sample_key=missing, plot_id=[k.split("|")[0] for k in missing]))
        res.append(_mk("soil", m, lambda r: r.sample_key, "*", "soil.missing_record", "warn", "expected sample is absent"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()


# ----------------------------------------------------------------------------- trees


def check_trees(trees: pd.DataFrame, plots: pd.DataFrame, cfg: TrialConfig, as_of: date) -> pd.DataFrame:
    t = trees.copy()
    res = [_from_pandera("tree", t, TREE_SCHEMA, lambda r: r["obs_key"], null_rule="tree.missing_value")]
    a = t[t["alive"] & t["height_cm"].isna()]
    res.append(_mk("tree", a, lambda r: r.obs_key, "height_cm", "tree.alive_without_height", "fail", "tree recorded alive but height missing"))
    # survival must be monotone: a tree recorded dead cannot be alive in a later campaign
    order = {c: i for i, c in enumerate(CAMPAIGNS)}
    t["ci"] = t["campaign"].map(order)
    t = t.sort_values(["tree_id", "ci"])
    first_dead = t[~t["alive"]].groupby("tree_id")["ci"].min()
    back = t[t["alive"] & t["tree_id"].map(first_dead).lt(t["ci"])]
    res.append(_mk("tree", back, lambda r: r.obs_key, "alive", "tree.resurrection", "fail", "tree alive after being recorded dead"))
    # growth plausibility between consecutive campaigns
    t["h_prev"] = t.groupby("tree_id")["height_cm"].shift(1)
    t["dh"] = t["height_cm"] - t["h_prev"]
    bad = t[(t["dh"] < -15) | (t["dh"] > 90)]
    res.append(_mk("tree", bad, lambda r: r.obs_key, "height_cm", "tree.growth_plausibility", "warn", "height change between campaigns outside -15..+90 cm", value="dh"))
    # completeness: n_trees rows per plot x campaign
    cnt = t.groupby(["plot_id", "campaign"]).size().reset_index(name="n")
    short = cnt[cnt["n"] < cfg.trees_per_plot].copy()
    short["key"] = short["plot_id"] + "|" + short["campaign"] + "|*"
    res.append(_mk("tree", short, lambda r: r.key, "*", "tree.incomplete_plot", "warn", "fewer tree records than planted trees", value="n"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()


# ----------------------------------------------------------------------------- sensors


def check_sensors(sensors: pd.DataFrame, spike_threshold: float = 0.04, flat_hours: int = 24, gap_hours: int = 6) -> pd.DataFrame:
    s = sensors.sort_values(["plot_id", "ts"]).reset_index(drop=True)
    key = lambda r: f"{r['plot_id']}|{pd.Timestamp(r['ts']):%Y-%m-%dT%H}"
    res = [_from_pandera("sensor", s, SENSOR_SCHEMA, key, ts_col="ts", null_rule="sensor.missing_value")]

    # isolated spike: both neighbours differ in the same direction by more than the threshold.
    # Robust to irrigation steps (a step has one neighbour at the same level).
    grp = s.groupby("plot_id")["vwc"]
    d_prev, d_next = s["vwc"] - grp.shift(1), s["vwc"] - grp.shift(-1)
    spike = (d_prev.abs() > spike_threshold) & (d_next.abs() > spike_threshold) & (np.sign(d_prev) == np.sign(d_next))
    sp = s[spike].assign(delta=d_prev[spike])
    res.append(_mk("sensor", sp, lambda r: f"{r.plot_id}|{r.ts:%Y-%m-%dT%H}", "vwc", "sensor.spike", "fail", "isolated spike versus both neighbours", value="vwc", ts="ts"))

    # flatline: identical consecutive values for >= flat_hours
    run = (s["vwc"].ne(grp.shift(1)) | s["plot_id"].ne(s["plot_id"].shift(1))).cumsum()
    length = s.groupby(run)["vwc"].transform("size")
    fl = s[length >= flat_hours]
    res.append(_mk("sensor", fl, lambda r: f"{r.plot_id}|{r.ts:%Y-%m-%dT%H}", "vwc", "sensor.flatline", "fail", f"value unchanged for >= {flat_hours} h (stuck sensor)", value="vwc", ts="ts"))

    # gaps: informational, rows are simply absent
    dt = s.groupby("plot_id")["ts"].diff()
    gaps = s[dt > pd.Timedelta(hours=gap_hours + 1)].copy()
    if len(gaps):
        gaps["gap_h"] = (dt[gaps.index] / pd.Timedelta(hours=1)) - 1
        gaps["ts"] = gaps["ts"] - dt[gaps.index] + pd.Timedelta(hours=1)  # first missing hour
        res.append(_mk("sensor", gaps, lambda r: f"{r.plot_id}|{r.ts:%Y-%m-%dT%H}", "*", "sensor.gap", "info", "logger gap", value="gap_h", ts="ts"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()


# ----------------------------------------------------------------------------- meters, NDVI, weather


def check_meters(meters: pd.DataFrame, area_m2: float, max_mm_per_day: float = 60.0) -> pd.DataFrame:
    m = meters.sort_values(["plot_id", "date"]).copy()
    m["date"] = pd.to_datetime(m["date"])
    key = lambda r: f"{r.plot_id}|{r.date:%Y-%m-%d}"
    d = m.groupby("plot_id")["cum_volume_l"].diff()
    reset = m[d < 0]
    res = [_mk("meter", reset, key, "volume_l", "meter.register_reset", "fail", "cumulative register decreased (meter replaced/reset)", ts="date")]
    big = m[(d / area_m2) > max_mm_per_day]
    res.append(_mk("meter", big, key, "volume_l", "meter.implausible_volume", "fail", f"> {max_mm_per_day:.0f} mm/day equivalent", ts="date"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()


def check_ndvi(ndvi: pd.DataFrame, cloud_fail: float = 0.30) -> pd.DataFrame:
    n = ndvi.sort_values(["plot_id", "date"]).reset_index(drop=True)  # clean 0..n-1 index: later groupby/transform results align
    n["date"] = pd.to_datetime(n["date"])
    key = lambda r: f"{r.plot_id}|{r.date:%Y-%m-%d}"
    res = [_from_pandera("ndvi", n, NDVI_SCHEMA, lambda r: f"{r['plot_id']}|{pd.Timestamp(r['date']):%Y-%m-%d}", ts_col="date", null_rule="ndvi.missing_value")]
    cl = n[n["cloud_frac"] > cloud_fail]
    res.append(_mk("ndvi", cl, key, "ndvi", "ndvi.cloud_contaminated", "fail", f"scene cloud fraction > {cloud_fail}", value="cloud_frac", ts="date"))
    # temporal consistency against the previous 5 clean observations (catches undeclared cloud/shadow)
    clean = n[n["cloud_frac"] <= cloud_fail].copy()
    clean["med"] = clean.groupby("plot_id")["ndvi"].transform(lambda x: x.shift(1).rolling(5, min_periods=3).median())
    clean["dev"] = clean["ndvi"] - clean["med"]
    jump = clean[clean["dev"].abs() > 0.03]
    res.append(_mk("ndvi", jump, key, "ndvi", "ndvi.temporal_jump", "warn", "|NDVI - rolling median| > 0.03", value="dev", ts="date"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()


def check_weather(w: pd.DataFrame, cfg: TrialConfig) -> pd.DataFrame:
    w = w.copy()
    w["date"] = pd.to_datetime(w["date"])
    w["plot_id"] = None
    key = lambda r: f"{pd.Timestamp(r['date']):%Y-%m-%d}"
    res = [_from_pandera("weather", w, WEATHER_SCHEMA, key, ts_col="date", null_rule="weather.missing_value")]
    kfun = lambda r: f"{r.date:%Y-%m-%d}"
    sw = w[w["rh_max"] < w["rh_min"]]
    res.append(_mk("weather", sw, kfun, "rh_max", "weather.rh_order", "fail", "RHmax < RHmin (swapped columns?)", ts="date", plot_id="plot_id"))
    tt = w[w["tmax"] < w["tmin"]]
    res.append(_mk("weather", tt, kfun, "tmax", "weather.t_order", "fail", "Tmax < Tmin", ts="date", plot_id="plot_id"))
    ra = fao56.extraterrestrial_radiation(cfg.site_lat, w["date"].dt.dayofyear.to_numpy())
    hi = w[w["rs_mj"] > 1.05 * ra]
    res.append(_mk("weather", hi, kfun, "rs_mj", "weather.rs_above_extraterrestrial", "fail", "Rs exceeds extraterrestrial radiation", ts="date", plot_id="plot_id"))
    return pd.concat([r for r in res if len(r)], ignore_index=True) if any(len(r) for r in res) else empty()
