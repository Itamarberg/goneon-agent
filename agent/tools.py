"""The tool surface as the model sees it.

Thin wrappers over `tools/core.py`. They exist for two reasons and no others:

* bind the planner's current area and object, so the model does not have to
  restate them (and cannot get them wrong);
* keep geometry out of the conversation — full variants are stashed on the
  session for the map, the model gets a summary.

Every docstring here is part of the prompt: the SDK turns it into the tool
description the model reads.
"""

from __future__ import annotations

from typing import Any

from anthropic import beta_tool

from agent.session import Session, summarise_variant
from domain.models import Geometry
from tools import core


def build_tools(session: Session) -> list:
    """Tools bound to one planning session."""

    def record(name: str) -> None:
        session.tool_calls.append(name)

    def area() -> Geometry | None:
        return session.area

    @beta_tool
    def list_layers() -> dict:
        """List the real open-data layers available, and the layers that are deliberately
        missing with the reason. Call this before referring to a layer by name."""
        record("list_layers")
        return core.list_layers()

    @beta_tool
    def describe_area() -> dict:
        """Describe the planner's current area: how many buildings, trees, schools,
        stops and so on it contains, and what cannot be evaluated there."""
        record("describe_area")
        return core.describe_area(area())

    @beta_tool
    def list_catalog(object_kind: str = "") -> dict:
        """List the curated constraints with their thresholds, sources and whether they
        can be checked against open data. This is where numbers come from.

        Args:
            object_kind: Optional object kind (tree, bike_rack, power_line) to filter by.
        """
        record("list_catalog")
        return core.list_catalog(object_kind or None)

    @beta_tool
    def explain_constraint(constraint_id: str) -> dict:
        """Explain one catalog constraint: what it requires, where the number comes from,
        and whether it can be checked here.

        Args:
            constraint_id: The catalog id, e.g. lev-building-clearance.
        """
        record("explain_constraint")
        return core.explain_constraint(constraint_id)

    @beta_tool
    def propose_constraint(
        id: str,
        title: str,
        type: str,
        source_text: str,
        layer: str = "",
        applies_to: str = "",
        d_m: float | None = None,
        hard: bool = True,
    ) -> dict:
        """Turn a constraint the planner described in their own words into a structured
        proposal. The planner must confirm it before it is used — this never applies it.

        Use the threshold the planner gave. If they did not give one, call this without
        d_m: the result will tell you to ask them for the number and its source.

        Args:
            id: Short kebab-case id, e.g. racks-near-stops.
            title: One line in the planner's own terms.
            type: min_distance, max_distance, within, not_within or min_spacing.
            source_text: Where the planner said the rule comes from, in their words.
            layer: The data layer it measures against. Empty for min_spacing.
            applies_to: The object kind it applies to, e.g. tree. Empty means any.
            d_m: The distance in metres, if the planner gave one.
            hard: True if it must hold, False if it is a preference to trade off.
        """
        record("propose_constraint")
        result = core.propose_constraint(
            id=id,
            title=title,
            type=type,
            source_text=source_text,
            params={"d_m": d_m} if d_m is not None else {},
            layer=layer or None,
            applies_to=applies_to or None,
            hard=hard,
        )
        session.proposals.append(result)
        return result

    @beta_tool
    def preview_zones(constraints: list[Any]) -> dict:
        """Show how much of the area these constraints leave available, and which of
        them did not contribute. Use it to check a request is possible before generating.

        Args:
            constraints: Catalog ids, or constraint objects the planner confirmed.
        """
        record("preview_zones")
        result = core.preview_zones(constraints, area())
        session.zones = result.pop("geometry")
        return result

    @beta_tool
    def generate_points(
        object_kind: str,
        constraints: list[Any],
        count: int | None = None,
        spacing_m: float | None = None,
    ) -> dict:
        """Generate plan variants for point objects (trees, bike racks, benches,
        charging stations). Returns variants to compare, or an explanation of why no
        plan is possible.

        Args:
            object_kind: What is being placed, e.g. tree or bike_rack.
            constraints: Catalog ids, or constraint objects the planner confirmed.
            count: How many objects the planner wants.
            spacing_m: Minimum distance between them, if the planner gave one.
        """
        record("generate_points")
        result = core.generate_points(object_kind, constraints, count, spacing_m, area())
        session.record_variants(result["variants"])
        return {
            "variants": [summarise_variant(v) for v in result["variants"]],
            "infeasibility": result["infeasibility"],
        }

    @beta_tool
    def generate_line(
        object_kind: str,
        constraints: list[Any],
        start: list[float],
        end: list[float],
    ) -> dict:
        """Route a line object (cable, power line, pipe, path) between two points the
        planner chose. Returns route variants, or an explanation of why none exists.

        Args:
            object_kind: What is being routed, e.g. power_line.
            constraints: Catalog ids, or constraint objects the planner confirmed.
            start: [x, y] in EPSG:2056, from the planner's map click.
            end: [x, y] in EPSG:2056, from the planner's map click.
        """
        record("generate_line")
        result = core.generate_line(object_kind, constraints, tuple(start), tuple(end), area())
        session.record_variants(result["variants"])
        return {
            "variants": [summarise_variant(v) for v in result["variants"]],
            "infeasibility": result["infeasibility"],
        }

    @beta_tool
    def check_variant(variant_id: str, constraints: list[Any]) -> dict:
        """Check a variant that was already generated against a constraint set. Use this
        to answer "does variant X also satisfy Y?" without regenerating.

        Args:
            variant_id: The id of a variant from this conversation.
            constraints: Catalog ids, or constraint objects the planner confirmed.
        """
        record("check_variant")
        variant = session.variants.get(variant_id)
        if variant is None:
            known = ", ".join(sorted(session.variants)) or "none yet"
            return {
                "error": f"No variant {variant_id!r} exists. Generated so far: {known}.",
            }
        return core.check_plan(variant["features"], constraints)

    @beta_tool
    def explain_infeasibility(
        constraints: list[Any],
        object_kind: str = "object",
        geometry: str = "point",
        count: int | None = None,
        spacing_m: float | None = None,
    ) -> dict:
        """Work out which hard constraint makes a request impossible, and what value
        would make it work.

        Args:
            constraints: Catalog ids, or constraint objects the planner confirmed.
            object_kind: What is being placed.
            geometry: point or line.
            count: How many objects the planner wants.
            spacing_m: Minimum distance between them, if any.
        """
        record("explain_infeasibility")
        return core.explain_infeasibility(
            constraints,
            object_kind=object_kind,
            geometry=geometry,
            count=count,
            spacing_m=spacing_m,
            area=area(),
        )

    return [
        list_layers,
        describe_area,
        list_catalog,
        explain_constraint,
        propose_constraint,
        preview_zones,
        generate_points,
        generate_line,
        check_variant,
        explain_infeasibility,
    ]
