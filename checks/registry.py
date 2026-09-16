"""The constraint-type registry: one place that says what a type *means*.

A type is two pure functions:

* `zone(constraint)` — the geometry the constraint implies, which the generators
  subtract from or intersect with, and the UI previews.
* `evaluate(feature, constraint, context)` — the verdict for one planned object.

Adding a constraint type is registering both (ARCHITECTURE.md, extension point 2).
Nothing else in the system switches on a constraint's type, so a new type reaches
the generators, the checker, the preview and the agent at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from shapely.geometry.base import BaseGeometry

from domain.models import Constraint, Feature, Finding

ZoneRole = Literal["forbidden", "required", "placement"]


@dataclass(frozen=True)
class Zone:
    """What a constraint carves out of the map.

    forbidden  — objects may not be here (subtracted from the allowed area)
    required   — objects may only be here (intersected with the allowed area)
    placement  — not expressible as an area; enforced while placing (min_spacing)
    """

    role: ZoneRole
    geometry: BaseGeometry | None


@dataclass(frozen=True)
class CheckType:
    name: str
    describe: Callable[[Constraint], str]
    zone: Callable[[Constraint], Zone]
    evaluate: Callable[[Feature, Constraint, CheckContext], Finding | None]
    needs_layer: bool = True
    param_names: tuple[str, ...] = ()


@dataclass
class CheckContext:
    """Everything an evaluation may need beyond the single feature.

    `siblings` carries the other planned objects, which is what min_spacing
    measures against; a check never reaches into global state for it.
    """

    siblings: list[Feature]


_REGISTRY: dict[str, CheckType] = {}


def register(check_type: CheckType) -> CheckType:
    if check_type.name in _REGISTRY:
        raise ValueError(f"constraint type {check_type.name!r} is already registered")
    _REGISTRY[check_type.name] = check_type
    return check_type


def get(name: str) -> CheckType:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"unknown constraint type {name!r}; registered types: {known}")
    return _REGISTRY[name]


def registered_types() -> list[str]:
    return sorted(_REGISTRY)
