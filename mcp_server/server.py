"""The same tools, exposed over MCP.

The point of this file is how little is in it. Every function comes straight
from `tools/core.py` — the surface the in-app agent uses. A hackathon team can
point Claude Desktop, their own agent, or any MCP client at this and generate
and check plans against real Zurich open data without our UI
(ARCHITECTURE.md, extension point 4).

Run it two ways:

    uv run --extra mcp python -m mcp_server.server          # stdio, for a desktop client
    uv run --extra mcp python -m mcp_server.server --http   # streamable HTTP

The HTTP app is also mounted on the API container at /mcp.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from data.study_area import STUDY_AREA
from tools import core

INSTRUCTIONS = f"""\
Deterministic infrastructure planning tools for {STUDY_AREA.description}

Constraints are data: pass a curated catalog id (see list_catalog) or a full
constraint object. Thresholds must come from the catalog or from the person you
are helping — never from your own knowledge.

Geometry is EPSG:2056 (LV95, metres). Plans come only from generate_points and
generate_line; verdicts come only from check_plan. Some constraints cannot be
evaluated because the data is not open; the tools say so rather than passing them.
"""

server = MCPServer(
    name="neon-agent",
    title="neon-agent planning tools",
    version="0.1.0",
    instructions=INSTRUCTIONS,
)


def _register() -> None:
    """Register every function in the shared surface.

    Driven by `core.TOOL_FUNCTIONS` rather than a second hand-written list, so
    MCP cannot drift from what the in-app agent can do.
    """
    for name, fn in core.TOOL_FUNCTIONS.items():
        server.add_tool(
            fn,
            name=name,
            description=(fn.__doc__ or "").strip(),
            # Every function returns a dict, so clients get parsed results rather
            # than a JSON blob inside a text block.
            structured_output=True,
        )


_register()


def http_app() -> Any:
    """The streamable-HTTP ASGI app, for mounting on the FastAPI container."""
    return server.streamable_http_app()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    args = parser.parse_args()
    server.run(transport="streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
