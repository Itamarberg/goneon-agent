"""Per-turn state for a chat, and the guardrail on what the agent may claim.

There is no server-side session (docs/PLAN.md §7): the browser holds the plan and
sends it with each message. A Session exists only for the duration of one turn —
it binds the planner's current area and object to the tools, and records what the
tools actually produced so two things can happen afterwards:

* the full geometry goes to the map, while the model only ever sees a summary;
* any variant id in the model's answer is checked against what really exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from domain.models import Geometry, ObjectSpec

# Variant ids are generated, never free text: points-<strategy> / line-<letter>.
VARIANT_ID = re.compile(r"\b(?:points|line)-[a-z_]+\b", re.IGNORECASE)


@dataclass
class Session:
    """What the planner has on screen, plus what this turn produced."""

    area: Geometry | None = None
    object: ObjectSpec | None = None
    constraints: list[dict[str, Any]] = field(default_factory=list)

    # Filled in by the tool wrappers as the turn runs.
    variants: dict[str, dict] = field(default_factory=dict)
    zones: dict | None = None
    proposals: list[dict] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

    def record_variants(self, variants: list[dict]) -> None:
        for v in variants:
            self.variants[v["id"]] = v

    def known_variant_ids(self) -> set[str]:
        return set(self.variants)

    def unknown_variant_ids(self, text: str) -> list[str]:
        """Variant ids the model named that no tool produced.

        The model is told to reference variants by id. If it invents one, the
        answer is about a plan that does not exist, and the server must not pass
        that to a planner (docs/PLAN.md §7, agent guardrails).
        """
        mentioned = {m.lower() for m in VARIANT_ID.findall(text)}
        return sorted(mentioned - {v.lower() for v in self.known_variant_ids()})


def summarise_variant(variant: dict) -> dict:
    """What the model is shown: everything except the coordinates.

    A 20-tree plan is 20 coordinate pairs the model has no use for — it must not
    restate them, and feeding them in invites exactly that.
    """
    findings = variant.get("findings", [])
    return {
        "id": variant["id"],
        "label": variant["label"],
        "strategy": variant["strategy"],
        "description": variant.get("description", ""),
        "object_count": len(variant.get("features", [])),
        "metrics": variant.get("metrics", {}),
        "tradeoffs": variant.get("tradeoffs", []),
        "findings_summary": {
            "violation": sum(1 for f in findings if f["severity"] == "violation"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "not_evaluable": sum(1 for f in findings if f["severity"] == "not_evaluable"),
        },
        "not_evaluable": [
            {"constraint_id": f["constraint_id"], "message": f["message"]}
            for f in findings
            if f["severity"] == "not_evaluable"
        ],
    }
