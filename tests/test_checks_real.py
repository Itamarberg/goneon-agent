"""The checks against the real baked layers.

These prove the pipeline works on actual Zurich data, at a size and speed the
website has to live with.
"""

import time

from shapely.geometry import Point, shape

from catalog.loader import get_constraint
from checks.plan import check_features, has_hard_violation, summarise
from checks.zones import compute_zones, zones_as_geojson
from data.store import load_layer
from data.study_area import STUDY_AREA
from domain.models import Feature


def _tree_on_a_real_building() -> Feature:
    building = shape(load_layer("building").features[0].geometry)
    return Feature(id="t1", kind="tree", geometry=building.representative_point().__geo_interface__)


def test_a_tree_inside_a_real_building_is_flagged():
    findings = check_features([_tree_on_a_real_building()], [get_constraint("not-on-building")])
    assert has_hard_violation(findings)
    assert "inside" in findings[0].message


def test_a_tree_on_top_of_a_real_hydrant_is_flagged_with_the_distance():
    hydrant = shape(load_layer("hydrant").features[0].geometry)
    tree = Feature(
        id="t1", kind="tree", geometry=Point(hydrant.x + 0.5, hydrant.y).__geo_interface__
    )
    findings = check_features([tree], [get_constraint("tree-hydrant-access")])
    assert findings[0].measured_m == 0.5
    assert findings[0].required_m == 2.0


def test_an_unevaluable_constraint_is_reported_once_not_per_object():
    trees = [
        Feature(id=f"t{i}", kind="tree", geometry=Point(2682000 + i, 1248000).__geo_interface__)
        for i in range(5)
    ]
    findings = check_features(trees, [get_constraint("tree-fahrleitung")])
    assert len(findings) == 1
    assert findings[0].severity == "not_evaluable"
    assert summarise(findings)["not_evaluable"] == 1


def test_constraints_do_not_fire_on_the_wrong_object_kind():
    # lev-building-clearance applies_to power_line; a bike rack must not trip it.
    rack = Feature(id="b1", kind="bike_rack", geometry=Point(2682000, 1248000).__geo_interface__)
    assert check_features([rack], [get_constraint("lev-building-clearance")]) == []


def test_zones_on_the_real_area_leave_usable_space_and_are_fast():
    constraints = [get_constraint("not-on-building"), get_constraint("tree-hydrant-access")]
    start = time.perf_counter()
    zones = compute_zones(STUDY_AREA.polygon, constraints)
    elapsed = time.perf_counter() - start

    geo = zones_as_geojson(zones)
    assert 0 < geo["allowed_area_m2"] < geo["area_m2"]
    # Re-running zones is what the UI does on every constraint tick; the caches
    # must make that cheap.
    assert elapsed < 5.0


def test_zone_preview_and_the_checker_agree():
    # A point taken from inside the allowed area must not violate the same
    # constraints. If these ever disagree, the preview is lying to the planner.
    constraints = [get_constraint("not-on-building"), get_constraint("tree-hydrant-access")]
    zones = compute_zones(STUDY_AREA.polygon, constraints)
    inside = zones.allowed.representative_point()
    tree = Feature(id="t1", kind="tree", geometry=inside.__geo_interface__)
    assert not has_hard_violation(check_features([tree], constraints))
