"""The areas the MVP ships data for.

Not the whole city: the baked layers must fit in a container image and load at
startup, and every check has to be fast enough to re-run while a planner drags a
point (docs/PLAN.md §5). So a handful of quarters are pre-fetched, and the
planner picks one.

Three rather than one, because "does this generalise beyond the demo area?" is
the obvious question, and a dense inner-city quarter, an industrial one in
transition and an outer centre are genuinely different planning problems on the
same code.

`districts` records which statistical quarters each box actually covers,
measured against the city's own boundary data. It is there because the first
version of this file named its area from memory and got it wrong — the box
labelled "Kreis 5" was 90% Kreis 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StudyArea:
    id: str
    title: str
    description: str
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax in EPSG:2056
    # (share of the box in percent, quarter name) from Statistische Quartiere.
    districts: tuple[tuple[float, str], ...] = field(default_factory=tuple)

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

    @property
    def districts_label(self) -> str:
        return ", ".join(f"{name} {share:.0f}%" for share, name in self.districts)


STUDY_AREAS: dict[str, StudyArea] = {
    a.id: a
    for a in (
        StudyArea(
            id="langstrasse",
            title="Langstrasse (Kreis 4)",
            description=(
                "Dense inner-city quarter: tight street grid, little open ground, "
                "trees and hydrants everywhere, a >36 kV cable crossing it."
            ),
            bbox=(2681800.0, 1247500.0, 2682800.0, 1248500.0),
            districts=(
                (85.4, "Langstrasse (Kreis 4)"),
                (6.5, "Gewerbeschule (Kreis 5)"),
                (4.9, "Werd (Kreis 4)"),
                (3.1, "City (Kreis 1)"),
            ),
        ),
        StudyArea(
            id="escher-wyss",
            title="Escher-Wyss (Kreis 5)",
            description=(
                "Former industrial area in transition: larger plots, more open "
                "ground and green space, the Limmat along its northern edge."
            ),
            bbox=(2681400.0, 1248700.0, 2682400.0, 1249700.0),
            districts=(
                (48.9, "Escher Wyss (Kreis 5)"),
                (27.8, "Gewerbeschule (Kreis 5)"),
                (19.9, "Wipkingen (Kreis 10)"),
                (3.1, "Langstrasse (Kreis 4)"),
            ),
        ),
        StudyArea(
            id="oerlikon",
            title="Oerlikon (Kreis 11)",
            description=(
                "Outer centre: wider streets, more room between buildings, and "
                "the highest number of kindergartens of the three."
            ),
            bbox=(2683000.0, 1251500.0, 2684000.0, 1252500.0),
            districts=(
                (59.4, "Oerlikon (Kreis 11)"),
                (40.6, "Seebach (Kreis 11)"),
            ),
        ),
    )
}

DEFAULT_AREA_ID = "langstrasse"


class UnknownStudyArea(LookupError):
    """Asked for an area this deployment has no data for."""


def get_area(area_id: str | None = None) -> StudyArea:
    """Look up a study area, defaulting to the one the UI opens on."""
    if area_id is None:
        return STUDY_AREAS[DEFAULT_AREA_ID]
    if area_id not in STUDY_AREAS:
        known = ", ".join(STUDY_AREAS)
        raise UnknownStudyArea(f"Unknown study area {area_id!r}. Available: {known}.")
    return STUDY_AREAS[area_id]


#: Kept for the many call sites that just want the default.
STUDY_AREA = STUDY_AREAS[DEFAULT_AREA_ID]
