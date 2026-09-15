# Planning Agent — Plan & Architecture

Take-home for goNEON, Platform & Ecosystem Owner. Scenario: an overnight MVP for
agentic infrastructure planning that ~100 young planners can use at a hackathon.

Detailed plans live in `docs/plan/`:

| Doc | Covers |
|---|---|
| [`plan/01-engineering.md`](plan/01-engineering.md) | module-by-module gap list, contracts, tests, deploy pipeline, phases with acceptance criteria |
| [`plan/02-domain.md`](plan/02-domain.md) | data layers with real endpoints, the rule catalog with sources, hydraulics, proposal algorithms |
| [`plan/03-product.md`](plan/03-product.md) | personas, user journey, the three challenges, contribution ladder, organiser needs |
| [`plan/04-submission.md`](plan/04-submission.md) | the 24 h message, video scripts, pre-send checklist, presentation prep |

This document is the summary and the decision log. Every scope cut is written down with its
reason, because "you cut the scope yourself and say why" is a grading criterion.

---

## 1. What we are building, in one sentence

A map-based planning assistant for a Zurich study area where an LLM agent proposes
sewer runs, power-line alignments and tree placements, **deterministic tools check
them against cited rules**, and the planner picks between options.

Three principles, in priority order:

1. **The LLM plans, the tools compute.** No geometry, gradient or clearance number
   ever comes from the model. Every check is a pure function with a testable result.
2. **Rules are data, and every rule cites its source.** A violation always reads
   "distance 1.8 m < 2.5 m minimum — Rule T-01, source: …". This is goNEON's thesis
   (spatial data + guidelines + regulations) and it is what makes the engineering
   explainable.
3. **Decision support, not autopilot.** The agent returns options with trade-offs and
   flags rule conflicts. The planner decides. Nothing is auto-applied.

---

## 2. Scope decisions

| # | Decision | Why |
|---|---|---|
| S1 | One fixed study area (a Zurich neighbourhood, ~1 km²) | Pre-loading data for one area removes the biggest overnight risk (data plumbing). Planners work on the same map, so their results are comparable at the hackathon. |
| S2 | Underground utilities are a **synthetic layer** | The Leitungskataster is not public. We generate a plausible network (water, gas, electricity, telecom, existing sewer) along the road graph and label it clearly as synthetic. |
| S3 | Sewer hydraulics: gravity slope, cover depth, Manning capacity per segment. **No network simulation.** | Segment checks are what a young planner can reason about and what a checker can explain. A full network model is a week, not a night. |
| S4 | Power line: least-cost path over a raster with building clearance buffers. **No electromagnetic field calculation** beyond a distance-based NISV proxy. | Distance to sensitive-use buildings is the rule that bites in an urban context; it is checkable with geometry. |
| S5 | Trees: setback from utilities and buildings, species-agnostic crown radius. **No root-zone modelling.** | Setback is the rule the hackathon problem statement names. |
| S6 | Rule figures are placeholders until verified against the original source. Each rule has a `verified: false` flag until checked. | Never ship a number from memory as if it were the regulation. |
| S7 | No auth. Projects are shareable by URL (opaque id). | Hackathon: zero friction. Abuse surface is acceptable for a one-day event. |
| S8 | No CAD/DXF import or export. GeoJSON in, GeoJSON out. | GeoJSON is what every hackathon team can produce and read. |
| S9 | English UI first; German strings behind an i18n table if time allows. | Brief is in English; the planner audience is Swiss. Cheap to add later, expensive to do half-way now. |
| S10 | Anthropic Claude via tool use for the agent | Best tool-use reliability at this date; the agent is thin by design so the model is swappable. |

---

## 3. Architecture

See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — that file is the source of truth.
Summary: one FastAPI container serving a static MapLibre page; a thin Claude
tool-runner agent; `tools/core.py` as the single function surface, exposed both to
the agent and over MCP; pure `checks/` registered by type; a YAML rule catalog
with citations; pre-fetched GeoJSON fixtures in EPSG:2056.

The subsections below are kept only where they add detail beyond that file.

<details>
<summary>Earlier draft of this section (superseded, kept for the decision trail)</summary>

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser — Next.js + MapLibre                                     │
│  map (study area, layers) · sketch tools · chat panel             │
│  option cards (A/B/C with trade-offs) · violation overlay         │
└───────────────┬──────────────────────────────────────────────────┘
                │ HTTPS / SSE
┌───────────────▼──────────────────────────────────────────────────┐
│  API — FastAPI (Python)                                           │
│                                                                   │
│  /agent/run  ──▶  Agent loop (Claude tool use)                    │
│                     │  may only call registered tools             │
│                     ▼                                             │
│  /tools/*    ──▶  Tool registry  ◀── same functions, callable     │
│                     │                 directly over HTTP          │
│                     ▼                                             │
│               Engineering tools (Shapely / NetworkX / rasterio)   │
│               check_sewer · route_power_line · place_trees        │
│               query_features · explain_rule                       │
│                     │                                             │
│                     ▼                                             │
│               Rule engine  ◀── rule packs (JSON, versioned, cited)│
│                     │                                             │
│                     ▼                                             │
│               Data store (GeoPackage / PostGIS): terrain,         │
│               buildings, roads, trees, synthetic utilities,       │
│               projects & scenarios                                │
└──────────────────────────────────────────────────────────────────┘
        │
        └── /mcp  — MCP server exposing the same tool registry,
                    so any agent (Claude Code, Cursor, a team's own
                    bot) can plan against the same rules and data
```

### 3.1 Components

**Frontend** — Next.js, MapLibre GL, a single page per project.
- Layers: terrain hillshade, buildings, roads, existing trees, utilities (synthetic,
  labelled), user features.
- Sketch: draw a polyline (sewer, power line) or point set (trees), or drop a start/end
  and let the agent propose.
- Chat: natural-language requests to the agent; streams the reasoning trace.
- Option cards: each proposal shows metrics (length, cost proxy, min clearance,
  violations) side by side; "Adopt" copies it into the project. Nothing is adopted
  automatically.

**Agent** — one loop, ~200 lines.
- System prompt: role, the three principles, the tool list, the rule pack summary.
- Allowed actions: call tools, ask the planner a clarifying question, return options.
- Forbidden: emit coordinates that did not come from a tool; state a rule value that
  did not come from `explain_rule`.
- Every tool call and result is recorded as a trace step and shown in the UI. That
  trace is the "explain what it does and how".

**Tool registry** — a decorator-based registry (`@tool`) producing, from one function
definition: the Claude tool schema, the FastAPI route, and the MCP tool. One source
of truth, three surfaces. This is the core of the "others can build on it" story.

**Rule engine** — loads rule packs, evaluates a feature against applicable rules,
returns structured violations. Rules are data; the engine is generic.

**Data store** — GeoPackage for the MVP (single file, no server); PostGIS is a drop-in
later. Projects and scenarios are JSON documents.

### 3.2 Rule pack format

```json
{
  "pack": "zh-trees",
  "version": "0.1.0",
  "jurisdiction": "CH-ZH",
  "rules": [
    {
      "id": "T-01",
      "title": "Minimum distance tree to underground utility",
      "applies_to": "tree",
      "against": "utility",
      "check": "min_distance",
      "params": { "meters": 2.5 },
      "severity": "error",
      "source": { "doc": "TODO: cite", "clause": "TODO", "url": null },
      "verified": false,
      "rationale": "Root intrusion into pipe joints; excavation access."
    }
  ]
}
```

Check types in v0.1: `min_distance`, `max_distance`, `min_slope`, `max_slope`,
`min_cover`, `capacity_manning`, `not_within`. Adding a check type is one Python
function; adding a rule is one JSON object. A hackathon team can ship a new rule pack
without touching Python.

Initial packs (figures to be verified before the event, see S6):

| Pack | Rules (draft) | Source to verify against |
|---|---|---|
| `zh-sewer` | min slope per diameter, min cover depth, Manning capacity vs design flow, min distance to water mains | VSA guidelines, SN 592000 |
| `zh-power` | clearance to buildings, NISV distance proxy for sensitive-use sites (schools, housing), corridor width | NISV, LeV, ESTI guidance |
| `zh-trees` | setback from utilities, setback from building façades, crown clearance from lines, spacing | Stadt Zürich Grün Stadt Zürich tree standards, VSS |

### 3.3 Tools

All tools are pure: (features, context) → result. No tool calls the LLM.

| Tool | Input | Output |
|---|---|---|
| `query_features(layer, bbox\|geometry, buffer_m)` | layer name, area | GeoJSON features — how the agent "sees" the map |
| `check_sewer(polyline, diameter_mm, design_flow_lps)` | alignment with manhole nodes | per-segment slope, cover depth (from terrain), capacity, violations |
| `route_power_line(start, end, voltage_class)` | two points | least-cost path avoiding building buffers; clearance profile; violations; 1–3 alternatives |
| `place_trees(polygon\|line, count\|spacing_m, species_class)` | planting area | candidate points that pass setbacks; rejected candidates with reasons |
| `check(feature, packs)` | any feature | generic rule evaluation — the other tools call this internally |
| `explain_rule(rule_id)` | id | title, params, source, rationale — the only way the agent may quote a rule |
| `compare(options)` | list of proposals | normalised metrics table for the option cards |

### 3.4 Agent loop (pseudocode)

```
messages = [system, planner_request, project_context_summary]
for step in range(MAX_STEPS):
    reply = claude(messages, tools=registry.schemas())
    if reply.has_tool_calls:
        for call in reply.tool_calls:
            result = registry.run(call)          # deterministic, logged
            trace.append(call, result)
            messages.append(tool_result(result))
        continue
    return parse_options(reply), trace           # 1–3 options, each with violations
```

Guardrail: the final answer is validated — every geometry in an option must match a
geometry that appeared in a tool result. If not, the answer is rejected and the agent
is asked to redo it. This is cheap and it closes the "LLM invented coordinates" hole.

### 3.5 Extension surface (the ecosystem part)

| Surface | Who uses it | What they can do |
|---|---|---|
| Web UI | planners | plan, compare, adopt |
| HTTP API (`/tools/*`, OpenAPI) | hackathon teams | call checkers from their own scripts / notebooks |
| MCP server (`/mcp`) | anyone with an agent | plug the same tools into Claude Code, Cursor, their own bot |
| Rule packs (`rules/*.json`) | domain people, cities | add or override rules without code; PR-able |
| `@tool` decorator | engineers | add a new checker, automatically exposed on all three surfaces |
| Scenario files (`scenarios/*.json`) | organisers | define a challenge: area, task, packs, starting features |

The hackathon then has a natural ladder: use the UI → script against the API → write
a rule pack → contribute a tool.

</details>

### Deltas between the draft and the skeleton now in `src/`

| Draft said | Skeleton does | Keep |
|---|---|---|
| Next.js frontend on Vercel + FastAPI on Fly | static MapLibre page served by FastAPI, one container | skeleton — no build step, one deploy |
| JSON rule packs | one YAML file per rule in `rules/catalog/` | skeleton — reviewable per-rule diffs |
| `@tool` decorator generating agent + HTTP + MCP | plain typed functions in `tools/core.py`, wrapped by tool runner and MCP | skeleton — simpler, same effect |
| domain-specific tools (`check_sewer`, `route_power_line`, `place_trees`) | one generic `check_plan` over the rule catalog | skeleton for checking; **routing/placement proposals still need their own tools** (see §3.3) — add as `propose_*` in `tools/` once `check_plan` works |
| geometry guardrail on the final answer | not yet | add — cheap, and it is the Part 1 demo moment |
| GeoPackage | GeoJSON fixtures | skeleton |

---

## 4. Data

Study area: to be chosen — a Zurich quarter with terrain variation (sewer gradients
need it), mixed housing, a school (sensitive-use for the power rule), and street trees.
Candidates: Wipkingen, Unterstrass, Altstetten. Decide by looking at the DEM.

| Layer | Source | Notes |
|---|---|---|
| Terrain | swisstopo swissALTI3D (2 m or 0.5 m) | clipped to area; drives cover depth and slope |
| Buildings | swisstopo swissBUILDINGS3D or Stadt Zürich open data | footprints + height; tag schools/hospitals/housing as sensitive-use |
| Roads | Stadt Zürich open data / OSM | road graph for utility generation and power-line corridors |
| Existing trees | Stadt Zürich Baumkataster (open data) | real data — good for the tutorial |
| Utilities | **synthetic** | generated along roads: water, gas, electricity, telecom, existing sewer with invert levels; clearly labelled |

Coordinates: LV95 (EPSG:2056) internally; WGS84 only at the browser edge.

---

## 5. Hackathon scenarios (shipped as scenario files)

1. **Sewer** — connect a new housing block to the existing main; must drain by gravity,
   respect cover depth, and not cross the water main closer than allowed.
2. **Power line** — bring a medium-voltage line from a substation point to a new
   development while keeping distance from the school and housing.
3. **Trees** — plant a street with N trees without hitting the synthetic utilities and
   without shading the school windows more than the rule allows (stretch).

Each scenario has a "starting state" and a set of metrics the organiser can compare
across teams.

---

## 6. Build plan

Total budget ≈ one long day + one night. Order is by risk: data and deployment first,
polish last. Every phase ends with something deployable.

| Phase | Hours | Deliverable | Done when |
|---|---|---|---|
| 0. Setup | 1 | uv + Python 3.12 locally, `pytest` green on the skeleton, Dockerfile, GitHub repo, first deploy of the container (Render or Fly) | the public URL answers `/health` |
| 1. Data | 3 | study area chosen; terrain, buildings, roads, trees loaded into a GeoPackage; synthetic utilities generated | map shows all layers |
| 2. Rule engine + registry | 2 | rule pack loader, `check()`, `@tool` decorator → OpenAPI | unit tests for each check type pass |
| 3. Tools | 4 | `check_sewer`, `route_power_line`, `place_trees`, `query_features` | each tool has a fixture test with a known violation |
| 4. Agent | 2 | loop, trace, geometry guardrail, streaming | scenario 1 solved end to end via chat |
| 5. UI | 3 | option cards, violation overlay, sketch tools, scenario loader | a planner can do scenario 1 without reading code |
| 6. MCP + docs | 1.5 | `/mcp` endpoint, README with "3 lines to start", API examples | tool works from Claude Code |
| 7. Scenarios 2–3, polish | 2 | remaining scenarios, German strings if time | all three scenarios runnable |
| 8. Video | 2 | three-part recording from a script | sent |

Cut order if time runs out: phase 7 stretch → scenario 3 → German strings → sketch
tools (agent-only input) → MCP (keep HTTP API).

---

## 7. Video plan

| Part | For | Content (2–3 min) |
|---|---|---|
| 1 | Lukas | Architecture in 60 s; live: one chat request, show the trace, open a rule pack, show the guardrail rejecting an invented geometry; the `@tool` decorator producing three surfaces |
| 2 | Raphael | Screen-recorded tutorial: load scenario 1, ask for options, read the violation, adopt an option, tweak it by hand, re-check. No jargon. |
| 3 | Raphael | Same engine, new rule pack = new domain (link to goNEON's mobility work); the contribution ladder for cities and firms; what a team of 3 builds in the next 90 days (verified rule packs, real Leitungskataster ingestion, PostGIS, review workflow) |

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Data download / CRS issues eat the night | Phase 1 first; fall back to OSM buildings and a coarser DEM |
| Agent produces plausible nonsense | Tools are the only source of numbers; geometry guardrail; trace is always visible |
| Rule figures wrong | `verified: false` flag shown in UI as "draft rule"; verify the three most visible ones before the video |
| Hosting cost / cold starts during demo | Fly machine kept warm; static data baked into the image |
| Demo only works for me | Scenario files + README tested by someone else before sending |

---

## 9. Open decisions (need input)

- Study area: pick from the DEM (Wipkingen / Unterstrass / Altstetten). `config.py`
  currently defaults to a 2×2 km bbox near Zurich HB; revisit once terrain is loaded.
- Hosting (decided 2026-09-15): **Vercel for the frontend, Render for the API
  container** (Docker built from GitHub; Fly.io is the fallback if Render's cold
  starts hurt the demo). Consequence: the frontend is its own app under `web/`,
  the API needs CORS for the Vercel origin, and the API base URL is a build-time
  env var in the frontend. `ARCHITECTURE.md` still says "static page served by
  FastAPI" — update it when `web/` is created.
- Local toolchain (decided): `uv` + Python 3.12 installed user-locally. Node is
  still missing; needed once `web/` becomes a Next.js app (install via `uv`-like
  route: `fnm` or the official pkg — decide when Phase 5 starts).
- No GitHub remote and no commits yet; the deploy path needs a remote.
- Anthropic API key: read from `NEON_ANTHROPIC_API_KEY` on the host; add a
  bring-your-own-key field in the UI as fallback. (Default, no decision needed.)
- Language of the video: English. (Default.)
