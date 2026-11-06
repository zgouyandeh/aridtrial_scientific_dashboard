"""Tests for the synthetic input-data build and its output contract."""
from __future__ import annotations

import json

import pandas as pd

from aridtrial.config import TrialConfig
from aridtrial.simulate.builder import build
from aridtrial.simulate.weather import simulate_weather


def test_synthetic_build_writes_expected_input_and_truth_files(tmp_path):
    cfg = TrialConfig()
    result = build(cfg, tmp_path)

    assert len(result["layout"]) == cfg.n_blocks * len(cfg.treatments)
    assert len(result["sim"]["plots"]) == cfg.n_blocks * len(cfg.treatments)
    assert len(result["sim"]["sensors"]) > 0

    expected_files = [
        "drops/plot_layout.csv",
        "drops/sensors_hourly.csv",
        "drops/water_meters_daily.csv",
        "drops/ndvi_sentinel2.csv",
        "drops/weather_daily.csv",
        "kobo/soil_sampling.json",
        "kobo/tree_survey.json",
        "xlsform/soil_sampling.xlsx",
        "xlsform/tree_survey.xlsx",
        "truth/randomisation_record.json",
        "truth/injected_faults.csv",
        "truth/soil_truth.csv",
        "truth/tree_truth.csv",
        "truth/daily_truth.csv",
        "truth/plot_truth.csv",
        "truth/true_effects.json",
        "input_manifest.json",
    ]
    for relative_path in expected_files:
        assert (tmp_path / relative_path).is_file(), relative_path

    weather_drop = pd.read_csv(tmp_path / "drops/weather_daily.csv")
    assert "et0_mm" not in weather_drop.columns
    meter_drop = pd.read_csv(tmp_path / "drops/water_meters_daily.csv")
    assert "cum_volume_l" in meter_drop.columns
    assert "volume_l" not in meter_drop.columns

    truth_effects = json.loads((tmp_path / "truth/true_effects.json").read_text())
    assert set(truth_effects["soc_effect_end_pct"]) == set(cfg.treatments)


def test_build_with_real_reference_inputs_preserves_reference_drops(tmp_path):
    cfg = TrialConfig()
    real_weather = simulate_weather(cfg).drop(columns=["et0_mm"])
    reanalysis_path = tmp_path / "real_reanalysis.csv"
    real_weather.to_csv(reanalysis_path, index=False)

    ndvi_rows = []
    for block in range(1, cfg.n_blocks + 1):
        for date in cfg.dates[::10]:
            ndvi_rows.append({"block": block, "date": date, "ndvi_mean": 0.08 + block * 0.001,
                              "ndvi_std": 0.01, "n_valid": 8, "n_total": 10})
    ndvi_path = tmp_path / "real_ndvi.csv"
    pd.DataFrame(ndvi_rows).to_csv(ndvi_path, index=False)

    output_dir = tmp_path / "generated"
    build(cfg, output_dir, reanalysis_csv=reanalysis_path, ndvi_blocks_csv=ndvi_path)

    assert (output_dir / "drops/reanalysis_daily.csv").is_file()
    assert (output_dir / "drops/ndvi_blocks_s2.csv").is_file()
    assert len(pd.read_csv(output_dir / "drops/reanalysis_daily.csv")) == cfg.n_days


def test_synthetic_only_build_removes_stale_optional_reference_files(tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    (drops / "reanalysis_daily.csv").write_text("stale")
    (drops / "ndvi_blocks_s2.csv").write_text("stale")

    build(TrialConfig(), tmp_path)

    assert not (drops / "reanalysis_daily.csv").exists()
    assert not (drops / "ndvi_blocks_s2.csv").exists()
