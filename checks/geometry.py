"""Cached layer geometry for checks and generators.

A constraint like "5 m from any building" needs the union of 1170 building
polygons buffered by 5 m. Computing that once per request would be visible; the
planner re-runs checks while dragging a point. Layers never change at runtime,
so both the union and each buffered union are cached.
"""

from __future__ import annotations

from functools import cache

from shapely import unary_union
from shapely.geometry.base import BaseGeometry

from data.store import geometries


@cache
def layer_union(layer: str) -> BaseGeometry:
    """All features of a layer as one geometry."""
    return unary_union(geometries(layer))


@cache
def buffered_union(layer: str, distance_m: float) -> BaseGeometry:
    """The layer grown by `distance_m`. This is what a clearance rule looks like."""
    if distance_m <= 0:
        return layer_union(layer)
    # Buffer before union: buffering 1170 small polygons and merging is far
    # cheaper than buffering one huge multipolygon with thousands of vertices.
    return unary_union([g.buffer(distance_m) for g in geometries(layer)])


def nearest_distance_m(geom: BaseGeometry, layer: str) -> tuple[float, BaseGeometry | None]:
    """Distance from `geom` to the closest feature of `layer`, and that feature.

    Returns (inf, None) for an empty layer. Uses the store's spatial index with a
    widening search so a point near one building does not measure against 1169
    others.
    """
    from data.store import query

    for radius in (25.0, 100.0, 400.0, 2000.0):
        hits = query(layer, geom, distance_m=radius)
        if hits:
            best_geom, best_d = None, float("inf")
            for _feature, shp in hits:
                d = geom.distance(shp)
                if d < best_d:
                    best_d, best_geom = d, shp
            # A hit inside the search radius might still be beaten by one just
            # outside it, unless the winner is comfortably inside.
            if best_d <= radius or radius >= 2000.0:
                return best_d, best_geom
    return float("inf"), None


def connecting_line(a: BaseGeometry, b: BaseGeometry) -> dict | None:
    """The shortest line between two geometries — what the map draws for a finding."""
    from shapely import shortest_line

    if a is None or b is None:
        return None
    line = shortest_line(a, b)
    return None if line is None else line.__geo_interface__


def nearest_distances(points, layer: str):
    """Distance from each point to the nearest feature of a layer, vectorised.

    Uses the layer's STRtree rather than the distance to the merged union:
    measuring against one multipolygon costs O(all vertices) per point, while a
    nearest-neighbour query costs O(log n). On 170k candidates against the
    pavement layer that is the difference between seconds and milliseconds.
    """
    import numpy as np
    import shapely

    from data.store import _index

    tree, _features, geoms = _index(layer)
    if not geoms:
        return np.full(len(points), np.inf)
    pts = shapely.points(points[:, 0], points[:, 1])
    _idx, distances = tree.query_nearest(pts, return_distance=True, all_matches=False)
    return distances


def contains_mask(points, layer: str):
    """True where a point falls inside the layer's polygons.

    A prepared containment test, which is far cheaper than asking for a distance
    when the answer only needs to be inside/outside.
    """
    import numpy as np
    import shapely

    union = layer_union(layer)
    if union.is_empty:
        return np.zeros(len(points), dtype=bool)
    shapely.prepare(union)
    return shapely.contains_xy(union, points[:, 0], points[:, 1])
