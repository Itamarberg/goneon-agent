"""Place point objects — trees, bike racks, benches, charging stations.

The whole generator is four steps and no domain knowledge:

    allowed area  →  candidate grid  →  soft cost per candidate  →  greedy pick

What changes between disciplines is the constraint set, not this code
(docs/PLAN.md §4). Three strategies produce three variants that are all legal
but differ in what they optimise, which is what gives the planner a choice.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from checks.plan import check_features, has_hard_violation
from checks.zones import Zones, compute_zones
from domain.models import Constraint, Feature, Geometry, Tradeoff, Variant
from generate.scoring import cost_surface

# Candidate resolution. 2 m is finer than the placement tolerance of a street
# tree and keeps a 1 km² area at ~250k candidates before filtering.
GRID_STEP_M = 2.0
MAX_CANDIDATES = 400_000


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
            )
        )
    return sorted(out, key=lambda t: -t.count)


STRATEGIES = (
    ("A", "Most objects", "max_count"),
    ("B", "Best constraint fit", "best_score"),
    ("C", "Even spacing", "regular"),
)


def generate_points(
    area: Geometry,
    constraints: list[Constraint],
    object_kind: str = "object",
    target_count: int | None = None,
    spacing_m: float | None = None,
    zones: Zones | None = None,
) -> list[Variant]:
    """Two or three plan variants for point objects. Deterministic.

    Every variant is verified by the independent checker before it is returned;
    a variant with a hard violation is a generator bug, so it is dropped rather
    than shown (docs/PLAN.md §4).
    """
    zones = zones or compute_zones(area, constraints)
    if zones.is_empty:
        return []

    points = candidate_grid(zones.allowed)
    if len(points) == 0:
        return []

    cost, _scored_with = cost_surface(points, constraints)
    spacing = _spacing_from(constraints, spacing_m)
    target = target_count or len(points)

    orders = {
        # Sweep in rows: packs in as many as spacing allows.
        "max_count": _row_major_order(points),
        # Cheapest positions first: fewest soft-constraint compromises.
        "best_score": np.lexsort((points[:, 0], points[:, 1], cost)),
        # Rows again, but from the area's centre outwards, which reads as a
        # deliberate layout rather than a corner-first fill.
        "regular": _regular_order(points),
    }

    variants: list[Variant] = []
    for label_id, label, strategy in STRATEGIES:
        chosen = _greedy_select(points, orders[strategy], spacing, target)
        if not chosen:
            continue
        variant_id = f"points-{strategy}"
        features = _features(points, chosen, object_kind, variant_id)
        findings = check_features(features, constraints)

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
                features=features,
                metrics={
                    "count": len(features),
                    "mean_soft_cost": round(float(np.mean(cost[chosen])), 3),
                    "min_spacing_m": spacing,
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
