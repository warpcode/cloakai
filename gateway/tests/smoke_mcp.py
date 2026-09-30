"""End-to-end smoke test: a real MCP client against the real gateway.

Not a mock. This starts the gateway as a subprocess over stdio exactly as a
client would, asks it for its tools, calls one, and checks what came back.

Run inside the gateway image with the Docker socket mounted:

    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$PWD/dist:/srv/dist:ro" -v "$PWD/gateway:/srv/gateway:ro" \
      -e CLOAKAI_NETWORK=cloakai-internal \
      cloakai/gateway:latest python -m gateway.tests.smoke_mcp
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe() -> int:
    env = dict(os.environ, PYTHONPATH="/srv")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "gateway.server"], env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools/list -> {names}")
            if not names:
                print("FAIL: the gateway advertised no tools")
                return 1

            for tool in tools.tools:
                print(f"  {tool.name}: {tool.description[:70]}...")

            # The advertised schema must be the prompt-only contract, or a client
            # model cannot know what to send.
            target = next(t for t in tools.tools if t.name == "dev")
            # mcp 2.x renamed inputSchema -> input_schema.
            required = (target.input_schema or {}).get("required")
            if required != ["prompt"]:
                print(f"FAIL: expected required=['prompt'], got {required!r}")
                return 1

            print("\ncalling dev...")
            result = await session.call_tool("dev", {"prompt": "reply with exactly PONG"})
            text = "".join(
                c.text for c in result.content if getattr(c, "type", "") == "text"
            )
            print(f"tools/call dev -> {text.strip()[:200]}")

            if "PONG" not in text:
                print("FAIL: the agent did not answer")
                return 1

            # The MCP SDK validates arguments and rejects unknown tool names
            # before the gateway's own handler runs. So these two asserts that a
            # bad call is legible rather than a crash — the wording is the SDK's,
            # and dispatch.GatewayError is the backstop if something bypasses it.
            bad = await session.call_tool("dev", {})
            bad_text = "".join(
                c.text for c in bad.content if getattr(c, "type", "") == "text"
            )
            print(f"\ncall with no prompt -> {bad_text.strip()[:120]}")
            if "prompt" not in bad_text.lower():
                print("FAIL: a missing prompt produced no legible error")
                return 1

            ghost = await session.call_tool("nonexistent", {"prompt": "hi"})
            ghost_text = "".join(
                c.text for c in ghost.content if getattr(c, "type", "") == "text"
            )
            print(f"call unknown agent -> {ghost_text.strip()[:120]}")
            if "unknown" not in ghost_text.lower():
                print("FAIL: an unknown agent produced no legible error")
                return 1

    print("\nPASS: gateway exposes agents as tools and calls them")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
