"""The chat endpoint, without calling a model.

The agent loop is stubbed: what matters at this layer is that the server returns
map-ready geometry, passes the guardrail's warnings through, degrades honestly
without an API key, and limits what a single client can spend.
"""

import pytest
from fastapi.testclient import TestClient

from agent.loop import AgentReply, AgentUnavailable
from api import ratelimit
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_limits():
    ratelimit.reset()
    yield
    ratelimit.reset()


def _reply(**kw):
    base = dict(text="", tool_calls=[], variants=[], zones=None, proposals=[], warnings=[])
    return AgentReply(**{**base, **kw})


def test_chat_returns_503_without_a_key_and_says_what_still_works(monkeypatch):
    def unavailable(**_kw):
        raise AgentUnavailable("ANTHROPIC_API_KEY is not set. The map ... still works.")

    monkeypatch.setattr("api.main.agent_loop.run_turn", unavailable)
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_variant_geometry_comes_back_in_wgs84(monkeypatch):
    variant = {
        "id": "points-max_count",
        "label": "A",
        "strategy": "max_count",
        "features": [
            {
                "id": "t1",
                "kind": "tree",
                "geometry": {"type": "Point", "coordinates": [2682000.0, 1248000.0]},
            }
        ],
        "metrics": {},
        "findings": [],
        "tradeoffs": [],
    }
    monkeypatch.setattr(
        "api.main.agent_loop.run_turn",
        lambda **_kw: _reply(text="See points-max_count.", variants=[variant]),
    )
    body = client.post("/api/chat", json={"messages": []}).json()
    lon, lat = body["variants"][0]["features"][0]["geometry"]["coordinates"]
    assert 8 < lon < 9 and 47 < lat < 48


def test_a_guardrail_warning_reaches_the_client(monkeypatch):
    monkeypatch.setattr(
        "api.main.agent_loop.run_turn",
        lambda **_kw: _reply(text="Use line-made-up.", warnings=["referred to plan variants..."]),
    )
    body = client.post("/api/chat", json={"messages": []}).json()
    assert body["warnings"]


def test_chat_is_rate_limited_per_client(monkeypatch):
    monkeypatch.setattr("api.main.agent_loop.run_turn", lambda **_kw: _reply(text="ok"))
    monkeypatch.setattr(ratelimit, "MAX_REQUESTS", 2)
    ok = [client.post("/api/chat", json={"messages": []}).status_code for _ in range(2)]
    blocked = client.post("/api/chat", json={"messages": []})
    assert ok == [200, 200]
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_status_reports_whether_chat_is_configured():
    body = client.get("/api/chat/status").json()
    assert set(body) == {"available", "model"}
