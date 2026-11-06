"""Contract tests for explicit dataset source selection and mixing restrictions."""
from __future__ import annotations

import pytest

from aridtrial.preparation.source_selection import (
    SourceSelectionError,
    load_source_contract,
    validate_source_selection,
)


def test_default_contract_selects_explicit_sources():
    contract, digest, path = load_source_contract()
    selected = validate_source_selection(contract, reanalysis_csv=None, ndvi_blocks_csv=None)
    assert len(digest) == 64 and path.exists()
    assert set(selected) == set(contract["datasets"])
    assert selected["plot_layout"] == "synthetic"
    assert selected["station_weather"] == "synthetic_station"
    assert selected["plot_level_ndvi"] == "synthetic_plot"
    assert selected["ground_truth"] == "synthetic_validation_only"
    assert selected["weather_reanalysis"] == "absent"
    assert selected["sentinel2_block_ndvi"] == "absent"


def test_contract_accepts_real_reference_context(tmp_path):
    contract, _, _ = load_source_contract()
    weather = tmp_path / "weather.csv"
    ndvi = tmp_path / "ndvi.csv"
    weather.write_text("date,tmax\n2025-10-01,35\n")
    ndvi.write_text("block,date,ndvi_mean\n1,2025-10-01,0.08\n")
    selected = validate_source_selection(contract, reanalysis_csv=weather, ndvi_blocks_csv=ndvi)
    assert selected["weather_reanalysis"] == "real"
    assert selected["sentinel2_block_ndvi"] == "real"
    assert selected["plot_layout"] == "synthetic"
    assert selected["station_weather"] == "synthetic_station"
    assert selected["plot_level_ndvi"] == "synthetic_plot"


def test_contract_rejects_missing_selected_real_file(tmp_path):
    contract, _, _ = load_source_contract()
    with pytest.raises(SourceSelectionError, match="does not exist"):
        validate_source_selection(contract, reanalysis_csv=tmp_path / "missing.csv", ndvi_blocks_csv=None)


def test_contract_rejects_kobo_source_mixing():
    contract, _, _ = load_source_contract()
    contract["combination_rules"]["kobo"]["allow_real_synthetic_union"] = True
    with pytest.raises(SourceSelectionError, match="Kobo"):
        validate_source_selection(contract, reanalysis_csv=None, ndvi_blocks_csv=None)
