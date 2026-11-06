"""Real-data integration, tested offline: parsing, unit conversions, guards, QC recall, pixel geometry, DB ingest.
No network is used; Open-Meteo / CDSE responses are mocked (the live services were not reachable from the build environment)."""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from aridtrial.config import TrialConfig
from aridtrial.design import pixel_purity as PX
from aridtrial.design.layout import make_layout
from aridtrial.qc import reanalysis as RQ
from aridtrial.simulate.weather import station_from_reanalysis, weather_from_reanalysis
from aridtrial.sources import copernicus, openmeteo

from conftest import AS_OF, fake_reanalysis


def _hourly_payload(days=3, w_ms=4.0, rad=100.0):
    t = pd.date_range("2025-10-01", periods=24 * days, freq="h")
    hr = t.hour.to_numpy()
    return dict(latitude=24.2, longitude=54.6, elevation=40, timezone="Asia/Dubai", utc_offset_seconds=14400, hourly=dict(
        time=[x.strftime("%Y-%m-%dT%H:%M") for x in t],
        temperature_2m=list(30 + 10 * np.sin((hr - 9) / 24 * 2 * np.pi)), relative_humidity_2m=list(50 - 30 * np.sin((hr - 9) / 24 * 2 * np.pi)),
        precipitation=[0.1] * len(t), shortwave_radiation=[rad] * len(t), wind_speed_10m=[w_ms] * len(t), et0_fao_evapotranspiration=[0.2] * len(t)))


def test_hourly_to_daily_units():
    d = openmeteo.hourly_to_daily(_hourly_payload())
    assert len(d) == 3
    assert d["tmax"].iloc[0] == pytest.approx(40.0, abs=0.01) and d["tmin"].iloc[0] == pytest.approx(20.0, abs=0.01)
    assert d["rs_mj"].iloc[0] == pytest.approx(100 * 3600 * 24 / 1e6)          # W/m2 mean -> MJ/m2/day
    assert d["rain_mm"].iloc[0] == pytest.approx(2.4)
    assert d["wind_2m"].iloc[0] == pytest.approx(4.0 * 4.87 / np.log(67.8 * 10 - 5.42), rel=1e-6)   # FAO-56 eq. 47
    assert d["et0_openmeteo_mm"].iloc[0] == pytest.approx(4.8)
    assert d["rh_max"].iloc[0] > d["rh_min"].iloc[0]


def test_incomplete_day_is_nan_not_averaged():
    p = _hourly_payload()
    for k in p["hourly"]:
        p["hourly"][k] = p["hourly"][k][:-5]
    d = openmeteo.hourly_to_daily(p)
    assert d.iloc[-1][["tmax", "rs_mj"]].isna().all() and d.iloc[0][["tmax"]].notna().all()


def test_lag_guard_and_cache(tmp_path):
    with pytest.raises(openmeteo.ReanalysisError):
        openmeteo.fetch_hourly(24.2, 54.6, date(2026, 1, 1), date(2026, 3, 1), today=date(2026, 3, 3))

    class Resp:
        content = json.dumps(_hourly_payload()).encode()
        url = "https://archive-api.open-meteo.com/v1/archive?x"
        def raise_for_status(self): pass
        def json(self): return json.loads(self.content)

    class Sess:
        n = 0
        def get(self, url, params, timeout):
            Sess.n += 1
            assert params["models"] == "era5_land" and params["wind_speed_unit"] == "ms"
            return Resp()

    a = openmeteo.fetch_hourly(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 3), session=Sess(), cache_dir=tmp_path, today=date(2026, 10, 28))
    b = openmeteo.fetch_hourly(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 3), session=Sess(), cache_dir=tmp_path, today=date(2026, 10, 28))
    assert Sess.n == 1 and not a["cached"] and b["cached"] and a["sha256"] == b["sha256"]


def test_weather_from_reanalysis_gaps():
    cfg = TrialConfig()
    r = fake_reanalysis(cfg)
    w = weather_from_reanalysis(cfg, r)
    assert len(w) == cfg.n_days and w["et0_mm"].between(0.5, 14).all()
    r2 = r.copy(); r2.loc[10:12, "rs_mj"] = np.nan
    assert weather_from_reanalysis(cfg, r2)["rs_mj"].notna().all()                 # short gap interpolated
    r3 = r.copy(); r3.loc[10:20, "rs_mj"] = np.nan
    with pytest.raises(ValueError):
        weather_from_reanalysis(cfg, r3)                                             # long gap refused, not hidden


def _station_with_faults(cfg):
    r = fake_reanalysis(cfg)
    truth = weather_from_reanalysis(cfg, r)
    st = station_from_reanalysis(cfg, truth, np.random.default_rng(3))
    return r, truth, st


def test_station_discrepancy_is_realistic_and_clean_qc_is_quiet():
    cfg = TrialConfig()
    r, truth, st = _station_with_faults(cfg)
    assert (st["tmax"] > st["tmin"]).all() and (st["rh_max"] >= st["rh_min"]).all()
    assert 0.5 < (st["tmax"] - truth["tmax"]).std() < 1.5
    flags = RQ.check_weather_vs_reanalysis(st, r)
    assert flags["record_key"].nunique() <= 0.03 * len(st)                          # false-positive rate on clean station data
    assert (flags["severity"] == "warn").all() if len(flags) else True


def test_qc_catches_drift_and_offset():
    cfg = TrialConfig()
    r, truth, st = _station_with_faults(cfg)
    st = st.copy()
    st.loc[200:, "rs_mj"] *= 0.82
    st.loc[60:64, "tmax"] += 6.0
    flags = RQ.check_weather_vs_reanalysis(st, r)
    dates = pd.to_datetime(st["date"])
    off = {f"{d:%Y-%m-%d}" for d in dates[60:65]}
    assert off <= set(flags[flags.rule_id == "weather.vs_reanalysis_residual"]["record_key"])
    drift = flags[(flags.rule_id == "weather.vs_reanalysis_drift") & (flags.variable == "rs_mj")]
    assert len(drift) > 100 and drift["record_key"].min() >= f"{dates[190]:%Y-%m-%d}"


def test_compare_weather_et0_consistency():
    cfg = TrialConfig()
    r, truth, st = _station_with_faults(cfg)
    c = RQ.compare_weather(st, r, cfg)
    assert abs(c["et0"]["reanalysisPM_vs_openmeteo"]["bias_pct"]) < 3                # fake Open-Meteo ET0 = our PM +/- 2 % noise
    assert set(c["variables"]["variable"]) >= {"tmax", "rs_mj", "wind_2m"}


def test_pixel_purity_geometry():
    assert PX.pure_pixels((0, 0, 20, 20)) == 4 and PX.pure_pixels((0, 0, 20, 20), 5) == 0
    assert PX.pure_pixels((0, 0, 12, 20)) == 2           # aligned: one 10 m column, two rows
    assert PX.pure_pixels((5, 0, 17, 20)) == 0           # shifted 5 m: no whole pixel fits in the 12 m width (10..20 sticks out)
    assert PX.pure_pixels((8, 0, 20, 20)) == 2           # shifted so that pixel 10..20 fits
    p = PX.plot_purity(make_layout(TrialConfig()))
    assert p["pure_px_buf0m"].max() <= 1 and (p["pure_px_buf5m"] == 0).all()               # 12 x 20 m plots cannot hold a clean 10 m pixel with a buffer
    assert (PX.block_purity(make_layout(TrialConfig()))["pure_px_buf0m"] >= 4).all()


def test_copernicus_request_and_parse():
    ring = PX.block_zone_polygons(make_layout(TrialConfig()))[1]
    body = copernicus.stats_request(ring, date(2025, 10, 1), date(2025, 12, 31))
    assert body["input"]["bounds"]["properties"]["crs"].endswith("32640") and body["aggregation"]["resx"] == 10
    assert ring[0] == ring[-1] and "SCL" in body["aggregation"]["evalscript"] and "dataMask" in body["aggregation"]["evalscript"]
    resp = dict(status="OK", data=[
        dict(interval=dict(**{"from": "2025-10-04T00:00:00Z", "to": "2025-10-05T00:00:00Z"}), outputs=dict(ndvi=dict(bands=dict(B0=dict(stats=dict(mean=0.081, stDev=0.01, sampleCount=12, noDataCount=2)))))),
        dict(interval=dict(**{"from": "2025-10-09T00:00:00Z", "to": "2025-10-10T00:00:00Z"}), outputs=dict(ndvi=dict(bands=dict(B0=dict(stats=dict(mean="NaN", stDev="NaN", sampleCount=12, noDataCount=12)))))),
    ])
    df = copernicus.parse_statistics(resp, 1)
    assert len(df) == 2 and df["n_valid"].tolist() == [10, 0] and np.isnan(df["ndvi_mean"].iloc[1]) and df["ndvi_mean"].iloc[0] == pytest.approx(0.081)



def test_copernicus_needs_credentials(monkeypatch):
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False); monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    with pytest.raises(copernicus.CopernicusError):
        copernicus.get_token()


def test_end_to_end_with_reanalysis(engine2, tmp_path):
    from aridtrial.pipeline import audit, db, ingest
    from aridtrial.pipeline.db import read_sql
    from aridtrial.qc.run import evaluate_against_injected, run_qc
    from aridtrial.simulate.export import build

    cfg = TrialConfig()
    rcsv = tmp_path / "rean.csv"
    fake_reanalysis(cfg).to_csv(rcsv, index=False)
    out = tmp_path / "data"
    days = pd.date_range("2025-11-01", periods=6, freq="10D").date
    nb = pd.DataFrame([dict(block=b, date=d, ndvi_mean=0.07 + 0.01 * b, ndvi_std=0.01, n_valid=9, n_total=10) for b in (1, 2) for d in days])
    ncsv = tmp_path / "nb.csv"
    nb.to_csv(ncsv, index=False)
    build(cfg, out, reanalysis_csv=rcsv, ndvi_blocks_csv=ncsv)     # the source contract requires reference drops to come from the build, not be added by hand
    db.reset_database(engine2); db.apply_schema(engine2)
    res = ingest.ingest_all(engine2, out, cfg)
    assert res["reanalysis"]["rows"] == cfg.n_days and res["ndvi_block"]["rows"] == len(nb)
    again = ingest.ingest_all(engine2, out, cfg)
    assert again["reanalysis"]["skipped"] and again["ndvi_block"]["skipped"]                                       # idempotent, source identity unchanged
    qc = run_qc(engine2, cfg, as_of=AS_OF)
    rec = evaluate_against_injected(engine2, pd.read_csv(out / "truth" / "injected_faults.csv"), qc["run_id"])
    w = rec[rec.dataset == "weather"].set_index("fault")
    assert w.loc["rs_drift", "detected"] == 1 and w.loc["tmax_offset", "recall"] == 1.0
    assert w.loc["missing_values", "recall"] == 1.0 and w.loc["rh_swapped", "recall"] == 1.0
    assert len(read_sql(engine2, "SELECT * FROM mart.v_weather_vs_reanalysis")) == cfg.n_days and len(read_sql(engine2, "SELECT * FROM mart.v_ndvi_block")) == len(nb)
    # warn-severity reanalysis flags must not remove station data from the mart
    assert read_sql(engine2, "SELECT count(*) n FROM mart.weather_clean WHERE et0_mm IS NULL")["n"].iloc[0] <= 6
    v = audit.create_dataset_version(engine2, "t"); assert audit.verify_dataset_version(engine2, v["version_id"])["ok"] and audit.verify_chain(engine2)["ok"]
