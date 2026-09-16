"""The one tool surface. Plain functions, JSON in, JSON out.

Everything a planning agent can do lives here: the in-app agent wraps these with
the Anthropic tool runner, MCP exposes the same functions to other agents, and
the REST API calls them too. One contract, three front doors
(ARCHITECTURE.md, "There is one tool surface and two front doors").

Two rules hold throughout:

* Nothing here talks to a model. These functions are deterministic, so a tool
  result can be tested, cached and quoted.
* A constraint reference is either a curated catalog id or a full constraint
  object. That keeps an agent's tool call short without letting it invent a
  threshold: a bare id resolves to the catalog entry, numbers and all.
"""

from __future__ import annotations

from typing import Any

from catalog.loader import CatalogError, for_object_kind, get_constraint, load_catalog
from checks.plan import check_features, describe, summarise, unevaluable_reason
from checks.registry import registered_types
from checks.zones import compute_zones, zones_as_geojson
from data.sources import UNAVAILABLE_LAYERS
from data.store import LayerNotAvailable, get_features_in, study_area_summary
from data.store import list_layers as _list_layers
from data.study_area import STUDY_AREA
from domain.models import Constraint, Feature, Geometry, Source
from generate.infeasible import explain_for_line, explain_for_points
from generate.line import generate_line as _generate_line
from generate.points import generate_points as _generate_points

ConstraintRef = str | dict[str, Any]


class ToolError(ValueError):
    """A tool was called with something it cannot use. The message is for the caller."""


def resolve_constraints(refs: list[ConstraintRef] | None) -> list[Constraint]:
    """Turn catalog ids and constraint objects into validated constraints.

    An unknown id is an error rather than a skip: silently dropping a constraint
    would produce a plan that looks checked and is not.
    """
    resolved: list[Constraint] = []
    for ref in refs or []:
        if isinstance(ref, str):
            try:
                resolved.append(get_constraint(ref))
            except CatalogError as e:
                raise ToolError(str(e)) from e
        else:
            try:
                resolved.append(Constraint.model_validate(ref))
            except Exception as e:  # noqa: BLE001 - surfaced to the caller as-is
                raise ToolError(f"invalid constraint: {e}") from e
    return resolved


def _area(area: Geometry | None) -> Geometry:
    return area or STUDY_AREA.polygon


# --------------------------------------------------------------------------- #
# Context: what exists, and what does not
# --------------------------------------------------------------------------- #


def list_layers() -> dict:
    """Every real data layer in this deployment, and the ones deliberately absent."""
    return {
        "layers": [i.model_dump() for i in _list_layers()],
        "unavailable": [{"name": n, "reason": r} for n, r in sorted(UNAVAILABLE_LAYERS.items())],
    }


def describe_area(area: Geometry | None = None) -> dict:
    """What is inside an area: counts per layer, not the geometry.

    Counts are what a planner and an agent reason about ("214 buildings, 3
    schools"); the geometry belongs on the map, not in a conversation.
    """
    summary = study_area_summary()
    if area is None:
        return summary

    counts = {}
    for info in _list_layers():
        counts[info.name] = len(get_features_in(info.name, area))
    return {**summary, "feature_counts_in_area": counts, "area": area}


def get_layer(name: str, area: Geometry | None = None, limit: int = 2000) -> dict:
    """One layer's features as GeoJSON in EPSG:2056. For map and MCP clients."""
    try:
        features = get_features_in(name, area)
    except LayerNotAvailable as e:
        return {"layer": name, "available": False, "reason": e.reason, "features": []}
    return {
        "layer": name,
        "available": True,
        "feature_count": len(features),
        "truncated": len(features) > limit,
        "features": [f.model_dump() for f in features[:limit]],
    }


# --------------------------------------------------------------------------- #
# Constraints
# --------------------------------------------------------------------------- #


def list_catalog(object_kind: str | None = None) -> dict:
    """Curated constraints, with sources and whether they can actually be checked."""
    entries = for_object_kind(object_kind) if object_kind else list(load_catalog())
    return {
        "constraints": [
            {
                **c.model_dump(),
                "description": describe(c),
                "evaluable": unevaluable_reason(c) is None,
                "not_evaluable_reason": unevaluable_reason(c),
            }
            for c in entries
        ],
        "constraint_types": registered_types(),
    }


def propose_constraint(
    id: str,
    title: str,
    type: str,
    source_text: str,
    params: dict[str, float] | None = None,
    layer: str | None = None,
    applies_to: str | None = None,
    hard: bool = True,
    source_url: str | None = None,
    note: str | None = None,
) -> dict:
    """Validate a constraint a planner described in their own words.

    This is where an agent's translation becomes structured data — and where it
    stops. The result is a *proposal*: it is marked unconfirmed, and the API
    only ever applies a constraint the planner confirmed in the UI
    (docs/PLAN.md §7, agent guardrails).

    The threshold must come from the planner's own words. Nothing here supplies
    a number, and a missing parameter is returned as an error to put back to them.
    """
    if type not in registered_types():
        raise ToolError(f"unknown constraint type {type!r}; available: {registered_types()}")

    try:
        constraint = Constraint(
            id=id,
            title=title,
            type=type,
            applies_to=applies_to,
            layer=layer,
            params=params or {},
            hard=hard,
            source=Source(text=source_text, url=source_url, kind="user"),
            verified=False,
            note=note,
        )
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"invalid constraint: {e}") from e

    problems: list[str] = []
    from checks.registry import get as get_type

    check = get_type(type)
    for param in check.param_names:
        if param not in constraint.params:
            problems.append(
                f"{type} needs a {param} value. Ask the planner for the number and "
                f"the source they are taking it from."
            )

    reason = unevaluable_reason(constraint) if not problems else None

    return {
        "proposal": constraint.model_dump(),
        "description": describe(constraint) if not problems else None,
        "confirmed": False,
        "needs_planner_confirmation": True,
        "problems": problems,
        "evaluable": reason is None and not problems,
        "not_evaluable_reason": reason,
    }


def preview_zones(constraints: list[ConstraintRef], area: Geometry | None = None) -> dict:
    """Where objects may and may not go under these constraints.

    `allowed_area_m2` is the number worth reading: if it is zero, generation
    will fail, and `skipped` says which constraints did not contribute.
    """
    zones = compute_zones(_area(area), resolve_constraints(constraints))
    geo = zones_as_geojson(zones)
    return {
        "allowed_area_m2": geo["allowed_area_m2"],
        "area_m2": geo["area_m2"],
        "share_of_area": round(geo["allowed_area_m2"] / max(geo["area_m2"], 1), 3),
        "skipped": geo["skipped"],
        "geometry": {
            "forbidden": geo["forbidden"],
            "required": geo["required"],
            "allowed": geo["allowed"],
        },
    }


# --------------------------------------------------------------------------- #
# Generation and verification
# --------------------------------------------------------------------------- #


def generate_points(
    object_kind: str,
    constraints: list[ConstraintRef],
    count: int | None = None,
    spacing_m: float | None = None,
    area: Geometry | None = None,
) -> dict:
    """Plan variants for point objects, or an explanation of why there are none."""
    resolved = resolve_constraints(constraints)
    target = _area(area)
    variants = _generate_points(
        target, resolved, object_kind=object_kind, target_count=count, spacing_m=spacing_m
    )
    if not variants:
        report = explain_for_points(target, resolved, count, spacing_m)
        return {"variants": [], "infeasibility": report.model_dump()}
    return {"variants": [v.model_dump() for v in variants], "infeasibility": None}


def generate_line(
    object_kind: str,
    constraints: list[ConstraintRef],
    start: tuple[float, float],
    end: tuple[float, float],
    area: Geometry | None = None,
) -> dict:
    """Route variants between two points, or an explanation of why there are none."""
    resolved = resolve_constraints(constraints)
    target = _area(area)
    variants = _generate_line(target, resolved, tuple(start), tuple(end), object_kind=object_kind)
    if not variants:
        report = explain_for_line(target, resolved, tuple(start), tuple(end))
        return {"variants": [], "infeasibility": report.model_dump()}
    return {"variants": [v.model_dump() for v in variants], "infeasibility": None}


def check_plan(features: list[dict], constraints: list[ConstraintRef]) -> dict:
    """Verify a plan — whoever produced it — against a constraint set."""
    try:
        parsed = [Feature.model_validate(f) for f in features]
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"invalid feature: {e}") from e

    resolved = resolve_constraints(constraints)
    findings = check_features(parsed, resolved)
    return {
        "findings": [f.model_dump() for f in findings],
        "summary": summarise(findings),
        "constraints_checked": [
            {"id": c.id, "description": describe(c), "evaluable": unevaluable_reason(c) is None}
            for c in resolved
        ],
    }


def explain_constraint(constraint: ConstraintRef) -> dict:
    """What one constraint means, where its number comes from, and if it can be checked."""
    resolved = resolve_constraints([constraint])[0]
    reason = unevaluable_reason(resolved)
    return {
        "id": resolved.id,
        "description": describe(resolved),
        "hard": resolved.hard,
        "source": resolved.source.model_dump(),
        "verified": resolved.verified,
        "note": resolved.note,
        "evaluable": reason is None,
        "not_evaluable_reason": reason,
    }


def explain_infeasibility(
    constraints: list[ConstraintRef],
    object_kind: str = "object",
    geometry: str = "point",
    count: int | None = None,
    spacing_m: float | None = None,
    start: tuple[float, float] | None = None,
    end: tuple[float, float] | None = None,
    area: Geometry | None = None,
) -> dict:
    """Which hard constraint blocks a request, and what relaxing it would give."""
    resolved = resolve_constraints(constraints)
    target = _area(area)
    if geometry == "line":
        if not start or not end:
            raise ToolError("a line needs a start and an end")
        report = explain_for_line(target, resolved, tuple(start), tuple(end))
    else:
        report = explain_for_points(target, resolved, count, spacing_m)
    return report.model_dump()


#: Everything MCP and the agent expose. Listed explicitly so adding a function
#: to this module is a deliberate act, not an accidental API change.
TOOL_FUNCTIONS = {
    "list_layers": list_layers,
    "describe_area": describe_area,
    "get_layer": get_layer,
    "list_catalog": list_catalog,
    "propose_constraint": propose_constraint,
    "preview_zones": preview_zones,
    "generate_points": generate_points,
    "generate_line": generate_line,
    "check_plan": check_plan,
    "explain_constraint": explain_constraint,
    "explain_infeasibility": explain_infeasibility,
}
