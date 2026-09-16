"""Where every layer comes from, as data rather than ten bespoke scripts.

Each entry names a real open dataset, the endpoint it is fetched from and the
licence, so `scripts/fetch_layers.py` stays generic and adding a layer is one
record here (ARCHITECTURE.md, extension point 3).

Several planning layers come out of one dataset: the cantonal land-cover
polygons (AV Bodenbedeckung) carry an `artzh` class that distinguishes
buildings, pavements, roads and parks. `select` splits them into the layers a
planner actually reasons about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OGD_ZH_WFS = "https://maps.zh.ch/wfs/OGDZHWFS"
STZH_WFS = "https://www.ogd.stadt-zuerich.ch/wfs/geoportal"
GEOADMIN_IDENTIFY = "https://api3.geo.admin.ch/rest/services/api/MapServer/identify"

BODENBEDECKUNG = "ms:ogd-0401_arv_basis_avzh_bodenbedeckung_f"


@dataclass(frozen=True)
class LayerSource:
    name: str  # the layer name constraints refer to
    title: str
    geometry_type: Literal["Point", "LineString", "Polygon"]
    kind: Literal["wfs20", "wfs11", "geoadmin"]
    endpoint: str
    typename: str
    source: str  # human-readable attribution
    source_url: str
    licence: str
    # Keep only features whose `select_property` is in `select_values`.
    select_property: str | None = None
    select_values: tuple[str, ...] = ()
    # Property names copied onto the stored feature; everything else is dropped so
    # the baked files stay small and contain no personal data.
    keep_properties: tuple[str, ...] = ()
    notes: str = ""


BUILDING_CLASSES = (
    "Gebäude Wohnen",
    "Gebäude Verwaltung",
    "Gebäude Handel",
    "Gebäude Industrie",
    "Gebäude Gastgewerbe",
    "Gebäude Verkehrswesen",
    "Nebengebäude",
)
ROAD_CLASSES = ("Strasse, Weg", "Veloweg, Fussweg", "Verkehrsinsel befestigt", "Parkplatz")
GREEN_CLASSES = (
    "Parkanlage",
    "Bestockte Fläche",
    "Acker, Wiese, Weide",
    "Verkehrsinsel humusiert",
    "Sportanlage humusiert",
)

_AV_ATTRIBUTION = (
    "Kanton Zürich / Stadt Zürich, Amtliche Vermessung (AV MOpublic, Bodenbedeckung, OGD)"
)
_AV_URL = "https://data.stadt-zuerich.ch/dataset/ktzh_av_mopublic__bodenbedeckung__ogd_"

LAYER_SOURCES: list[LayerSource] = [
    LayerSource(
        name="building",
        title="Building footprints",
        geometry_type="Polygon",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename=BODENBEDECKUNG,
        select_property="artzh",
        select_values=BUILDING_CLASSES,
        keep_properties=("artzh", "objid"),
        source=_AV_ATTRIBUTION,
        source_url=_AV_URL,
        licence="CC0 / OGD",
    ),
    LayerSource(
        name="sidewalk",
        title="Pavements",
        geometry_type="Polygon",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename=BODENBEDECKUNG,
        select_property="artzh",
        select_values=("Trottoir",),
        keep_properties=("artzh", "objid"),
        source=_AV_ATTRIBUTION,
        source_url=_AV_URL,
        licence="CC0 / OGD",
    ),
    LayerSource(
        name="road",
        title="Roads, cycle and foot paths",
        geometry_type="Polygon",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename=BODENBEDECKUNG,
        select_property="artzh",
        select_values=ROAD_CLASSES,
        keep_properties=("artzh", "objid"),
        source=_AV_ATTRIBUTION,
        source_url=_AV_URL,
        licence="CC0 / OGD",
    ),
    LayerSource(
        name="green_space",
        title="Parks and green areas",
        geometry_type="Polygon",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename=BODENBEDECKUNG,
        select_property="artzh",
        select_values=GREEN_CLASSES,
        keep_properties=("artzh", "objid"),
        source=_AV_ATTRIBUTION,
        source_url=_AV_URL,
        licence="CC0 / OGD",
    ),
    LayerSource(
        name="water",
        title="Watercourses and basins",
        geometry_type="Polygon",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename=BODENBEDECKUNG,
        select_property="artzh",
        select_values=("Fliessgewässer", "Wasserbecken", "Stehendes Gewässer"),
        keep_properties=("artzh", "objid"),
        source=_AV_ATTRIBUTION,
        source_url=_AV_URL,
        licence="CC0 / OGD",
    ),
    LayerSource(
        name="tree",
        title="Street trees (Baumkataster)",
        geometry_type="Point",
        kind="wfs11",
        endpoint=f"{STZH_WFS}/Baumkataster",
        typename="baumkataster_baumstandorte",
        keep_properties=("baumnamedeu", "baumtyptext", "kronendurchmesser", "baumnummer"),
        source="Stadt Zürich, Grün Stadt Zürich — Baumkataster",
        source_url="https://data.stadt-zuerich.ch/dataset/geo_baumkataster",
        licence="CC0",
    ),
    LayerSource(
        name="school",
        title="Schools",
        geometry_type="Point",
        kind="wfs11",
        endpoint=f"{STZH_WFS}/Schulanlagen",
        typename="poi_volksschule_view",
        keep_properties=("name", "adresse", "stufe"),
        source="Stadt Zürich, Schulanlagen (POI)",
        source_url="https://data.stadt-zuerich.ch/dataset/geo_schulanlagen",
        licence="CC0",
        notes="Point location of the school, not the plot boundary. A clearance measured "
        "from it is a proxy; see the constraint's own note.",
    ),
    LayerSource(
        name="kindergarten",
        title="Kindergartens",
        geometry_type="Point",
        kind="wfs11",
        endpoint=f"{STZH_WFS}/Schulanlagen",
        typename="poi_kindergarten_view",
        keep_properties=("name", "adresse"),
        source="Stadt Zürich, Schulanlagen (POI)",
        source_url="https://data.stadt-zuerich.ch/dataset/geo_schulanlagen",
        licence="CC0",
    ),
    LayerSource(
        name="hydrant",
        title="Hydrants",
        geometry_type="Point",
        kind="wfs11",
        endpoint=f"{STZH_WFS}/Hydranten",
        typename="wvz_hydranten",
        keep_properties=("art_txt", "subart_txt", "oberflur_nummer"),
        source="Stadt Zürich, Wasserversorgung — Hydranten",
        source_url="https://data.stadt-zuerich.ch/dataset/geo_hydranten",
        licence="CC0",
    ),
    LayerSource(
        name="transit_stop",
        title="Public transport stops",
        geometry_type="Point",
        kind="wfs20",
        endpoint=OGD_ZH_WFS,
        typename="ms:ogd-0140_giszhpub_zvv_haltestellen_p",
        keep_properties=("chstname", "cname", "zonen"),
        source="Kanton Zürich / ZVV — Haltestellen",
        source_url="https://www.zh.ch/de/politik-staat/opendata.html",
        licence="OGD",
    ),
    LayerSource(
        name="power_line_hv",
        title="Electrical installations above 36 kV",
        geometry_type="LineString",
        kind="geoadmin",
        endpoint=GEOADMIN_IDENTIFY,
        typename="ch.bfe.elektrische-anlagen_ueber_36",
        keep_properties=(),
        source="BFE/ESTI — Elektrische Anlagen über 36 kV",
        source_url="https://map.geo.admin.ch/?layers=ch.bfe.elektrische-anlagen_ueber_36",
        licence="opendata.swiss / BFE",
        notes="Only installations above 36 kV are open. Low- and medium-voltage cables "
        "are not, so constraints against them cannot be evaluated.",
    ),
]

BY_NAME: dict[str, LayerSource] = {s.name: s for s in LAYER_SOURCES}

# Layers a planner may ask about that are deliberately absent, with the reason.
# check_plan() reports a constraint against one of these as "not_evaluable"
# instead of silently passing it (docs/PLAN.md §5).
UNAVAILABLE_LAYERS: dict[str, str] = {
    "underground_utility": "Leitungskataster is not open data; access is restricted to utilities.",
    "sewer": "The sewer network is not published as open data.",
    "power_line_lv": "Low- and medium-voltage cables are not open data; only >36 kV is.",
    "overhead_contact_line": (
        "VBZ masts and overhead contact lines are not published as open geodata."
    ),
}
