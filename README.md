# neon-agent

A planning website for planners from any field. Pick an area of Zurich, say what
you want to place (trees, bike racks, a cable, a power line), and choose the
constraints it must respect, from a catalog of cited rules or in your own words.
The agent turns that into a planning request, deterministic generators produce
plan variants on real open data, and every variant is checked against your
constraints. You compare, edit and decide.

**Status:** in build. P0–P4 done (skeleton, real data, constraints, generators, agent) — see `docs/PLAN.md` §8 for the phases.

- [docs/PLAN.md](docs/PLAN.md) — product, generation approach, data, build plan, decision log
- [ARCHITECTURE.md](ARCHITECTURE.md) — components and contracts
- [docs/adr/](docs/adr/) — architecture decisions
- [docs/plan/04-submission.md](docs/plan/04-submission.md) — timeline message, video scripts

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync                                   # install
uv run --group dev pytest                 # tests
uv run uvicorn api.main:app --reload      # API on http://127.0.0.1:8000
python3 -m http.server -d web 5173        # site on http://127.0.0.1:5173
```

The site reads its backend URL from `web/config.js`.

The chat needs `ANTHROPIC_API_KEY`; everything else — map, constraints,
generation, checks — works without one, and `/api/chat/status` says which you have.

The layers under `data/layers/` are committed. To re-fetch them from the open-data
services (only needed if the study area changes):

```sh
uv run python scripts/fetch_layers.py
```
