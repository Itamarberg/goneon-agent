import pytest
from pydantic import ValidationError

from domain.crs import to_lv95, to_wgs84
from domain.models import Constraint, Finding, Source


def _source() -> Source:
    return Source(text="LeV SR 734.31 Art. 38", kind="curated")


def test_constraint_round_trips_as_json():
    c = Constraint(
        id="lev-building-clearance",
        title="Power line keeps clearance from buildings",
        type="min_distance",
        layer="building",
        params={"d_m": 5.0},
        source=_source(),
    )
    assert Constraint.model_validate(c.model_dump()) == c


def test_unknown_constraint_type_is_rejected():
    # A typo in a catalog YAML must fail loudly rather than silently skip a check.
    with pytest.raises(ValidationError):
        Constraint(id="x", title="X", type="min_dist", source=_source())


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        Constraint(id="x", title="X", type="within", source=_source(), distance=5)


def test_finding_keeps_measured_and_required_as_numbers():
    f = Finding(
        constraint_id="lev-building-clearance",
        severity="violation",
        feature_id="tree-3",
        measured_m=1.2,
        required_m=2.0,
        message="1.2 m from a building, minimum is 2.0 m",
        source=_source(),
    )
    assert f.measured_m < f.required_m


def test_lv95_wgs84_round_trip_is_stable():
    # Zurich HB, roughly.
    lv95 = {"type": "Point", "coordinates": [2683200.0, 1247900.0]}
    wgs = to_wgs84(lv95)
    lon, lat = wgs["coordinates"]
    assert 8.4 < lon < 8.7 and 47.3 < lat < 47.5
    back = to_lv95(wgs)["coordinates"]
    assert abs(back[0] - 2683200.0) < 0.01
    assert abs(back[1] - 1247900.0) < 0.01


def test_polygon_reprojection_keeps_structure():
    poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [2683200.0, 1247900.0],
                [2683300.0, 1247900.0],
                [2683300.0, 1248000.0],
                [2683200.0, 1247900.0],
            ]
        ],
    }
    out = to_wgs84(poly)
    assert out["type"] == "Polygon"
    assert len(out["coordinates"][0]) == 4
