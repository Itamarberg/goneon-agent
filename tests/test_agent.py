"""Agent tests.

The loop itself is thin — the SDK drives it. What is worth testing is everything
the server keeps out of the model's hands: the tool surface it is given, the
refusal to invent thresholds, and the guardrail on what the answer may claim.

The live model is not called here. These tests run without an API key, which is
the point: the deterministic product does not depend on one.
"""

import json

import pytest

from agent import loop
from agent.loop import AgentUnavailable, run_turn
from agent.session import Session, summarise_variant
from agent.tools import build_tools
from tools import core


class TestSessionGuardrail:
    def test_a_variant_id_the_tools_never_produced_is_caught(self):
        session = Session()
        session.record_variants([_variant("points-max_count")])
        invented = session.unknown_variant_ids(
            "I recommend points-max_count over line-shortest_route."
        )
        assert invented == ["line-shortest_route"]

    def test_real_ids_pass(self):
        session = Session()
        session.record_variants([_variant("points-best_score")])
        assert session.unknown_variant_ids("points-best_score places 12 trees.") == []

    def test_prose_is_not_mistaken_for_an_id(self):
        session = Session()
        assert session.unknown_variant_ids("There are 12 points in this line of trees.") == []


class TestWhatTheModelSees:
    def test_variant_summary_carries_the_decision_data_but_no_coordinates(self):
        variant = _variant("points-max_count", features=[{"id": "t1"}, {"id": "t2"}])
        summary = summarise_variant(variant)
        assert summary["object_count"] == 2
        assert "features" not in summary
        assert "findings_summary" in summary

    def test_summary_surfaces_what_could_not_be_evaluated(self):
        variant = _variant(
            "points-max_count",
            findings=[
                {
                    "severity": "not_evaluable",
                    "constraint_id": "tree-fahrleitung",
                    "message": "Cannot be checked: masts are not open data.",
                }
            ],
        )
        summary = summarise_variant(variant)
        assert summary["not_evaluable"][0]["constraint_id"] == "tree-fahrleitung"

    def test_the_tool_list_is_the_one_we_intend(self):
        names = {t.name for t in build_tools(Session())}
        assert names == {
            "list_layers",
            "describe_area",
            "list_catalog",
            "explain_constraint",
            "propose_constraint",
            "preview_zones",
            "generate_points",
            "generate_line",
            "check_variant",
            "explain_infeasibility",
        }


class TestToolSurface:
    def test_proposing_a_clearance_without_a_number_asks_for_one(self):
        # The model must not fill in a threshold from memory (ADR 0001), so the
        # tool refuses and tells it what to ask the planner.
        result = core.propose_constraint(
            id="my-rule",
            title="Keep trees away from hydrants",
            type="min_distance",
            layer="hydrant",
            source_text="the planner said so",
        )
        assert result["problems"]
        assert "ask the planner" in result["problems"][0].lower()

    def test_a_proposal_is_never_confirmed(self):
        result = core.propose_constraint(
            id="my-rule",
            title="3 m from hydrants",
            type="min_distance",
            layer="hydrant",
            params={"d_m": 3.0},
            source_text="city guideline X",
        )
        assert result["confirmed"] is False
        assert result["needs_planner_confirmation"] is True
        assert result["proposal"]["source"]["kind"] == "user"
        assert result["proposal"]["verified"] is False

    def test_a_proposal_against_unavailable_data_says_so(self):
        result = core.propose_constraint(
            id="sewer-rule",
            title="2 m from the sewer",
            type="min_distance",
            layer="sewer",
            params={"d_m": 2.0},
            source_text="the planner's own rule",
        )
        assert result["evaluable"] is False
        assert "open data" in result["not_evaluable_reason"]

    def test_an_unknown_catalog_id_is_an_error_not_a_silent_skip(self):
        # Dropping it would produce a plan that looks checked and is not.
        with pytest.raises(core.ToolError, match="unknown constraint"):
            core.resolve_constraints(["no-such-rule"])

    def test_checking_a_variant_that_does_not_exist_returns_a_usable_error(self):
        session = Session()
        check = next(t for t in build_tools(session) if t.name == "check_variant")
        result = json.loads(check.call({"variant_id": "points-nope", "constraints": []}))
        assert "No variant" in result["error"]

    def test_every_tool_returns_a_string_the_api_will_accept(self):
        # A tool_result must be a string or content blocks. Returning a dict was
        # passed through untouched and the API rejected the whole request — a
        # failure no test without a live model had caught.
        session = Session()
        session.record_variants([_variant("points-max_count")])
        calls = {
            "list_layers": {},
            "describe_area": {},
            "list_catalog": {"object_kind": "tree"},
            "explain_constraint": {"constraint_id": "not-on-building"},
            "check_variant": {"variant_id": "points-max_count", "constraints": []},
        }
        for tool in build_tools(session):
            if tool.name not in calls:
                continue
            result = tool.call(calls[tool.name])
            assert isinstance(result, str), f"{tool.name} returned {type(result).__name__}"
            json.loads(result)  # and it must be parseable


class TestCredentials:
    def _no_credentials(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(loop, "PROFILE_DIR", tmp_path / "nothing-here")

    def test_the_chat_fails_clearly_and_says_what_still_works(self, monkeypatch, tmp_path):
        self._no_credentials(monkeypatch, tmp_path)
        with pytest.raises(AgentUnavailable) as e:
            run_turn(messages=[{"role": "user", "content": "hello"}])
        assert "MCP" in str(e.value) and "only the chat does not" in str(e.value)

    def test_an_api_key_counts_as_available(self, monkeypatch, tmp_path):
        self._no_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert loop.available()

    def test_an_oauth_profile_counts_as_available(self, monkeypatch, tmp_path):
        # Checking only the env var reported "no key" on a machine where the SDK
        # would have authenticated fine from a profile.
        self._no_credentials(monkeypatch, tmp_path)
        profile = tmp_path / "anthropic"
        profile.mkdir()
        (profile / "profiles.json").write_text("{}")
        monkeypatch.setattr(loop, "PROFILE_DIR", profile)
        assert loop.available()


def _variant(vid, features=None, findings=None):
    return {
        "id": vid,
        "label": f"{vid} label",
        "strategy": "s",
        "features": features or [],
        "metrics": {"count": len(features or [])},
        "tradeoffs": [],
        "findings": findings or [],
    }
