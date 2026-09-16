"""HTTP front door. Thin: it validates input, calls tools/, and serialises the result.

All planning logic lives below this layer (see ARCHITECTURE.md). Nothing here
computes geometry or decides a verdict.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import loop as agent_loop
from api import ratelimit
from catalog.loader import load_catalog
from checks.plan import check_features, describe, summarise, unevaluable_reason
from checks.registry import registered_types
from checks.zones import compute_zones, zones_as_geojson
from data.store import LayerNotAvailable, get_features_in, list_layers, study_area_summary
from data.study_area import STUDY_AREA
from domain.crs import to_wgs84
from domain.models import CRS_WGS84, Constraint, Feature, Geometry, ObjectSpec, Variant
from generate.infeasible import explain_for_line, explain_for_points
from generate.line import generate_line
from generate.points import generate_points

VERSION = "0.1.0"

# The static site is served from another origin (Vercel), so the API must allow it.
# Comma-separated list; "*" in local development.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(
    title="neon-agent API",
    version=VERSION,
    description=(
        "Deterministic infrastructure planning tools: real open data layers, "
        "cited constraints, generators and checks."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe, and what the static site uses to prove it reached the API."""
    return {
        "status": "ok",
        "version": VERSION,
        "service": "neon-agent",
    }


@app.get("/api/area")
def area() -> dict:
    """The study area and what real data it contains. Step 1 of the planner's journey."""
    summary = study_area_summary()
    summary["polygon_wgs84"] = to_wgs84(summary["polygon"])
    return summary


@app.get("/api/layers")
def layers() -> dict:
    """The layer catalogue: names constraints can refer to, with counts and attribution."""
    return {"layers": [i.model_dump() for i in list_layers()]}


@app.get("/api/layers/{name}")
def layer(
    name: str,
    limit: int = Query(default=5000, ge=1, le=20000),
) -> dict:
    """One layer as GeoJSON in WGS84, ready for the map.

    Reprojection happens here and only here: everything below this function
    works in metres (ARCHITECTURE.md, "Key contracts").
    """
    try:
        features = get_features_in(name)
    except LayerNotAvailable as e:
        # 404 with the reason, so the UI can say *why* a layer is missing rather
        # than implying the request was malformed.
        raise HTTPException(status_code=404, detail={"layer": name, "reason": e.reason}) from e

    truncated = len(features) > limit
    return {
        "type": "FeatureCollection",
        "name": name,
        "crs_note": f"coordinates are {CRS_WGS84}; the API computes in EPSG:2056",
        "truncated": truncated,
        "feature_count": len(features),
        "features": [
            {
                "type": "Feature",
                "id": f.id,
                "geometry": to_wgs84(f.geometry),
                "properties": {**f.properties, "kind": f.kind, "source": f.source},
            }
            for f in features[:limit]
        ],
    }


@app.get("/api/catalog")
def catalog() -> dict:
    """The curated constraints, in plain words, with sources and caveats.

    `evaluable` is the honest field: a constraint can be in the catalog, correct,
    and still impossible to check here because the data is not open.
    """
    entries = []
    for c in load_catalog():
        reason = unevaluable_reason(c)
        entries.append(
            {
                **c.model_dump(),
                "description": describe(c),
                "evaluable": reason is None,
                "not_evaluable_reason": reason,
            }
        )
    return {"constraints": entries, "types": registered_types()}


class ZonePreviewRequest(BaseModel):
    """Step 3 of the journey: show what the ticked constraints leave available."""

    area: Geometry | None = Field(
        default=None, description="Polygon in EPSG:2056. Defaults to the whole study area."
    )
    constraints: list[Constraint] = Field(default_factory=list)


@app.post("/api/zones")
def zones(request: ZonePreviewRequest) -> dict:
    """Forbidden, required and allowed areas for a constraint set, in WGS84.

    The generator uses this same function, so what a planner sees here is exactly
    what step 4 will place into.
    """
    area = request.area or STUDY_AREA.polygon
    computed = compute_zones(area, request.constraints)
    geo = zones_as_geojson(computed)
    for key in ("forbidden", "required", "allowed"):
        if geo[key] is not None:
            geo[key] = to_wgs84(geo[key])
    geo["crs_note"] = f"geometry is {CRS_WGS84}; areas are square metres computed in EPSG:2056"
    return geo


class CheckRequest(BaseModel):
    """Independent verification of a plan, whoever produced it."""

    features: list[Feature]
    constraints: list[Constraint]


@app.post("/api/check")
def check(request: CheckRequest) -> dict:
    findings = check_features(request.features, request.constraints)
    out = []
    for f in findings:
        item = f.model_dump()
        if f.geometry is not None:
            item["geometry"] = to_wgs84(f.geometry)
        out.append(item)
    return {
        "findings": out,
        "summary": summarise(findings),
        "constraints_checked": [
            {"id": c.id, "description": describe(c), "evaluable": unevaluable_reason(c) is None}
            for c in request.constraints
        ],
    }


class GenerateRequest(BaseModel):
    """Step 4: constraints in, plan variants out."""

    area: Geometry | None = Field(default=None, description="Polygon in EPSG:2056.")
    object: ObjectSpec
    constraints: list[Constraint] = Field(default_factory=list)


def _variant_for_map(variant: Variant) -> dict:
    """A variant with every geometry reprojected for the browser."""
    data = variant.model_dump()
    for feature in data["features"]:
        feature["geometry"] = to_wgs84(feature["geometry"])
    for finding in data["findings"]:
        if finding.get("geometry"):
            finding["geometry"] = to_wgs84(finding["geometry"])
    return data


@app.post("/api/generate")
def generate(request: GenerateRequest) -> dict:
    """Generate plan variants, or explain why none exist.

    An empty result is never returned on its own: if nothing can be placed, the
    response carries which hard constraint blocks it and what relaxing it would
    give, because "not possible" alone is not decision support.
    """
    area = request.area or STUDY_AREA.polygon
    spec = request.object

    if spec.geometry == "point":
        variants = generate_points(
            area,
            request.constraints,
            object_kind=spec.kind,
            target_count=spec.count,
            spacing_m=spec.spacing_m,
        )
        if not variants:
            report = explain_for_points(area, request.constraints, spec.count, spec.spacing_m)
            return {"variants": [], "infeasibility": report.model_dump()}
    else:
        if not spec.start or not spec.end:
            raise HTTPException(status_code=422, detail="a line needs a start and an end")
        variants = generate_line(
            area,
            request.constraints,
            tuple(spec.start),
            tuple(spec.end),
            object_kind=spec.kind,
        )
        if not variants:
            report = explain_for_line(area, request.constraints, tuple(spec.start), tuple(spec.end))
            return {"variants": [], "infeasibility": report.model_dump()}

    return {
        "variants": [_variant_for_map(v) for v in variants],
        "infeasibility": None,
    }


class ChatRequest(BaseModel):
    """One turn of the planning conversation.

    The whole history comes from the browser: there is no server-side session,
    so a share link or a reload loses nothing and the API scales by adding
    containers (docs/PLAN.md §7).
    """

    messages: list[dict] = Field(..., description="Full conversation so far.")
    area: Geometry | None = None
    object: ObjectSpec | None = None
    constraints: list[Constraint] = Field(
        default_factory=list, description="What the planner has confirmed in the UI."
    )


@app.get("/api/chat/status")
def chat_status() -> dict:
    """Whether chat is configured. The rest of the product does not need a key."""
    return {"available": agent_loop.available(), "model": agent_loop.MODEL}


@app.post("/api/chat")
def chat(request: ChatRequest, http_request: Request) -> dict:
    """Ask the agent. It calls the same tools the UI does and explains the results.

    Geometry the tools produced is returned alongside the text, reprojected for
    the map. The model never sees coordinates and never invents them.
    """
    client_id = http_request.client.host if http_request.client else "unknown"
    allowed, retry_after = ratelimit.check(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Chat is rate limited. Try again in {retry_after} s.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        reply = agent_loop.run_turn(
            messages=request.messages,
            area=request.area,
            object_spec=request.object,
            constraints=[c.model_dump() for c in request.constraints],
        )
    except agent_loop.AgentUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    zones = reply.zones
    if zones:
        zones = {k: (to_wgs84(v) if v else None) for k, v in zones.items()}

    return {
        "reply": reply.text,
        "tool_calls": reply.tool_calls,
        "variants": [_variant_for_map(Variant.model_validate(v)) for v in reply.variants],
        "zones": zones,
        "proposals": reply.proposals,
        "warnings": reply.warnings,
    }
