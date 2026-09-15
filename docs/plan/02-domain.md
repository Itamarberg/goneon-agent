# Domain plan: data, rules, checks

What each check computes, which Swiss sources each rule cites, and which data
layers feed them. Every threshold below is **draft** until someone has read the
source; the `verified` flag in the catalog is the single place that records that.
Values here come from web summaries of the sources on 2026-09-15, not from the
documents themselves.

Legend for the *status* column: **open** = downloadable open data; **restricted** =
exists but not public; **synthetic** = we generate it and label it.

## 1. Study area

Requirements: ≥ 15 m of terrain range (sewer gradients need slope), mixed housing,
at least one school (sensitive use for the power rule), street trees present, a
road grid that gives ≥ 2 distinct routes between most point pairs.

Choose in Phase 1 with `scripts/pick_area.py`: for each candidate bbox, report DEM
min/max, building count, school count, tree count from the fetched layers.
Candidates: Wipkingen (slope to the Limmat), Unterstrass (Milchbuck slope),
Altstetten (flatter; weaker for sewers). Keep the area ≤ 1 km² so fixtures stay
small (< 10 MB) and proposal tools run in < 2 s.

`config.default_bbox` is currently a 2 × 2 km square near the main station —
placeholder; replace when chosen.

## 2. Data layers

| Layer (`kind`) | Source | Status | Format / access | Notes |
|---|---|---|---|---|
| `building` | Stadt Zürich, *Amtliche Vermessungsdaten Stadt Zürich Jahresendstand 2025* (`geo_amtliche_vermessungsdaten_stadt_zuerich_jahresendstand_2025`), layer Bodenbedeckung → Gebäude | open, CC0 | GPKG / JSON / WFS | footprints, no heights. Heights not needed for horizontal checks. Fallback: swisstopo STAC `ch.swisstopo.swissbuildings3d_3_0` |
| `building.use` | Stadt Zürich *Schulanlagen* (`geo_schulanlagen`) points joined to nearest footprint | open, CC0 | GeoJSON | tags `use: school` → sensitive use. Extend with hospitals/kindergartens if present in the area |
| `road` (polygon) | same AV dataset, Bodenbedeckung → Strasse/Weg | open | GPKG | for "under the road" cover rule and drawing |
| `road_centreline` | OpenStreetMap highway ways (via Overpass, once) | open, ODbL | GeoJSON | the routing graph for sewers and the synthetic utilities. swissTLM3D is the official alternative if OSM quality is poor |
| `tree` | Stadt Zürich *Baumkataster* (`geo_baumkataster`) | open, CC0 | GeoJSON (WGS84 → reproject) | real trees; attributes incl. species, planting year |
| `terrain` | swisstopo swissALTI3D, STAC collection `ch.swisstopo.swissalti3d`, 2 m GeoTIFF, EPSG:2056 | open (swisstopo OGD terms) | STAC items by bbox → tiff | clip to bbox; `swisstopopy` handles the STAC query. 0.5 m is available but 2 m is enough |
| `sewer` (existing) | Stadt Zürich Leitungskataster (ERZ) | restricted | order via GeoShop | **synthetic** in the MVP |
| `water_main` | WVZ Leitungskataster | restricted | order via GeoShop | **synthetic** |
| `gas_main` | Energie 360° | restricted | — | **synthetic** |
| `power_cable` | ewz Werkleitungen | restricted | — | **synthetic** |
| `power_line` (existing overhead) | none in an urban area at MV | — | — | only user-proposed features |

Licences to state in the README: Stadt Zürich datasets CC0; OSM ODbL (attribution);
swisstopo free use with source statement.

### 2.1 `scripts/fetch_layers.py`

One function per layer, all writing `data/fixtures/<kind>.geojson` in EPSG:2056
with `properties.kind`, `properties.source`, `properties.source_url`,
`properties.fetched_at`. Re-runnable; idempotent; no API keys needed. Terrain is
written as `data/fixtures/terrain.tif` (clipped) plus `terrain.npy` + affine as the
no-rasterio fallback.

### 2.2 `scripts/make_synthetic_utilities.py`

Deterministic (seeded) generation along `road_centreline`:

| kind | Placement | Attributes |
|---|---|---|
| `sewer` (existing main) | road axis; invert = DEM − 2.5 m, smoothed to keep ≥ 0.3 % fall toward the lowest outlet | `diameter_mm` (300–800 by road class), `invert_start_m`, `invert_end_m`, `material` |
| `water_main` | offset +2.0 m from axis | `diameter_mm` 100–300, `depth_m` 1.2 |
| `gas_main` | offset −2.5 m | `diameter_mm` 100–200, `depth_m` 0.9 |
| `power_cable` | offset +4.0 m (under the sidewalk) | `voltage_kv` 1 / 10, `depth_m` 0.7 |
| `telecom` | offset −4.0 m | `depth_m` 0.6 |

Every feature: `properties.source = "synthetic"`, `properties.synthetic_note =
"Generated for the hackathon. The real Leitungskataster is not open data."` The UI
renders synthetic layers dashed with a legend note.

## 3. Rule catalog

Format is the existing YAML (`rules/catalog/*.yaml`). Add `jurisdiction`
(`CH`, `CH-ZH`) and `version` fields to `Rule`. Below: what to encode, the draft
value, the source to verify against, and the check type.

### 3.1 Drainage (`topic: drainage`, subject `sewer`)

| id | Rule | Draft value | Source to verify | Check |
|---|---|---|---|---|
| `sewer-min-gradient` | minimum gradient by diameter | 0.5 % (DN ≤ 300), 0.3 % (DN > 300) | SN 592 000:2024 applies to *property* drainage (DN 110 → 0.5 %, DN 90 → 1.5 % per VSA Q&A). For public collectors verify against **SIA 190** and the VSA guidance; the 0.3 % figure is a placeholder | `min_gradient_by_diameter` |
| `sewer-max-velocity` | max flow velocity at full pipe (scour) | 4.0 m/s (draft) | SIA 190 / VSA | `capacity_manning` (returns v) |
| `sewer-min-cover` | minimum cover under roads (frost, traffic) | 1.2 m (draft) | SIA 190; Stadt Zürich ERZ Werkleitungsnormen | `min_cover` |
| `sewer-capacity` | full-pipe capacity ≥ design flow at chosen fill ratio | fill ≤ 0.7 (draft); Manning n = 0.013 concrete / 0.010 PP (standard values) | VSA / SIA 190; n from standard hydraulics tables | `capacity_manning` |
| `sewer-water-main-clearance` | clearance to potable water mains (contamination) | 0.5 m vertical / 1.0 m horizontal (draft) | SVGW W4; Stadt Zürich WVZ | `min_distance` |
| `sewer-tree-clearance` | distance to existing trees | 2.0 m (same source as T-01, symmetric) | Standards Stadträume | `min_distance` |

Hydraulics implemented (all in `checks/hydraulics.py`, pure):
- gradient S = (invert_start − invert_end) / length
- full-pipe area A = π D²/4, hydraulic radius R = D/4
- Manning: Q_full = (1/n) · A · R^(2/3) · S^(1/2); v_full = Q_full / A
- partial-flow capacity at fill ratio f: use the standard circular-section ratio
  Q(f)/Q_full (table or closed form via the central angle). Keep the table version;
  it's readable.
- cover depth along the line: sample DEM every 5 m, cover = ground − (invert
  interpolated + D). Report the minimum and where.

Unit test with a hand-computed case: D = 300 mm, n = 0.013, S = 0.005 → Q_full ≈
… (compute once by hand, write the number into the test with the working shown).

### 3.2 Power (`topic: power`, subject `power_line`, object `building`)

| id | Rule | Draft value | Source to verify | Check |
|---|---|---|---|---|
| `powerline-building-clearance` | horizontal distance conductors/supports to buildings | 5.0 m | **LeV (SR 734.31) Art. 38 + Anhang 8**: horizontal ≥ 5 m; direct distance in wind deflection ≥ 2.5 m + 0.01 m/kV | `clearance_profile` (horizontal only; note that vertical/wind cases are not modelled) |
| `powerline-sensitive-use-corridor` | keep distance from sensitive-use sites (schools, housing) | corridor half-width **20 m** for MV — **this is a modelling proxy, not a regulatory number** | **NISV (SR 814.710) Anhang 1 Ziff. 1**: Anlagegrenzwert **1 µT** at OMEN for new installations. The field depends on current, geometry and phase order; we cannot compute it in the MVP, so the rule is `severity: warning` with `notes` saying a field calculation is required | `clearance_profile` with `object_filter: {use: school}` |
| `powerline-max-vertices` | keep alignment simple | 8 (scenario target, not a regulation) | — (scenario metric, not a rule) | metric only |

Honesty rule for the video: say explicitly that the NISV check is a distance proxy
and that a real assessment needs the field calculation per NISV — that is the kind
of scope statement they grade for.

### 3.3 Vegetation (`topic: vegetation`, subject `tree`)

| id | Rule | Draft value | Source to verify | Check |
|---|---|---|---|---|
| `tree-utility-clearance` | trunk to any underground utility | **2.00 m** | **Stadt Zürich Standards Stadträume, "Bäume und Baumscheiben"**: "zwischen Baumstamm und Werkleitung ein Mindestabstand von 2.00 m" | `min_distance` vs `[sewer, water_main, gas_main, power_cable, telecom]` |
| `tree-mast-clearance` | trunk to VBZ masts / overhead contact lines | 2.00 m | same page: "Mindestabstand zu VBZ-Masten und Fahrleitungen beträgt 2.00 m" | `min_distance` vs `mast` (layer optional) |
| `tree-building-clearance` | trunk to façade | 4.0 m (draft; not found in the page) | Standards Stadträume / TED-Norm 18.01 (referenced there); Kanton ZH PBG Grenzabstand rules are a different thing (property boundaries) | `min_distance` vs `building` |
| `tree-not-on-building` | trunk not inside a footprint | — | trivial | `not_within` |
| `tree-spacing` | spacing between street trees | 8 m (draft; page says "möglichst dicht … der Baumart angepasst", refers to TED-Norm 18.01) | TED-Norm 18.01 | `min_spacing` |
| `tree-pit-area` | tree pit ≥ area | 6 m² (informational; large trees need 20–30 m² root zone) | same page | `info` only — surfaced in `explain_rule`, not checked geometrically |

## 4. Verification workflow (before the video)

1. For each rule, open the source (fedlex PDF for LeV/NISV; Stadt Zürich page for
   Standards Stadträume; SIA/VSA are paywalled — cite the norm number and say the
   value is unverified if you cannot read it).
2. Paste the exact sentence into `citation.section` / `notes`, set `url`, flip
   `verified: true`.
3. `pytest` enforces: `verified: true` ⇒ `url` or `section` present.
4. Target before sending: the three rules shown in the video verified
   (`tree-utility-clearance`, `powerline-building-clearance`, `sewer-min-gradient`
   or `sewer-capacity`). The rest stay draft, visibly.

## 5. Proposal algorithms (domain view)

Engineering details in `01-engineering.md` §1.4; here the planning logic.

**Sewer**: route along the road graph (sewers follow streets); assign inverts from
the downstream end backwards: invert_end = existing main invert at the tie-in;
walk upstream raising invert by max(min gradient, terrain-driven) while keeping
cover ≥ min. If cover forces a gradient below the minimum → report it as the
trade-off ("either deeper excavation or a pumping station"; the tool doesn't
propose pumping). Manholes at every direction change and every ≤ 60 m (draft; SIA
190).

**Power line**: cost raster at 2 m: impassable inside footprints + 5 m; cost 10 in
5–20 m of a sensitive-use building; cost 1 on roads; 3 elsewhere. Least-cost path,
then Douglas–Peucker simplification to ≤ N vertices, then `clearance_profile`.
Alternatives by changing the sensitive-use weight (strict vs lenient) so the planner
sees the trade-off length ↔ distance from the school.

**Trees**: candidates every `spacing_m` along the line (or a grid in a polygon),
starting offset varied to produce alternatives; each candidate run through all
vegetation rules; output accepted + rejected with the failing rule and measured
value. Species class only sets crown radius (used for spacing) — keep it to three
classes: small / medium / large.

## 6. Sources (to cite in the catalog and README)

- Stadt Zürich Open Data: Baumkataster — https://data.stadt-zuerich.ch/dataset/geo_baumkataster
- Stadt Zürich Open Data: Amtliche Vermessungsdaten 2025 — https://data.stadt-zuerich.ch/dataset/geo_amtliche_vermessungsdaten_stadt_zuerich_jahresendstand_2025
- Stadt Zürich Open Data: Schulanlagen — https://data.stadt-zuerich.ch/dataset/geo_schulanlagen
- Stadt Zürich, Leitungskataster beziehen (restricted) — https://www.stadt-zuerich.ch/de/planen-und-bauen/bauvorschriften-und-planerische-grundlagen/planbezug-datenbezug/leitungskataster-beziehen.html
- swisstopo STAC, swissALTI3D — https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissalti3d
- swisstopo STAC, swissBUILDINGS3D 3.0 — https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissbuildings3d_3_0
- Stadt Zürich Standards Stadträume, Bäume und Baumscheiben — https://www.stadt-zuerich.ch/misc/de/standards-stadtraeume/elementkatalog-auswahl/vegetation/baeume_baumscheiben.html
- LeV, SR 734.31 (Art. 38, Anhang 8) — https://www.fedlex.admin.ch/eli/cc/1994/1233_1233_1233/de
- NISV, SR 814.710 (Anhang 1) — https://www.fedlex.admin.ch/eli/cc/1999/446/de
- BAFU, Hochspannungsleitungen: Grenzwerte — https://www.bafu.admin.ch/de/hochspannungsleitungen-grenzwerte
- VSA, SN 592 000:2024 — https://vsa.ch/Mediathek/sn-592-000/ ; VSA Q&A Mindestgefälle — https://vsa.ch/qa/mindestgefaelle-schmutzwasserleitung/
- SIA 190 Kanalisationen — (paywalled; cite number)
