"""Verify the Jules image is a working MCP server with no gateway involved.

Same claim as direct_use.py, for a different image: `docker run --rm -e
JULES_API_KEY cloakai/jules mcp` speaks MCP to any client. This one only calls
read-only tools (jules_sources, jules_status) — starting a session creates cloud
resources and spends a concurrency slot, so it is deliberately not done here.

Run as:

    JULES_API_KEY=$(cloakenv get "kp://Personal/Credentials/Google - Main - Jules - Api Key:Password") \
      docker run --rm --entrypoint python \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$PWD/agents:/srv/agents:ro" \
        -e JULES_API_KEY \
        cloakai/gateway:latest /srv/agents/jules-mcp/verify.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

IMAGE = os.environ.get("JULES_IMAGE", "cloakai/jules:latest")


async def main() -> int:
    # -e JULES_API_KEY with no value forwards the caller's key to the container.
    # It is never written into this file, a log line, or a tool result.
    params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "-e", "JULES_API_KEY", IMAGE, "mcp"],
        env=dict(os.environ),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=45)
            print(f"connected to {IMAGE}: {init.server_info.name}")

            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            names = sorted(t.name for t in tools.tools)
            print(f"tools/list -> {names}")
            for tool in tools.tools:
                print(f"  {tool.name}: {(tool.description or '')[:64]}...")

            expected = {"jules_start", "jules_send", "jules_status",
                        "jules_approve_plan", "jules_sources"}
            if not expected.issubset(set(names)):
                print(f"FAIL: missing {sorted(expected - set(names))}")
                return 1

            print("\ncalling jules_sources (read-only)...")
            result = await asyncio.wait_for(
                session.call_tool("jules_sources", {}), timeout=90,
            )
            text = "".join(c.text for c in result.content
                           if getattr(c, "type", "") == "text")
            print(f"jules_sources -> {text.strip()[:220]}")

            if "error" in text.lower()[:40]:
                print("FAIL: jules_sources returned an error")
                return 1

            # A bad session id must come back as a message naming the tool's own
            # failure, not as a crash and not as anything resembling a key.
            print("\ncalling jules_status with a nonsense id...")
            bad = await asyncio.wait_for(
                session.call_tool("jules_status", {"session_id": "0"}), timeout=90,
            )
            bad_text = "".join(c.text for c in bad.content
                               if getattr(c, "type", "") == "text")
            print(f"jules_status -> {bad_text.strip()[:160]}")

    print("\nPASS: Jules is an MCP server with no gateway involved")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
