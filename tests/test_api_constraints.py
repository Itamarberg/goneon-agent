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


class TestPlannerDefinedConstraints:
    """A rule typed into the form goes through the same door as one dictated to
    the agent: validated, never applied, and marked as the planner's own."""

    def draft(self, **kw):
        base = {
            "id": "my-rule",
            "title": "Trees stay clear of the school",
            "type": "min_distance",
            "layer": "school",
            "source_text": "Our own guideline, 2024",
            "d_m": 25.0,
        }
        return client.post("/api/constraints/draft", json={**base, **kw})

    def test_a_valid_rule_comes_back_as_an_unconfirmed_proposal(self):
        body = self.draft().json()
        assert body["confirmed"] is False
        assert body["needs_planner_confirmation"] is True
        assert body["proposal"]["source"]["kind"] == "user"
        assert body["proposal"]["verified"] is False
        assert body["evaluable"] is True
        assert "25" in body["description"]

    def test_a_missing_threshold_asks_the_planner_rather_than_inventing_one(self):
        body = self.draft(d_m=None).json()
        assert body["problems"]
        assert "ask the planner" in body["problems"][0].lower()

    def test_a_rule_against_data_we_do_not_have_says_so(self):
        body = self.draft(layer="sewer").json()
        assert body["evaluable"] is False
        assert "open data" in body["not_evaluable_reason"]

    def test_an_unknown_type_is_rejected(self):
        assert self.draft(type="vibes").status_code == 422

    def test_importance_is_carried_through(self):
        body = self.draft(hard=False, weight=8.0).json()
        assert body["proposal"]["weight"] == 8.0
        assert body["proposal"]["hard"] is False

    def test_the_form_can_be_built_from_the_type_list(self):
        body = client.get("/api/constraint-types").json()
        by_name = {t["name"]: t for t in body["types"]}
        assert set(by_name) == set(body["registered"])
        assert by_name["min_spacing"]["needs_layer"] is False
        assert by_name["within"]["needs_distance"] is False
