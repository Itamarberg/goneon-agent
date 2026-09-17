"""HTTP front door. Thin: it validates input, calls tools/, and serialises the result.

All planning logic lives below this layer (see ARCHITECTURE.md). Nothing here
computes geometry or decides a verdict.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent import loop as agent_loop
from api import ratelimit
from catalog.loader import load_catalog
from checks.plan import check_features, describe, summarise, unevaluable_reason
from checks.registry import registered_types
from checks.zones import compute_zones, zones_as_geojson
from data.store import LayerNotAvailable, get_features_in, list_layers, study_area_summary
from data.study_area import DEFAULT_AREA_ID, UnknownStudyArea, get_area
from domain.crs import to_lv95, to_wgs84
from domain.models import CRS_WGS84, Constraint, Feature, Geometry, ObjectSpec, Variant
from generate.infeasible import explain_for_line, explain_for_points
from generate.line import generate_line
from generate.points import DEFAULT_FIT_TOLERANCE, generate_points
from tools import core as tools_core

VERSION = "0.1.0"

# The static site is served from another origin (Vercel), so the API must allow it.
# Comma-separated list; "*" in local development.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]


def _mcp():
    """The MCP server and its ASGI app, if the optional dependency is installed.

    MCP is an extension point, not a requirement: the API and the website work
    without it, and a deployment that does not want to expose the tools to other
    agents simply leaves the extra out.

    The app is built once here — it owns the session manager the lifespan runs,
    so a second call would create a manager nothing is running.
    """
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        from mcp_server.server import server as mcp_server
    except ImportError:  # pragma: no cover - depends on the install profile
        return None, None

    # MCP defends against DNS rebinding by checking the Host header, which is
    # aimed at servers running on someone's laptop. This one is a public service
    # other people's agents are meant to reach, so the deployment states its own
    # hostnames instead. "*" turns the check off; it is opt-in, not the default.
    hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    wildcard = "*" in hosts
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=not wildcard,
        allowed_hosts=hosts or ["127.0.0.1:8000", "localhost:8000"],
        allowed_origins=hosts or ["*"],
    )

    app = mcp_server.streamable_http_app(
        # Serve at the mount root, so mounting at /mcp gives /mcp, not /mcp/mcp.
        streamable_http_path="/",
        transport_security=security,
    )
    return mcp_server, app


MCP, MCP_APP = _mcp()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the MCP session manager alongside the API when MCP is mounted."""
    if MCP is None:
        yield
        return
    async with MCP.session_manager.run():
        yield


app = FastAPI(
    title="neon-agent API",
    version=VERSION,
    description=(
        "Deterministic infrastructure planning tools: real open data layers, "
        "cited constraints, generators and checks."
    ),
    lifespan=lifespan,
)

if MCP_APP is not None:
    # The same tools the website and the in-app agent use, for any MCP client.
    app.mount("/mcp", MCP_APP)


@app.middleware("http")
async def errors_as_json(request: Request, call_next):
    """Turn an unhandled exception into a JSON 500 inside the CORS layer.

    Starlette's default 500 is produced outside every middleware, so it carries
    no CORS headers and the browser reports it as a failed fetch rather than as
    an error the planner can read and report.
    """
    try:
        return await call_next(request)
    except Exception as e:  # noqa: BLE001 - this is the last line of defence
        logging.getLogger(__name__).exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(e).__name__}: {e}"[:500]},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api")
def index() -> dict:
    """A signpost.

    Landing on the API prefix in a browser with a bare "Not Found" tells you
    nothing, so say what is where.
    """
    return {
        "service": "neon-agent API",
        "version": VERSION,
        "note": (
            "This is the API. The planner website is a separate static site "
            "(locally: python3 -m http.server -d web 5173)."
        ),
        "openapi_docs": "/docs",
        "endpoints": {
            "health": "/api/health",
            "study_area": "/api/area",
            "layers": "/api/layers",
            "catalog": "/api/catalog",
            "zones": "POST /api/zones",
            "generate": "POST /api/generate",
            "check": "POST /api/check",
            "chat": "POST /api/chat",
            "chat_status": "/api/chat/status",
            "mcp": "/mcp/" if MCP_APP is not None else None,
        },
    }


@app.get("/api/health")
def health() -> dict:
    """Liveness probe, and what the static site uses to prove it reached the API."""
    return {
        "status": "ok",
        "version": VERSION,
        "service": "neon-agent",
        "mcp": MCP is not None,
    }


def _area_id(area_id: str | None) -> str:
    """Validate a study area id from a request, or 400 with what is available."""
    try:
        return get_area(area_id).id
    except UnknownStudyArea as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/areas")
def areas() -> dict:
    """Every study area this deployment has data for. Step 1 of the journey."""
    out = []
    for entry in tools_core.list_study_areas()["areas"]:
        summary = study_area_summary(entry["id"])
        out.append(
            {
                **entry,
                "polygon_wgs84": to_wgs84(summary["polygon"]),
                "feature_count": sum(layer["feature_count"] for layer in summary["layers"]),
            }
        )
    return {"areas": out, "default_area_id": DEFAULT_AREA_ID}


@app.get("/api/area")
def area(area_id: str | None = None) -> dict:
    """One study area and what real data it contains."""
    summary = study_area_summary(_area_id(area_id))
    summary["polygon_wgs84"] = to_wgs84(summary["polygon"])
    return summary


@app.get("/api/layers")
def layers(area_id: str | None = None) -> dict:
    """The layer catalogue: names constraints can refer to, with counts and attribution."""
    return {"layers": [i.model_dump() for i in list_layers(_area_id(area_id))]}


@app.get("/api/layers/{name}")
def layer(
    name: str,
    limit: int = Query(default=5000, ge=1, le=20000),
    area_id: str | None = None,
) -> dict:
    """One layer as GeoJSON in WGS84, ready for the map.

    Reprojection happens here and only here: everything below this function
    works in metres (ARCHITECTURE.md, "Key contracts").
    """
    try:
        features = get_features_in(name, None, _area_id(area_id))
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
def catalog(area_id: str | None = None) -> dict:
    """The curated constraints, in plain words, with sources and caveats.

    `evaluable` is the honest field: a constraint can be in the catalog, correct,
    and still impossible to check here because the data is not open.
    """
    resolved = _area_id(area_id)
    entries = []
    for c in load_catalog():
        reason = unevaluable_reason(c, resolved)
        entries.append(
            {
                **c.model_dump(),
                "description": describe(c),
                "evaluable": reason is None,
                "not_evaluable_reason": reason,
            }
        )
    return {"area_id": resolved, "constraints": entries, "types": registered_types()}


class ProjectRequest(BaseModel):
    lon: float
    lat: float


@app.post("/api/project")
def project(request: ProjectRequest) -> dict:
    """Turn a map click into LV95 metres.

    The browser only ever has degrees. Rather than ship a projection library to
    it, the one conversion it needs happens here, next to the one that sends
    geometry the other way.
    """
    point = to_lv95({"type": "Point", "coordinates": [request.lon, request.lat]})
    x, y = point["coordinates"]
    return {"x": round(x, 2), "y": round(y, 2), "crs": "EPSG:2056"}


class DraftConstraintRequest(BaseModel):
    """A rule the planner is writing themselves, in the UI or through the agent."""

    id: str
    title: str
    type: str
    source_text: str = Field(..., description="Where the planner says the rule comes from.")
    layer: str | None = None
    applies_to: str | None = None
    d_m: float | None = None
    hard: bool = True
    weight: float = 1.0
    source_url: str | None = None
    area_id: str | None = None


@app.post("/api/constraints/draft")
def draft_constraint(request: DraftConstraintRequest) -> dict:
    """Validate a planner's own constraint and hand it back as a proposal.

    The same function the agent calls, so a rule typed into the form and a rule
    dictated to the chat are validated identically — and neither is applied here.
    Whether it is used is the planner's click (docs/PLAN.md §7).
    """
    try:
        result = tools_core.propose_constraint(
            id=request.id,
            title=request.title,
            type=request.type,
            source_text=request.source_text,
            params={"d_m": request.d_m} if request.d_m is not None else {},
            layer=request.layer,
            applies_to=request.applies_to,
            hard=request.hard,
            source_url=request.source_url,
            area_id=_area_id(request.area_id),
        )
    except tools_core.ToolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    result["proposal"]["weight"] = request.weight
    result["description"] = result.get("description")
    return result


@app.get("/api/constraint-types")
def constraint_types() -> dict:
    """The constraint vocabulary, so the UI can build a form instead of hard-coding it."""
    return {
        "types": [
            {
                "name": "min_distance",
                "label": "at least … m from",
                "needs_layer": True,
                "needs_distance": True,
            },
            {
                "name": "max_distance",
                "label": "within … m of",
                "needs_layer": True,
                "needs_distance": True,
            },
            {"name": "within", "label": "on", "needs_layer": True, "needs_distance": False},
            {
                "name": "not_within",
                "label": "not inside",
                "needs_layer": True,
                "needs_distance": False,
            },
            {
                "name": "min_spacing",
                "label": "… m between the objects",
                "needs_layer": False,
                "needs_distance": True,
            },
        ],
        "registered": registered_types(),
    }


class ZonePreviewRequest(BaseModel):
    """Step 3 of the journey: show what the ticked constraints leave available."""

    area: Geometry | None = Field(
        default=None, description="Polygon in EPSG:2056. Defaults to the whole study area."
    )
    area_id: str | None = None
    constraints: list[Constraint] = Field(default_factory=list)


@app.post("/api/zones")
def zones(request: ZonePreviewRequest) -> dict:
    """Forbidden, required and allowed areas for a constraint set, in WGS84.

    The generator uses this same function, so what a planner sees here is exactly
    what step 4 will place into.
    """
    resolved = _area_id(request.area_id)
    area = request.area or get_area(resolved).polygon
    computed = compute_zones(area, request.constraints, area_id=resolved)
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
    area_id: str | None = None


@app.post("/api/check")
def check(request: CheckRequest) -> dict:
    resolved = _area_id(request.area_id)
    findings = check_features(request.features, request.constraints, resolved)
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
            {
                "id": c.id,
                "description": describe(c),
                "evaluable": unevaluable_reason(c, resolved) is None,
            }
            for c in request.constraints
        ],
    }


class GenerateRequest(BaseModel):
    """Step 4: constraints in, plan variants out."""

    area: Geometry | None = Field(default=None, description="Polygon in EPSG:2056.")
    area_id: str | None = None
    object: ObjectSpec
    constraints: list[Constraint] = Field(default_factory=list)
    fit_tolerance: float = Field(
        default=DEFAULT_FIT_TOLERANCE,
        ge=0.0,
        le=1.0,
        description="The planner's dial for the best-fit variant: 0 strict, 1 anything goes.",
    )


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
    resolved = _area_id(request.area_id)
    area = request.area or get_area(resolved).polygon
    spec = request.object

    if spec.geometry == "point":
        variants = generate_points(
            area,
            request.constraints,
            object_kind=spec.kind,
            target_count=spec.count,
            spacing_m=spec.spacing_m,
            area_id=resolved,
            fit_tolerance=request.fit_tolerance,
        )
        if not variants:
            report = explain_for_points(
                area, request.constraints, spec.count, spec.spacing_m, area_id=resolved
            )
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
            area_id=resolved,
        )
        if not variants:
            report = explain_for_line(
                area,
                request.constraints,
                tuple(spec.start),
                tuple(spec.end),
                area_id=resolved,
            )
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


# The website, served by the same app: one origin locally, one deployment on
# Vercel (which lifts the directory onto its CDN at build time). Registered
# last so every API route wins over a file of the same name.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.frontend("/", directory=WEB_DIR, fallback="index.html", check_dir=WEB_DIR.is_dir())
