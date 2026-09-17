"""The contracts every other layer speaks (ARCHITECTURE.md, "Key contracts").

These models are deliberately JSON-shaped: the same objects cross the REST API,
the agent's tool calls and MCP without a second set of schemas. Geometry travels
as GeoJSON geometry dicts, never as shapely objects — shapely exists only inside
checks/ and generate/.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Everything is stored and computed in LV95, whose unit is the metre, so a
# distance in a constraint means what a planner thinks it means. WGS84 appears
# only at the API edge, for the map.
CRS_STORAGE = "EPSG:2056"  # CH1903+ / LV95
CRS_WGS84 = "EPSG:4326"

Geometry = dict[str, Any]  # a GeoJSON geometry: {"type": ..., "coordinates": ...}

ConstraintType = Literal[
    "min_distance",
    "max_distance",
    "not_within",
    "within",
    "min_spacing",
]

SourceKind = Literal["curated", "user", "convention"]
Severity = Literal["violation", "warning", "not_evaluable"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Base):
    """Where a number comes from. Every constraint must say.

    `kind` separates a cited regulation from a planner's own rule and from a
    convention with no legal basis, so the UI can badge them differently and a
    planner can tell at a glance what they are leaning on.
    """

    text: str
    url: str | None = None
    kind: SourceKind = "user"
    quote: str | None = Field(
        default=None,
        description="The sentence from the source. Required before verified may be true.",
    )


class Constraint(Base):
    """A rule the plan must respect, as data rather than code.

    The threshold in `params` comes from the curated catalog or from the planner
    (ADR 0001). The model never fills it in from memory.
    """

    id: str
    title: str
    type: ConstraintType
    applies_to: str | None = Field(
        default=None, description="Object kind this is meant for, e.g. 'tree'. None = any."
    )
    layer: str | None = Field(
        default=None, description="Data layer it measures against. None for min_spacing."
    )
    params: dict[str, float] = Field(default_factory=dict)
    hard: bool = Field(
        default=True,
        description=(
            "A hard rule is a regulation or a physical impossibility: it removes ground from "
            "the map and starts selected for every plan. A soft rule is a preference the "
            "planner ranks by weight."
        ),
    )
    weight: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description=(
            "How much this preference counts against the others when a plan cannot "
            "satisfy them all. Only meaningful when hard is false: a hard constraint "
            "is not traded off. 1.0 is normal."
        ),
    )
    source: Source
    verified: bool = False
    note: str | None = Field(
        default=None,
        description="Caveat shown next to the constraint, e.g. a proxy for a real calculation.",
    )


class Feature(Base):
    """One thing on the map: an existing feature from open data, or a planned object."""

    id: str
    kind: str  # "building", "school", "tree", "power_line", ...
    geometry: Geometry
    properties: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    source_url: str | None = None


class Layer(Base):
    """A real open-data layer, already reprojected to EPSG:2056."""

    name: str
    title: str
    geometry_type: Literal["Point", "LineString", "Polygon"]
    features: list[Feature] = Field(default_factory=list)
    source: str
    source_url: str | None = None
    licence: str | None = None


class LayerInfo(Base):
    """What list_layers() returns: the catalogue of layers without the geometry."""

    name: str
    title: str
    geometry_type: str
    feature_count: int
    source: str
    source_url: str | None = None
    licence: str | None = None


class ObjectSpec(Base):
    """What the planner wants to place."""

    kind: str  # "tree", "bike_rack", "power_line", ...
    geometry: Literal["point", "line"]
    count: int | None = None
    spacing_m: float | None = None
    start: tuple[float, float] | None = None  # LV95
    end: tuple[float, float] | None = None


class PlanRequest(Base):
    area: Geometry  # Polygon in LV95
    object: ObjectSpec
    constraints: list[Constraint] = Field(default_factory=list)


class Finding(Base):
    """The result of one check against one feature. Produced only by checks/.

    `measured` and `required` are kept as numbers so the UI and the agent can
    phrase the same fact without re-deriving it.
    """

    constraint_id: str
    severity: Severity
    feature_id: str
    measured_m: float | None = None
    required_m: float | None = None
    message: str
    source: Source
    geometry: Geometry | None = Field(
        default=None,
        description="Marks the conflict on the map, e.g. the shortest connecting line.",
    )


class Tradeoff(Base):
    """What a variant gives up on a soft constraint, so variants can be compared."""

    constraint_id: str
    title: str
    count: int  # objects that fall short of the soft constraint
    worst_measured_m: float | None = None
    required_m: float | None = None
    weight: float = 1.0  # the importance the planner gave it


class Variant(Base):
    """One generated plan. `features` come only from a generator (ADR 0001)."""

    id: str
    label: str
    strategy: str
    description: str = Field(
        default="",
        description="One sentence for the planner: how this variant chose its positions.",
    )
    features: list[Feature] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    tradeoffs: list[Tradeoff] = Field(default_factory=list)


class Relaxation(Base):
    """What relaxing one hard constraint would buy. Computed, not guessed."""

    constraint_id: str
    title: str
    current_required_m: float | None = None
    suggested_required_m: float | None = None
    freed_area_m2: float | None = None
    positions_gained: int | None = None


class InfeasibilityReport(Base):
    """Why nothing could be placed, and the cheapest way out."""

    feasible: bool
    reason: str
    blocking_constraint_id: str | None = None
    relaxations: list[Relaxation] = Field(default_factory=list)
