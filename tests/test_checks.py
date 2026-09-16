"""Check-type tests on synthetic geometry.

Deliberately not on the real layers: a check must be provable on a case whose
answer is known by hand. The real data is exercised in test_checks_real.py.
"""

import pytest
from shapely.geometry import Point, box

from checks.registry import CheckContext, get, registered_types
from checks.zones import compute_zones
from domain.models import Constraint, Feature, Source


def src(kind="user"):
    return Source(text="test", kind=kind)


def constraint(**kw):
    base = dict(id="c", title="c", source=src())
    return Constraint(**{**base, **kw})


def point_feature(x, y, kind="tree", fid="f1"):
    return Feature(id=fid, kind=kind, geometry=Point(x, y).__geo_interface__)


def evaluate(c, feature, siblings=None):
    return get(c.type).evaluate(feature, c, CheckContext(siblings=siblings or [feature]))


def test_all_five_types_are_registered():
    assert registered_types() == [
        "max_distance",
        "min_distance",
        "min_spacing",
        "not_within",
        "within",
    ]


class TestMinDistance:
    """A tree 1.2 m from a hydrant when the rule says 2 m is the canonical case."""

    def test_too_close_is_a_violation_with_the_numbers(self, monkeypatch):
        c = constraint(type="min_distance", layer="hydrant", params={"d_m": 2.0})
        _fake_layer(monkeypatch, "hydrant", [Point(0, 0)])
        f = point_feature(1.2, 0)
        finding = evaluate(c, f)
        assert finding.severity == "violation"
        assert finding.measured_m == 1.2
        assert finding.required_m == 2.0
        # The finding carries the conflict geometry so the map can show it.
        assert finding.geometry["type"] == "LineString"

    def test_exactly_at_the_threshold_passes(self, monkeypatch):
        # A boundary that silently fails would make every catalog default wrong by
        # an epsilon.
        c = constraint(type="min_distance", layer="hydrant", params={"d_m": 2.0})
        _fake_layer(monkeypatch, "hydrant", [Point(0, 0)])
        assert evaluate(c, point_feature(2.0, 0)) is None

    def test_soft_constraint_downgrades_to_a_warning(self, monkeypatch):
        c = constraint(type="min_distance", layer="hydrant", params={"d_m": 2.0}, hard=False)
        _fake_layer(monkeypatch, "hydrant", [Point(0, 0)])
        assert evaluate(c, point_feature(0.5, 0)).severity == "warning"

    def test_missing_threshold_is_an_error_not_a_pass(self):
        # The model is never allowed to supply this number, so its absence must
        # break loudly (ADR 0001).
        c = constraint(type="min_distance", layer="hydrant", params={})
        with pytest.raises(ValueError, match="d_m"):
            evaluate(c, point_feature(0, 0))


class TestMaxDistance:
    def test_too_far_is_a_finding(self, monkeypatch):
        c = constraint(type="max_distance", layer="transit_stop", params={"d_m": 50.0})
        _fake_layer(monkeypatch, "transit_stop", [Point(0, 0)])
        finding = evaluate(c, point_feature(80, 0, kind="bike_rack"))
        assert finding.measured_m == 80.0 and finding.required_m == 50.0

    def test_inside_the_radius_passes(self, monkeypatch):
        c = constraint(type="max_distance", layer="transit_stop", params={"d_m": 50.0})
        _fake_layer(monkeypatch, "transit_stop", [Point(0, 0)])
        assert evaluate(c, point_feature(20, 0, kind="bike_rack")) is None


class TestWithinAndNotWithin:
    def test_not_within_catches_a_point_inside_a_polygon(self, monkeypatch):
        c = constraint(type="not_within", layer="building")
        _fake_layer(monkeypatch, "building", [box(0, 0, 10, 10)])
        assert evaluate(c, point_feature(5, 5)).severity == "violation"

    def test_not_within_passes_outside(self, monkeypatch):
        c = constraint(type="not_within", layer="building")
        _fake_layer(monkeypatch, "building", [box(0, 0, 10, 10)])
        assert evaluate(c, point_feature(20, 20)) is None

    def test_within_reports_how_far_off_it_is(self, monkeypatch):
        c = constraint(type="within", layer="sidewalk")
        _fake_layer(monkeypatch, "sidewalk", [box(0, 0, 10, 10)])
        finding = evaluate(c, point_feature(13, 5))
        assert finding.measured_m == 3.0


class TestMinSpacing:
    def test_measures_against_the_other_planned_objects(self):
        c = constraint(type="min_spacing", params={"d_m": 8.0})
        a = point_feature(0, 0, fid="a")
        b = point_feature(5, 0, fid="b")
        finding = evaluate(c, a, siblings=[a, b])
        assert finding.measured_m == 5.0 and finding.required_m == 8.0

    def test_a_lone_object_never_violates_spacing(self):
        c = constraint(type="min_spacing", params={"d_m": 8.0})
        a = point_feature(0, 0, fid="a")
        assert evaluate(c, a, siblings=[a]) is None


class TestZones:
    def test_hard_min_distance_removes_area_soft_does_not(self, monkeypatch):
        _fake_layer(monkeypatch, "hydrant", [Point(50, 50)])
        area = box(0, 0, 100, 100).__geo_interface__
        hard = constraint(id="h", type="min_distance", layer="hydrant", params={"d_m": 10.0})
        soft = constraint(
            id="s", type="min_distance", layer="hydrant", params={"d_m": 10.0}, hard=False
        )

        z_hard = compute_zones(area, [hard])
        z_soft = compute_zones(area, [soft])
        assert z_hard.allowed.area < 10000
        # A preference must not shrink the buildable area behind the planner's back.
        assert z_soft.allowed.area == 10000
        assert "s" in z_soft.skipped

    def test_within_intersects_instead_of_subtracting(self, monkeypatch):
        _fake_layer(monkeypatch, "sidewalk", [box(0, 0, 20, 20)])
        area = box(0, 0, 100, 100).__geo_interface__
        c = constraint(type="within", layer="sidewalk")
        assert compute_zones(area, [c]).allowed.area == pytest.approx(400)

    def test_unevaluable_constraint_is_skipped_with_a_reason(self):
        area = box(0, 0, 100, 100).__geo_interface__
        c = constraint(id="u", type="min_distance", layer="sewer", params={"d_m": 2.0})
        zones = compute_zones(area, [c])
        assert zones.allowed.area == 10000  # it must not silently remove everything
        assert "open data" in zones.skipped["u"]


def _fake_layer(monkeypatch, name, geoms):
    """Swap one layer's geometry for a known one, and clear the geometry caches."""
    import checks.geometry as cg
    import data.store as store

    monkeypatch.setattr(store, "geometries", lambda n, _g=geoms, _n=name: _g if n == _n else [])
    monkeypatch.setattr(cg, "geometries", lambda n, _g=geoms, _n=name: _g if n == _n else [])
    monkeypatch.setattr(
        cg,
        "nearest_distance_m",
        lambda geom, layer, _g=geoms: _nearest(geom, _g),
    )
    cg.layer_union.cache_clear()
    cg.buffered_union.cache_clear()
    # checks.types imported these by value, so they must be rebound there too.
    import checks.types as ct

    monkeypatch.setattr(ct, "nearest_distance_m", lambda geom, layer, _g=geoms: _nearest(geom, _g))
    monkeypatch.setattr(ct, "layer_union", lambda layer, _g=geoms: _union(_g))
    monkeypatch.setattr(ct, "buffered_union", lambda layer, d, _g=geoms: _union(_g).buffer(d))


def _union(geoms):
    from shapely import unary_union

    return unary_union(geoms)


def _nearest(geom, geoms):
    best, best_g = float("inf"), None
    for g in geoms:
        d = geom.distance(g)
        if d < best:
            best, best_g = d, g
    return best, best_g
