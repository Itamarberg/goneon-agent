"""MCP exposes the same tools as the in-app agent.

That claim is the extension point the README sells ("point your own agent at
it"), so it is tested rather than asserted: same names, same behaviour, same
refusals.
"""

import pytest

from tools import core

mcp_server = pytest.importorskip("mcp_server.server", reason="the mcp extra is not installed")


async def call(name, args):
    result = await mcp_server.server.call_tool(name, args)
    assert not result.is_error, result.content
    return result.structured_content


@pytest.mark.anyio
async def test_mcp_exposes_exactly_the_shared_tool_surface():
    # Driven from core.TOOL_FUNCTIONS, so MCP cannot drift from the agent's tools.
    names = {t.name for t in await mcp_server.server.list_tools()}
    assert names == set(core.TOOL_FUNCTIONS)


@pytest.mark.anyio
async def test_every_tool_carries_a_description_for_the_client():
    for tool in await mcp_server.server.list_tools():
        assert tool.description, f"{tool.name} has no description"


@pytest.mark.anyio
async def test_an_mcp_client_can_generate_a_plan():
    result = await call(
        "generate_points",
        {"object_kind": "tree", "constraints": ["not-on-building", "tree-spacing"], "count": 6},
    )
    assert result["infeasibility"] is None
    assert result["variants"]
    assert len(result["variants"][0]["features"]) == 6


@pytest.mark.anyio
async def test_an_mcp_client_gets_the_same_refusal_as_the_agent():
    # No threshold means "ask the planner", through this front door too.
    result = await call(
        "propose_constraint",
        {
            "id": "my-rule",
            "title": "away from hydrants",
            "type": "min_distance",
            "layer": "hydrant",
            "source_text": "the planner's rule",
        },
    )
    assert result["problems"]
    assert result["confirmed"] is False


@pytest.mark.anyio
async def test_an_mcp_client_is_told_what_cannot_be_evaluated():
    result = await call("explain_constraint", {"constraint": "tree-fahrleitung"})
    assert result["evaluable"] is False
    assert result["not_evaluable_reason"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_the_api_container_mounts_mcp_without_shadowing_the_api():
    # Mounting it at the root once made every /api route 404. Both must answer.
    from fastapi.testclient import TestClient

    from api.main import app

    # As a context manager, so the lifespan that runs MCP's session manager fires.
    with TestClient(app) as client:
        assert client.get("/api/health").json()["mcp"] is True
        # The MCP app answers at /mcp/ rather than 404ing; a bare GET is refused
        # by the MCP transport, which is still a response from the MCP app.
        assert client.get("/mcp/").status_code != 404
