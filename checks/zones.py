"""Turn a set of constraints into the geometry they imply.

The same function serves two callers, which is the point: the map preview in
step 3 shows exactly the areas the generator will use in step 4. If a planner
sees no space left in red, the generator will say infeasible — no surprises.
"""

from __future__ import annotations

from shapely import unary_union
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

import checks.types  # noqa: F401 - importing registers the five types
from checks.plan import unevaluable_reason
from checks.registry import get
from domain.models import Constraint, Geometry


class Zones:
    """Forbidden, required and allowed geometry for one area and constraint set."""

    def __init__(
        self,
        area: BaseGeometry,
        forbidden: BaseGeometry,
        required: BaseGeometry | None,
        allowed: BaseGeometry,
        skipped: dict[str, str],
    ):
        self.area = area
        self.forbidden = forbidden
        self.required = required
        self.allowed = allowed
        self.skipped = skipped  # constraint id -> why it did not contribute

    @property
    def is_empty(self) -> bool:
        return self.allowed.is_empty


def compute_zones(area: Geometry, constraints: list[Constraint], hard_only: bool = True) -> Zones:
    """Allowed = area − forbidden ∩ required, using only constraints that apply.

    Soft constraints are excluded by default: they shape the *score* inside the
    allowed area, not its boundary. Including them would silently turn a
    preference into a rule.
    """
    area_geom = shape(area)
    forbidden_parts: list[BaseGeometry] = []
    required_parts: list[BaseGeometry] = []
    skipped: dict[str, str] = {}

    for constraint in constraints:
        if hard_only and not constraint.hard:
            skipped[constraint.id] = "soft: affects the score, not the allowed area"
            continue
        reason = unevaluable_reason(constraint)
        if reason:
            skipped[constraint.id] = reason
            continue

        zone = get(constraint.type).zone(constraint)
        if zone.geometry is None:
            skipped[constraint.id] = "enforced while placing, not as an area"
            continue
        if zone.role == "forbidden":
            forbidden_parts.append(zone.geometry)
        elif zone.role == "required":
            required_parts.append(zone.geometry)

    forbidden = unary_union(forbidden_parts) if forbidden_parts else Polygon()
    allowed = area_geom.difference(forbidden) if forbidden_parts else area_geom

    required: BaseGeometry | None = None
    for part in required_parts:
        required = part if required is None else required.intersection(part)
    if required is not None:
        allowed = allowed.intersection(required)

    return Zones(area_geom, forbidden, required, allowed, skipped)


def zones_as_geojson(zones: Zones) -> dict:
    """What the map draws: red forbidden, green allowed, plus what was skipped."""

    def geo(g: BaseGeometry | None) -> dict | None:
        if g is None or g.is_empty:
            return None
        return g.__geo_interface__

    return {
        "forbidden": geo(zones.forbidden.intersection(zones.area)),
        "required": geo(zones.required),
        "allowed": geo(zones.allowed),
        "allowed_area_m2": round(zones.allowed.area, 1),
        "area_m2": round(zones.area.area, 1),
        "skipped": zones.skipped,
    }
