"""HTTP front door. Thin: it validates input, calls tools/, and serialises the result.

All planning logic lives below this layer (see ARCHITECTURE.md). Nothing here
computes geometry or decides a verdict.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from data.store import LayerNotAvailable, get_features_in, list_layers, study_area_summary
from domain.crs import to_wgs84
from domain.models import CRS_WGS84

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
