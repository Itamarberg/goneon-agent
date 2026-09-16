"""The area the MVP ships data for.

One quarter, not the whole city: the baked layers must stay small enough to sit
in a container image and load at startup, and every check has to be fast enough
to re-run while a planner drags a point (docs/PLAN.md §5).

The area is chosen from what the open data actually contains — it needs street
trees, a school, tram stops and a high-voltage installation, so that the curated
catalog has something to bite on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StudyArea:
    name: str
    description: str
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax in EPSG:2056

    @property
    def polygon(self) -> dict:
        xmin, ymin, xmax, ymax = self.bbox
        return {
            "type": "Polygon",
            "coordinates": [[[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]],
        }

    @property
    def center(self) -> tuple[float, float]:
        xmin, ymin, xmax, ymax = self.bbox
        return ((xmin + xmax) / 2, (ymin + ymax) / 2)

    @property
    def area_km2(self) -> float:
        xmin, ymin, xmax, ymax = self.bbox
        return (xmax - xmin) * (ymax - ymin) / 1_000_000


STUDY_AREA = StudyArea(
    name="zurich-kreis-5",
    description=(
        "Zürich Kreis 5 (Escher-Wyss / Limmatplatz) and the northern edge of Kreis 4. "
        "Dense street trees, schools, tram stops and a >36 kV installation."
    ),
    bbox=(2681800.0, 1247500.0, 2682800.0, 1248500.0),
)
