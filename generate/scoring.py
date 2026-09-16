"""How good is a position? Soft constraints answer that, as a number.

Hard constraints decide *where* an object may go (they carve the allowed area).
Soft constraints decide *which* of the allowed positions is better, and they are
the reason two variants can differ while both being legal. Keeping them as a
cost surface rather than a filter is what lets the tool say "this variant places
12 trees but 3 of them are closer to the school than you asked" instead of
silently dropping them.
"""

from __future__ import annotations

import numpy as np

from checks.geometry import contains_mask, nearest_distances
from checks.plan import unevaluable_reason
from domain.models import Constraint

# A cost of 1.0 means "fully violates this soft constraint". Costs are summed
# over the soft constraints, so a position's cost is comparable across variants.
FULL_COST = 1.0


def _normalised_shortfall(distances: np.ndarray, required: float) -> np.ndarray:
    """1.0 at zero distance, 0.0 once the requirement is met, linear in between."""
    if required <= 0:
        return np.zeros_like(distances)
    return np.clip((required - distances) / required, 0.0, 1.0)


def _normalised_excess(distances: np.ndarray, allowed: float) -> np.ndarray:
    """0.0 inside the radius, rising to 1.0 once twice the radius is exceeded."""
    if allowed <= 0:
        return np.where(distances > 0, FULL_COST, 0.0)
    return np.clip((distances - allowed) / allowed, 0.0, 1.0)


def constraint_cost(points: np.ndarray, constraint: Constraint) -> np.ndarray | None:
    """Cost in [0, 1] for each candidate point under one soft constraint.

    Returns None when the constraint cannot shape the cost surface — an
    unavailable layer, or a type that is enforced during placement instead.
    """
    if unevaluable_reason(constraint):
        return None
    if constraint.type == "min_spacing":
        return None  # enforced while placing; it is about the plan, not the place

    # Containment only needs a prepared inside/outside test; a clearance needs a
    # nearest-neighbour distance. Both avoid measuring against the merged union.
    if constraint.type == "within":
        return np.where(contains_mask(points, constraint.layer), 0.0, FULL_COST)
    if constraint.type == "not_within":
        return np.where(contains_mask(points, constraint.layer), FULL_COST, 0.0)

    distances = nearest_distances(points, constraint.layer)
    if not np.isfinite(distances).any():
        return None

    d = float(constraint.params.get("d_m", 0.0))
    if constraint.type == "min_distance":
        return _normalised_shortfall(distances, d)
    if constraint.type == "max_distance":
        return _normalised_excess(distances, d)
    return None


def cost_surface(points: np.ndarray, constraints: list[Constraint]) -> tuple[np.ndarray, list[str]]:
    """Total soft cost per candidate, and which constraints contributed.

    Hard constraints are not included: they have already removed everything they
    forbid from the candidate set, so adding them here would double-count.
    """
    total = np.zeros(len(points), dtype=float)
    used: list[str] = []
    for c in constraints:
        if c.hard:
            continue
        cost = constraint_cost(points, c)
        if cost is None:
            continue
        total += cost
        used.append(c.id)
    return total, used


def distance_to_layer(points: np.ndarray, layer: str) -> np.ndarray:
    """Metres from each candidate to the nearest feature of a layer."""
    return nearest_distances(points, layer)
