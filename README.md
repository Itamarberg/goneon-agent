# neon-agent

A planning website for planners from any field. Pick an area of Zurich, say what
you want to place (trees, bike racks, a cable, a power line), and choose the
constraints it must respect, from a catalog of cited rules or in your own words.
Deterministic generators produce plan variants on real open data, every variant
is checked against your constraints, and you compare, edit and decide.

It is decision support. It never approves a plan.

**Status:** P0–P6 complete. See [docs/PLAN.md](docs/PLAN.md) §8 for the phases.

---

## The one idea

**The LLM orchestrates; code decides.**

- Every coordinate comes from a generator. Every verdict comes from a check.
  Both are shapely, both are pure, both are tested.
- Every threshold comes from the cited catalog or from the planner. The agent is
  structurally unable to supply one: the tool that builds a constraint returns an
  error telling it to ask.
- Every rule is a YAML file with a source. Adding one is a file, not a code change.
- What cannot be checked says so. The Leitungskataster is not open data, so a
  constraint against underground utilities comes back `not_evaluable` rather than
  quietly passing.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/adr/0001](docs/adr/0001-deterministic-checks-llm-orchestrates.md).

## Four ways in

### 1. The website

Pick an area, choose what to place, tick constraints (the map shows what they
leave), generate, compare variants, export GeoJSON or a one-page report.
The chat is available at every step and required at none.

### 2. The REST API

OpenAPI at `/docs`. The website uses only these.

| Endpoint | Does |
|---|---|
| `GET /api/area` | The study area, its layers and what it cannot evaluate |
| `GET /api/layers/{name}` | One layer as GeoJSON (WGS84) |
| `GET /api/catalog` | Curated constraints with sources and `evaluable` |
| `POST /api/zones` | Forbidden / required / allowed areas for a constraint set |
| `POST /api/generate` | Plan variants, or an explanation of why there are none |
| `POST /api/check` | Verify any plan against any constraints |
| `POST /api/chat` | Ask the agent |

```sh
curl -X POST $API/api/generate -H 'content-type: application/json' -d '{
  "object": {"kind": "tree", "geometry": "point", "count": 12},
  "constraints": ["not-on-building", "tree-spacing", "tree-hydrant-access"]
}'
```

### 3. MCP — bring your own agent

The same tools, at `/mcp`. Point Claude Desktop, your own agent, or any MCP
client at it and generate and check plans without our UI.

```json
{ "mcpServers": { "neon-agent": { "url": "https://<your-deployment>/mcp/" } } }
```

Locally over stdio:

```sh
uv run --extra mcp python -m mcp_server.server
```

Eleven tools: `list_layers`, `describe_area`, `get_layer`, `list_catalog`,
`propose_constraint`, `preview_zones`, `generate_points`, `generate_line`,
`check_plan`, `explain_constraint`, `explain_infeasibility`. They are registered
from the same dict the in-app agent uses, so the two surfaces cannot drift.

### 4. The Python functions

`tools/core.py` is the surface all three of the above share. Plain functions,
JSON in, JSON out, no model anywhere in them.

## Extending it

| To add | Do | Cost |
|---|---|---|
| A constraint | Drop a YAML file in `catalog/constraints/` | 1 file; `pytest` validates it |
| A constraint type | Register `zone()` and `evaluate()` in `checks/` | 1 function pair + a test; generators, checker, preview and agent all pick it up |
| A data layer | Add a `LayerSource` record in `data/sources.py`, re-run the fetch script | 1 record |
| A study area | Change `data/study_area.py`, re-run the fetch script | 1 record |

A constraint file:

```yaml
id: tree-hydrant-access
title: Tree keeps clear of hydrants
type: min_distance          # min_distance | max_distance | within | not_within | min_spacing
applies_to: tree
layer: hydrant
params: {d_m: 2.0}
hard: true
source:
  text: "Operational convention: the fire brigade needs unobstructed access."
  kind: convention          # curated (a regulation) | convention | user
verified: false             # true only once the source sentence is quoted
```

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync                                   # geometry only — no API key needed
uv sync --extra agent --extra mcp         # plus the agent and MCP
uv run --group dev pytest                 # 98 tests
uv run uvicorn api.main:app --reload      # API on http://127.0.0.1:8000
python3 -m http.server -d web 5173        # site on http://127.0.0.1:5173
```

The site's backend URL is the one knob in `web/config.js`.

The chat needs `ANTHROPIC_API_KEY`. Everything else — map, constraints,
generation, checks, export, MCP — works without one, and `/api/chat/status`
says which you have.

The layers in `data/layers/` are committed. Re-fetch them only if the study area
changes:

```sh
uv run python scripts/fetch_layers.py
```

## Deployment

`web/` is static on Vercel; the API is one Dockerfile on Render with the data
baked into the image, so nothing calls a third-party WFS at request time.

| Variable | For |
|---|---|
| `ALLOWED_ORIGINS` | The site's origin, for CORS |
| `ANTHROPIC_API_KEY` | Chat only; never reaches the browser |
| `MCP_ALLOWED_HOSTS` | Hostnames MCP clients may use; `*` disables the check |
| `NEON_MODEL` | Defaults to `claude-opus-5` |

## Data

Eleven layers over one 1 km² quarter of Zürich Kreis 5, all real, all attributed:
buildings, pavements, roads, parks, water (cantonal AV Bodenbedeckung), street
trees, schools, kindergartens, hydrants (Stadt Zürich), transit stops (ZVV) and
electrical installations above 36 kV (BFE).

Not available and not invented: the Leitungskataster, the sewer network,
low- and medium-voltage cables, and VBZ masts. Constraints that need them are
reported as unchecked. `tree-fahrleitung` ships in the catalog for exactly that
reason.

## What is deliberately not modelled

Magnetic field calculations (NISV compliance needs a real field model, so the
catalog entry is labelled a proxy), hydraulics, and underground conflict
detection. Each needs either data that is not public or a dedicated check type.
The registry is where those would go.
