from fastapi.testclient import TestClient

from api.main import app
from catalog.loader import get_constraint

client = TestClient(app)


def catalog_json(*ids):
    return [get_constraint(i).model_dump() for i in ids]


def test_generate_points_returns_variants_in_wgs84():
    r = client.post(
        "/api/generate",
        json={
            "object": {"kind": "tree", "geometry": "point", "count": 8},
            "constraints": catalog_json("not-on-building", "tree-hydrant-access", "tree-spacing"),
        },
    )
    body = r.json()
    assert body["infeasibility"] is None
    assert len(body["variants"]) >= 1
    lon, lat = body["variants"][0]["features"][0]["geometry"]["coordinates"]
    assert 8 < lon < 9 and 47 < lat < 48


def test_an_impossible_request_is_explained_not_just_refused():
    r = client.post(
        "/api/generate",
        json={
            "object": {"kind": "tree", "geometry": "point", "count": 5},
            "constraints": [
                {
                    "id": "far-from-buildings",
                    "title": "80 m from every building",
                    "type": "min_distance",
                    "layer": "building",
                    "params": {"d_m": 80.0},
                    "hard": True,
                    "source": {"text": "planner's own rule", "kind": "user"},
                }
            ],
        },
    )
    body = r.json()
    assert body["variants"] == []
    report = body["infeasibility"]
    assert report["blocking_constraint_id"] == "far-from-buildings"
    assert report["relaxations"][0]["suggested_required_m"] < 80


def test_a_line_request_without_endpoints_is_rejected():
    r = client.post(
        "/api/generate",
        json={"object": {"kind": "power_line", "geometry": "line"}, "constraints": []},
    )
    assert r.status_code == 422
