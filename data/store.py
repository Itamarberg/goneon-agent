"""Loads the baked layers once and answers questions about them.

Everything here is in EPSG:2056. Files are read on first use and cached for the
process lifetime: they are a few megabytes, they never change while the service
runs, and re-reading them per request would dominate the response time.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

from data.sources import BY_NAME, LAYER_SOURCES, UNAVAILABLE_LAYERS
from data.study_area import STUDY_AREA
from domain.models import Feature, Geometry, Layer, LayerInfo

LAYERS_DIR = Path(__file__).resolve().parent / "layers"


class LayerNotAvailable(LookupError):
    """Asked for a layer that is not in the deployment.

    Carries `reason` so the tools can answer "cannot be evaluated: the data is
    not open" rather than a bare 404 — that distinction is the honest part of
    the product (docs/PLAN.md §5).
    """

    def __init__(self, name: str, reason: str, evaluable: bool = False):
        super().__init__(reason)
        self.name = name
        self.reason = reason
        self.evaluable = evaluable


@cache
def load_layer(name: str) -> Layer:
    """Read one baked layer. Cached; the files are immutable at runtime."""
    if name in UNAVAILABLE_LAYERS:
        raise LayerNotAvailable(name, UNAVAILABLE_LAYERS[name])
    src = BY_NAME.get(name)
    path = LAYERS_DIR / f"{name}.geojson"
    if src is None or not path.exists():
        known = ", ".join(sorted(BY_NAME))
        raise LayerNotAvailable(name, f"Unknown layer '{name}'. Available layers: {known}.")

    doc = json.loads(path.read_text(encoding="utf-8"))
    props = doc.get("properties", {})
    features = [
        Feature(
            id=f.get("id") or f"{name}-{i}",
            kind=name,
            geometry=f["geometry"],
            properties=f.get("properties", {}),
            source=props.get("source"),
            source_url=props.get("source_url"),
        )
        for i, f in enumerate(doc["features"])
    ]
    return Layer(
        name=name,
        title=props.get("title", name),
        geometry_type=props.get("geometry_type", src.geometry_type),
        features=features,
        source=props.get("source", src.source),
        source_url=props.get("source_url", src.source_url),
        licence=props.get("licence", src.licence),
    )


@cache
def _index(name: str) -> tuple[STRtree, list[Feature], list]:
    """Spatial index and parsed geometries for a layer, built once.

    Without the index, a clearance check against 1170 buildings for every
    candidate position in a generator is O(candidates x buildings) and the
    generator stops being interactive. Parsing GeoJSON into shapely is the other
    half of that cost, so the parsed geometries are cached alongside the tree.
    """
    layer = load_layer(name)
    geoms = [shape(f.geometry) for f in layer.features]
    return STRtree(geoms), layer.features, geoms


def geometries(name: str) -> list:
    """Shapely geometries of a layer, in file order. For checks/ and generate/."""
    return _index(name)[2]


def query(name: str, geom, distance_m: float = 0.0) -> list[tuple[Feature, object]]:
    """Features of `name` whose bounds are within `distance_m` of `geom`.

    A bounds-level filter: the caller still measures exactly. It exists to keep
    the exact measurement off 1000+ irrelevant features.
    """
    tree, features, shapes = _index(name)
    search = geom.buffer(distance_m) if distance_m else geom
    return [(features[i], shapes[i]) for i in tree.query(search)]


def list_layers() -> list[LayerInfo]:
    """Every layer in the deployment, with counts and attribution."""
    out: list[LayerInfo] = []
    for src in LAYER_SOURCES:
        try:
            layer = load_layer(src.name)
        except LayerNotAvailable:
            continue
        out.append(
            LayerInfo(
                name=layer.name,
                title=layer.title,
                geometry_type=layer.geometry_type,
                feature_count=len(layer.features),
                source=layer.source,
                source_url=layer.source_url,
                licence=layer.licence,
            )
        )
    return out


def get_features_in(name: str, area: Geometry | None = None) -> list[Feature]:
    """Features of a layer, optionally clipped to an area (both in EPSG:2056).

    Clipping is by intersection, not containment: a building half inside the
    planner's polygon still constrains what happens inside it.
    """
    layer = load_layer(name)
    if area is None:
        return layer.features
    poly = shape(area)
    tree, features, shapes = _index(name)
    hits = tree.query(poly)
    return [features[i] for i in sorted(hits) if shapes[i].intersects(poly)]


def study_area_summary() -> dict:
    """What the UI shows in step 1: where we are and what is in it."""
    return {
        "name": STUDY_AREA.name,
        "description": STUDY_AREA.description,
        "bbox": list(STUDY_AREA.bbox),
        "polygon": STUDY_AREA.polygon,
        "area_km2": round(STUDY_AREA.area_km2, 3),
        "layers": [info.model_dump() for info in list_layers()],
        "unavailable_layers": [
            {"name": n, "reason": r} for n, r in sorted(UNAVAILABLE_LAYERS.items())
        ],
    }
