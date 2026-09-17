"""The catalog is the shared asset of the product, so it is tested like code.

Adding a YAML file to catalog/constraints/ must be enough to ship a rule — and
must fail here if the file is wrong (ARCHITECTURE.md, extension point 1).
"""

import pytest
import yaml

from catalog.loader import CATALOG_DIR, CatalogError, for_object_kind, get_constraint, load_catalog
from checks.plan import describe, unevaluable_reason
from checks.registry import get


def test_catalog_loads_and_is_not_empty():
    assert len(load_catalog()) >= 8


def test_every_entry_is_valid_and_describable():
    for c in load_catalog():
        assert get(c.type)  # type exists
        assert describe(c)  # can be put in words for a planner
        assert c.source.text, f"{c.id} has no source"


def test_a_convention_never_dresses_itself_up_as_a_regulation():
    # kind is what the UI badges. A convention carrying a legal-looking URL would
    # let a planner cite it to an authority as if it were a rule.
    for c in load_catalog():
        assert c.source.kind in ("curated", "user", "convention")
        if c.source.kind == "convention":
            assert c.source.url is None, f"{c.id}: a convention must not cite a source URL"


def test_every_curated_entry_names_its_regulation_or_guideline():
    for c in load_catalog():
        if c.source.kind == "curated":
            assert len(c.source.text) > 20, f"{c.id}: source text is too vague to check"


def test_unverified_curated_entries_are_marked_not_verified():
    # verified=true means the source sentence was actually read. Nothing may claim
    # it without a quote.
    for c in load_catalog():
        if c.verified and c.source.kind == "curated":
            assert c.source.quote


def test_a_constraint_ships_that_cannot_be_evaluated():
    # The product's honesty case: the rule exists, the data does not, and the tool
    # says so instead of passing it.
    c = get_constraint("tree-fahrleitung")
    reason = unevaluable_reason(c)
    assert reason and "not published" in reason


def test_nisv_has_no_default_threshold():
    # There is no cited distance for the NISV limit, so the catalog must not invent
    # one (docs/PLAN.md §2, principle 2).
    c = get_constraint("nisv-sensitive-use")
    assert c.params["d_m"] == 0.0
    assert "proxy" in (c.note or "").lower()


def test_filtering_by_object_kind_keeps_generic_rules():
    tree_rules = {c.id for c in for_object_kind("tree")}
    assert "tree-werkleitung" in tree_rules
    assert "not-on-building" in tree_rules  # applies_to is None: applies to anything
    assert "lev-building-clearance" not in tree_rules  # power lines only


def test_unknown_constraint_id_lists_what_exists():
    with pytest.raises(CatalogError, match="not-on-building"):
        get_constraint("no-such-rule")


def test_a_broken_file_is_rejected(tmp_path, monkeypatch):
    import catalog.loader as loader

    bad = tmp_path / "broken-rule.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "id": "broken-rule",
                "title": "Broken",
                "type": "min_distance",
                "layer": "building",
                "params": {},  # missing d_m
                "source": {"text": "x", "kind": "user"},
            }
        )
    )
    monkeypatch.setattr(loader, "CATALOG_DIR", tmp_path)
    loader.load_catalog.cache_clear()
    try:
        with pytest.raises(CatalogError, match="d_m"):
            loader.load_catalog()
    finally:
        loader.load_catalog.cache_clear()


def test_ids_match_file_names():
    for path in CATALOG_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["id"] == path.stem


def test_every_hard_rule_is_a_regulation_or_an_impossibility_and_says_when_it_cannot_check():
    # Hard rules start selected for every plan, so the planner never chose them.
    # Each must therefore either be checkable in every study area or carry a
    # reason the tool can show — a silent pass would be a false sense of safety.
    from data.sources import UNAVAILABLE_LAYERS
    from data.study_area import STUDY_AREAS

    hard = [c for c in load_catalog() if c.hard]
    assert {"not-on-building", "not-in-water"} <= {c.id for c in hard}
    for c in hard:
        for area_id in STUDY_AREAS:
            reason = unevaluable_reason(c, area_id)
            if reason is not None:
                assert c.layer in UNAVAILABLE_LAYERS, f"{c.id}: unexplained gap in {area_id}"


def test_every_point_kind_has_preferences_the_planner_can_rank():
    # Importance only means something when there is more than one preference
    # competing for the same ground.
    for kind in ("tree", "bike_rack", "bench", "charging_station"):
        soft = [c for c in for_object_kind(kind) if not c.hard]
        assert len(soft) >= 2, f"{kind}: {[c.id for c in soft]}"
        for c in soft:
            assert c.source.kind == "convention" or c.verified, c.id
