"""Route a line object — a cable, a pipe, a path — between two points.

Same idea as the point generator, one dimension up: constraints become a cost
raster, and the route is the cheapest path across it.

    allowed area  →  cost raster  →  least-cost path  →  simplified line

Hard constraints make cells impassable, so a returned route cannot violate one.
Soft constraints raise the cost of crossing a cell, so the route bends around a
school rather than refusing to exist (docs/PLAN.md §4).
"""

from __future__ import annotations

import logging

import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from shapely.geometry import LineString

from checks.plan import check_features, has_hard_violation
from checks.zones import Zones, compute_zones
from domain.models import Constraint, Feature, Geometry, Variant
from generate.points import _tradeoffs
from generate.scoring import cost_surface

log = logging.getLogger(__name__)

# 2 m cells: fine enough for a clearance rule stated in metres, coarse enough
# that a 1 km² area stays at 250k cells and the path solves in well under a second.
CELL_M = 2.0
MAX_CELLS = 400_000
IMPASSABLE = np.inf

# Moving diagonally covers sqrt(2) cells, so it must cost that much more —
# otherwise the cheapest path is a staircase that looks arbitrary to a planner.
NEIGHBOURS = [
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (0, -1, 1.0),
    (0, 1, 1.0),
    (-1, -1, 2**0.5),
    (-1, 1, 2**0.5),
    (1, -1, 2**0.5),
    (1, 1, 2**0.5),
]


class Raster:
    """A cost grid over the area, with the mapping back to LV95 coordinates."""

    def __init__(self, bounds: tuple[float, float, float, float], cell_m: float):
        xmin, ymin, xmax, ymax = bounds
        self.cell_m = cell_m
        self.xmin, self.ymin = xmin, ymin
        self.ncols = max(2, int((xmax - xmin) / cell_m))
        self.nrows = max(2, int((ymax - ymin) / cell_m))

    @property
    def size(self) -> int:
        return self.nrows * self.ncols

    def centres(self) -> np.ndarray:
        xs = self.xmin + self.cell_m / 2 + np.arange(self.ncols) * self.cell_m
        ys = self.ymin + self.cell_m / 2 + np.arange(self.nrows) * self.cell_m
        gx, gy = np.meshgrid(xs, ys)
        return np.column_stack([gx.ravel(), gy.ravel()])

    def index_of(self, x: float, y: float) -> int:
        col = int(np.clip((x - self.xmin) / self.cell_m, 0, self.ncols - 1))
        row = int(np.clip((y - self.ymin) / self.cell_m, 0, self.nrows - 1))
        return row * self.ncols + col


def build_raster(zones: Zones, constraints: list[Constraint], soft_weight: float) -> tuple:
    """Cost per cell: 1 to cross a free cell, more where soft constraints object.

    Cells outside the allowed area are impassable, which is how a hard
    constraint is enforced here — the router cannot express a violation.
    """
    bounds = zones.area.bounds
    cell = CELL_M
    raster = Raster(bounds, cell)
    if raster.size > MAX_CELLS:
        cell *= np.sqrt(raster.size / MAX_CELLS)
        raster = Raster(bounds, cell)

    centres = raster.centres()
    shapely.prepare(zones.allowed)
    passable = shapely.contains_xy(zones.allowed, centres[:, 0], centres[:, 1])

    cost = np.ones(raster.size, dtype=float)
    soft, _used = cost_surface(centres, constraints)
    cost += soft_weight * soft
    cost[~passable] = IMPASSABLE
    return raster, cost, centres


# How far an endpoint may be moved to reach legal ground. Beyond this the request
# is infeasible and gets explained, rather than quietly relocated.
SNAP_TOLERANCE_M = 30.0


class Grid:
    """The passable cells as a graph, so a route can be solved and reasoned about.

    Hard constraints fragment a dense quarter: the study area's allowed cells fall
    into one large region plus a couple of hundred courtyards enclosed by
    buildings. Routing is only meaningful inside one connected region, so the
    graph exposes its components and endpoints are snapped into the main one.
    """

    def __init__(self, raster: Raster, cost: np.ndarray):
        self.raster = raster
        self.cost = cost
        self.passable = np.isfinite(cost)

        rows = np.arange(raster.size)
        grid_r, grid_c = np.divmod(rows, raster.ncols)
        src_list, dst_list, w_list = [], [], []
        for dr, dc, step in NEIGHBOURS:
            r2, c2 = grid_r + dr, grid_c + dc
            ok = (r2 >= 0) & (r2 < raster.nrows) & (c2 >= 0) & (c2 < raster.ncols)
            src = rows[ok]
            dst = (r2[ok] * raster.ncols + c2[ok]).astype(np.int64)
            # Entering a cell costs that cell's cost; averaging the two endpoints
            # keeps the traversal symmetric.
            w = step * raster.cell_m * (cost[src] + cost[dst]) / 2.0

            if dr and dc:
                # A diagonal step passes through the corner between the two
                # orthogonal cells. If either is forbidden, the route would clip
                # a building corner while both its endpoints look legal — the
                # checker catches that, so do not generate it in the first place.
                side_a = grid_r[ok] * raster.ncols + c2[ok]
                side_b = r2[ok] * raster.ncols + grid_c[ok]
                w = np.where(np.isfinite(cost[side_a]) & np.isfinite(cost[side_b]), w, np.inf)

            finite = np.isfinite(w)
            src_list.append(src[finite])
            dst_list.append(dst[finite])
            w_list.append(w[finite])

        self.graph = coo_matrix(
            (np.concatenate(w_list), (np.concatenate(src_list), np.concatenate(dst_list))),
            shape=(raster.size, raster.size),
        ).tocsr()

        _n, labels = connected_components(self.graph, directed=False)
        self.labels = labels
        # The largest region among passable cells: the part of the area a route
        # can actually cross.
        counts = np.bincount(labels[self.passable], minlength=labels.max() + 1)
        self.main_label = int(np.argmax(counts))
        self.main_cells = np.flatnonzero((labels == self.main_label) & self.passable)

    def snap(self, centres: np.ndarray, x: float, y: float) -> tuple[int, float] | None:
        """Nearest routable cell to a clicked point, and how far that is.

        A planner clicks on a map; the point may land on a roof or inside a
        clearance zone. Moving it is acceptable, hiding the move is not — the
        caller reports the distance.
        """
        if self.main_cells.size == 0:
            return None
        index = self.raster.index_of(x, y)
        if self.passable[index] and self.labels[index] == self.main_label:
            return index, 0.0

        d = np.hypot(centres[self.main_cells, 0] - x, centres[self.main_cells, 1] - y)
        nearest = int(self.main_cells[np.argmin(d)])
        moved = float(d.min())
        return (nearest, moved) if moved <= SNAP_TOLERANCE_M else None

    def least_cost_path(self, start: int, end: int) -> list[int] | None:
        """Dijkstra over the grid. Returns cell indices, or None if disconnected.

        Solved as a sparse matrix in scipy rather than a Python heap: 250k cells
        and 2M edges is seconds in Python and milliseconds here, and the planner
        is waiting.
        """
        distances, predecessors = dijkstra(
            self.graph, directed=False, indices=start, return_predecessors=True
        )
        if not np.isfinite(distances[end]):
            return None

        path = [end]
        while path[-1] != start:
            prev = predecessors[path[-1]]
            if prev < 0:
                return None
            path.append(int(prev))
        return path[::-1]


def _to_line(centres: np.ndarray, path: list[int], tolerance_m: float, allowed) -> LineString:
    """Cell centres to a line, simplified as far as the allowed area permits.

    Simplification is what turns a staircase of cell centres into something a
    planner recognises as a route, but a shortcut may cross ground the hard
    constraints forbid. So each tolerance is tried and kept only if the result
    still lies inside the allowed area — the unsimplified path always does.
    """
    coords = [(float(centres[i][0]), float(centres[i][1])) for i in path]
    line = LineString(coords)
    if tolerance_m <= 0:
        return line

    tolerance = tolerance_m
    while tolerance >= 0.5:
        candidate = line.simplify(tolerance, preserve_topology=False)
        if allowed.covers(candidate):
            return candidate
        tolerance /= 2
    return line


STRATEGIES = (
    ("A", "Shortest route", 1.0, 1.0),
    ("B", "Keeps its distance", 6.0, 1.0),
    ("C", "Fewest bends", 1.0, 12.0),
)


def generate_line(
    area: Geometry,
    constraints: list[Constraint],
    start: tuple[float, float],
    end: tuple[float, float],
    object_kind: str = "line",
    zones: Zones | None = None,
) -> list[Variant]:
    """Route variants between two points. Deterministic.

    The three strategies differ only in how much the soft constraints weigh and
    how hard the result is simplified — same raster, same solver.
    """
    zones = zones or compute_zones(area, constraints)
    if zones.is_empty:
        return []

    variants: list[Variant] = []
    for label_id, label, soft_weight, tolerance in STRATEGIES:
        raster, cost, centres = build_raster(zones, constraints, soft_weight)
        grid = Grid(raster, cost)
        snapped_start = grid.snap(centres, *start)
        snapped_end = grid.snap(centres, *end)
        if snapped_start is None or snapped_end is None:
            # The endpoint is forbidden and nothing routable is near it. That is
            # an infeasibility to explain, not a route to fudge.
            continue
        s, moved_start = snapped_start
        e, moved_end = snapped_end

        path = grid.least_cost_path(s, e)
        if path is None:
            continue

        line = _to_line(centres, path, tolerance, zones.allowed)
        variant_id = f"line-{label_id}"
        feature = Feature(
            id=f"{variant_id}-0",
            kind=object_kind,
            geometry=line.__geo_interface__,
            properties={"generated": True},
        )
        findings = check_features([feature], constraints)
        if has_hard_violation(findings):
            log.error("route %s violates a hard constraint it was generated under", variant_id)
            continue

        variants.append(
            Variant(
                id=variant_id,
                label=f"{label_id} — {label}",
                strategy=label.lower().replace(" ", "_"),
                features=[feature],
                metrics={
                    "length_m": round(line.length, 1),
                    "vertices": len(line.coords),
                    "cell_m": round(raster.cell_m, 2),
                    "soft_weight": soft_weight,
                    # Reported, not hidden: how far each endpoint had to move to
                    # reach ground the hard constraints allow.
                    "start_moved_m": round(moved_start, 1),
                    "end_moved_m": round(moved_end, 1),
                },
                findings=findings,
                tradeoffs=_tradeoffs([feature], constraints, findings),
            )
        )

    return _dedupe_routes(variants)


def _dedupe_routes(variants: list[Variant]) -> list[Variant]:
    seen: dict[tuple, Variant] = {}
    for v in variants:
        key = tuple(tuple(c) for c in v.features[0].geometry["coordinates"])
        seen.setdefault(key, v)
    return list(seen.values())
