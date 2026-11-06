"""Builds the self-contained HTML analysis report (figures embedded) and the figure PNGs.

Palette: the four-slot categorical set validated with the dataviz validator (light: worst adjacent CVD dE 9.1,
normal-vision dE 22.9). Contrast warnings on aqua/yellow are covered by the relief rule: every figure carries direct
labels and an accompanying table. The control arm is also dashed (secondary encoding).
"""
from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..config import CAMPAIGNS, ENDPOINT, MID_TRIAL, TREATMENT_LABELS, TREATMENTS, TrialConfig
from ..pipeline.db import read_sql

CAMPAIGN_X_END = CAMPAIGNS[ENDPOINT]
COL = {"CTRL": "#2a78d6", "COMP": "#eb6834", "BIOC": "#1baf7a", "COMP_BIOC": "#eda100"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white", "axes.edgecolor": MUTED, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                     "ytick.color": MUTED, "text.color": INK, "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False})
LS = {"CTRL": (0, (4, 2)), "COMP": "-", "BIOC": "-", "COMP_BIOC": "-"}


def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _img(fig, alt: str) -> str:
    return f'<figure><img alt="{alt}" src="data:image/png;base64,{_b64(fig)}"><figcaption>{alt}</figcaption></figure>'


def _tbl(df: pd.DataFrame, fmt: dict | None = None, index=False) -> str:
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            f = (fmt or {}).get(c)
            d[c] = d[c].map(lambda v, f=f: "" if pd.isna(v) else (f.format(v) if f else (f"{v:.4g}")))
    return d.to_html(index=index, border=0, classes="t", escape=False)


def _spread_labels(ax, x, items, gap=0.085):
    """items: {name: y_end}. Places boxed direct labels at x, nudged apart in axis-fraction space so none overlap."""
    lo, hi = ax.get_ylim()
    span = hi - lo
    order = sorted(items.items(), key=lambda kv: kv[1])
    pos, last = {}, None
    for name, y in order:
        f = (y - lo) / span
        f = f if last is None or f - last >= gap else last + gap
        pos[name], last = f, f
    top = max(pos.values())
    if top > 0.97:  # shift the stack down if it ran off the top
        for k in pos:
            pos[k] -= top - 0.97
    for name, f in pos.items():
        _label_end(ax, x, lo + f * span, name, COL[name])


def _label_end(ax, x, y, text, color):
    ax.annotate(text, (x, y), xytext=(5, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec=color, lw=1.0))


# ----------------------------------------------------------------------------- figures


def fig_traj(emm, response, depth, ylabel, title):
    d = emm[(emm.response == response) & (emm.depth == depth)].copy()
    d["day"] = d["campaign"].map(CAMPAIGNS)
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ends = {}
    for t in TREATMENTS:
        g = d[d.treatment == t].sort_values("day")
        ax.fill_between(g["day"], g["lo"], g["hi"], color=COL[t], alpha=0.12, lw=0)
        ax.plot(g["day"], g["emm"], color=COL[t], lw=2, ls=LS[t], marker="o", ms=4.5, mec="white", mew=1.2)
        ends[t] = g["emm"].iloc[-1]
    _spread_labels(ax, CAMPAIGN_X_END, ends)
    ax.set_xticks(list(CAMPAIGNS.values()), list(CAMPAIGNS))
    ax.set_xlim(-10, 440)
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", fontsize=10.5, color=INK)
    ax.axvline(CAMPAIGNS[MID_TRIAL], color=MUTED, lw=0.8, ls=":")
    ax.text(CAMPAIGNS[MID_TRIAL] + 4, ax.get_ylim()[0], "mid-trial review", fontsize=8, color=MUTED, va="bottom")
    return fig


def fig_forest(contrasts, items, title):
    fig, axes = plt.subplots(1, len(items), figsize=(2.9 * len(items), 3.2), sharey=True)
    axes = np.atleast_1d(axes)
    trts = [t for t in TREATMENTS if t != "CTRL"]
    for ax, (resp, depth, lab) in zip(axes, items):
        c = contrasts[(contrasts.response == resp) & (contrasts.depth == depth) & (contrasts.review == "endpoint")]
        for i, t in enumerate(trts):
            r = c[c.contrast == f"{t} - CTRL"].iloc[0]
            ax.plot([r.lo, r.hi], [i, i], color=COL[t], lw=2.4, solid_capstyle="round")
            ax.plot(r.estimate, i, "o", color=COL[t], ms=7, mec="white", mew=1.4)
        ax.axvline(0, color=MUTED, lw=1)
        ax.set_yticks(range(len(trts)), trts)
        ax.set_title(lab, fontsize=9.5, loc="left")
        ax.grid(axis="y", visible=False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=10.5)
    fig.tight_layout()
    return fig


def fig_km(frame):
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    from lifelines import KaplanMeierFitter

    ends = {}
    for t in TREATMENTS:
        g = frame[frame.treatment == t]
        km = KaplanMeierFitter().fit(g["duration"], g["event"])
        s = km.survival_function_
        ax.step(s.index, s.iloc[:, 0], where="post", color=COL[t], lw=2, ls=LS[t])
        ends[t] = float(s.iloc[-1, 0])
    ax.set_ylim(0.4, 1.02)
    ax.set_xlim(0, 440)
    _spread_labels(ax, 364, ends)
    ax.set_xlabel("days since trial start")
    ax.set_ylabel("tree survival probability")
    ax.set_title("Kaplan-Meier survival by treatment (24 plots, 240 trees)", loc="left", fontsize=10.5)
    return fig


def fig_water(engine):
    v = read_sql(engine, "SELECT * FROM mart.v_vwc_daily_treatment ORDER BY 1")
    c = read_sql(engine, "SELECT * FROM mart.v_irrigation_cum_treatment ORDER BY 1")
    w = read_sql(engine, "SELECT date, et0_mm, rain_mm FROM mart.weather_clean ORDER BY date")
    fig, axes = plt.subplots(3, 1, figsize=(7, 7.2), sharex=True, gridspec_kw=dict(height_ratios=[1, 1.3, 1.3]))
    a0, a1, a2 = axes
    ends = {}
    a0.plot(pd.to_datetime(w["date"]), w["et0_mm"].rolling(7, min_periods=3).mean(), color=MUTED, lw=1.6)
    a0.bar(pd.to_datetime(w["date"]), w["rain_mm"], color=COL["CTRL"], width=1.5)
    a0.set_ylabel("mm/day")
    a0.set_title("Reference ET₀ (7-day mean, line) and rainfall (bars)", loc="left", fontsize=10)
    for t in TREATMENTS:
        g = v[v.treatment == t]
        x = pd.to_datetime(g["time"]).dt.tz_localize(None)
        a1.plot(x, g["vwc"].rolling(5, min_periods=1).mean(), color=COL[t], lw=1.6, ls=LS[t])
        h = c[c.treatment == t]
        xh = pd.to_datetime(h["time"]).dt.tz_localize(None)
        a2.plot(xh, h["cum_irrigation_kl"], color=COL[t], lw=2, ls=LS[t])
        ends[t], xl = float(h["cum_irrigation_kl"].iloc[-1]), xh.iloc[-1]
    a2.set_xlim(a2.get_xlim()[0], a2.get_xlim()[1] + 35)
    _spread_labels(a2, xl, ends)
    a1.set_ylabel("VWC (m³/m³)")
    a1.set_title("Root-zone soil moisture, treatment mean of QC-passed hourly data (5-day smooth)", loc="left", fontsize=10)
    a2.set_ylabel("kL per plot")
    a2.set_title("Cumulative metered irrigation per plot", loc="left", fontsize=10)
    fig.tight_layout()
    return fig


def fig_ndvi(engine):
    n = read_sql(engine, "SELECT * FROM mart.v_ndvi_treatment ORDER BY 1")
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ends = {}
    for t in TREATMENTS:
        g = n[n.treatment == t]
        x = pd.to_datetime(g["time"]).dt.tz_localize(None)
        ax.plot(x, g["ndvi"].rolling(3, min_periods=1).mean(), color=COL[t], lw=2, ls=LS[t])
        ends[t], xl = float(g["ndvi"].rolling(3, min_periods=1).mean().iloc[-1]), x.iloc[-1]
    _spread_labels(ax, xl, ends)
    ax.set_ylabel("plot-mean NDVI (cloud-screened)")
    ax.set_title("Sentinel-2 NDVI by treatment (QC-passed scenes)", loc="left", fontsize=10.5)
    return fig


def fig_calibration(R):
    ser, par = R["_series"], R["calibration_params"]
    from ..models import calibration as C

    pid = par.sort_values("loss").iloc[len(par) // 2]["plot_id"]
    p = par[par.plot_id == pid].iloc[0]
    s = ser[pid]
    vwc, irr = C.simulate_plot((p.theta_fc, p.kc_min, p.kc_max), s.et0, s.rain, s.canopy)
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.3))
    x = np.arange(len(vwc))
    axes[0].plot(x, s.vwc, color=COL["CTRL"], lw=1.2, label="sensor")
    axes[0].plot(x, vwc, color=COL["COMP"], lw=1.2, label="model")
    axes[0].axvline(273, color=MUTED, ls=":", lw=1)
    axes[0].text(276, axes[0].get_ylim()[0], "hold-out", fontsize=8, color=MUTED, va="bottom")
    axes[0].set_title(f"Plot {pid}: daily VWC", loc="left", fontsize=10)
    axes[0].legend(loc="upper left", fontsize=8)
    wo, wm = C._weekly(s.irr_mm), C._weekly(irr)
    axes[1].plot(wo, color=COL["CTRL"], lw=1.6, label="meter")
    axes[1].plot(wm, color=COL["COMP"], lw=1.6, label="model")
    axes[1].axvline(273 / 7, color=MUTED, ls=":", lw=1)
    axes[1].set_title("Weekly irrigation (mm)", loc="left", fontsize=10)
    axes[1].legend(loc="upper left", fontsize=8)
    truth = R["_plots"][["plot_id", "treatment"]]
    pt = pd.read_csv(Path(R["_truth_dir"]) / "plot_truth.csv") if R.get("_truth_dir") else None
    ax = axes[2]
    for i, t in enumerate(TREATMENTS):
        g = par[par.treatment == t]
        ax.plot([i] * len(g), g["theta_fc"], "o", color=COL[t], ms=6, mec="white", mew=1.2, alpha=0.95)
        ax.plot([i - 0.25, i + 0.25], [g["theta_fc"].mean()] * 2, color=INK, lw=1.4)
    ax.set_xticks(range(4), TREATMENTS, fontsize=8)
    ax.set_ylabel("calibrated θ_FC (m³/m³)")
    ax.set_title("Calibrated field capacity by plot (bar = mean)", loc="left", fontsize=10)
    fig.tight_layout()
    return fig


def fig_scenario(aq):
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.4))
    for ax, (st, ttl, col) in zip(axes, [("net", "Full irrigation (refill at 70 % TAW)", "irrigation_mm"), ("interval", "Deficit: ≤25 mm every 10 days", "dry_yield_t_ha")]):
        g = aq[aq.strategy == st]
        for i, t in enumerate(TREATMENTS):
            y = g[g.treatment == t][col if st == "interval" else "irrigation_mm"]
            ax.boxplot([y], positions=[i], widths=0.5, patch_artist=True, showfliers=False,
                       boxprops=dict(facecolor=COL[t], alpha=0.55, edgecolor=COL[t]), medianprops=dict(color=INK), whiskerprops=dict(color=MUTED), capprops=dict(color=MUTED))
        ax.set_xticks(range(4), TREATMENTS, fontsize=8)
        ax.set_ylabel("seasonal irrigation (mm)" if st == "net" else "dry yield (t/ha)")
        ax.set_title(ttl, loc="left", fontsize=10)
    fig.tight_layout()
    return fig


def fig_qc(R):
    rc = R["qc_recall"].copy()
    rc["label"] = rc["dataset"] + ": " + rc["fault"]
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    ax.barh(rc["label"], rc["recall"], color=COL["CTRL"], height=0.55)
    ax.set_xlim(0, 1.08)
    for y, (r, n) in enumerate(zip(rc["recall"], rc["n"])):
        ax.text(min(r, 1.0) + 0.01, y, f"{r:.0%} of {n}", va="center", fontsize=8, color=INK)
    ax.invert_yaxis()
    ax.set_xlabel("share of injected faults caught or handled")
    ax.grid(axis="y", visible=False)
    ax.set_title("QC recall against known injected faults", loc="left", fontsize=10.5)
    return fig


def fig_power(R):
    pt, mc, sv = R["power_table"], R["power_mc"], R["power_survival"]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.4))
    axes[0].plot(pt["n_blocks"], pt["power_contrast"], color=COL["CTRL"], lw=2, marker="o", ms=5, mec="white")
    axes[0].axhline(0.8, color=MUTED, lw=1, ls=":")
    axes[0].set_xlabel("blocks (replicates)")
    axes[0].set_ylabel("power")
    axes[0].set_title("SOC: one treatment vs control, true Δ = 0.10 % (analytic)", loc="left", fontsize=9.5)
    axes[1].plot(sv["n_blocks"], sv["power"], color=COL["COMP"], lw=2, marker="o", ms=5, mec="white")
    axes[1].axhline(0.8, color=MUTED, lw=1, ls=":")
    axes[1].set_xlabel("blocks (10 trees per plot)")
    axes[1].set_title("Survival: 0.78 vs 0.93 (simulation, over-dispersed)", loc="left", fontsize=9.5)
    axes[1].set_ylim(0, 1.02)
    axes[0].set_ylim(0, 1.02)
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------------- report

CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e6e5e1;--accent:#2a78d6;--card:#ffffff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#333330;--accent:#3987e5;--card:#242423}}
body{background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0}
main{max-width:980px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:1.7rem;margin:.2em 0}h2{font-size:1.25rem;margin-top:2.2em;border-bottom:1px solid var(--line);padding-bottom:.3em}h3{font-size:1.02rem;margin-top:1.6em}
.sub{color:var(--ink2)}figure{margin:1em 0;background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto}
figure img{max-width:100%;height:auto}figcaption{color:#52514e;font-size:.85rem;margin-top:4px}
table.t{border-collapse:collapse;font-size:.85rem;margin:.6em 0;display:block;overflow-x:auto}table.t th,table.t td{padding:4px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.t th{color:var(--ink2);font-weight:600}table.t td:first-child,table.t th:first-child{text-align:left}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card b{display:block;font-size:1.25rem}.card span{color:var(--ink2);font-size:.85rem}
.note{border-left:3px solid var(--accent);padding:2px 12px;color:var(--ink2);margin:1em 0}
code{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 4px}
"""


def fig_external(R):
    """Station vs reanalysis (Tmax scatter, cumulative ET0 from three estimates) and block-level Sentinel-2 NDVI, when present."""
    panels = []
    if R.get("reanalysis_present"):
        panels += ["tmax", "et0"]
    if R.get("ndvi_block_present"):
        panels += ["ndvi"]
    if not panels:
        return ""
    fig, axs = plt.subplots(1, len(panels), figsize=(4.6 * len(panels), 3.6))
    axs = np.atleast_1d(axs)
    for ax, kind in zip(axs, panels):
        if kind == "tmax":
            st, re = R["_weather_pair"]
            m = st.merge(re, on="date", suffixes=("_s", "_r"))
            ax.scatter(m["tmax_r"], m["tmax_s"], s=6, color=COL["CTRL"], alpha=0.6)
            lo, hi = m[["tmax_r", "tmax_s"]].min().min(), m[["tmax_r", "tmax_s"]].max().max()
            ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.8, ls=(0, (4, 2)))
            ax.set_xlabel("ERA5-Land Tmax (°C)"); ax.set_ylabel("station Tmax (°C)"); ax.set_title("Station vs reanalysis (flagged days sit off the 1:1 line)", fontsize=9)
        elif kind == "et0":
            st, re = R["_weather_pair"]
            from ..models import fao56
            cfg_ = TrialConfig()
            et_r = fao56.eto_from_frame(re, cfg_.site_lat, cfg_.site_elevation_m)
            x = pd.to_datetime(re["date"])
            for y, lab, c, ls in [(st["et0_mm"].to_numpy(), "station (PM)", COL["COMP"], "-"), (et_r.to_numpy(), "ERA5-Land (PM)", COL["CTRL"], (0, (4, 2))),
                                  (re["et0_openmeteo_mm"].to_numpy(), "Open-Meteo ET0", COL["BIOC"], (0, (1, 1.5)))]:
                ax.plot(x, np.nancumsum(y), color=c, lw=1.6, ls=ls, label=f"{lab}: {np.nansum(y):.0f} mm")
            ax.legend(loc="upper left", fontsize=8)
            ax.set_ylabel("cumulative ET0 (mm)"); ax.set_title("ET0: three estimates", fontsize=9)
        else:
            nb = R["ndvi_block"]
            for b, g in nb.groupby("block"):
                ax.plot(pd.to_datetime(g["date"]), g["ndvi_mean"], lw=1, marker="o", ms=2, label=f"block {b}")
            ax.set_ylabel("NDVI (S2, 10 m, SCL-masked)"); ax.set_title("Block-level Sentinel-2 NDVI", fontsize=9)
            ax.legend(fontsize=7, ncol=2)
        ax.tick_params(axis="x", labelrotation=30)
    fig.tight_layout()
    return _img(fig, "external reference data")


def _external_section(R) -> str:
    ps, pp, pb = R["pixel_summary"], R["pixel_plot"], R["pixel_block"]
    h = ["<h2>3b. External reference data (Open-Meteo / ERA5-Land, Sentinel-2)</h2>"]
    if R.get("reanalysis_present"):
        et = R["weather_cmp_et0"]
        tot = R["weather_cmp_totals"]
        h.append("<p>Real ERA5-Land reanalysis is the weather forcing of the simulated plots and the independent reference for the (simulated) on-site station. "
                 "Disagreement raises <i>warn</i> flags (<code>weather.vs_reanalysis_residual</code>, <code>weather.vs_reanalysis_drift</code>) and never excludes or edits station data: "
                 "reanalysis (~9-11 km cell, modelled radiation and wind) is not ground truth.</p>")
        h.append(_tbl(R["weather_cmp_vars"], {"bias": "{:.2f}", "rmse": "{:.2f}", "r": "{:.3f}"}))
        h.append(f"<p>Trial-period ET0 totals: station {tot['station']:.0f} mm, ERA5-Land + our Penman-Monteith {tot['reanalysis_pm']:.0f} mm, Open-Meteo ET0 {tot['openmeteo']:.0f} mm. "
                 f"Our PM vs Open-Meteo on identical reanalysis: bias {et['reanalysisPM_vs_openmeteo']['bias_pct']:.1f} % (an implementation/aggregation check, since both are FAO-56).</p>")
    else:
        h.append("<p class='note'>No reanalysis was loaded in this run (offline default: fully synthetic weather). Run <code>aridtrial fetch-weather</code> and <code>aridtrial demo --reanalysis</code> to use real ERA5-Land forcing.</p>")
    if R.get("ndvi_block_present"):
        nb = R["ndvi_block"]
        h.append(f"<p>Real Sentinel-2 L2A NDVI, block-level zonal means (SCL cloud/shadow masked): {len(nb)} block-days. Real NDVI over a young desert plantation mostly reflects bare soil; "
                 "it is context and a cross-check on the synthetic NDVI series, not a treatment response.</p>")
    else:
        h.append("<p class='note'>No Sentinel-2 block statistics were loaded (needs CDSE credentials; <code>aridtrial fetch-ndvi</code>). The plot-level NDVI in this report is synthetic.</p>")
    h.append(fig_external(R))
    h.append("<h3>Pixel purity: can 10 m Sentinel-2 pixels resolve 12 × 20 m plots?</h3>"
             f"<p><b>{ps['plots_ge1_pure_px_nobuf']} of {ps['plots']} plots</b> contain at least one whole 10 m pixel (max {ps['max_pure_px_nobuf']}); with a 5 m edge buffer (co-registration / point-spread) "
             f"<b>{ps['plots_ge1_pure_px_buf5']} of {ps['plots']}</b>. Plot-level NDVI comparison between treatments is therefore not supported; use block zones (below) or drone/UAV imagery for plot-level canopy.</p>")
    h.append(_tbl(pb))
    return "\n".join(h)


def _sig(p):
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def build_report(engine, R: dict, cfg: TrialConfig, out: Path) -> Path:
    out = Path(out)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    from ..config import data_dir

    R["_truth_dir"] = str(data_dir() / "truth")
    con, emm = R["contrasts"], R["emm"]
    ep = con[con.review == "endpoint"]
    mid = con[con.review == "mid-trial"]

    def eff(resp, depth="0-15 cm", t="COMP_BIOC", rev=ep):
        r = rev[(rev.response == resp) & (rev.depth == depth) & (rev.contrast == f"{t} - CTRL")].iloc[0]
        return r

    r_soc, r_ec, r_h = eff("soc_pct"), eff("ec_dsm"), eff("height_cm", "trees (plot means)")
    cox = R["cox"].set_index("contrast")
    hr = cox.loc["COMP_BIOC vs CTRL"]
    qc = R["qc_summary"]
    ver = R["dataset_version"]["version_id"]
    cov = R["coverage_soc"]

    figs = {}
    figs["layout"] = f'<figure><img alt="RCBD layout" src="data:image/png;base64,{base64.b64encode(open(data_dir() / "truth" / "layout.png", "rb").read()).decode()}"><figcaption>RCBD: 6 blocks × 4 treatments, randomised within block (seeded, hash recorded in the audit log).</figcaption></figure>'
    figs["soc"] = _img(fig_traj(emm, "soc_pct", "0-15 cm", "SOC (%), 0-15 cm", "Soil organic carbon, model-estimated means ± 95 % CI"), "SOC trajectory by treatment (mixed-model EMMs). Bands are 95 % CIs of each mean and include between-block variability; treatments are compared through the paired contrasts in the table, whose intervals are much narrower.")
    figs["ec"] = _img(fig_traj(emm, "ec_dsm", "0-15 cm", "ECe (dS/m), 0-15 cm", "Salinity (ECe), model-estimated means ± 95 % CI"), "ECe trajectory by treatment (mixed-model EMMs). The wide bands are the strong block-to-block salinity gradient (block SD ≈ 2.5 dS/m) that the RCBD removes from the treatment contrasts, which is why contrasts are far tighter than the bands suggest.")
    figs["forest"] = _img(fig_forest(con, [("soc_pct", "0-15 cm", "SOC (% pts)"), ("ec_dsm", "0-15 cm", "ECe (dS/m)"), ("height_cm", "trees (plot means)", "Height (cm)"),
                                          ("bulk_density", "0-15 cm", "Bulk dens. (g/cm³)")], "Endpoint effect vs control, 95 % CI (unadjusted intervals; Holm p in table)"), "Endpoint treatment-vs-control effects.")
    figs["km"] = _img(fig_km(R["survival_frame"]), "Kaplan-Meier survival.")
    figs["water"] = _img(fig_water(engine), "Water balance inputs and outcomes.")
    figs["ndvi"] = _img(fig_ndvi(engine), "NDVI by treatment.")
    figs["cal"] = _img(fig_calibration(R), "FAO-56 calibration: example plot (left, middle) and calibrated field capacity by plot (right).")
    figs["aq"] = _img(fig_scenario(R["aquacrop"]), "AquaCrop-OSPy companion-forage scenario by treatment.")
    figs["qc"] = _img(fig_qc(R), "QC recall.")
    figs["power"] = _img(fig_power(R), "Design-stage power.")
    for k, v in figs.items():
        pass

    ev = R["calibration_eval"]
    cal_sum = ev[["cal_vwc_rmse", "val_vwc_rmse", "cal_irr_total_err_pct", "val_irr_total_err_pct", "val_weekly_irr_rmse_mm"]].agg(["mean", "median"]).T
    fc_anova = R["calibration_anova_fc"]
    aq = R["aquacrop"].groupby(["strategy", "treatment"]).agg(irrigation_mm=("irrigation_mm", "mean"), yield_t_ha=("dry_yield_t_ha", "mean"), yield_sd=("dry_yield_t_ha", "std"),
                                                              wp_kg_m3=("wp_kg_m3", "mean")).reset_index()
    cmp_cols = ["response", "depth", "contrast", "estimate", "lo", "hi", "p", "p_holm"]
    end_tbl = ep[cmp_cols].copy()
    mid_tbl = mid[cmp_cols].copy()

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aridtrial Analysis Report</title><style>{CSS}</style></head><body><main>
<h1>Desert soil-amendment trial: endpoint analysis report</h1>
<p class="sub">Synthetic demonstration · generated {datetime.now():%Y-%m-%d %H:%M} · frozen dataset <code>{ver}</code> · all data are simulated; true effects are known and used only for validation.</p>

<h2>1. Summary</h2>
<div class="cards">
<div class="card"><b>+{r_soc.estimate:.2f} % pts SOC</b><span>COMP_BIOC vs control at endpoint (0-15 cm), 95 % CI [{r_soc.lo:.2f}, {r_soc.hi:.2f}], Holm p {r_soc.p_holm:.1e}</span></div>
<div class="card"><b>{r_ec.estimate:+.2f} dS/m ECe</b><span>COMP_BIOC vs control, 95 % CI [{r_ec.lo:.2f}, {r_ec.hi:.2f}]</span></div>
<div class="card"><b>HR {hr.HR:.2f}</b><span>tree mortality, COMP_BIOC vs control, 95 % CI [{hr.lo:.2f}, {hr.hi:.2f}] (Cox, block-stratified, plot-clustered)</span></div>
<div class="card"><b>{r_h.estimate:+.1f} cm height</b><span>COMP_BIOC vs control, plot means, 95 % CI [{r_h.lo:.1f}, {r_h.hi:.1f}]</span></div>
<div class="card"><b>{100*(1-qc['pass_rate']['soil']):.1f} % soil rows flagged</b><span>QC pass rates: {', '.join(f'{k} {v:.1%}' for k, v in qc['pass_rate'].items())}</span></div>
<div class="card"><b>{cov.coverage95.mean():.0%} coverage</b><span>mean 95 % CI coverage of SOC endpoint contrasts over closed-loop replicate trials with known truth</span></div>
</div>
<p class="note">Inference unit is the <b>plot</b> (24 plots; 6 blocks × 4 treatments). Trees, soil layers and repeated visits are subsamples/repeated measures. Intervals use RCBD error df (15), not normal approximations.</p>

<h2>2. Design and power (before baseline sampling)</h2>
{figs['layout']}
<p>Randomised complete block design, blocks aligned with the expected salinity gradient. Design-stage standard deviations (block {cfg.assumed_sd_block}, plot {cfg.assumed_sd_plot}, residual {cfg.assumed_sd_resid} % SOC) give a minimum detectable SOC difference (80 % power, Bonferroni over 3 comparisons) shown below; tree survival needs more replicates than soil chemistry.</p>
{figs['power']}
<h3>Analytic power table (SOC contrast, Δ = 0.10)</h3>{_tbl(R['power_table'], {'power_contrast': '{:.3f}', 'mdd_80': '{:.3f}'})}
<h3>Simulation with the production mixed model (repeated measures, 3 contrasts, Bonferroni)</h3>{_tbl(R['power_mc'])}
<h3>Baseline randomisation check (T0, RCBD ANOVA of treatment)</h3>{_tbl(R['baseline_balance'][['response','F','p','means']])}
<p class="sub">Baseline variance components estimated from T0: block SD {R['baseline_variance']['sd_block']:.3f}, residual SD {R['baseline_variance']['sd_resid']:.3f} (n = {R['baseline_variance']['n']}), feeding back into the design check.</p>

<h2>3. Data, QC and audit</h2>
<p>All inputs land in an append-only raw layer; typed, de-duplicated core tables are rebuildable from raw; QC <i>flags</i> (never edits) and the analysis reads QC-filtered marts. Every ingest, QC run and export is a link in a SHA-256 hash chain; the endpoint dataset is frozen as version <code>{ver}</code> with per-table content hashes.</p>
{figs['qc']}
{_tbl(R['qc_recall'], {'recall': '{:.3f}'})}

{_external_section(R)}

<h2>4. Results</h2>
<h3>Mid-trial review ({MID_TRIAL}, day {CAMPAIGNS[MID_TRIAL]}) — data available at that point only</h3>{_tbl(mid_tbl)}
<h3>Endpoint review ({ENDPOINT}, day {CAMPAIGNS[ENDPOINT]})</h3>
{figs['soc']}{figs['ec']}{figs['forest']}
{_tbl(end_tbl)}
<h3>Tree survival</h3>{figs['km']}
<p>Primary: Cox PH stratified by block, SE clustered on plot. Cross-check: plot-level quasi-binomial GLM at the endpoint.</p>
{_tbl(R['cox'])}{_tbl(R['survival_glm'])}
<p class="sub">Mortality is interval-censored at campaign visits (event time = first campaign recorded dead); the PH assumption should be re-checked on real data with Schoenfeld residuals.</p>
<h3>Model diagnostics</h3>{_tbl(R['diagnostics'])}
<h3>Irrigation (metered)</h3>
<p>Seasonal depth by treatment, RCBD ANOVA F = {R['irrigation_anova']['F']:.2f}, p {'< 0.001' if R['irrigation_anova']['p'] < 1e-3 else '= ' + format(R['irrigation_anova']['p'], '.3f')}.</p>{_tbl(R['irrigation_contrasts'])}
{figs['ndvi']}

<h2>5. Water models</h2>
{figs['water']}
<h3>FAO-56 single-Kc balance, calibrated per plot</h3>
<p>Fitted: θ<sub>FC</sub>, K<sub>c,min</sub>, K<sub>c,max</sub> (NDVI-driven canopy index) against daily soil moisture and weekly irrigation; calibration on days 0–272, <b>hold-out validation on days 273–364</b>. θ<sub>FC</sub> by treatment as a response (RCBD ANOVA): F = {fc_anova['F']:.2f}, p {'< 0.0001' if fc_anova['p'] < 1e-4 else '= ' + format(fc_anova['p'], '.4f')}.</p>
{figs['cal']}{_tbl(cal_sum.reset_index().rename(columns={'index':'metric'}))}
<h3>AquaCrop-OSPy companion-forage scenario</h3>
<p class="note">AquaCrop-OSPy is not calibrated against crop data here (none exist for this tree trial). It consumes the trial-calibrated soil water-holding capacity and asks how amendment-driven differences change irrigation need and Sorghum yield; uncertainty comes from bootstrapping the plot-level calibrations. Under refill-to-target irrigation, seasonal water need barely changes; under deficit irrigation, water-holding capacity governs yield.</p>
{figs['aq']}{_tbl(aq)}

<h2>6. Closed-loop validation (synthetic truth)</h2>
<p>The whole trial was simulated {R['n_cov']} times (new randomisation, new random effects, known true effects) and each replicate analysed with the production model. Bias, RMSE, 95 % CI coverage and Holm-adjusted power of the endpoint contrasts:</p>
<h3>SOC (0-15 cm), endpoint contrasts</h3>{_tbl(R['coverage_soc'])}
<h3>ECe (0-15 cm), endpoint contrasts</h3>{_tbl(R['coverage_ec'])}

<h2>7. Limitations</h2>
<ul>
<li>Synthetic data: effect sizes and variance components are assumptions; real baseline data should replace them in the power analysis.</li>
<li>Calibration uses a pedotransfer relation for θ<sub>WP</sub> with the same form as the simulator (optimistic parameter recovery); the hold-out period is the honest accuracy test.</li>
<li>Small-sample inference uses the RCBD between-plot df (15); Kenward–Roger (lme4/lmerTest) is the refinement for the final report.</li>
<li>Tree height and diameter are survivor-conditional plot means; survival is analysed separately and a joint model would be needed if mortality is informative about growth.</li>
<li>PostGIS spatial SQL (<code>sql/03_postgis.sql</code>) is provided but was not executed in the build environment (plain PostgreSQL only).</li>
<li>The live KoboToolbox, Open-Meteo and Copernicus (CDSE) clients are implemented and unit-tested against mocked payloads; they could not be called from the build environment (network blocked), so the first live run is a commissioning test. Reanalysis stands in for neither station nor field data; the on-site station and plot-level NDVI remain simulated.</li>
</ul>
</main></body></html>"""
    path = out / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
