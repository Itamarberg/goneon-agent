"""The agent loop: one planner message in, one answer plus map artifacts out.

Uses the SDK's tool runner rather than a hand-written while loop — the loop is
not where this product's value is, and the runner already handles the
request → execute → continue cycle.

The server owns two things the model does not:
* the full geometry of everything the tools produced, which goes to the map;
* a check that every variant the answer names actually exists.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import anthropic

from agent.prompt import SYSTEM_PROMPT
from agent.session import Session
from agent.tools import build_tools
from domain.models import Geometry, ObjectSpec

log = logging.getLogger(__name__)

MODEL = os.getenv("NEON_MODEL", "claude-opus-5")
MAX_TOKENS = int(os.getenv("NEON_MAX_TOKENS", "8000"))
# A planning turn is a handful of tool calls. A much higher ceiling would let a
# confused turn spend a hackathon's budget.
MAX_TOOL_ITERATIONS = int(os.getenv("NEON_MAX_TOOL_ITERATIONS", "12"))


class AgentUnavailable(RuntimeError):
    """No API key configured. The rest of the product works without one."""


@dataclass
class AgentReply:
    text: str
    tool_calls: list[str]
    variants: list[dict]
    zones: dict | None
    proposals: list[dict]
    warnings: list[str]


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _client() -> anthropic.Anthropic:
    if not available():
        raise AgentUnavailable(
            "ANTHROPIC_API_KEY is not set. The map, constraints, generation and checks "
            "all work without it; only the chat does not."
        )
    return anthropic.Anthropic()


def run_turn(
    messages: list[dict[str, Any]],
    area: Geometry | None = None,
    object_spec: ObjectSpec | None = None,
    constraints: list[dict] | None = None,
) -> AgentReply:
    """Run one turn of the conversation.

    `messages` is the whole history, sent by the browser — there is no server-side
    session (docs/PLAN.md §7). The planner's current area and object are bound to
    the tools rather than described in the prompt, so the model cannot drift from
    what is on screen.
    """
    session = Session(area=area, object=object_spec, constraints=constraints or [])
    tools = build_tools(session)

    system = SYSTEM_PROMPT
    if session.constraints:
        # Given as context, not as authority: the planner confirmed these, and the
        # tools re-validate every one of them anyway.
        chosen = ", ".join(c.get("id", "?") for c in session.constraints)
        system += f"\n\nThe planner has currently selected these constraints: {chosen}."
    if session.object:
        system += (
            f"\nThey are planning: {session.object.kind} ({session.object.geometry} geometry)."
        )

    runner = _client().beta.messages.tool_runner(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        thinking={"type": "adaptive"},
        tools=tools,
        messages=messages,
    )

    final = None
    for iteration, message in enumerate(runner, start=1):
        final = message
        if iteration >= MAX_TOOL_ITERATIONS:
            log.warning("turn stopped after %s tool iterations", iteration)
            break

    text = _text_of(final)
    warnings: list[str] = []

    # The guardrail: an id the model made up refers to a plan that does not exist.
    invented = session.unknown_variant_ids(text)
    if invented:
        log.error("agent referenced variants that do not exist: %s", invented)
        warnings.append(
            "The assistant referred to plan variants that were not generated "
            f"({', '.join(invented)}). Ignore those references."
        )

    return AgentReply(
        text=text,
        tool_calls=session.tool_calls,
        variants=list(session.variants.values()),
        zones=session.zones,
        proposals=session.proposals,
        warnings=warnings,
    )


def _text_of(message) -> str:
    if message is None:
        return ""
    return "\n".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()
