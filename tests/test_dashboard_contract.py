from pathlib import Path

import pytest

from aridtrial.dashboard.service import DATASETS


def test_dashboard_dataset_contract_covers_registered_data_layers():
    names = {item["dataset"] for item in DATASETS}
    assert names == {
        "plot_layout", "soil_samples", "tree_observations", "sensor_readings",
        "station_weather", "water_meter_readings", "plot_level_ndvi",
        "weather_reanalysis", "sentinel2_block_ndvi",
    }


def test_dashboard_table_names_are_fixed_and_schema_qualified():
    for item in DATASETS:
        assert item["core_table"].startswith("core.")
        assert item["final_table"].startswith(("core.", "mart."))


def test_dashboard_static_assets_and_documentation_exist():
    root = Path(__file__).parents[1]
    static = root / "src/aridtrial/dashboard/static"
    assert (static / "index.html").is_file()
    assert list((static / "assets").glob("index-*.js")), "run `npm run build` in frontend/"
    assert list((static / "assets").glob("index-*.css"))
    for f in ("main.jsx", "AnalysisPage.jsx", "charts.jsx", "styles.css", "analysis.css"):
        assert (root / "frontend/src" / f).is_file()
    assert (root / "frontend/package.json").is_file()
    for d in ("DATA_QUALITY_DASHBOARD.md", "REACT_DASHBOARD.md", "SCIENTIFIC_DASHBOARD.md"):
        assert (root / "docs" / d).is_file()
    html = (static / "index.html").read_text()
    assert "unpkg" not in html and "babel" not in html.lower(), "single bundled frontend: no CDN / in-browser Babel"
    assert not (static / "dashboard.jsx").exists()


def test_dashboard_homepage_and_bundle_are_served():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aridtrial.dashboard.app import app

    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200 and 'id="root"' in home.text
    import re
    asset = re.search(r'/static/assets/index-[\w-]+\.js', home.text).group(0)
    assert client.get(asset).status_code == 200


def test_react_dashboards_expose_sections_and_safe_states():
    root = Path(__file__).parents[1]
    main = (root / "frontend/src/main.jsx").read_text()
    for needle in ("Data sources & pipeline flow", "Reconciliation checks", "Quality findings", "Not checked", "api('/api/overview')", "#/analysis/"):
        assert needle in main
    page = (root / "frontend/src/AnalysisPage.jsx").read_text()
    for tab in ("eda", "design", "comparison", "models", "inference", "power", "agronomy", "survival", "evaluation"):
        assert f"id: '{tab}'" in page
    assert "No analysis run stored yet" in page and "Synthetic data" in page
