"""Fetch the real open-data layers for the study area and bake them into data/layers/.

Run once (and whenever the study area changes):

    uv run python scripts/fetch_layers.py

The API never calls a third-party service at request time: a hackathon with 100
planners cannot depend on someone else's rate limit (docs/PLAN.md §5). The files
this writes are committed and copied into the Docker image.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.sources import LAYER_SOURCES, LayerSource  # noqa: E402
from data.study_area import STUDY_AREA  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "layers"
TIMEOUT_S = 180
RETRIES = 3
# 1 cm. The sources publish ~10 decimals; keeping them triples the file size and
# means nothing for a survey whose own accuracy is centimetres at best.
COORD_DECIMALS = 2


def _get(url: str) -> bytes:
    """GET with a couple of retries: these are public services and they do throttle."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "neon-agent/0.1 (open data fetch)"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 - fixed hosts
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < RETRIES - 1:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{last}")


def _round_coords(coords):
    if isinstance(coords, (int, float)):
        return round(coords, COORD_DECIMALS)
    return [_round_coords(c) for c in coords]


def _round_geometry(geom: dict) -> dict:
    return {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"])}


def _bbox_str(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(str(v) for v in bbox)


def fetch_raw(src: LayerSource, bbox: tuple[float, float, float, float]) -> list[dict]:
    """Return raw GeoJSON features for one source inside the bbox (EPSG:2056)."""
    if src.kind in ("wfs20", "wfs11"):
        is20 = src.kind == "wfs20"
        params = {
            "service": "WFS",
            "version": "2.0.0" if is20 else "1.1.0",
            "request": "GetFeature",
            ("typenames" if is20 else "typename"): src.typename,
            "outputFormat": "geojson" if is20 else "application/json",
            "srsName": "EPSG:2056",
            "bbox": f"{_bbox_str(bbox)},EPSG:2056",
        }
        url = f"{src.endpoint}?{urllib.parse.urlencode(params)}"
    elif src.kind == "geoadmin":
        # The federal API has no WFS for this layer; identify over an envelope is the
        # documented way to pull its geometry.
        extent = _bbox_str(bbox)
        params = {
            "geometryType": "esriGeometryEnvelope",
            "geometry": extent,
            "mapExtent": extent,
            "imageDisplay": "100,100,96",
            "tolerance": "0",
            "layers": f"all:{src.typename}",
            "returnGeometry": "true",
            "sr": "2056",
            "geometryFormat": "geojson",
            "limit": "200",
        }
        url = f"{src.endpoint}?{urllib.parse.urlencode(params)}"
    else:  # pragma: no cover - guarded by the dataclass Literal
        raise ValueError(f"unknown source kind {src.kind}")

    payload = json.loads(_get(url))
    return payload.get("features") or payload.get("results") or []


def normalise(src: LayerSource, raw: list[dict]) -> list[dict]:
    """Apply the class filter, drop properties we do not use, and stamp provenance."""
    out: list[dict] = []
    for i, f in enumerate(raw):
        props = f.get("properties") or {}
        if src.select_property and props.get(src.select_property) not in src.select_values:
            continue
        geom = f.get("geometry")
        if not geom:
            continue
        kept = {k: props[k] for k in src.keep_properties if k in props}
        out.append(
            {
                "type": "Feature",
                "id": f"{src.name}-{i}",
                "geometry": _round_geometry(geom),
                "properties": kept,
            }
        )
    return out


def write_layer(src: LayerSource, features: list[dict]) -> Path:
    path = OUT_DIR / f"{src.name}.geojson"
    doc = {
        "type": "FeatureCollection",
        "name": src.name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2056"}},
        "properties": {
            "title": src.title,
            "geometry_type": src.geometry_type,
            "source": src.source,
            "source_url": src.source_url,
            "licence": src.licence,
            "notes": src.notes,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "study_area": STUDY_AREA.name,
        },
        "features": features,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="layer names to fetch (default: all)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bbox = STUDY_AREA.bbox
    print(f"study area: {STUDY_AREA.name}  bbox={_bbox_str(bbox)} (EPSG:2056)")

    failures = 0
    for src in LAYER_SOURCES:
        if args.only and src.name not in args.only:
            continue
        try:
            raw = fetch_raw(src, bbox)
            features = normalise(src, raw)
            path = write_layer(src, features)
            size_kb = path.stat().st_size / 1024
            print(f"  {src.name:<16} {len(features):>6} features  {size_kb:>8.0f} KB")
        except Exception as e:  # noqa: BLE001 - a failing source must not stop the rest
            failures += 1
            print(f"  {src.name:<16} FAILED: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
