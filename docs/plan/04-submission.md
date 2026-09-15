# Submission plan

What goNEON receives, in what order, and the scripts for it. Grading criteria from
the brief, restated so every artefact maps to one:

| Criterion | Where it is shown |
|---|---|
| Cut the scope yourself and say why | `docs/PLAN.md` §2 (decision log) + Part 1 of the video |
| Works end to end | the hosted URL + Part 2 |
| Explain what it does and how, in engineering terms | Part 1 + `ARCHITECTURE.md` + ADRs |
| Others can build on it | Part 3 + README's contribution ladder + `/mcp` |
| *Loses them:* waiting for a fuller brief | the 24 h message goes out with decisions already made, not questions |
| *Loses them:* shipping the unexplainable | every number traceable to a rule; trace visible |
| *Loses them:* a solo demo | tutorial tested by someone else; scenario URLs work without me |

## 1. The 24-hour message (due first)

Send within one day of receiving the brief. Short. It must contain the timeline,
the presentation slot, and the decisions — not questions.

> Subject: Take-home — timeline and plan
>
> Hi Lukas, hi Raphael,
>
> Thanks for the brief. My plan:
>
> **What I'll build.** A map-based planning assistant for one Zurich study area.
> An agent proposes sewer runs, power-line alignments and tree placements;
> deterministic checks evaluate them against a cited rule catalog (VSA/SN 592000,
> LeV/NISV, Stadt Zürich Standards Stadträume); the planner compares options and
> decides. The same tools are exposed over HTTP and MCP so hackathon teams can
> build on them.
>
> **Decisions I've made without asking.** One study area with pre-fetched
> swisstopo / Stadt Zürich data. Underground utilities are synthetic (the
> Leitungskataster isn't public) and labelled as such. Segment-level hydraulics,
> not a network model. No auth; sessions by URL. Rule thresholds are marked
> "draft" until I've verified them against the source — I'd rather flag than
> guess. Full log in the repo.
>
> **Timeline.** Build: [date] evening → [date]. Link + video: [date, time].
> Presentation: [date, time], [in person at … / video call], 30 min.
>
> **Stack.** Python/FastAPI, shapely, Claude tool use, MapLibre. Render + Vercel.
>
> Anything in the brief I read differently than intended, I'll say so in the video.
>
> [name]

Fill the dates from `docs/PLAN.md` §6 once the start time is fixed. Aim to promise
one day more than the plan needs.

## 2. What is sent at the end

1. The URL (Vercel), with 3 lines of instructions in the README and in the message.
2. The repo link (public GitHub).
3. One video file or unlisted link, ~8 minutes, three chapters with timestamps in
   the description.
4. The message: two paragraphs — what shipped, what was cut, one thing I'd do
   differently.

## 3. Video scripts

Record each part separately, then concatenate. Screen + face cam. Rehearse once
with a timer; the 2–3 minute limit per part is real.

### Part 1 — to Lukas (engineering), 3 min

| t | Screen | Say |
|---|---|---|
| 0:00 | `ARCHITECTURE.md` diagram | "One principle: the LLM orchestrates, code decides. Every verdict is a pure function with a cited rule as parameter. The model chooses which tools to call and explains results; it cannot produce a number." |
| 0:30 | `checks/spatial.py`, `rules/catalog/*.yaml`, `tests/` | "Rules are data. Check types are registered functions. Adding coverage means adding a YAML file and, if needed, a check type with a test. Prompting doesn't add coverage." |
| 1:00 | Live chat on `sewer-01`; trace panel | "Watch the trace: elevation sampling, proposal, check. All deterministic. The model's final answer references feature ids from tool results —" |
| 1:30 | Guardrail test in terminal (`pytest -k guardrail`) | "— and if it references an id that never came from a tool, the server rejects the answer. It can't invent geometry." |
| 2:00 | `tools/core.py` → `/docs` → `claude mcp add …` | "One tool contract, three front doors: the in-app agent, REST, MCP. That's the platform surface." |
| 2:30 | `docs/PLAN.md` §2 | "Scope cuts, with reasons. The biggest: synthetic utilities and segment-level hydraulics. Both are adapter boundaries, not rewrites." |

### Part 2 — to Raphael (planner tutorial), 3 min

No code on screen. Speak as to a planner.

| t | Screen | Say |
|---|---|---|
| 0:00 | Scenario tab, `trees-01` | "You're planting a street. Here's the brief and the target." |
| 0:20 | Map: layers toggled | "Buildings, existing trees from the city's Baumkataster, and the utilities under the pavement — these are synthetic, the real cadastre isn't public." |
| 0:45 | Chat: "plant trees every 8 m along X" | "Ask in plain language." |
| 1:00 | Option card; rejected dots | "Green dots pass. Grey dots were rejected — hover: 1.6 m from the gas main, minimum is 2.0. Click the rule." |
| 1:30 | Rules tab | "Source: Stadt Zürich Standards Stadträume. This badge means I haven't verified it against the document yet — so treat it as draft." |
| 1:50 | Adopt; drag a tree; Findings update | "Adopt the option, then move a tree by hand. The check re-runs instantly. It tells you; it doesn't stop you." |
| 2:20 | Export / share link | "Share the URL with your team. Nothing here is approved — you decide." |

### Part 3 — to Raphael (vision), 2.5 min

| t | Screen | Say |
|---|---|---|
| 0:00 | Rule catalog folder | "goNEON's thesis is spatial data + guidelines + regulations. The asset in this system is the rule catalog. Three domains tonight; a fourth is a folder." |
| 0:30 | Contribution ladder (README) | "At the hackathon: use it, script it, point your own agent at it, add a rule, add a data source. Prizes for rungs 4–5 turn 100 planners into contributors." |
| 1:00 | Slide: 90-day plan | "With a team of three: (1) verified rule packs with a review workflow — a rule becomes `verified` by PR with the source quoted; (2) real Leitungskataster ingestion behind the same `DataSource` protocol; (3) PostGIS + persistence; (4) the mobility rules you already work with — parking, bike corridors, traffic calming — as the next packs." |
| 1:45 | Slide: what I'd do differently | "Time-box the data step harder; it's where the night goes." |
| 2:15 | Close | "Everything I showed is at the URL. Try to break it." |

## 4. Pre-send checklist

- [ ] Someone else completes the Part 2 tutorial from the README without me in the room
- [ ] Fresh browser, no cache: URL → first finding in < 3 min
- [ ] `/api/health` shows model, rule count, git sha of the deployed commit
- [ ] Render instance on a paid plan (no cold start) for the review window
- [ ] LLM rate limit set; Anthropic spend alert set
- [ ] Every rule: `verified` true with `url`/`section`, or shown as draft in the UI
- [ ] README: run locally, API, MCP, add a rule, known limits
- [ ] `docs/PLAN.md` §2 is current (every cut actually made is listed)
- [ ] Video: three chapters, timestamps, audio checked, < 9 min total
- [ ] Repo public; no secrets in history (`git log -p | grep -i api_key`)

## 5. Presentation (30 min after they've watched)

Expect questions, not a demo. Prepare short answers for:
- "Why not let the model do the geometry?" → ADR 0001; reproducibility; authority defensibility.
- "How would you verify 200 rules?" → PR workflow, quoted source, two reviewers, `verified` flag; LLM-assisted extraction as *proposals* only.
- "What breaks at 10 000 users?" → sessions to Redis, PostGIS, one queue for LLM calls; checks are stateless.
- "What did you get wrong?" → have one honest example ready from the build.
