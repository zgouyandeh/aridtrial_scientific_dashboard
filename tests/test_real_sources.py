"""CDS (ERA5-Land NetCDF) conversion and real-NDVI background, tested offline with synthetic NetCDF / CSV."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from aridtrial.config import TrialConfig
from aridtrial.design import pixel_purity as PX
from aridtrial.design.layout import make_layout
from aridtrial.simulate.field import simulate_field
from aridtrial.simulate.ndvi_background import load_background
from aridtrial.simulate.weather import simulate_weather
from aridtrial.sources import cds


def _era5_nc(path, start="2025-09-30", end="2025-10-06", lat=24.2, lon=54.6, rain_mm_h=1.0, w_m2=100.0):
    """ERA5-Land-like hourly NetCDF: accumulations restart at 00 UTC, value stamped 00 UTC = previous day's full total (the real convention)."""
    t = pd.date_range(f"{start}T00:00", f"{end}T23:00", freq="h")
    n = len(t)
    hr = t.hour.to_numpy()
    acc_steps = np.where(hr == 0, 24, hr)                       # hours accumulated at each stamp
    tp = acc_steps * rain_mm_h / 1000.0                          # m
    ssrd = acc_steps * w_m2 * 3600.0                              # J m-2
    shape = (n, 2, 2)
    la, lo = np.array([lat + 0.05, lat - 0.05]), np.array([lon - 0.05, lon + 0.05])
    f = lambda a: np.broadcast_to(np.asarray(a, float)[:, None, None], shape).copy()
    ds = xr.Dataset({"t2m": (("valid_time", "latitude", "longitude"), f(273.15 + 30 + 10 * np.sin((hr - 9) / 24 * 2 * np.pi))),
                     "d2m": (("valid_time", "latitude", "longitude"), f(np.full(n, 273.15 + 5.0))),
                     "tp": (("valid_time", "latitude", "longitude"), f(tp)), "ssrd": (("valid_time", "latitude", "longitude"), f(ssrd)),
                     "u10": (("valid_time", "latitude", "longitude"), f(np.full(n, 3.0))), "v10": (("valid_time", "latitude", "longitude"), f(np.full(n, 4.0)))},
                    coords=dict(valid_time=t.values, latitude=la, longitude=lo))
    ds.to_netcdf(path)


class FakeClient:
    def __init__(self):
        self.calls = []

    def retrieve(self, dataset, request, target):
        self.calls.append((dataset, request))
        _era5_nc(target)


def test_build_requests_cover_buffer_and_months():
    r = cds.build_requests(24.24, 54.6, date(2025, 10, 1), date(2025, 12, 5))
    assert [(x["year"], x["month"]) for x in r] == [("2025", "09"), ("2025", "10"), ("2025", "11"), ("2025", "12")]
    assert r[0]["day"] == ["30"] and len(r[1]["time"]) == 24 and r[0]["area"][0] > r[0]["area"][2]
    assert r[-1]["day"][-1] == "06"                                                        # one day after `end` (UTC -> local shift)


def test_deaccumulation_and_units(tmp_path):
    client = FakeClient()
    d, meta = cds.fetch_daily_weather(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 4), tmp_path, client=client)
    assert len(d) == 4 and client.calls[0][0] == "reanalysis-era5-land"
    assert meta["source_type"] == "real" and meta["dataset"] == "reanalysis-era5-land"
    assert meta["requested_latitude"] == 24.2 and meta["files"]
    assert d["rain_mm"].tolist() == pytest.approx([24.0] * 4)                             # 1 mm/h x 24 h, no loss/double count at 00/01 UTC
    assert d["rs_mj"].tolist() == pytest.approx([100 * 3600 * 24 / 1e6] * 4)               # 100 W/m2 -> 8.64 MJ/m2/day
    assert d["tmax"].iloc[0] == pytest.approx(40.0, abs=0.1) and d["tmin"].iloc[0] == pytest.approx(20.0, abs=0.1)
    assert d["wind_2m"].iloc[0] == pytest.approx(5.0 * 4.87 / np.log(67.8 * 10 - 5.42), rel=1e-6)   # |(3,4)| = 5 m/s at 10 m -> 2 m
    es = lambda t: 0.6108 * np.exp(17.27 * t / (t + 237.3))
    assert d["rh_min"].iloc[0] == pytest.approx(100 * es(5.0) / es(40.0), rel=0.02) and (d["rh_max"] > d["rh_min"]).all()
    assert meta["grid"]["grid_lat"] in (24.25, 24.15) and len(meta["file_sha256"]) == 2


def test_cache_prevents_second_download(tmp_path):
    c = FakeClient()
    cds.fetch_daily_weather(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 4), tmp_path, client=c)
    cds.fetch_daily_weather(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 4), tmp_path, client=c)
    assert len(c.calls) == 2                                                               # Sep (buffer) + Oct, each downloaded once


def test_gap_in_hours_does_not_create_fake_rain():
    t = pd.date_range("2025-10-01T01:00", periods=6, freq="h", tz="UTC").delete(3)       # one missing hour
    v = pd.Series(np.arange(1, 7, dtype=float)[: len(t)], index=t)
    inc = cds.deaccumulate(v)
    assert inc.iloc[0] == 1.0 and np.isnan(inc.iloc[3])                                    # the step across the gap is refused, not attributed to one hour


def _ndvi_csv(path, cfg, level=0.07):
    days = pd.date_range(cfg.trial_start, periods=cfg.n_days, freq="5D")
    rows = [dict(block=b, date=d.date(), ndvi_mean=level + 0.01 * b + 0.01 * np.sin(i / 8), ndvi_std=0.01, n_valid=8, n_total=10)
            for b in range(1, cfg.n_blocks + 1) for i, d in enumerate(days)]
    pd.DataFrame(rows).to_csv(path, index=False)


def test_real_ndvi_background_used_by_simulator(tmp_path):
    cfg = TrialConfig()
    f = tmp_path / "nd.csv"
    _ndvi_csv(f, cfg)
    bg = load_background(f, cfg)
    assert bg.shape == (cfg.n_days, cfg.n_blocks) and bg.notna().all().all() and bg[6].mean() > bg[1].mean()
    lay, w = make_layout(cfg), simulate_weather(cfg)
    sim = simulate_field(cfg, lay, w, ndvi_background=bg)
    base = simulate_field(cfg, lay, w)
    m = sim["ndvi"].merge(lay[["plot_id", "block"]], on="plot_id")
    early = m[pd.to_datetime(m["date"]) < "2025-12-01"].groupby("block")["ndvi"].mean()
    assert early.is_monotonic_increasing                                                  # block ordering of the real background propagates
    assert abs(base["ndvi"]["ndvi"].mean() - sim["ndvi"]["ndvi"].mean()) < 0.03


def test_background_refuses_sparse_data(tmp_path):
    cfg = TrialConfig()
    pd.DataFrame(dict(block=[1], date=["2025-11-01"], ndvi_mean=[0.07], ndvi_std=[0.01], n_valid=[9], n_total=[10])).to_csv(tmp_path / "x.csv", index=False)
    with pytest.raises(ValueError):
        load_background(tmp_path / "x.csv", cfg)


def test_cli_zone_printing_and_real_flags(tmp_path, monkeypatch, capsys):
    from aridtrial import cli

    cli.main(["fetch-ndvi", "--lon", "54.62", "--lat", "24.10", "--print-zones"])
    out = capsys.readouterr().out
    assert "block 1:" in out and "block 6:" in out and "54.62" in out
    monkeypatch.setattr(cli, "_external_dir", lambda: tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["simulate", "--real"])                                                  # files missing -> clear error, not a silent synthetic fallback
