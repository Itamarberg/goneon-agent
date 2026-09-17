"""Load and validate the curated constraints.

One YAML file per constraint, so adding a rule is a file and a test run, not a
code change (ARCHITECTURE.md, extension point 1), and so a planner can read the
catalog without reading Python.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import ValidationError

import checks.types  # noqa: F401 - registers the types the catalog may use
from checks.registry import get, registered_types
from data.sources import BY_NAME, UNAVAILABLE_LAYERS
from domain.models import Constraint

CATALOG_DIR = Path(__file__).resolve().parent / "constraints"


class CatalogError(ValueError):
    """A catalog file is wrong. Raised at load time, so it cannot reach a planner."""


def _validate(constraint: Constraint, path: Path) -> None:
    where = path.name
    if constraint.id != path.stem:
        raise CatalogError(f"{where}: id {constraint.id!r} must match the file name")

    if constraint.type not in registered_types():
        raise CatalogError(f"{where}: unknown type {constraint.type!r}")

    check = get(constraint.type)
    for param in check.param_names:
        if param not in constraint.params:
            raise CatalogError(f"{where}: type {constraint.type} needs param {param!r}")

    if check.needs_layer:
        if not constraint.layer:
            raise CatalogError(f"{where}: type {constraint.type} needs a layer")
        # A layer that is known-unavailable is allowed: the constraint is shipped
        # precisely so the tool can say it cannot be evaluated. A layer that is
        # neither available nor known is a typo.
        if constraint.layer not in BY_NAME and constraint.layer not in UNAVAILABLE_LAYERS:
            raise CatalogError(f"{where}: layer {constraint.layer!r} does not exist")

    if constraint.verified and not (constraint.source.quote or constraint.source.kind != "curated"):
        raise CatalogError(f"{where}: verified=true requires source.quote for a curated constraint")


@cache
def load_catalog() -> tuple[Constraint, ...]:
    """Every curated constraint, sorted by id. Cached; the files are static."""
    constraints: list[Constraint] = []
    for path in sorted(CATALOG_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            constraint = Constraint.model_validate(raw)
        except ValidationError as e:
            raise CatalogError(f"{path.name}: {e}") from e
        _validate(constraint, path)
        constraints.append(constraint)
    return tuple(constraints)


def get_constraint(constraint_id: str) -> Constraint:
    for c in load_catalog():
        if c.id == constraint_id:
            return c
    known = ", ".join(c.id for c in load_catalog())
    raise CatalogError(f"unknown constraint {constraint_id!r}; catalog holds: {known}")


def for_object_kind(kind: str | None) -> list[Constraint]:
    """Catalog entries relevant to an object kind, generic ones included."""
    return [c for c in load_catalog() if kind is None or c.applies_to in (None, kind)]
