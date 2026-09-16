"""Run constraints over a set of planned features and return findings.

This is the independent verifier. The generators produce geometry; this module
decides whether it holds up, and it is the only thing the UI and the agent are
allowed to quote (ADR 0001). Keeping it a separate code path from the generators
is deliberate: a variant that comes back with a hard violation is a generator
bug, and we want to see it rather than paper over it.
"""

from __future__ import annotations

import checks.types  # noqa: F401 - importing registers the five types
from checks.registry import CheckContext, get
from data.sources import UNAVAILABLE_LAYERS
from data.store import LayerNotAvailable, load_layer
from domain.models import Constraint, Feature, Finding, Severity


def unevaluable_reason(constraint: Constraint) -> str | None:
    """Why this constraint cannot be decided here, or None if it can be.

    Saying "the Leitungskataster is not open, so this rule is unchecked" is the
    honest answer; silently passing the constraint would be the dangerous one
    (docs/PLAN.md §2, principle 3).
    """
    check = get(constraint.type)
    if not check.needs_layer:
        return None
    if constraint.layer is None:
        return f"{constraint.type} needs a layer and none was given"
    if constraint.layer in UNAVAILABLE_LAYERS:
        return UNAVAILABLE_LAYERS[constraint.layer]
    try:
        load_layer(constraint.layer)
    except LayerNotAvailable as e:
        return e.reason
    return None


def _not_evaluable(constraint: Constraint, feature_id: str, reason: str) -> Finding:
    return Finding(
        constraint_id=constraint.id,
        severity="not_evaluable",
        feature_id=feature_id,
        message=f"Cannot be checked: {reason}",
        source=constraint.source,
    )


def check_features(features: list[Feature], constraints: list[Constraint]) -> list[Finding]:
    """Every finding for every (feature, constraint) pair. Deterministic.

    A constraint whose `applies_to` names a different object kind is skipped:
    a catalog rule about power lines should not fire on a bike rack.
    """
    findings: list[Finding] = []
    ctx = CheckContext(siblings=features)

    for constraint in constraints:
        reason = unevaluable_reason(constraint)
        if reason:
            # Reported once for the plan, not once per object: it is a statement
            # about the data, not about any single position.
            findings.append(_not_evaluable(constraint, feature_id="*", reason=reason))
            continue

        check = get(constraint.type)
        for feature in features:
            if constraint.applies_to and feature.kind != constraint.applies_to:
                continue
            finding = check.evaluate(feature, constraint, ctx)
            if finding is not None:
                findings.append(finding)

    return findings


def summarise(findings: list[Finding]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {"violation": 0, "warning": 0, "not_evaluable": 0}
    for f in findings:
        counts[f.severity] += 1
    return counts


def has_hard_violation(findings: list[Finding]) -> bool:
    return any(f.severity == "violation" for f in findings)


def describe(constraint: Constraint) -> str:
    """Plain words for one constraint, for the UI and for the agent to quote."""
    kind = "must be" if constraint.hard else "should preferably be"
    return f"{constraint.applies_to or 'object'} {kind} {get(constraint.type).describe(constraint)}"
