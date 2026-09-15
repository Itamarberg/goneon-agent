# ADR 0001: Deterministic checks, the LLM orchestrates

**Status:** accepted

## Context
Planners need verdicts they can defend in front of an authority. LLMs are good at
understanding intent and explaining results. They are unreliable at measuring
distances or remembering exact regulatory thresholds.

## Decision
Every pass/fail verdict comes from a pure function in `checks/`. Its parameters
come from a cited rule in `rules/catalog/`. The LLM may only choose which tools
to call and explain what they return. The system prompt requires it to cite
`rule_id`s returned by tools and not to state thresholds from memory.

## Consequences
- Verdicts are reproducible and can be unit-tested.
- Adding coverage means adding rules and checks. Prompt engineering does not add
  coverage.
- The agent can't handle a rule that has no check type. It says that it can't
  check the rule and doesn't guess.
