"""Offline checks for source metadata and the prepared-input lineage manifest."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from aridtrial.config import TrialConfig
from aridtrial.preparation.manifest import write_input_manifest
from aridtrial.sources import copernicus, openmeteo


def _hourly_payload():
    t = pd.date_range("2025-10-01", periods=48, freq="h")
    return {"latitude": 24.2, "longitude": 54.6, "elevation": 40, "timezone": "Asia/Dubai",
            "hourly": {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t],
                       "temperature_2m": [30.0] * 48, "relative_humidity_2m": [40.0] * 48,
                       "precipitation": [0.0] * 48, "shortwave_radiation": [100.0] * 48,
                       "wind_speed_10m": [3.0] * 48, "et0_fao_evapotranspiration": [0.1] * 48}}


def test_openmeteo_metadata_identifies_real_source_and_request(tmp_path):
    class Response:
        content = json.dumps(_hourly_payload()).encode()
        url = "https://archive-api.open-meteo.com/v1/archive?test=1"
        def raise_for_status(self): pass
        def json(self): return _hourly_payload()
    class Session:
        def get(self, url, params, timeout): return Response()

    _, meta = openmeteo.fetch_daily_weather(24.2, 54.6, date(2025, 10, 1), date(2025, 10, 2),
                                            cache_dir=tmp_path, session=Session(), today=date(2026, 11, 5))
    assert meta["source_type"] == "real"
    assert meta["source_name"] == "open_meteo_archive"
    assert meta["dataset"] == "ERA5-Land"
    assert meta["request_params"]["models"] == "era5_land"
    assert meta["response_sha256"] and meta["retrieved_at"]


def test_copernicus_raw_response_and_provenance(tmp_path):
    class Response:
        def __init__(self, body):
            self.body = body
            self.content = json.dumps(body).encode()
        def raise_for_status(self): pass
        def json(self): return self.body
    class Session:
        def post(self, url, json, headers, timeout):
            return Response({"status": "OK", "data": [
                {"interval": {"from": "2025-10-04T00:00:00Z", "to": "2025-10-05T00:00:00Z"},
                 "outputs": {"ndvi": {"bands": {"B0": {"stats": {"mean": 0.08, "stDev": 0.01, "sampleCount": 10, "noDataCount": 1}}}}}}
            ]})

    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    df, meta = copernicus.fetch_block_ndvi({1: ring}, date(2025, 10, 1), date(2025, 10, 31),
                                           session=Session(), token="test-token", cache_dir=tmp_path)
    assert len(df) == 1 and meta["source_type"] == "real"
    assert meta["product"] == "sentinel-2-l2a" and meta["block_geometries_utm"]["1"] == ring
    assert meta["response_sha256"][1] and meta["request_sha256"][1]
    assert (tmp_path / meta["raw_response_files"][1]).exists()


def test_input_manifest_keeps_real_and_synthetic_lineage_separate(tmp_path):
    weather = tmp_path / "reanalysis_daily.csv"
    ndvi = tmp_path / "ndvi_blocks_s2.csv"
    pd.DataFrame({"date": ["2025-10-01", "2025-10-02"], "tmax": [35, 36]}).to_csv(weather, index=False)
    pd.DataFrame({"date": ["2025-10-01"], "block": [1], "ndvi_mean": [0.08]}).to_csv(ndvi, index=False)
    (tmp_path / "reanalysis_provenance.json").write_text('{"source_name":"open_meteo_archive"}')
    (tmp_path / "ndvi_provenance.json").write_text('{"source_name":"cdse_sentinel_2_l2a"}')

    output = tmp_path / "prepared"
    output.mkdir()
    path = write_input_manifest(output, TrialConfig(), weather, ndvi)
    manifest = json.loads(path.read_text())
    assert manifest["real_context_inputs"]["weather_reanalysis"]["source_type"] == "real"
    assert manifest["real_context_inputs"]["weather_reanalysis"]["source_provenance"]["source_name"] == "open_meteo_archive"
    assert manifest["generated_datasets"]["station_weather"]["source_type"] == "synthetic"
    assert manifest["generated_datasets"]["station_weather"]["derived_from"] == ["weather_reanalysis"]
    assert manifest["generated_datasets"]["plot_level_ndvi"]["derived_from"] == ["sentinel2_block_ndvi"]
    assert manifest["generated_datasets"]["ground_truth"]["ingest"] is False
    assert manifest["generated_artifacts"] == []  # this test writes only the manifest, not a simulation build


def test_prepare_real_kobo_payloads_keeps_them_separate(tmp_path):
    from aridtrial.preparation.kobo import prepare_kobo_downloads

    soil_dir, tree_dir = tmp_path / "soil-asset", tmp_path / "tree-asset"
    soil_dir.mkdir(); tree_dir.mkdir()
    soil_payload = {"count": 1, "next": None, "previous": None, "results": [{
        "_uuid": "soil-instance-1", "meta/rootUuid": "uuid:soil-root-1",
        "_submission_time": "2026-01-02T10:00:00Z", "_geolocation": [24.24, 54.60],
        "plot_id": "B1-P1", "campaign": "T0", "sample_date": "2025-10-01", "collector": "field-team",
        "layers": [{"layers/depth_top_cm": "0", "layers/depth_bottom_cm": "15", "layers/soc_pct": "0.8",
                     "layers/ec_dsm": "2.1", "layers/ph": "7.2", "layers/bulk_density": "1.4"}],
    }]}
    tree_payload = {"count": 1, "next": None, "previous": None, "results": [{
        "_uuid": "tree-instance-1", "meta/rootUuid": "uuid:tree-root-1",
        "_submission_time": "2026-01-02T10:00:00Z", "_geolocation": [24.24, 54.60],
        "plot_id": "B1-P1", "campaign": "T0", "obs_date": "2025-10-01",
        "trees": [{"trees/tree_no": "1", "trees/alive": "yes", "trees/height_cm": "90", "trees/stem_diam_mm": "12"}],
    }]}
    (soil_dir / "submissions.json").write_text(json.dumps(soil_payload))
    (tree_dir / "submissions.json").write_text(json.dumps(tree_payload))
    (soil_dir / "provenance.json").write_text(json.dumps({"asset_uid": "soil-asset", "source_type": "real"}))
    (tree_dir / "provenance.json").write_text(json.dumps({"asset_uid": "tree-asset", "source_type": "real"}))

    output = tmp_path / "prepared"
    result = prepare_kobo_downloads(soil_dir / "submissions.json", tree_dir / "submissions.json", output)
    soil = pd.read_csv(output / "soil_samples_real.csv")
    trees = pd.read_csv(output / "tree_observations_real.csv")
    manifest = json.loads((output / "provenance.json").read_text())
    assert result["soil_rows"] == 1 and result["tree_rows"] == 1
    assert soil.loc[0, "source_type"] == "real" and soil.loc[0, "status"] == "current"
    assert trees.loc[0, "source_name"] == "kobotoolbox_api_v2" and trees.loc[0, "status"] == "current"
    assert manifest["database_ingest"] is False
    assert manifest["soil"]["asset_uid"] == "soil-asset"
