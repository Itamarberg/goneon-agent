"""Reprojection between storage CRS (LV95, metres) and the map's WGS84.

Kept in one place so the rule "compute in metres, display in degrees" has a
single implementation. Transformers are built once; pyproj caches are not free.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyproj import Transformer

from domain.models import CRS_STORAGE, CRS_WGS84, Geometry


@lru_cache(maxsize=4)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def _map_coords(coords: Any, fn) -> Any:
    """Walk a GeoJSON coordinate tree and apply fn to each position."""
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        x, y = fn(coords[0], coords[1])
        return [x, y]
    return [_map_coords(c, fn) for c in coords]


def reproject_geometry(geometry: Geometry, src: str, dst: str) -> Geometry:
    if src == dst:
        return geometry
    tf = _transformer(src, dst)
    if geometry.get("type") == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [reproject_geometry(g, src, dst) for g in geometry["geometries"]],
        }
    return {
        "type": geometry["type"],
        "coordinates": _map_coords(geometry["coordinates"], tf.transform),
    }


def to_wgs84(geometry: Geometry) -> Geometry:
    """For the map only. Never measure distances on the result."""
    return reproject_geometry(geometry, CRS_STORAGE, CRS_WGS84)


def to_lv95(geometry: Geometry) -> Geometry:
    """For anything the planner drew in the browser, before it touches a check."""
    return reproject_geometry(geometry, CRS_WGS84, CRS_STORAGE)
