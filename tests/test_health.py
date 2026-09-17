from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_reports_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "neon-agent"


def test_cors_allows_the_static_site():
    r = client.get("/api/health", headers={"Origin": "https://example.vercel.app"})
    assert r.headers["access-control-allow-origin"] in ("*", "https://example.vercel.app")


def test_area_lists_layers_and_gaps():
    body = client.get("/api/area").json()
    assert body["area_km2"] > 0
    assert any(layer["name"] == "tree" for layer in body["layers"])
    assert body["unavailable_layers"], "the area must say what it cannot evaluate"
    # The map needs the area in degrees.
    lon, lat = body["polygon_wgs84"]["coordinates"][0][0]
    assert 8 < lon < 9 and 47 < lat < 48


def test_layer_endpoint_returns_wgs84_geojson():
    body = client.get("/api/layers/tree").json()
    assert body["type"] == "FeatureCollection"
    lon, lat = body["features"][0]["geometry"]["coordinates"]
    assert 8 < lon < 9 and 47 < lat < 48


def test_missing_layer_explains_why():
    r = client.get("/api/layers/underground_utility")
    assert r.status_code == 404
    assert "reason" in r.json()["detail"]


def test_the_root_points_at_the_docs_and_the_website():
    # Hitting the API port in a browser used to return a bare "Not Found".
    body = client.get("/").json()
    assert body["openapi_docs"] == "/docs"
    assert "static site" in body["note"]
    assert body["endpoints"]["study_area"] == "/api/area"
