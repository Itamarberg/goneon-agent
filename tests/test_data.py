"""The baked layers are a deliverable, not a fixture: these tests guard them."""

import pytest
from shapely.geometry import Point, shape

from data.sources import LAYER_SOURCES, UNAVAILABLE_LAYERS
from data.store import (
    LayerNotAvailable,
    get_features_in,
    list_layers,
    load_layer,
    query,
    study_area_summary,
)
from data.study_area import STUDY_AREA


def test_every_declared_layer_is_baked_and_non_empty():
    baked = {i.name: i for i in list_layers()}
    for src in LAYER_SOURCES:
        assert src.name in baked, f"{src.name} was declared but never fetched"
        assert baked[src.name].feature_count > 0, f"{src.name} is empty"


def test_every_layer_carries_attribution():
    # The product claims real, cited data; a layer without a source breaks that claim.
    for info in list_layers():
        assert info.source and info.licence


def test_geometry_lands_inside_the_study_area():
    area = shape(STUDY_AREA.polygon)
    for info in list_layers():
        layer = load_layer(info.name)
        assert shape(layer.features[0].geometry).intersects(area.buffer(50))


def test_coordinates_are_lv95_not_wgs84():
    # The classic bug in this kind of tool: degrees leaking into a metre computation.
    p = shape(load_layer("tree").features[0].geometry)
    assert 2_600_000 < p.x < 2_800_000
    assert 1_200_000 < p.y < 1_300_000


def test_unavailable_layer_explains_itself_instead_of_404():
    with pytest.raises(LayerNotAvailable) as e:
        load_layer("underground_utility")
    assert not e.value.evaluable
    assert "not open" in e.value.reason.lower() or "restricted" in e.value.reason.lower()


def test_unknown_layer_lists_what_exists():
    with pytest.raises(LayerNotAvailable) as e:
        load_layer("unicorns")
    assert "building" in e.value.reason


def test_clipping_to_a_small_area_returns_fewer_features():
    x, y = STUDY_AREA.center
    small = Point(x, y).buffer(100)
    clipped = get_features_in("building", small.__geo_interface__)
    assert 0 < len(clipped) < len(load_layer("building").features)


def test_query_finds_a_building_near_the_centre():
    x, y = STUDY_AREA.center
    hits = query("building", Point(x, y), distance_m=200)
    assert hits, "no buildings within 200 m of the study area centre"


def test_summary_lists_what_cannot_be_evaluated():
    s = study_area_summary()
    names = {u["name"] for u in s["unavailable_layers"]}
    assert names == set(UNAVAILABLE_LAYERS)
