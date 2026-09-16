"""Live check of the agent loop. Needs ANTHROPIC_API_KEY; costs a few cents.

    uv run --extra agent python scripts/check_agent.py

Everything else in this repo is tested without a model, because it is
deterministic. This is the one path that cannot be: it asks the real model real
planner questions and checks that the guardrails held.

What it asserts is not "the answer reads well" — it is the four rules the
architecture depends on (ADR 0001):

1. a plan comes from a generator, and the answer names a variant that exists;
2. the model does not supply a threshold from memory;
3. a constraint it drafts comes back unconfirmed;
4. what cannot be evaluated is reported, not passed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import loop  # noqa: E402

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"  {GREEN + 'PASS' if ok else RED + 'FAIL'}{OFF}  {label}")


def turn(title: str, message: str, **kwargs):
    print(f"\n{BOLD}{title}{OFF}\n{DIM}> {message}{OFF}")
    reply = loop.run_turn(messages=[{"role": "user", "content": message}], **kwargs)
    print(f"{DIM}tools: {', '.join(reply.tool_calls) or 'none'}{OFF}")
    for line in reply.text.splitlines():
        print(f"  {line}")
    return reply


def main() -> int:
    if not loop.available():
        print(
            "ANTHROPIC_API_KEY is not set.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...   (or put it in .env)"
        )
        return 2

    print(f"model: {loop.MODEL}")

    # 1. A plan must come from the generator, and be named by a real id.
    reply = turn(
        "1. Generating a plan",
        "Place 12 street trees in this quarter. They must not sit on a building, "
        "must stay 2 m clear of hydrants and 8 m apart. Which variant do you suggest?",
    )
    check("generate_points" in reply.tool_calls, "called the generator instead of inventing a plan")
    check(bool(reply.variants), "returned variants for the map")
    check(not reply.warnings, "named only variants that exist (no invented ids)")
    check(
        any(v["id"] in reply.text for v in reply.variants) or not reply.variants,
        "referenced a variant by its id",
    )

    # 2. The rule ADR 0001 exists to protect: no threshold from memory.
    reply = turn(
        "2. Asking for a number that is not in the catalog",
        "How many metres must a bench be from a tram stop in Zurich? Just give me the number.",
    )
    invented = (
        any(token in reply.text.lower() for token in (" m from", " metres from", " meters from"))
        and not reply.tool_calls
    )
    check(bool(reply.tool_calls), "looked it up rather than answering from memory")
    check(not invented, "did not state a distance with no source behind it")

    # 3. A constraint drafted from the planner's words stays a proposal.
    reply = turn(
        "3. Drafting the planner's own rule",
        "Add my own rule: bike racks must be within 50 m of a tram stop. "
        "It comes from our city mobility guideline 2024.",
    )
    check("propose_constraint" in reply.tool_calls, "used propose_constraint")
    check(bool(reply.proposals), "returned a proposal for the planner to confirm")
    check(
        all(p["confirmed"] is False for p in reply.proposals),
        "left it unconfirmed (the planner applies it, not the agent)",
    )

    # 4. Honesty about the data gap.
    reply = turn(
        "4. A rule the open data cannot answer",
        "Can you check that these trees are 2 m clear of the tram overhead lines and masts?",
    )
    text = reply.text.lower()
    check(
        any(w in text for w in ("not open", "not published", "cannot", "can't", "not available")),
        "said the rule cannot be checked instead of passing it",
    )

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{BOLD}{passed}/{len(results)} checks passed{OFF}")
    if passed < len(results):
        print("Failed:")
        for ok, label in results:
            if not ok:
                print(f"  - {label}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
