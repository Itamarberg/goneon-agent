from fastapi.testclient import TestClient
from shapely.geometry import shape

from api.main import app
from data.store import load_layer

client = TestClient(app)


def test_catalog_says_which_constraints_can_actually_be_checked():
    body = client.get("/api/catalog").json()
    by_id = {c["id"]: c for c in body["constraints"]}
    assert by_id["not-on-building"]["evaluable"] is True
    blocked = by_id["tree-fahrleitung"]
    assert blocked["evaluable"] is False and blocked["not_evaluable_reason"]
    assert by_id["not-on-building"]["description"]


def test_zone_preview_returns_wgs84_and_shrinks_the_area():
    r = client.post(
        "/api/zones",
        json={
            "constraints": [
                {
                    "id": "not-on-building",
                    "title": "not on a building",
                    "type": "not_within",
                    "layer": "building",
                    "hard": True,
                    "source": {"text": "geometric sanity", "kind": "convention"},
                }
            ]
        },
    )
    body = r.json()
    assert 0 < body["allowed_area_m2"] < body["area_m2"]
    lon, lat = body["forbidden"]["coordinates"][0][0][0]
    assert 8 < lon < 9 and 47 < lat < 48


def test_check_endpoint_flags_a_tree_inside_a_building():
    building = shape(load_layer("building").features[0].geometry)
    p = building.representative_point()
    r = client.post(
        "/api/check",
        json={
            "features": [
                {
                    "id": "t1",
                    "kind": "tree",
                    "geometry": {"type": "Point", "coordinates": [p.x, p.y]},
                }
            ],
            "constraints": [
                {
                    "id": "not-on-building",
                    "title": "not on a building",
                    "type": "not_within",
                    "layer": "building",
                    "hard": True,
                    "source": {"text": "geometric sanity", "kind": "convention"},
                }
            ],
        },
    )
    body = r.json()
    assert body["summary"]["violation"] == 1
    # The finding's geometry is reprojected for the map like everything else.
    lon, lat = body["findings"][0]["geometry"]["coordinates"]
    assert 8 < lon < 9 and 47 < lat < 48


def test_check_reports_an_unevaluable_constraint_instead_of_passing_it():
    r = client.post(
        "/api/check",
        json={
            "features": [
                {
                    "id": "t1",
                    "kind": "tree",
                    "geometry": {"type": "Point", "coordinates": [2682000, 1248000]},
                }
            ],
            "constraints": [
                {
                    "id": "tree-sewer",
                    "title": "2 m from the sewer",
                    "type": "min_distance",
                    "layer": "sewer",
                    "params": {"d_m": 2.0},
                    "hard": True,
                    "source": {"text": "planner's own rule", "kind": "user"},
                }
            ],
        },
    )
    body = r.json()
    assert body["summary"]["not_evaluable"] == 1
    assert body["summary"]["violation"] == 0
    assert body["constraints_checked"][0]["evaluable"] is False


def test_project_converts_a_map_click_to_metres():
    # The browser has degrees; every check needs LV95 metres.
    r = client.post("/api/project", json={"lon": 8.5313, "lat": 47.3752})
    body = r.json()
    assert 2_681_000 < body["x"] < 2_684_000
    assert 1_247_000 < body["y"] < 1_249_000
