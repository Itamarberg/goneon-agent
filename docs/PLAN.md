# Plan

Take-home for goNEON, Platform & Ecosystem Owner: an overnight MVP for agentic
infrastructure planning that ~100 young planners can use at a hackathon.

This document is the plan and the decision log. Scope changes are recorded in §9
with the reason, because "cut the scope yourself and say why" is graded.

---

## 1. The product in one paragraph

A website where a planner picks an area of Zurich, says **what** they want to place
(trees, bike racks, a cable, a power line, …), and **which constraints** it must
respect — chosen from a catalog of cited rules or added in their own words. The
agent turns that into a structured planning request, a deterministic generator
produces 2–3 plan variants on **real open data**, the same checks verify every
variant, and the planner compares, adopts, edits and exports. When no plan is
possible, the tool says which constraint blocks it and what relaxing it would give.

Planners from any field use the same tool, because disciplines are data
(object types + constraints), not code.

## 2. Principles

1. **Code generates and decides; the LLM translates and explains.** Every geometry
   comes from a generator, every verdict from a check. The model maps natural
   language to structured constraints, calls tools, and explains results.
   ([ADR 0001](adr/0001-deterministic-checks-llm-orchestrates.md))
2. **The planner owns the numbers.** Thresholds come from the curated catalog
   (with a quoted source) or from the planner (with the source they state). The
   agent never supplies a threshold from memory; it asks.
3. **Real data only.** No synthetic layers. If a constraint needs data that isn't
   open (underground utilities), the tool says the constraint can't be evaluated.
4. **Decision support, not approval.** Variants are proposals. Nothing is adopted
   without a click.

## 3. The planner's journey (the website)

One page, map on the left, a stepper panel on the right.

| Step | Planner does | System does |
|---|---|---|
| **1. Area** | draws a polygon or line on the map (or picks a street by name) | loads the real layers inside the area; shows counts ("214 buildings, 3 schools, 56 trees") |
| **2. What** | chooses an object type: **points** (trees, bike racks, charging stations, benches) or **a line** (cable, power line, pipe, path); sets its basics: number wanted / spacing, or start and end point | — |
| **3. Constraints** | ticks constraints from the catalog **or** writes one ("keep bike racks within 50 m of tram stops, city guideline X"); marks each **hard** or **soft** | catalog: shows source + badge. Own text: agent drafts the structured rule, shows it back in plain words and as parameters; planner confirms or edits. **Live preview**: forbidden zones (red) and preferred zones (green) appear on the map as constraints are ticked |
| **4. Generate** | clicks *Generate* | generator produces 2–3 variants; checks run on each; variant cards show metrics and any soft-constraint trade-offs. If infeasible: "Hard constraint *X* leaves no space in the area. Relaxing it to *Y* gives *N* positions." |
| **5. Refine** | asks in chat ("fewer trees but further from the school"), drags a point, deletes one | re-checks in < 1 s without the LLM; the agent regenerates when the request changes constraints |
| **6. Export** | downloads GeoJSON, copies a share link, or prints a one-page report (constraints with sources, variant chosen, findings) | — |

The chat is available at every step but never required: a planner can do the
whole journey with clicks. The agent adds value at step 3 (own constraints),
step 4 (explaining infeasibility and trade-offs) and step 5 (natural-language
refinements).

## 4. How generation works

Constraints become geometry. That makes generation generic, fast and explainable.

**Constraint types** (the whole vocabulary for the MVP):

| Type | Meaning | As geometry | Hard use | Soft use |
|---|---|---|---|---|
| `min_distance(layer, d)` | stay ≥ d m from features of a layer | buffer(layer, d) = forbidden | subtract from allowed area | cost ↑ inside the buffer |
| `max_distance(layer, d)` | stay ≤ d m from a layer | buffer(layer, d) = required | intersect allowed area | cost ↑ outside the buffer |
| `not_within(layer)` | not on/inside features | layer polygons = forbidden | subtract | — |
| `within(layer)` | only on/inside features (e.g. sidewalks, parks) | layer polygons = required | intersect | cost ↑ outside |
| `min_spacing(d)` | generated objects ≥ d m apart | applied during placement | enforced | — |

**Point generator** `generate_points(area, constraints, target_count | spacing)`:
1. allowed = area − ⋃ hard forbidden ∩ ⋂ hard required (shapely).
2. candidates = regular grid (or stations along a line) at ≤ 1 m inside allowed.
3. greedy selection maximising a score (soft constraint costs) under `min_spacing`,
   up to the target.
4. variants: A = max count, B = best soft score, C = regular spacing (street trees).

**Line generator** `generate_line(start, end, area, constraints)`:
1. cost raster at 2 m over the area: hard forbidden = impassable, soft = weighted cost,
   base cost 1.
2. least-cost path (Dijkstra on the grid), then simplification to ≤ N vertices while
   staying in allowed cells.
3. variants: A = shortest feasible, B = soft weights ×3 (keeps further from preferred-
   avoid layers), C = fewest vertices.

**Verification**: every variant runs through `check_plan` with the same constraints.
The generator and the checker are separate code paths; a variant with a hard
violation is a bug and is dropped (and logged).

**Infeasibility**: if allowed is empty (points) or no path exists (line), compute
for each hard constraint the allowed area without it and report the one whose
removal frees the most space, plus the nearest threshold that makes it feasible
(binary search on d). This is deterministic and gives the agent something concrete
to explain.

## 5. Real data

All layers are pre-fetched for the study area, reprojected to EPSG:2056, and baked
into the deployment. Every feature carries `source` and `source_url`.

| Layer | Source | Licence | Status |
|---|---|---|---|
| buildings (footprints) | AV MOpublic Bodenbedeckung (cantonal WFS) | CC0 | confirmed in P1 |
| schools / kindergartens | Stadt Zürich, Schulanlagen | CC0 | confirmed |
| street trees | Stadt Zürich, Baumkataster | CC0 | confirmed |
| hydrants | Stadt Zürich, Hydranten | CC0 | confirmed |
| roads & sidewalks (areas) | same dataset, split by the `artzh` class | CC0 | confirmed in P1 |
| electrical installations > 36 kV (lines, cables, substations) | BFE, opendata.swiss | open | confirmed |
| public transport stops | Kanton Zürich / ZVV, `ogd-0140_giszhpub_zvv_haltestellen_p` | open | confirmed in P1 |
| parks & green spaces | part of AV Bodenbedeckung (`Parkanlage`, …) | CC0 | confirmed in P1 |
| zoning (BZO) | Stadt Zürich open data | CC0 | **cut**: no constraint in the MVP needs it |
| terrain | swisstopo swissALTI3D 2 m | swisstopo OGD | confirmed; **cut** unless a constraint needs it |

Not available and not faked: underground utilities (Leitungskataster is restricted),
sewer network, low/medium-voltage cables. Constraints that need them show
"cannot be evaluated: data not open".

**Study area (decided in P1)**: `zurich-kreis-5`, the 1 km² square
`2681800, 1247500 – 2682800, 1248500` (LV95) covering Escher-Wyss / Limmatplatz and
the northern edge of Kreis 4. Chosen by counting features in three candidates; it
has the most of what the catalog needs: 3 schools, 6 kindergartens, 1245 street
trees, 10 stops and 41 segments of >36 kV installation. Baked size: 2.8 MB, well
inside the 30 MB budget.

## 6. Curated catalog (starting set)

Shipped constraints with sources. Each one is `verified` only after the sentence
is quoted from the source; otherwise it shows a *draft* badge.

| Id | Constraint | Default | Source | Evaluable with open data |
|---|---|---|---|---|
| `lev-building-clearance` | power line ≥ 5 m horizontally from buildings | 5 m | LeV SR 734.31 Art. 38 / Anhang 8 | yes |
| `nisv-sensitive-use` | power line away from schools (proxy for the 1 µT limit at sensitive-use sites) | planner sets distance; no default | NISV SR 814.710 Anhang 1 — **distance is a proxy; real compliance needs a field calculation** | yes, as a warning |
| `tree-werkleitung` | tree trunk ≥ 2.00 m from utility lines | 2.00 m | Stadt Zürich Standards Stadträume, "Bäume und Baumscheiben" | **partly**: only against >36 kV cables and hydrants; the tool says so |
| `tree-fahrleitung` | tree ≥ 2.00 m from VBZ masts / overhead contact lines | 2.00 m | same page | only if a masts layer is open (verify) |
| `not-on-building` | object not inside a building footprint | — | geometric sanity | yes |
| `on-public-ground` | object within road/sidewalk/park areas | — | planning convention, no legal source (badge: convention) | yes |

Everything else comes from planners (§3 step 3) and is labelled
*user-defined — source as stated by the planner*.

## 7. Architecture

```
Browser (static site on Vercel)
  MapLibre map · stepper panel · chat · variant cards
        │  HTTPS (JSON)
        ▼
API (FastAPI on Render, one container)
  /api/layers  /api/catalog  /api/constraints/draft  /api/generate
  /api/check   /api/chat     /api/export            /mcp
        │
        ├─ agent/      Claude tool loop: translate request → tools → explain
        │               (never emits geometry; returns variant ids from tool results)
        ├─ tools/      one JSON-safe function surface, shared by agent, REST, MCP
        │               list_layers · get_layer · list_catalog · draft_constraint
        │               preview_zones · generate_points · generate_line
        │               check_plan · explain_constraint · explain_infeasibility
        ├─ generate/   point + line generators (shapely, numpy)
        ├─ checks/     pure constraint checks, registry by type
        ├─ catalog/    curated constraints (YAML, cited)
        ├─ data/       real layers, EPSG:2056, loaded at startup
        └─ domain/     pydantic models
```

Contracts:
- **Constraint**: `{id, type, layer, params, hard, source: {text, url?, kind: curated|user|convention}, verified}`.
- **PlanRequest**: `{area, object: {kind, geometry: point|line, count?, spacing?, start?, end?}, constraints: [Constraint]}`.
- **Variant**: `{id, label, features, metrics, findings, tradeoffs}`; features only from generators.
- **Finding**: `{constraint_id, severity, feature_id, measured, required, message, source, geometry}`.

Agent guardrails:
- `draft_constraint` output is a *proposal*; the API only stores a constraint the
  planner confirmed in the UI.
- The agent's final answer references variant ids; the server resolves them. An
  unknown id is rejected.
- If the planner asks for a threshold ("how far should trees be from houses?"), the
  agent answers from the catalog or says there is no curated rule and asks for
  their source.

Platform surface (kept small on purpose):
- REST with OpenAPI at `/docs` — the website uses only this.
- MCP at `/mcp` exposing the same tools — any agent can generate and check plans.
- Adding a curated constraint = one YAML file; adding a constraint type or layer =
  one registered function / one loader, each with a test.

Deploy: Vercel serves `web/` (static, no build). Render builds the Dockerfile from
GitHub; data files are in the image. API key only on Render. CORS for the Vercel
origin. No accounts, no sessions on the server: the plan state lives in the browser
(URL-encoded for share links).

## 8. Build plan

About 14 hours of work. Ordered by risk; each phase ends deployed.

| # | Phase | Hours | Done when |
|---|---|---|---|
| P0 | Skeleton: repo layout, FastAPI health, static page, Render + Vercel deploys, CORS | 1 | the Vercel page shows the API health from Render |
| P1 | Data: fetch scripts for all "confirmed" layers, verify the three "verify" layers, pick the study area, reproject, bake | 3 | map shows every layer in the area with correct positions |
| P2 | Constraints: models, 5 check types, catalog YAML with sources, `preview_zones` | 2 | ticking a constraint draws its forbidden/required zone; checks have unit tests |
| P3 | Generators: points, line, variants, infeasibility report | 3 | both generators return 2–3 variants with zero hard findings on the study area; infeasible case explained |
| P4 | Agent: tool loop, `draft_constraint` + confirm flow, variant-id guardrail, refine-by-chat | 2 | "bike racks within 50 m of tram stops, not on sidewalks narrower than…" becomes a confirmed constraint and a generated plan |
| P5 | Website: stepper, variant cards, drag-edit + re-check, export GeoJSON + one-page report | 2.5 | someone who hasn't seen it completes the tutorial unaided |
| P6 | MCP endpoint, README (use the site / use the API / use MCP / add a constraint) | 0.5 | an MCP client lists and runs `generate_points` against the deployed URL |

Cut order if behind: one-page report → line generator variants (keep one) →
refine-by-chat (keep chat for drafting constraints) → MCP (keep REST).

Then the video (≈ 2 h, §10).

## 9. Decision log

| Date | Decision | Why |
|---|---|---|
| 09-15 | Architecture: LLM orchestrates, code decides | Verdicts must be reproducible and defensible. ADR 0001 |
| 09-15 | Hosting: Vercel (site) + Render (API) | chosen by me; site stays a static page anyone can fork |
| 09-16 | **Dropped** three fixed domains (sewer, power line, trees) with domain-specific algorithms | Too big for one night; three half-working features lose on "works end to end" |
| 09-16 | **Real data only**; synthetic utilities removed | A planning tool on invented data isn't credible. Sewer is out entirely (network not open) |
| 09-16 | **Generic, multi-discipline**: object types + constraints as data | Lets planners from any field use it; turns goNEON's "data + guidelines + regulations" into the product; the ecosystem is the constraint catalog |
| 09-16 | **Generate, not only check** | Planners asked for plans on top of chosen constraints; generators are generic because constraints become geometry |
| 09-16 | Thresholds come from the catalog or the planner, never the model | Keeps ADR 0001 true for user-defined constraints |
| 09-16 | No server sessions; state in the browser | Removes persistence, auth and scaling work; share links still work |
| 09-16 | One dataset (AV Bodenbedeckung) split into five layers by its `artzh` class | Buildings, pavements, roads, parks and water come from one fetch; a layer is a filter, not a new integration |
| 09-16 | Layers baked into the repo and the image, fetched by a script, not at request time | 100 planners must not hit a public WFS at once; a fetch script keeps the provenance reproducible |
| 09-16 | Not modelled, and said so: magnetic fields (NISV), hydraulics, underground conflicts | Need calculations or data that aren't possible overnight; shown as "proxy" or "cannot be evaluated" |

## 10. Submission

**Within 24 h**: the timeline message (draft in [plan/04-submission.md](plan/04-submission.md)).

**Video, three parts**:
1. **Lukas (engineering)**: constraints → geometry → generators → independent checks;
   the guardrail; one tool surface on REST and MCP; what's deliberately not modelled.
2. **Raphael (planner tutorial)**: two planners, same tool. A landscape planner places
   street trees with the Stadt Zürich rule and sees where the data can't evaluate it;
   an energy planner routes a line with the LeV clearance and a school buffer, hits
   infeasibility, relaxes a soft constraint, compares variants.
3. **Raphael (vision)**: the constraint catalog as the shared asset; goNEON's mobility
   work (parking, bike corridors, traffic calming) as the next catalogs; what a team
   adds next: utility data access, engineering check types (hydraulics, field
   calculation), reviewed rule workflow.

## 11. Open questions

- ~~Which quarter~~ → `zurich-kreis-5`, see §5.
- ~~Are tram stops and parks open geodata?~~ → yes, both baked. **VBZ masts and
  overhead contact lines are not**, so `tree-fahrleitung` cannot be evaluated and
  the tool says so instead of passing it.
- Model for the hackathon: `claude-opus-5` vs a cheaper model, depending on the
  organisers' budget; per-IP rate limit on chat only.
