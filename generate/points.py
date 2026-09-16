"""Place point objects — trees, bike racks, benches, charging stations.

The whole generator is four steps and no domain knowledge:

    allowed area  →  candidate grid  →  soft cost per candidate  →  greedy pick

What changes between disciplines is the constraint set, not this code
(docs/PLAN.md §4). Three strategies produce three variants that are all legal
but differ in what they optimise, which is what gives the planner a choice.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from checks.plan import check_features, has_hard_violation
from checks.zones import Zones, compute_zones
from data.study_area import DEFAULT_AREA_ID
from domain.models import Constraint, Feature, Geometry, Tradeoff, Variant
from generate.scoring import cost_surface

# Candidate resolution. 2 m is finer than the placement tolerance of a street
# tree and keeps a 1 km² area at ~250k candidates before filtering.
GRID_STEP_M = 2.0
MAX_CANDIDATES = 400_000

# When a planner asks for 12 trees in a square kilometre, the binding constraint
# spacing (say 8 m) is not what they mean by "12 trees here" — taking the first
# 12 candidates in sweep order puts them all in one row along the edge. So the
# objects are spread over the ground that is actually available: an even layout
# of n objects over an area A sits about sqrt(A/n) apart, and this backs off
# from that until the target can be met.
SPREAD_FACTOR = 0.85
SPREAD_BACKOFF = 0.7


def candidate_grid(allowed: BaseGeometry, step_m: float = GRID_STEP_M) -> np.ndarray:
    """A regular lattice of positions inside the allowed area.

    A grid rather than random sampling: it is reproducible, it gives the
    "regular spacing" strategy something to work with, and the same request
    always returns the same plan.
    """
    if allowed.is_empty:
        return np.empty((0, 2))

    xmin, ymin, xmax, ymax = allowed.bounds
    nx = max(1, int((xmax - xmin) / step_m))
    ny = max(1, int((ymax - ymin) / step_m))
    if nx * ny > MAX_CANDIDATES:
        # Coarsen rather than refuse: a bigger area still gets a plan, just at a
        # coarser placement resolution, and the response says so.
        step_m *= np.sqrt(nx * ny / MAX_CANDIDATES)
        nx = max(1, int((xmax - xmin) / step_m))
        ny = max(1, int((ymax - ymin) / step_m))

    xs = xmin + step_m / 2 + np.arange(nx) * step_m
    ys = ymin + step_m / 2 + np.arange(ny) * step_m
    gx, gy = np.meshgrid(xs, ys)
    points = np.column_stack([gx.ravel(), gy.ravel()])

    inside = shapely.contains_xy(allowed, points[:, 0], points[:, 1])
    return points[inside]


def _greedy_select(
    points: np.ndarray,
    order: np.ndarray,
    spacing_m: float,
    target: int,
) -> list[int]:
    """Take candidates in `order`, skipping any too close to one already taken.

    O(n · k) in the number taken, which stays small (tens of objects). Doing it
    greedily is what makes min_spacing hold by construction rather than by luck.
    """
    taken: list[int] = []
    taken_xy: list[tuple[float, float]] = []
    for idx in order:
        x, y = points[idx]
        if spacing_m > 0 and any(
            (x - tx) ** 2 + (y - ty) ** 2 < spacing_m**2 for tx, ty in taken_xy
        ):
            continue
        taken.append(int(idx))
        taken_xy.append((x, y))
        if len(taken) >= target:
            break
    return taken


def _spread_spacing(allowed_area_m2: float, target: int | None, floor_m: float) -> float:
    """Spacing that distributes `target` objects over the available ground.

    Never below the floor, which is whatever min_spacing the constraints (or the
    planner) require: spreading is a preference, the constraint is not.
    """
    if not target or target <= 1 or allowed_area_m2 <= 0:
        return floor_m
    return max(floor_m, SPREAD_FACTOR * math.sqrt(allowed_area_m2 / target))


def _select_spread(
    points: np.ndarray,
    order: np.ndarray,
    floor_spacing_m: float,
    target: int | None,
    allowed_area_m2: float,
) -> tuple[list[int], float]:
    """Pick objects spread across the area, relaxing the spread until they fit.

    Returns the chosen indices and the spacing actually achieved, which the
    variant reports — "12 trees, 178 m apart" is a fact a planner can judge.
    """
    if target is None:
        return _greedy_select(points, order, floor_spacing_m, len(points)), floor_spacing_m

    spacing = _spread_spacing(allowed_area_m2, target, floor_spacing_m)
    while True:
        chosen = _greedy_select(points, order, spacing, target)
        if len(chosen) >= target or spacing <= floor_spacing_m + 1e-9:
            return chosen, spacing
        spacing = max(floor_spacing_m, spacing * SPREAD_BACKOFF)


def _farthest_point_select(
    points: np.ndarray, target: int, floor_spacing_m: float
) -> tuple[list[int], float]:
    """Place each object as far as possible from the ones already placed.

    Greedy sweep order fills the first row and stops, which is why 12 trees came
    out in a line along one edge. Farthest-point sampling instead covers the
    whole area at any count, and is deterministic: it starts from the candidate
    nearest the centre of the available ground.

    Returns the chosen indices and the smallest gap between any two of them.
    """
    centre = points.mean(axis=0)
    start = int(np.argmin(np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1])))
    chosen = [start]

    # distance from every candidate to the nearest chosen object, updated per pick
    nearest = np.hypot(points[:, 0] - points[start, 0], points[:, 1] - points[start, 1])
    achieved = float("inf")

    while len(chosen) < target:
        pick = int(np.argmax(nearest))
        gap = float(nearest[pick])
        if gap < floor_spacing_m:
            # Everything left is too close to something already placed; the
            # min_spacing constraint wins over the wish to spread.
            break
        chosen.append(pick)
        achieved = min(achieved, gap)
        nearest = np.minimum(
            nearest, np.hypot(points[:, 0] - points[pick, 0], points[:, 1] - points[pick, 1])
        )

    return chosen, (achieved if math.isfinite(achieved) else floor_spacing_m)


def _best_fit_select(
    points: np.ndarray,
    cost: np.ndarray,
    target: int,
    floor_spacing_m: float,
    tolerance: float,
) -> tuple[list[int], float]:
    """Spread within the ground that best satisfies the soft constraints.

    Ranking by cost alone puts every object in whichever street the sweep order
    reached first, because thousands of positions tie at the same cost. So the
    cheap positions are pooled and then sampled for spread: the planner gets
    objects that both sit where the constraints want them and cover the area.

    `tolerance` is the one dial: how much worse than the best position a
    candidate may be, as a fraction of the cost range, and still count as "an
    equally good place to stand". Near zero it is the strictest fit; wider, it
    trades constraint quality for room to spread.
    """
    order = np.argsort(cost, kind="stable")
    best, worst = float(cost[order[0]]), float(cost[order[-1]])
    pool = order[cost[order] <= best + tolerance * (worst - best) + 1e-9]
    if len(pool) < target * 4:  # too tight to spread within; widen to the cheapest
        pool = order[: max(target * 4, 1)]

    local, gap = _farthest_point_select(points[pool], target, floor_spacing_m)
    return [int(pool[i]) for i in local], gap


def _spacing_from(constraints: list[Constraint], fallback: float | None) -> float:
    """The binding min_spacing, whether it came from the catalog or the request."""
    spacings = [float(c.params["d_m"]) for c in constraints if c.type == "min_spacing"]
    if fallback:
        spacings.append(float(fallback))
    return max(spacings) if spacings else 0.0


def _row_major_order(points: np.ndarray) -> np.ndarray:
    """Sweep the area in rows, which yields evenly spread positions (street trees)."""
    return np.lexsort((points[:, 0], points[:, 1]))


def _features(points: np.ndarray, indices: list[int], kind: str, variant_id: str) -> list[Feature]:
    return [
        Feature(
            id=f"{variant_id}-{n}",
            kind=kind,
            geometry={"type": "Point", "coordinates": [round(float(x), 2), round(float(y), 2)]},
            properties={"generated": True},
        )
        for n, (x, y) in enumerate(points[i] for i in indices)
    ]


def _tradeoffs(
    features: list[Feature], constraints: list[Constraint], findings: list
) -> list[Tradeoff]:
    """Group soft-constraint findings into one line per constraint.

    A planner comparing variants wants "3 racks are further from a stop than you
    asked, worst 78 m", not 3 separate findings.
    """
    soft = {c.id: c for c in constraints if not c.hard}
    grouped: dict[str, list] = {}
    for f in findings:
        if f.constraint_id in soft and f.severity == "warning":
            grouped.setdefault(f.constraint_id, []).append(f)

    out = []
    for cid, items in grouped.items():
        measured = [i.measured_m for i in items if i.measured_m is not None]
        required = items[0].required_m
        worst = (
            max(measured, default=None)
            if soft[cid].type == "max_distance"
            else min(measured, default=None)
        )
        out.append(
            Tradeoff(
                constraint_id=cid,
                title=soft[cid].title,
                count=len(items),
                worst_measured_m=worst,
                required_m=required,
                weight=soft[cid].weight,
            )
        )
    # Heaviest compromise first: what the planner said matters most, broken most.
    return sorted(out, key=lambda t: (-t.weight, -t.count))


# The planner's dial for B: how much worse than the best position a candidate
# may score and still count as good ground. Near 0 the fit is strict; near 1
# everything is good enough and B converges on A.
DEFAULT_FIT_TOLERANCE = 0.05

STRATEGIES = (
    (
        "A",
        "Even coverage",
        "max_count",
        "Hard rules only: spread as widely as the ground allows. Preferences are ignored, "
        "so compare against B to see what they cost.",
    ),
    (
        "B",
        "Best fit",
        "best_score",
        "Positions within {pct}% of the best fit for your preferences, spread within that "
        "ground. Turn the dial down for a stricter fit, up for more room.",
    ),
    (
        "C",
        "Regular rows",
        "regular",
        "Rows from the middle of the area outwards, evenly spaced. A deliberate layout, "
        "preferences ignored.",
    ),
)


def generate_points(
    area: Geometry,
    constraints: list[Constraint],
    object_kind: str = "object",
    target_count: int | None = None,
    spacing_m: float | None = None,
    zones: Zones | None = None,
    area_id: str = DEFAULT_AREA_ID,
    fit_tolerance: float = DEFAULT_FIT_TOLERANCE,
) -> list[Variant]:
    """Two or three plan variants for point objects. Deterministic.

    Every variant is verified by the independent checker before it is returned;
    a variant with a hard violation is a generator bug, so it is dropped rather
    than shown (docs/PLAN.md §4).
    """
    zones = zones or compute_zones(area, constraints, area_id=area_id)
    if zones.is_empty:
        return []

    points = candidate_grid(zones.allowed)
    if len(points) == 0:
        return []

    cost, _scored_with = cost_surface(points, constraints, area_id)
    floor_spacing = _spacing_from(constraints, spacing_m)
    allowed_area = zones.allowed.area

    orders = {
        # Sweep in rows: packs in as many as spacing allows.
        "max_count": _row_major_order(points),
        # Rows again, but from the area's centre outwards, which reads as a
        # deliberate layout rather than a corner-first fill.
        "regular": _regular_order(points),
    }

    variants: list[Variant] = []
    fit_tolerance = min(max(float(fit_tolerance), 0.0), 1.0)
    for label_id, label, strategy, description in STRATEGIES:
        description = description.format(pct=round(fit_tolerance * 100))
        if strategy == "max_count" and target_count:
            # Even coverage: spread over the whole area rather than filling from
            # one corner until the count is reached.
            chosen, spacing = _farthest_point_select(points, target_count, floor_spacing)
        elif strategy == "best_score" and target_count:
            chosen, spacing = _best_fit_select(
                points, cost, target_count, floor_spacing, fit_tolerance
            )
        else:
            chosen, spacing = _select_spread(
                points, orders[strategy], floor_spacing, target_count, allowed_area
            )
        if not chosen:
            continue
        variant_id = f"points-{strategy}"
        features = _features(points, chosen, object_kind, variant_id)
        findings = check_features(features, constraints, area_id)

        if has_hard_violation(findings):
            # Loud on purpose: the generator and the checker disagreeing is the
            # one failure this architecture must never hide.
            import logging

            logging.getLogger(__name__).error(
                "variant %s violates a hard constraint it was generated under", variant_id
            )
            continue

        variants.append(
            Variant(
                id=variant_id,
                label=f"{label_id} — {label}",
                strategy=strategy,
                description=description,
                features=features,
                metrics={
                    "count": len(features),
                    "mean_soft_cost": round(float(np.mean(cost[chosen])), 3),
                    # What the constraints required, and what the layout achieved.
                    "fit_tolerance": fit_tolerance,
                    "required_spacing_m": round(floor_spacing, 1),
                    "achieved_spacing_m": round(spacing, 1),
                    "candidates_considered": len(points),
                },
                findings=findings,
                tradeoffs=_tradeoffs(features, constraints, findings),
            )
        )

    return _deduplicate(variants)


def _regular_order(points: np.ndarray) -> np.ndarray:
    """Row-major, but starting from the middle row outwards."""
    mid_y = float(np.median(points[:, 1]))
    return np.lexsort((points[:, 0], np.abs(points[:, 1] - mid_y)))


def _deduplicate(variants: list[Variant]) -> list[Variant]:
    """Drop variants that came out identical, so the planner never compares twins."""
    seen: dict[tuple, Variant] = {}
    for v in variants:
        key = tuple(sorted(tuple(f.geometry["coordinates"]) for f in v.features))
        if key not in seen:
            seen[key] = v
    return list(seen.values())
