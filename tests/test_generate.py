"""Generator tests.

The contract that matters: a returned variant never violates a hard constraint
it was generated under, and the same request always gives the same plan.
"""

import numpy as np
import pytest
from shapely.geometry import box

from catalog.loader import get_constraint
from checks.plan import has_hard_violation
from checks.zones import compute_zones
from data.study_area import STUDY_AREA
from domain.models import Constraint, Source
from generate.infeasible import explain_for_points
from generate.line import Grid, build_raster, generate_line
from generate.points import candidate_grid, generate_points


def user_constraint(**kw):
    base = dict(id="c", title="c", source=Source(text="planner", kind="user"))
    return Constraint(**{**base, **kw})


TREE_CONSTRAINTS = [
    get_constraint("not-on-building"),
    get_constraint("tree-hydrant-access"),
    get_constraint("tree-spacing"),
]


class TestPointGenerator:
    def test_returns_variants_that_pass_their_own_constraints(self):
        variants = generate_points(
            STUDY_AREA.polygon, TREE_CONSTRAINTS, object_kind="tree", target_count=15
        )
        assert variants, "the study area should support 15 trees"
        for v in variants:
            assert len(v.features) == 15
            # The independent checker, not the generator, has the last word.
            assert not has_hard_violation(v.findings)

    def test_is_deterministic(self):
        # A planner who reloads a share link must see the same plan.
        a = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 10)
        b = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 10)
        assert [f.geometry for f in a[0].features] == [f.geometry for f in b[0].features]

    def test_min_spacing_holds_between_generated_objects(self):
        variants = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 20)
        spacing = get_constraint("tree-spacing").params["d_m"]
        for v in variants:
            xy = np.array([f.geometry["coordinates"] for f in v.features])
            d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
            np.fill_diagonal(d, np.inf)
            assert d.min() >= spacing - 1e-6

    def test_a_soft_constraint_changes_the_variants_without_blocking_them(self):
        soft = get_constraint("on-public-ground")
        variants = generate_points(STUDY_AREA.polygon, [*TREE_CONSTRAINTS, soft], "tree", 20)
        costs = {v.id: v.metrics["mean_soft_cost"] for v in variants}
        # The strategy that optimises for the soft constraint must actually be better.
        assert costs["points-best_score"] < max(costs.values())
        best = next(v for v in variants if v.id == "points-best_score")
        assert len(best.features) == 20  # soft never costs us objects

    def test_tradeoffs_are_grouped_per_constraint_not_per_object(self):
        variants = generate_points(
            STUDY_AREA.polygon,
            [*TREE_CONSTRAINTS, get_constraint("on-public-ground")],
            "tree",
            20,
        )
        worst = max(variants, key=lambda v: v.metrics["mean_soft_cost"])
        assert worst.tradeoffs
        t = worst.tradeoffs[0]
        assert t.count > 1 and t.title

    def test_objects_are_spread_over_the_area_not_lined_up_on_one_edge(self):
        """The bug a planner spotted: 12 trees in a 1 km² quarter came back as a
        single 148 m row hugging the bottom edge, because the greedy sweep filled
        the first row and stopped at the target. Count and legality were both
        fine, which is why no test caught it."""
        variants = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 12)
        spreads = {}
        for v in variants:
            xy = np.array([f.geometry["coordinates"] for f in v.features])
            width = xy[:, 0].max() - xy[:, 0].min()
            height = xy[:, 1].max() - xy[:, 1].min()
            spreads[v.id] = (width, height)
            # Nothing may come back as a line.
            assert height > 50, f"{v.id} is {height:.0f} m tall — objects in one row"
            assert width > 50, f"{v.id} is {width:.0f} m wide"

        # The "even coverage" variant must genuinely cover the quarter.
        width, height = spreads["points-max_count"]
        assert width * height > 0.5e6, "even coverage spans less than half the area"

    def test_spread_never_breaks_the_spacing_constraint(self):
        # Spreading is a preference; min_spacing is a rule. It must still hold.
        required = get_constraint("tree-spacing").params["d_m"]
        for v in generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 25):
            assert v.metrics["achieved_spacing_m"] >= required
            assert not has_hard_violation(v.findings)

    def test_a_large_target_still_packs_in(self):
        # Spreading must not stop the generator reaching a count that fits.
        variants = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 60)
        assert all(len(v.features) == 60 for v in variants)

    def test_target_count_is_respected_and_never_exceeded(self):
        variants = generate_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, "tree", 3)
        assert all(len(v.features) == 3 for v in variants)

    def test_no_candidates_yields_no_variants(self):
        impossible = user_constraint(
            id="impossible", type="min_distance", layer="building", params={"d_m": 500.0}
        )
        assert generate_points(STUDY_AREA.polygon, [impossible], "tree", 5) == []

    def test_candidate_grid_stays_inside_the_allowed_area(self):
        zones = compute_zones(STUDY_AREA.polygon, [get_constraint("not-on-building")])
        points = candidate_grid(zones.allowed)
        sample = points[:: max(1, len(points) // 200)]
        assert all(zones.allowed.covers(_point(x, y)) for x, y in sample)


class TestLineGenerator:
    def _routable_endpoints(self, constraints):
        zones = compute_zones(STUDY_AREA.polygon, constraints)
        raster, cost, centres = build_raster(zones, constraints, 1.0)
        main = centres[Grid(raster, cost).main_cells]
        return (
            tuple(main[int(np.argmin(main[:, 0] + main[:, 1]))]),
            tuple(main[int(np.argmax(main[:, 0] + main[:, 1]))]),
        )

    def test_routes_pass_their_own_hard_constraints(self):
        constraints = [get_constraint("lev-building-clearance")]
        start, end = self._routable_endpoints(constraints)
        variants = generate_line(STUDY_AREA.polygon, constraints, start, end, "power_line")
        assert variants
        for v in variants:
            assert not has_hard_violation(v.findings)
            assert v.features[0].geometry["type"] == "LineString"
            assert v.metrics["length_m"] > 0

    def test_a_diagonal_step_never_clips_a_building_corner(self):
        # The bug this guards: two legal cell centres joined diagonally through a
        # forbidden corner. It passed the endpoint test and failed the checker.
        constraints = [get_constraint("lev-building-clearance")]
        start, end = self._routable_endpoints(constraints)
        zones = compute_zones(STUDY_AREA.polygon, constraints)
        for v in generate_line(STUDY_AREA.polygon, constraints, start, end, "power_line"):
            from shapely.geometry import shape

            assert zones.allowed.covers(shape(v.features[0].geometry))

    def test_an_endpoint_far_from_legal_ground_produces_no_route(self):
        # Rather than quietly relocating the planner's endpoint across the block.
        constraints = [get_constraint("lev-building-clearance")]
        xmin, ymin, xmax, ymax = STUDY_AREA.bbox
        assert (
            generate_line(
                STUDY_AREA.polygon, constraints, (xmin + 80, ymin + 80), (xmax - 80, ymax - 80)
            )
            == []
        )


class TestInfeasibility:
    def test_names_the_blocking_constraint_and_what_relaxing_it_gives(self):
        impossible = user_constraint(
            id="far-from-buildings",
            type="min_distance",
            layer="building",
            params={"d_m": 80.0},
        )
        report = explain_for_points(
            STUDY_AREA.polygon,
            [get_constraint("not-on-building"), impossible],
            target_count=5,
            spacing_m=8.0,
        )
        assert report.feasible is False
        assert report.blocking_constraint_id == "far-from-buildings"
        relaxation = report.relaxations[0]
        # Concrete and computed: a threshold that works, and what it is worth.
        assert relaxation.suggested_required_m < 80.0
        assert relaxation.positions_gained >= 1
        assert relaxation.freed_area_m2 > 0

    def test_a_satisfiable_request_is_not_called_impossible(self):
        # Explaining a request that actually works would be a lie; the explainer
        # re-verifies instead of trusting the caller.
        report = explain_for_points(STUDY_AREA.polygon, TREE_CONSTRAINTS, target_count=5)
        assert report.feasible is True
        assert report.relaxations == []

    def test_says_so_when_no_hard_constraint_is_to_blame(self):
        tiny = box(2682000, 1248000, 2682000.5, 1248000.5).__geo_interface__
        report = explain_for_points(tiny, [], target_count=5)
        assert report.feasible is False
        assert "hard constraint" in report.reason


def _point(x, y):
    from shapely.geometry import Point

    return Point(x, y)


@pytest.fixture(autouse=True)
def _quiet_generator_logging(caplog):
    """A hard violation in a variant is logged as an error; fail loudly if it happens."""
    yield
    assert not [r for r in caplog.records if "violates a hard constraint" in r.message]
