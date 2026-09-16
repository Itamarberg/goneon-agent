# Submission plan

What goNEON receives, in what order, and the scripts for it. Scope is defined in
[`../PLAN.md`](../PLAN.md).

| Grading criterion | Where it is shown |
|---|---|
| Cut the scope yourself and say why | `PLAN.md` §9 decision log + Part 1 |
| Works end to end | the hosted URL + Part 2 |
| Explain what it does and how, in engineering terms | Part 1 + `ARCHITECTURE.md` + ADR 0001 |
| Others can build on it | multi-discipline constraints, REST + MCP, Part 3 |
| *Loses them:* waiting for a fuller brief | the 24 h message states decisions, not questions |
| *Loses them:* shipping the unexplainable | generators and checks are deterministic; every constraint shows its source |
| *Loses them:* a solo demo | tutorial tested by someone else; the site needs no account and no explanation |

## 1. The 24-hour message

> Subject: Take-home — timeline and plan
>
> Hi Lukas, hi Raphael,
>
> Thanks for the brief. My plan:
>
> **What I'll build.** A website where planners from any field pick an area of
> Zurich, say what they want to place — trees, bike racks, a cable, a power line —
> and choose the constraints it must respect, from a catalog of cited rules or in
> their own words. An agent turns that into a structured request; deterministic
> generators produce plan variants on real open data (Stadt Zürich, BFE,
> swisstopo); the same checks verify every variant. When no plan fits, the tool
> says which constraint blocks it. The planner decides. The same tools are open
> over REST and MCP.
>
> **Decisions I've made.** Real data only — no synthetic layers; constraints that
> need non-public data (underground utilities) are reported as not evaluable.
> Generic placement and clearance constraints instead of domain physics
> (hydraulics, magnetic fields), which I'll name as the next step. Thresholds come
> from cited sources or from the planner, never from the model. No accounts. Full
> decision log in the repo.
>
> **Timeline.** Build: [date] → [date]. Link + video: [date, time].
> Presentation: [date, time], [in person / video call].
>
> [name]

Fill the dates once the start is fixed; promise one day more than the plan needs.

## 2. What is sent at the end

1. The site URL, with 3 lines of instructions.
2. The repo link.
3. The video (~8 min, three chapters with timestamps).
4. Two paragraphs: what shipped, what was cut, one thing I'd do differently.

## 3. Video scripts

### Part 1 — Lukas (engineering), 3 min

| t | Screen | Say |
|---|---|---|
| 0:00 | architecture diagram | "Code generates and decides; the model translates and explains. It never emits a coordinate or a threshold." |
| 0:30 | constraint YAML → zones on the map | "Every constraint becomes geometry: forbidden and required zones. That's why one generator works for trees and for bike racks." |
| 1:00 | generator code, variants | "Points: allowed area, candidate grid, greedy selection under spacing. Lines: cost raster, least-cost path. Two to three variants each." |
| 1:30 | check_plan on a variant; infeasibility report | "An independent checker verifies every variant. If nothing fits, we find which hard constraint blocks it and the threshold that would free space." |
| 2:00 | chat → draft constraint → confirm | "Planner-defined constraints: the model drafts structure, the planner confirms the number and the source." |
| 2:30 | `/docs`, MCP client | "One tool surface, used by the site, REST and MCP. Not modelled, deliberately: NISV field calculation, hydraulics, underground conflicts." |

### Part 2 — Raphael (planner tutorial), 3 min

| t | Screen | Say |
|---|---|---|
| 0:00 | draw a street | "A landscape planner wants trees here." |
| 0:20 | tick `tree-werkleitung`, `not-on-building`, spacing 8 m | "Pick constraints. Red zones show where trees can't go. This one can only be checked against the utility data that's public — the tool says so." |
| 0:50 | Generate → 3 variants | "Three options: most trees, best spacing, most regular." |
| 1:10 | switch planner: line from A to B, LeV 5 m + school 50 m | "An energy planner, same tool. Five metres from buildings, source LeV." |
| 1:40 | infeasible → relax school buffer to soft | "No route fits. The tool says the school buffer blocks it; making it soft gives two routes with the trade-off shown." |
| 2:20 | add own constraint in chat; export | "Add your own rule in words, confirm it, export. You decide." |

### Part 3 — Raphael (vision), 2.5 min

| t | Screen | Say |
|---|---|---|
| 0:00 | catalog | "The asset is the constraint catalog: data, guidelines, regulations, linked. Every discipline adds to it." |
| 0:40 | goNEON's own topics | "Parking, bike corridors, traffic calming are catalogs, not new code." |
| 1:20 | next 90 days | "With a team: access to utility data, engineering check types (hydraulics, field calculation), a review workflow that makes rules verified, city-wide data." |
| 2:00 | close | "It's live at the URL. Try to break it." |

## 4. Pre-send checklist

- [ ] Someone else completes the Part 2 tutorial without help
- [ ] Fresh browser: URL → first generated plan in < 3 min
- [ ] Every curated constraint: source quoted and `verified`, or visibly *draft*
- [ ] Every layer shows its source in the map legend
- [ ] Render on a warm instance for the review window; LLM rate limit and spend alert set
- [ ] README: use the site, the API, MCP; add a constraint; known limits
- [ ] `PLAN.md` §9 matches what was actually built
- [ ] Video < 9 min, chapters, audio checked
- [ ] No secrets in the repo history

## 5. Presentation prep

- "Why not let the model generate the geometry?" → reproducibility; ADR 0001.
- "How is a planner's own rule trustworthy?" → it isn't until reviewed; badge + source; review workflow is the next step.
- "What about physics?" → constraint-type registry; hydraulics or field calculation are check types with their own tests.
- "What did you get wrong?" → one honest example from the build.
