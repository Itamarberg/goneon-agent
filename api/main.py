"""HTTP front door. Thin: it validates input, calls tools/, and serialises the result.

All planning logic lives below this layer (see ARCHITECTURE.md). Nothing here
computes geometry or decides a verdict.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from catalog.loader import load_catalog
from checks.plan import check_features, describe, summarise, unevaluable_reason
from checks.registry import registered_types
from checks.zones import compute_zones, zones_as_geojson
from data.store import LayerNotAvailable, get_features_in, list_layers, study_area_summary
from data.study_area import STUDY_AREA
from domain.crs import to_wgs84
from domain.models import CRS_WGS84, Constraint, Feature, Geometry

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
