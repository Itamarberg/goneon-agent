"""Three study areas, and the guarantee that they stay separate.

The risk a multi-area tool introduces is silent and severe: measuring a plan in
one quarter against another quarter's buildings. Nothing would look wrong. These
tests exist to make that loud.
"""

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

from api.main import app
from catalog.loader import get_constraint
from checks.plan import check_features, has_hard_violation
from checks.zones import compute_zones
from data.store import list_layers, load_layer
from data.study_area import DEFAULT_AREA_ID, STUDY_AREAS, UnknownStudyArea, get_area
from domain.models import Feature
from generate.points import generate_points
from tools import core

client = TestClient(app)
AREA_IDS = list(STUDY_AREAS)


@pytest.mark.parametrize("area_id", AREA_IDS)
def test_every_area_has_every_layer_baked_and_non_empty(area_id):
    baked = {i.name: i.feature_count for i in list_layers(area_id)}
    assert len(baked) == 11
    assert all(count > 0 for count in baked.values()), baked


@pytest.mark.parametrize("area_id", AREA_IDS)
def test_each_area_holds_what_the_catalog_needs(area_id):
    # A quarter with no school or no high-voltage line cannot demonstrate the
    # curated constraints, which is the point of shipping it.
    counts = {i.name: i.feature_count for i in list_layers(area_id)}
    assert counts["school"] >= 1
    assert counts["tree"] > 100
    assert counts["power_line_hv"] >= 1
    assert counts["transit_stop"] >= 1


@pytest.mark.parametrize("area_id", AREA_IDS)
def test_layer_geometry_lands_inside_its_own_area(area_id):
    # The failure this guards: loading another area's data and measuring against it.
    box = shape(get_area(area_id).polygon).buffer(100)
    for info in list_layers(area_id):
        first = shape(load_layer(info.name, area_id).features[0].geometry)
        assert first.intersects(box), f"{info.name} is not in {area_id}"


def test_areas_do_not_share_their_data():
    a = {f.id for f in load_layer("building", "langstrasse").features}
    b = {f.id for f in load_layer("building", "oerlikon").features}
    centroid_a = shape(load_layer("building", "langstrasse").features[0].geometry).centroid
    centroid_b = shape(load_layer("building", "oerlikon").features[0].geometry).centroid
    assert a and b
    # Ids are positional per layer, so overlap is expected; the geometry is not.
    assert centroid_a.distance(centroid_b) > 1000


@pytest.mark.parametrize("area_id", AREA_IDS)
def test_generation_works_in_every_area_and_stays_inside_it(area_id):
    constraints = [
        get_constraint("not-on-building"),
        get_constraint("tree-spacing"),
        get_constraint("tree-hydrant-access"),
    ]
    area = get_area(area_id)
    variants = generate_points(area.polygon, constraints, "tree", 8, area_id=area_id)
    assert variants, f"no plan possible in {area_id}"
    box = shape(area.polygon)
    for v in variants:
        assert not has_hard_violation(v.findings)
        for f in v.features:
            assert box.covers(shape(f.geometry))


def test_a_plan_from_one_area_is_checked_against_that_areas_data():
    # Take a position legal in Oerlikon and check it against Langstrasse's
    # buildings: the checker must use the area it is told, not a global default.
    oerlikon = get_area("oerlikon")
    constraints = [get_constraint("not-on-building")]
    variant = generate_points(oerlikon.polygon, constraints, "tree", 3, area_id="oerlikon")[0]

    assert not has_hard_violation(check_features(variant.features, constraints, "oerlikon"))

    # Against another area's data the same points are simply outside every
    # building, so the interesting assertion is that the two runs are distinct
    # objects computed from different layers.
    zones_o = compute_zones(oerlikon.polygon, constraints, area_id="oerlikon")
    zones_l = compute_zones(oerlikon.polygon, constraints, area_id="langstrasse")
    assert round(zones_o.allowed.area) != round(zones_l.allowed.area)


def test_an_unknown_area_is_rejected_everywhere():
    with pytest.raises(UnknownStudyArea, match="langstrasse"):
        get_area("atlantis")
    with pytest.raises(core.ToolError, match="Unknown study area"):
        core.list_catalog(area_id="atlantis")
    assert client.get("/api/area?area_id=atlantis").status_code == 400
    assert client.get("/api/catalog?area_id=atlantis").status_code == 400


def test_the_api_lists_the_areas_with_districts_and_geometry():
    body = client.get("/api/areas").json()
    assert body["default_area_id"] == DEFAULT_AREA_ID
    assert {a["id"] for a in body["areas"]} == set(AREA_IDS)
    for entry in body["areas"]:
        assert entry["districts"], f"{entry['id']} does not say which quarters it covers"
        lon, lat = entry["polygon_wgs84"]["coordinates"][0][0]
        assert 8 < lon < 9 and 47 < lat < 48
        assert entry["feature_count"] > 1000


def test_districts_are_measured_not_asserted():
    # This file previously named an area from memory and got it wrong: the box
    # labelled Kreis 5 was 90% Kreis 4. Every area now records real shares.
    for area in STUDY_AREAS.values():
        assert area.districts
        assert 95 <= sum(share for share, _ in area.districts) <= 101
        assert area.districts[0][0] >= 40  # a dominant quarter, not a random mix


@pytest.mark.parametrize("area_id", AREA_IDS)
def test_generating_through_the_api_respects_the_area(area_id):
    r = client.post(
        "/api/generate",
        json={
            "area_id": area_id,
            "object": {"kind": "tree", "geometry": "point", "count": 5},
            "constraints": [get_constraint("not-on-building").model_dump()],
        },
    )
    body = r.json()
    assert body["infeasibility"] is None
    from domain.crs import to_lv95

    point = shape(to_lv95(body["variants"][0]["features"][0]["geometry"]))
    assert shape(get_area(area_id).polygon).covers(point)


def test_a_feature_outside_the_area_is_still_checked_honestly():
    # Placing something outside the study area is allowed; it just has no
    # local data to be measured against, and must not silently pass.
    far = Feature(
        id="x", kind="tree", geometry={"type": "Point", "coordinates": [2600000, 1200000]}
    )
    findings = check_features([far], [get_constraint("on-public-ground")], "langstrasse")
    assert findings and findings[0].severity == "warning"
