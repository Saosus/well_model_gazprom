from fastapi.testclient import TestClient

from scripts.local_frontend import app


client = TestClient(app)


def test_dashboard_html_uses_versioned_module_url_and_no_cache_headers():
    response = client.get("/")
    assert response.status_code == 200
    assert 'dashboard.js?v=1.0.0-2dp' in response.text
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.headers["pragma"] == "no-cache"


def test_dashboard_module_is_served_without_cache():
    response = client.get("/dashboard.js?v=1.0.0-2dp")
    assert response.status_code == 200
    assert 'from "./number-format.js"' in response.text
    assert "formatOperatingPoint(point.q_liq)" in response.text
    assert "formatOperatingPoint(point.p_wf)" in response.text
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"


def test_number_format_asset_is_served_without_cache():
    response = client.get("/number-format.js")
    assert response.status_code == 200
    assert 'maximumFractionDigits: 2' in response.text
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
