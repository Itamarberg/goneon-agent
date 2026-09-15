# Engineering plan

Companion to [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) (the design) and
[`../PLAN.md`](../PLAN.md) (scope and schedule). This document says, module by
module, what exists, what is missing, what the contracts are, and how each phase is
verified. Nothing here is implemented yet beyond the "exists" column.

## 0. Where the code stands (2026-09-15)

> **Update:** the code scaffold described in this section was deleted on
> 2026-09-15 to start clean from the plan. The table is kept as the target
> module list: "Exists" = what the first implementation should contain, "Missing"
> = what follows. The defects listed below are things to avoid when rewriting.

| Module | Exists | Missing |
|---|---|---|
| `domain/models.py` | `Rule`, `Feature`, `Finding`, `Citation`, `Severity`, `AppliesTo` | `Plan`, `Proposal`, `TraceStep`, `Session` |
| `checks/` | registry, `min_distance`, `min_gradient` | `min_cover`, `capacity_manning`, `max_slope`, `not_within`, `min_spacing`, `clearance_profile` |
| `rules/` | loader, 3 placeholder rules (all `verified: false`) | the real catalog (§ domain plan), `jurisdiction`, `version` fields |
| `data/` | `DataSource` protocol, `FixtureSource` (GeoJSON, bbox filter) | fixtures themselves, terrain sampling (`ElevationSource`), synthetic utility generator |
| `tools/core.py` | `list_layers`, `get_layer`, `list_rules`, `check_plan`, `explain_rule` | `propose_sewer`, `propose_power_line`, `propose_trees`, `compare_proposals`, `sample_elevation` |
| `agent/` | system prompt, tool-runner loop (non-streaming, no trace) | trace capture, streaming, geometry guardrail, per-session context, rate limit |
| `api/app.py` | health, rules, layers, check, chat; serves `web/` | CORS, SSE streaming, sessions, proposals endpoint, scenarios endpoint |
| `mcp_server/` | FastMCP over `TOOLS` (stdio) | HTTP transport for remote use; instructions text |
| `web/` | placeholder | the whole UI |
| `scripts/fetch_layers.py` | docstring | everything |
| `tests/` | catalog test, 2 check tests | tool tests, API tests, agent guardrail test, fixture-based scenario tests |
| Deploy | Dockerfile (pip, not uv; installs editable) | Render config, Vercel config, CI |

Known defects in the skeleton to fix in Phase 1:
- `min_distance` builds `Finding.geometry` from `a.union(b).envelope` — for two
  points that is a degenerate polygon. Use a `LineString([a, b])` (the conflict
  segment) instead. Tests pass only because they don't inspect geometry.
- `check_plan` loads context for `settings.default_bbox`, not for the plan's own
  extent. Fine for one study area; document it, or compute the plan bbox + buffer.
- `agent/runner.py` creates `anthropic.Anthropic()` at import time → import fails
  without a key. The API already imports lazily; keep that and make the client lazy
  too, so `pytest` never needs a key.
- Dockerfile `pip install -e .` copies `pyproject.toml` only; without `uv.lock` the
  image can drift from the local env. Switch to `uv` in the image (below).

## 1. Target module specs

### 1.1 `domain/` additions

```python
class TraceStep(BaseModel):
    step: int
    tool: str
    args: dict
    result_summary: str          # first ~200 chars, for the UI
    result_ref: str              # id into the session's result store
    duration_ms: int

class Proposal(BaseModel):
    id: str
    label: str                   # "A", "B", "C"
    features: list[Feature]      # geometry MUST originate from a tool result
    metrics: dict[str, float]    # length_m, min_clearance_m, n_errors, n_warnings, cost_index
    findings: list[Finding]
    rationale: str               # LLM-written, cites rule_ids only
    source_steps: list[int]      # trace steps whose results contain these geometries

class Session(BaseModel):
    id: str
    scenario_id: str | None
    plan: list[Feature]          # what the planner has adopted / drawn
    messages: list[dict]         # Anthropic message history
    trace: list[TraceStep]
    proposals: list[Proposal]
    created_at: datetime
```

Sessions live in an in-memory dict with a TTL (scope decision S7). A `SessionStore`
protocol lets a Redis/SQLite version replace it later.

### 1.2 `checks/` additions

All checks keep the signature `(rule, features) -> list[Finding]`. They read only
`feature.geometry` and `feature.properties`. Terrain is passed in as properties
(`start_elevation_m`, `cover_depth_m`, …) — checks never call a data source.
That keeps them pure and trivially testable.

| Check type | Rule params | Reads properties | Finding when |
|---|---|---|---|
| `min_distance` (exists) | `min_m` | — | subject–object distance < min |
| `min_spacing` | `min_m` | — | two subjects of the same kind closer than min (trees to trees) |
| `not_within` | — | — | subject intersects object (tree on a building footprint) |
| `min_gradient` (exists) | `min_percent` | `start_elevation_m`, `end_elevation_m` | gradient < min, per feature |
| `min_gradient_by_diameter` | `table: {dn: min_percent}` | + `diameter_mm` | as above, threshold from table |
| `max_gradient` | `max_percent` | same | too steep (scour) |
| `min_cover` | `min_m` | `cover_depth_m` (sampled along line by a tool) | any sample < min |
| `capacity_manning` | `n`, `fill_ratio` | `diameter_mm`, gradient, `design_flow_lps` | Q_full × fill < design flow |
| `clearance_profile` | `min_m`, `object_filter` | `object_filter` selects buildings by `use` | horizontal clearance at any vertex < min; distinguishes sensitive-use buildings |

`capacity_manning` is the only formula with real engineering content. Q = (1/n)·A·R^(2/3)·S^(1/2), circular full-pipe. The tool that prepares the feature computes S from elevations; the check only applies the formula. Unit test against a hand-computed value.

### 1.3 `data/` additions

```python
class ElevationSource(Protocol):
    def sample(self, points: list[tuple[float, float]]) -> list[float]: ...   # metres, EPSG:2056

class RasterElevation(ElevationSource):   # reads a clipped GeoTIFF with rasterio
class TableElevation(ElevationSource):    # tests: dict lookup / plane function
```

`FixtureSource` gains `get_layer(name, geometry=…)` (any geometry, not only bbox) so
proposal tools can query around a corridor. Utilities are a fixture like any other
layer, generated once by `scripts/make_synthetic_utilities.py` (domain plan §3).

Adds `rasterio` to dependencies. It has manylinux and macOS x86_64 wheels; check the
image builds before relying on it. Fallback: pre-sample the DEM onto a 2 m grid and
store it as a NumPy `.npy` + affine transform, read with NumPy only.

### 1.4 `tools/` additions

Proposal tools are where the "agentic" part gets real. They generate candidates
deterministically; the agent chooses which to call and how to describe the result.
Every proposal tool returns `{candidates: [ {features, metrics, findings} ], notes}`
so `check_plan` semantics stay identical.

| Tool | Method | Inputs | Notes |
|---|---|---|---|
| `sample_elevation(coords)` | DEM lookup | list of [x,y] | lets the agent (and UI) attach elevations to a sketched line |
| `propose_sewer(start, end, diameter_mm, design_flow_lps, n_alternatives=2)` | shortest path along the road graph between the two points, then invert-level assignment from terrain (min cover + min gradient), manhole nodes every ≤ N m | two points | returns alignment + manholes + per-segment gradient/cover/capacity + findings. Alternatives: different road-graph routes (k-shortest) |
| `propose_power_line(start, end, voltage_class, n_alternatives=2)` | least-cost path over a 2 m cost raster: buildings = impassable, buffer zone = high cost, sensitive-use buffer = higher, roads = low | two points | returns polyline + clearance profile + findings. Alternatives: vary buffer weights |
| `propose_trees(area_or_line, spacing_m or count, crown_radius_m)` | candidate grid along the line/area; reject any candidate violating `not_within`/`min_distance`/`min_spacing`; keep the rest | polygon or line | returns accepted points + rejected points with the reason each failed — this is the tutorial's best moment |
| `compare_proposals(proposal_ids)` | table | ids from the session | normalised metrics for option cards |

Graph and raster are built once per study area at startup (cached), from the road
and building fixtures. NetworkX for k-shortest paths; `scikit-image`'s `route_through_array`
or a small Dijkstra over the cost grid for the power line. Add `networkx`, `numpy`.

### 1.5 `agent/` changes

1. **Trace.** Wrap each tool function so the runner records `TraceStep`s; store full
   results in the session by `result_ref`. The UI gets the trace as it happens.
2. **Streaming.** `/api/chat` becomes SSE: events `text`, `tool_call`, `tool_result`,
   `proposal`, `done`. Client shows the trace live.
3. **Geometry guardrail.** After the loop ends, parse proposals from the final
   message (structured output: ask the model to emit a JSON block with
   `proposals: [{label, feature_ids, rationale}]` referencing feature ids **from
   tool results**, never coordinates). The server resolves ids → features from the
   result store. If any id is unknown, reject and re-prompt once; then fail closed.
   This makes it impossible for the model to invent geometry, and it's cheap.
4. **Context.** Prepend a session summary (scenario brief, adopted plan feature ids,
   available layers) so the model doesn't re-list layers every turn.
5. **Rate limit.** Per-IP token bucket on `/api/chat` (`settings.llm_rate_limit_per_min`).
   `/api/check` is unlimited — it's cheap and deterministic.
6. **Model.** `settings.model` is `claude-opus-5` with adaptive thinking. Keep. For
   the hackathon's 100 users consider `claude-sonnet-5` for cost; make it an env var
   (it already is).

### 1.6 `api/` changes

| Route | Change |
|---|---|
| `GET /api/health` | add `model`, `n_rules`, `layers`, `git_sha` |
| `GET /api/scenarios`, `GET /api/scenarios/{id}` | list & load scenario files |
| `POST /api/sessions` → `{session_id}` | from a scenario id |
| `GET /api/sessions/{id}` | full state for the UI (plan, proposals, trace) |
| `POST /api/sessions/{id}/plan` | replace adopted features (from the map) |
| `POST /api/sessions/{id}/chat` (SSE) | agent turn, streamed |
| `POST /api/check` | unchanged; add optional `bbox` |
| `POST /api/propose/{kind}` | direct access to proposal tools without the LLM (scripting, fallback) |
| `GET /api/layers/{name}.geojson?crs=4326` | WGS84 for the map (pyproj at the edge) |
| CORS | allow the Vercel origin(s) from `NEON_CORS_ORIGINS` |
| `/mcp` | mount FastMCP's streamable-HTTP app so remote agents can connect; stdio stays for local |

OpenAPI at `/docs` is the API documentation; no separate doc.

### 1.7 `web/` — the frontend

Decision: **static, no build step**, deployed to Vercel as a static site. Reasons:
no Node on the dev machine, nothing in the UI needs a framework, and a static page
is what a hackathon team can fork in a minute. MapLibre GL, `mapbox-gl-draw`
(works with MapLibre) for sketching, plain ES modules.

```
web/
  index.html            layout: map | side panel (tabs: Scenario · Chat · Findings · Rules)
  config.js             window.NEON_API = "https://…onrender.com"  (edited by Vercel env at build? no — it's static; use a small `config.js` per environment, or read from `?api=` query for local dev)
  app.js                session bootstrap, layer loading, draw tools
  chat.js               SSE client, trace rendering, option cards
  findings.js           list + map highlight of Finding.geometry
  style.css             goNEON-ish: black, white, pink #F23093 accent, cyan #3EFFF1 (matches the brief deck)
  vercel.json           rewrites none; headers for caching
```

CRS: the API serves WGS84 for display; drawn geometries go back as WGS84 and the API
converts to 2056 in one place (`api/crs.py`). The browser never sees LV95.

Screens (one page, tabs):
1. **Scenario** — brief text, "Load" fills the map, metrics targets.
2. **Chat** — request → live trace → 1–3 option cards (metrics table, violations
   count, "Show on map", "Adopt").
3. **Findings** — after any adopt/draw, `/api/check` runs automatically; list with
   severity, measured vs required, rule id → opens the Rules tab; click highlights.
4. **Rules** — the catalog, citations, `verified` badge ("draft rule").

### 1.8 Scenarios

`scenarios/*.yaml`, loaded by the API:

```yaml
id: sewer-01
title: Connect the new block to the main
brief: >
  ...
bbox: [2682000, 1247000, 2683000, 1248000]
kinds_allowed: [sewer]
rule_topics: [drainage]
starting_features: [...]         # GeoJSON, EPSG:2056
targets: {n_errors: 0, max_length_m: 400}
```

## 2. Test plan

| Level | What | Tooling |
|---|---|---|
| Unit — checks | each check type: pass, fail, missing-data-warning; `capacity_manning` vs hand calc | pytest, synthetic geometries |
| Unit — rules | catalog validates; every rule's check exists; every `verified: true` rule has `url` or `section` | `tests/test_catalog.py` (extend) |
| Unit — tools | each proposal tool on a tiny fixture study area (`tests/fixtures/mini/`): deterministic output, findings present when expected | pytest |
| Contract | tool docstrings/signatures produce valid Anthropic and MCP schemas (`beta_tool(fn)` doesn't raise; FastMCP registers) | pytest |
| API | every route with `httpx.AsyncClient`; SSE yields `done`; guardrail rejects unknown feature ids (mock the model) | pytest + `respx`/fake runner |
| Scenario (e2e, no LLM) | for each scenario: `propose_*` → `check_plan` → zero errors on the reference solution stored next to the scenario | pytest, marked `slow` |
| Manual | the tutorial script from the video plan, executed by someone who is not me | checklist in `docs/plan/04-submission.md` |

Rule: no test needs an API key or the network. The agent is tested with a fake
runner that replays a recorded tool-call sequence.

## 3. Deployment pipeline

```
GitHub (main) ──push──▶ Render: builds Dockerfile, runs uvicorn on $PORT
                └─────▶ Vercel: deploys web/ as static (root dir = web)
```

- **Dockerfile** → multi-stage with `uv`: `uv sync --frozen --no-dev` into
  `/app/.venv`, copy `src`, `scenarios`, `data/fixtures`. Fixtures are baked into
  the image (S: no runtime downloads). Image size budget: < 600 MB with rasterio.
- **Render** `render.yaml`: web service, Docker, plan `starter` (kept warm for the
  demo; free tier sleeps), env `ANTHROPIC_API_KEY`, `NEON_MODEL`,
  `NEON_CORS_ORIGINS`, health check `/api/health`.
- **Vercel**: project root `web/`, framework "Other", no build. `config.js` holds the
  API URL; local dev overrides with `?api=http://localhost:8000`.
- **CI** (GitHub Actions): `uv sync`, `ruff check`, `pytest -m "not slow"` on PR;
  `pytest` full on main. Render only deploys green main (Render's "auto-deploy on
  CI pass" setting, or a deploy hook from the workflow).
- **Secrets**: only on Render. The browser never holds the Anthropic key; the
  bring-your-own-key fallback (if built) sends it per request over HTTPS and the
  server never stores it.

Local: `uv run uvicorn neon_agent.api.app:app --reload` + open `web/index.html?api=http://localhost:8000`
(or serve `web/` with `python -m http.server`). No Node needed anywhere.

## 4. Phases with acceptance criteria

Order is by risk. Each phase ends deployed.

| Phase | Deliverables | Accept when |
|---|---|---|
| **P0 Foundation** (≈2 h) | uv Dockerfile; `render.yaml`; CORS; lazy Anthropic client; fix `min_distance` geometry; GitHub repo + Actions; Vercel project with placeholder page | `curl https://<render>/api/health` → ok; Vercel page loads and calls it; CI green |
| **P1 Data** (≈4 h) | study area chosen; `fetch_layers.py` for buildings, roads, trees; DEM clipped; synthetic utilities generator; fixtures committed (or LFS if > 20 MB); `ElevationSource` | `/api/layers` lists building, road, tree, gas_main, water_main, power_cable, sewer; map shows them |
| **P2 Rules + checks** (≈3 h) | check types in §1.2; catalog per domain plan; catalog test enforces citation fields | `pytest` green; `/api/rules` returns ≥ 12 rules across 3 topics |
| **P3 Proposal tools** (≈5 h) | `propose_sewer`, `propose_power_line`, `propose_trees`, `sample_elevation`, `compare_proposals`; mini fixture tests | `POST /api/propose/trees` on scenario 3 returns accepted + rejected points with reasons |
| **P4 Agent** (≈3 h) | trace, SSE, guardrail, sessions, rate limit | chat on scenario 1 streams a trace and returns ≥ 2 proposals with resolvable feature ids; a forged id is rejected (test) |
| **P5 UI** (≈5 h) | the four tabs; draw tools; option cards; findings highlight | someone else completes the tutorial script from the README without help |
| **P6 Ecosystem** (≈2 h) | `/mcp` HTTP transport; README "3 lines to start" for UI, API, MCP, rule PR; `docs/adding-a-rule.md` written; example notebook | `claude mcp add` against the deployed URL and `list_rules` works |
| **P7 Scenarios + polish** (≈2 h) | 3 scenario files with reference solutions; German strings if time | e2e scenario tests green |
| **P8 Video** (≈2 h) | per `04-submission.md` | sent |

Cut order if behind: German → scenario 3 → draw tools (chat-only input) → `/mcp` HTTP (keep stdio) → power-line alternatives (one route only).

## 5. Open engineering questions

0. Local build on this Mac (x86_64): `cryptography>=50` has no x86_64-macOS wheel
   and fails to build (it's pulled in by `mcp` → `pyjwt[crypto]`). When recreating
   `pyproject.toml`, add `[tool.uv] constraint-dependencies = ["cryptography<46"]`.

1. `rasterio` in the Render image — verify early in P1; fallback is the `.npy` grid.
2. Anthropic SDK: `beta_tool` + `tool_runner` is a beta surface; pin `anthropic` to
   the version in `uv.lock` and read the release notes before upgrading. If the
   runner can't expose per-call hooks for the trace, replace it with a 40-line
   manual loop (the plan already assumes that is acceptable).
3. Hackathon load: 100 users × a few chats each is fine for one Render instance
   for the API; the LLM spend is the real limit. Decide model + per-IP limit with
   the organisers' budget in mind (§ product plan).
4. Session persistence across Render restarts: none in MVP. Acceptable for a one-day
   event; say so in the README.
