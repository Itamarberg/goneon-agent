"""The five constraint types of the MVP (docs/PLAN.md §4).

Every verdict in the product is computed here, by shapely, from a threshold the
planner or the catalog supplied. No verdict is ever produced by the model
(ADR 0001).
"""

from __future__ import annotations

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from checks.geometry import buffered_union, connecting_line, layer_union, nearest_distance_m
from checks.registry import CheckContext, CheckType, Zone, register
from data.study_area import DEFAULT_AREA_ID
from domain.models import Constraint, Feature, Finding


def _d(constraint: Constraint) -> float:
    """The distance parameter. Missing means the catalog or the planner is wrong."""
    if "d_m" not in constraint.params:
        raise ValueError(f"constraint {constraint.id!r} is missing its d_m parameter")
    return float(constraint.params["d_m"])


def _layer(constraint: Constraint) -> str:
    if not constraint.layer:
        raise ValueError(f"constraint {constraint.id!r} needs a layer")
    return constraint.layer


def _severity(constraint: Constraint) -> str:
    # A soft constraint is a preference; breaking it is a trade-off, not a failure.
    return "violation" if constraint.hard else "warning"


def _finding(
    constraint: Constraint,
    feature: Feature,
    message: str,
    measured: float | None = None,
    required: float | None = None,
    geometry: dict | None = None,
) -> Finding:
    return Finding(
        constraint_id=constraint.id,
        severity=_severity(constraint),
        feature_id=feature.id,
        measured_m=None if measured is None else round(measured, 2),
        required_m=required,
        message=message,
        source=constraint.source,
        geometry=geometry,
    )


# --------------------------------------------------------------------------- #
# min_distance: stay at least d metres away from a layer.
# --------------------------------------------------------------------------- #


def _min_distance_zone(c: Constraint, area_id: str = DEFAULT_AREA_ID) -> Zone:
    return Zone("forbidden", buffered_union(_layer(c), _d(c), area_id))


def _min_distance_eval(f: Feature, c: Constraint, ctx: CheckContext) -> Finding | None:
    geom = shape(f.geometry)
    required = _d(c)
    measured, nearest = nearest_distance_m(geom, _layer(c), ctx.area_id)
    if measured >= required:
        return None
    return _finding(
        c,
        f,
        f"{measured:.2f} m from the nearest {c.layer}, the minimum is {required:.2f} m",
        measured,
        required,
        connecting_line(geom, nearest),
    )


register(
    CheckType(
        name="min_distance",
        describe=lambda c: f"at least {_d(c):.2f} m from {c.layer}",
        zone=_min_distance_zone,
        evaluate=_min_distance_eval,
        param_names=("d_m",),
    )
)


# --------------------------------------------------------------------------- #
# max_distance: stay within d metres of a layer (proximity, e.g. tram stops).
# --------------------------------------------------------------------------- #


def _max_distance_zone(c: Constraint, area_id: str = DEFAULT_AREA_ID) -> Zone:
    return Zone("required", buffered_union(_layer(c), _d(c), area_id))


def _max_distance_eval(f: Feature, c: Constraint, ctx: CheckContext) -> Finding | None:
    geom = shape(f.geometry)
    required = _d(c)
    measured, nearest = nearest_distance_m(geom, _layer(c), ctx.area_id)
    if measured <= required:
        return None
    return _finding(
        c,
        f,
        f"{measured:.2f} m from the nearest {c.layer}, the maximum is {required:.2f} m",
        measured,
        required,
        connecting_line(geom, nearest),
    )


register(
    CheckType(
        name="max_distance",
        describe=lambda c: f"within {_d(c):.2f} m of {c.layer}",
        zone=_max_distance_zone,
        evaluate=_max_distance_eval,
        param_names=("d_m",),
    )
)


# --------------------------------------------------------------------------- #
# not_within / within: containment in a layer's polygons.
# --------------------------------------------------------------------------- #


def _not_within_zone(c: Constraint, area_id: str = DEFAULT_AREA_ID) -> Zone:
    return Zone("forbidden", layer_union(_layer(c), area_id))


def _not_within_eval(f: Feature, c: Constraint, ctx: CheckContext) -> Finding | None:
    geom = shape(f.geometry)
    union = layer_union(_layer(c), ctx.area_id)
    if not geom.intersects(union):
        return None
    return _finding(c, f, f"lies inside a {c.layer}", 0.0, None, f.geometry)


register(
    CheckType(
        name="not_within",
        describe=lambda c: f"not inside {c.layer}",
        zone=_not_within_zone,
        evaluate=_not_within_eval,
    )
)


def _within_zone(c: Constraint, area_id: str = DEFAULT_AREA_ID) -> Zone:
    return Zone("required", layer_union(_layer(c), area_id))


def _within_eval(f: Feature, c: Constraint, ctx: CheckContext) -> Finding | None:
    geom = shape(f.geometry)
    union = layer_union(_layer(c), ctx.area_id)
    if geom.intersects(union):
        return None
    measured = geom.distance(union)
    return _finding(
        c,
        f,
        f"does not lie on {c.layer} ({measured:.2f} m away from the nearest one)",
        measured,
        0.0,
        connecting_line(geom, union),
    )


register(
    CheckType(
        name="within",
        describe=lambda c: f"on {c.layer}",
        zone=_within_zone,
        evaluate=_within_eval,
    )
)


# --------------------------------------------------------------------------- #
# min_spacing: the planned objects keep their distance from each other.
# --------------------------------------------------------------------------- #


def _min_spacing_zone(_c: Constraint, _area_id: str = DEFAULT_AREA_ID) -> Zone:
    # Not an area: it depends on where the other objects end up, so the generator
    # enforces it while placing rather than by subtracting a region.
    return Zone("placement", None)


def _min_spacing_eval(f: Feature, c: Constraint, ctx: CheckContext) -> Finding | None:
    geom = shape(f.geometry)
    required = _d(c)
    closest: tuple[float, BaseGeometry | None] = (float("inf"), None)
    for other in ctx.siblings:
        if other.id == f.id:
            continue
        d = geom.distance(shape(other.geometry))
        if d < closest[0]:
            closest = (d, shape(other.geometry))
    measured = closest[0]
    if measured >= required:
        return None
    return _finding(
        c,
        f,
        f"{measured:.2f} m from the nearest planned object, the minimum is {required:.2f} m",
        measured,
        required,
        connecting_line(geom, closest[1]),
    )


register(
    CheckType(
        name="min_spacing",
        describe=lambda c: f"at least {_d(c):.2f} m between planned objects",
        zone=_min_spacing_zone,
        evaluate=_min_spacing_eval,
        needs_layer=False,
        param_names=("d_m",),
    )
)
