# Product & hackathon plan

Who uses the thing, what they do with it, and what the organisers need. The
engineering plan says how it is built; this one says what it must feel like.

## 1. The people

| Person | Wants | Fears | How the MVP serves them |
|---|---|---|---|
| **Hackathon planner** (student / junior, mixed GIS and civil background) | solve the challenge, understand *why* a layout fails, produce something to show | a black box that says "no" without reasons; tooling that eats the day | every finding says measured vs required + the rule + its source; proposals come with rejected alternatives and the reason each failed |
| **Hackathon dev team** | build something on top in hours, not days | reading a codebase; auth; data plumbing | HTTP API with OpenAPI, MCP endpoint, one fixture study area, rule packs as YAML |
| **Organiser** (goNEON) | 100 people working without support tickets; comparable results | rate limits, outages, LLM cost blow-ups | scenarios with targets and metrics; deterministic `/api/check` works without the LLM; per-IP LLM limit; status page = `/api/health` |
| **Raphael** (planning) | see that a planner stays in control and learns the rules | autopilot; hand-waving | option cards, never auto-adopt; rule tab with citations and "draft" badges |
| **Lukas** (engineering) | see that verdicts are reproducible and the design extends | LLM doing geometry; prompt-engineering as architecture | trace, guardrail, pure checks, one tool contract on three surfaces |

## 2. Core user journey (planner)

```
open scenario URL
  → read the brief (3 sentences, one target)
  → map shows context layers; utilities are dashed + labelled "synthetic"
  → ask in chat: "connect the new block at X to the main, 250 mm"
  → trace streams: sample_elevation → propose_sewer → check_plan
  → 2 option cards: A shorter but 0.4 % (fails min gradient), B longer, passes
  → click "Show on map" A: conflict segment highlighted in pink
  → click rule id → Rules tab: threshold, source, "verified: false → draft"
  → adopt B; drag one manhole; Findings tab re-checks in <1 s (no LLM)
  → export GeoJSON / share URL
```

Design constraints that follow:
- **Findings first, chat second.** The chat is a way to generate options; the
  findings panel is the product. It must work when the LLM is slow or down.
- **Every number has a source.** No metric without a unit; no threshold without a
  rule id; no rule without a citation, verified or flagged.
- **Rejected candidates are shown.** For trees especially: the grey dots with "1.6 m
  from gas main (min 2.0)" teach the rule better than the green ones.
- **Nothing is ever adopted by the agent.** "Adopt" is a button the planner clicks.
- **Under 3 minutes from URL to first finding**, with no account.

## 3. The three challenges (as the planners will read them)

Each scenario ships with: brief, starting features, targets, and a reference
solution used only by the organisers' scoring script.

### 3.1 Drain the new block (`sewer-01`)
> A new residential block at *[address in the study area]* needs a gravity
> connection to the existing sewer main on *[street]*. Design flow 12 l/s.
> Deliver an alignment with manholes that drains by gravity, keeps minimum cover
> under the road, does not cross the water main closer than the rule allows, and is
> as short as reasonably possible.

Targets: `n_errors = 0`, `length_m ≤ 1.2 × reference`. Teaches: gradient vs cover
trade-off, why the shortest path often fails.

### 3.2 Power to the depot (`power-01`)
> A medium-voltage overhead line must connect the substation at *[point]* to the new
> depot at *[point]*. Keep the clearance to buildings required by the LeV and stay
> outside the sensitive-use buffer around the school. Minimise length and the number
> of direction changes.

Targets: `n_errors = 0`, `min_clearance_m ≥ rule`, `n_vertices ≤ 8`. Teaches:
horizontal clearance, sensitive-use buildings as a distinct class, that
undergrounding changes the rule set (stretch: switch `kind` to `power_cable` and see
different rules apply).

### 3.3 Green the street (`trees-01`)
> Plant as many street trees as possible along *[street]* between *[a]* and *[b]*,
> spacing at least 8 m, without conflicting with the utilities under the pavement or
> the building façades.

Targets: `n_errors = 0`, `n_trees ≥ reference − 1`. Teaches: setbacks, that the
utility layer decides more than the street does; comparing species classes (crown
radius) is a one-parameter change.

Stretch 3.4 (if time): **combine** — plant trees along the sewer route you designed
in 3.1. Rules interact; this is where "space + guidelines + regulations" shows.

## 4. The contribution ladder (for dev teams)

| Rung | Effort | What they do | What we provide |
|---|---|---|---|
| 1 | 5 min | use the UI | scenario URLs |
| 2 | 30 min | script against the API from a notebook | OpenAPI at `/docs`, `examples/notebook.ipynb`, `/api/propose/*` without the LLM |
| 3 | 30 min | point their own agent at our tools | `/mcp` URL, `claude mcp add neon --transport http <url>` one-liner |
| 4 | 1 h | add a rule | `docs/adding-a-rule.md`, PR template, catalog test tells them if it's wrong |
| 5 | 2 h | add a check type or a data layer | registry decorator, `DataSource` protocol, mini fixture for tests |

Hackathon prize categories could map onto rungs 4–5 ("best new rule pack", "best
new data source"). That is also the Part 3 vision pitch: the platform grows by
contributions, and the rule catalog is the asset.

## 5. Organiser needs

- **Capacity**: 100 users. API is cheap; the LLM is the constraint. Budget per user
  ≈ 10 chats × ~15k tokens. Decide `NEON_MODEL` and `llm_rate_limit_per_min` with
  the organisers' budget; expose both on `/api/health` so it's visible.
- **Degradation**: LLM down → chat shows "assistant unavailable, checks still work";
  `/api/check` and `/api/propose/*` keep working.
- **Scoring**: `scripts/score.py <scenario> <geojson>` runs `check_plan` + targets →
  JSON. Same code the UI uses; no separate truth.
- **Support**: README has a "known limits" section (synthetic utilities, draft rules,
  no persistence across restarts) so nobody files it as a bug.
- **Comparability**: everyone works on the same study area and starting features.

## 6. Out of scope, said plainly in the UI

A footer line on every screen: *"Utilities are synthetic. Rules marked ‹draft› have
not been verified against their source. This is decision support, not approval."*
That sentence protects the tool and matches goNEON's own framing.

## 7. Success criteria for the MVP (product view)

1. A planner who has never seen it reaches a first finding in under 3 minutes.
2. Every finding in the tutorial can be traced to a rule with a citation.
3. A dev team reaches rung 3 (own agent on our MCP) in under 30 minutes using only
   the README.
4. The three challenges are solvable to `n_errors = 0` with the shipped tools.
5. Nothing in the UI implies the tool approves a plan.
