"""Explain why nothing could be placed, and what would change that.

"No plan is possible" is a useless answer. A planner needs to know *which*
constraint is binding and *what relaxing it would buy*, so they can decide
whether to argue for an exception, change the area, or drop a preference.

Everything here is computed by re-running the same zone and routing code with
one constraint removed or loosened. Nothing is inferred, so the agent can quote
it (ADR 0001).
"""

from __future__ import annotations

from checks.plan import unevaluable_reason
from checks.zones import compute_zones
from domain.models import Constraint, Geometry, InfeasibilityReport, Relaxation

# How close the binary search gets to the threshold that makes a request work.
THRESHOLD_TOLERANCE_M = 0.25
MAX_SEARCH_STEPS = 12


def _hard_evaluable(constraints: list[Constraint]) -> list[Constraint]:
    return [c for c in constraints if c.hard and not unevaluable_reason(c)]


def _allowed_area(area: Geometry, constraints: list[Constraint]) -> float:
    return compute_zones(area, constraints).allowed.area


def _relaxed_threshold(
    area: Geometry,
    constraints: list[Constraint],
    target: Constraint,
    feasible,
) -> float | None:
    """Largest threshold for `target` that still satisfies `feasible`.

    Binary search on the distance parameter. Deterministic and cheap: each step
    is one zone computation, and the caches make the layer geometry free after
    the first.
    """
    if "d_m" not in target.params:
        return None

    others = [c for c in constraints if c.id != target.id]
    if not feasible(others):
        return None  # even removing it entirely does not help

    low, high = 0.0, float(target.params["d_m"])
    for _ in range(MAX_SEARCH_STEPS):
        if high - low <= THRESHOLD_TOLERANCE_M:
            break
        mid = (low + high) / 2
        relaxed = target.model_copy(update={"params": {**target.params, "d_m": mid}})
        if feasible([*others, relaxed]):
            low = mid
        else:
            high = mid
    return round(low, 2)


def explain(
    area: Geometry,
    constraints: list[Constraint],
    feasible=None,
    reason: str = "No position in the area satisfies every hard constraint.",
) -> InfeasibilityReport:
    """Which hard constraint blocks the request, and what relaxing it gives.

    `feasible(constraints) -> bool` lets the caller decide what "possible" means:
    free area for points, a connected route for a line.
    """
    if feasible is None:

        def feasible(cs):
            return _allowed_area(area, cs) > 0

    # Verify rather than assume. Being asked to explain a request that in fact
    # works means the caller has a bug, and saying "impossible" would be a lie.
    if feasible(constraints):
        return InfeasibilityReport(
            feasible=True,
            reason="This request is satisfiable; there is nothing to relax.",
        )

    hard = _hard_evaluable(constraints)
    if not hard:
        return InfeasibilityReport(
            feasible=False,
            reason=(
                "No hard constraint is blocking this. The area itself is empty, or the "
                "constraints that would decide it cannot be evaluated with open data."
            ),
        )

    if feasible is None:

        def feasible(cs):  # noqa: E306 - local default, kept next to its use
            return _allowed_area(area, cs) > 0

    base_area = _allowed_area(area, constraints)
    relaxations: list[Relaxation] = []

    for constraint in hard:
        without = [c for c in constraints if c.id != constraint.id]
        freed = _allowed_area(area, without) - base_area
        suggested = _relaxed_threshold(area, constraints, constraint, feasible)
        relaxations.append(
            Relaxation(
                constraint_id=constraint.id,
                title=constraint.title,
                current_required_m=constraint.params.get("d_m"),
                suggested_required_m=suggested,
                freed_area_m2=round(float(freed), 1),
            )
        )

    # The constraint whose removal frees the most space is the one to talk about.
    relaxations.sort(key=lambda r: -(r.freed_area_m2 or 0))
    blocking = relaxations[0].constraint_id if relaxations else None

    return InfeasibilityReport(
        feasible=False,
        reason=reason,
        blocking_constraint_id=blocking,
        relaxations=relaxations,
    )


def explain_for_points(
    area: Geometry,
    constraints: list[Constraint],
    target_count: int | None = None,
    spacing_m: float | None = None,
) -> InfeasibilityReport:
    """Infeasibility for a point request, counting positions rather than area.

    Area alone is misleading: 300 m² of allowed space split into slivers holds no
    trees at 8 m spacing. Feasibility is "does the generator return anything".
    """
    from generate.points import candidate_grid

    def feasible(cs) -> bool:
        zones = compute_zones(area, cs)
        if zones.is_empty:
            return False
        return len(candidate_grid(zones.allowed)) > 0

    report = explain(area, constraints, feasible=feasible)

    # Say what each relaxation is actually worth, in positions.
    for relaxation in report.relaxations:
        target = next(c for c in constraints if c.id == relaxation.constraint_id)
        if relaxation.suggested_required_m is None:
            continue
        relaxed = target.model_copy(
            update={"params": {**target.params, "d_m": relaxation.suggested_required_m}}
        )
        others = [c for c in constraints if c.id != target.id]
        zones = compute_zones(area, [*others, relaxed])
        if not zones.is_empty:
            points = candidate_grid(zones.allowed)
            relaxation.positions_gained = _max_positions(points, spacing_m, target_count)
    return report


def _max_positions(points, spacing_m: float | None, target_count: int | None) -> int:
    """How many objects actually fit, not how many candidate cells exist."""
    from generate.points import _greedy_select, _row_major_order

    if len(points) == 0:
        return 0
    order = _row_major_order(points)
    chosen = _greedy_select(points, order, spacing_m or 0.0, target_count or len(points))
    return len(chosen)


def explain_for_line(
    area: Geometry,
    constraints: list[Constraint],
    start: tuple[float, float],
    end: tuple[float, float],
) -> InfeasibilityReport:
    """Infeasibility for a route: feasible means start and end are connected."""
    from generate.line import Grid, build_raster

    def feasible(cs) -> bool:
        zones = compute_zones(area, cs)
        if zones.is_empty:
            return False
        raster, cost, centres = build_raster(zones, cs, soft_weight=1.0)
        grid = Grid(raster, cost)
        s = grid.snap(centres, *start)
        e = grid.snap(centres, *end)
        return s is not None and e is not None and grid.least_cost_path(s[0], e[0]) is not None

    return explain(
        area,
        constraints,
        feasible=feasible,
        reason=(
            "No route between these two points stays inside the area the hard constraints allow."
        ),
    )
