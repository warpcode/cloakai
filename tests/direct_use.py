"""Proof that an agent image is usable with no gateway in the picture.

The claim this checks: an agent image is the product. Any MCP client can use it
directly with

    docker run --rm <image> mcp

and get a working MCP server over stdio. The gateway, if it exists at all, is
just one more client of that interface — not something the image depends on.

So this spawns the agent container exactly as a third-party client would, speaks
MCP to it over stdio, and never imports gateway code. If this passes, the image is
independently reusable and nothing about the tool surface depends on cloakai
being in the middle.

Run inside the probe image, which exists only to provide an MCP client:

    docker run --rm --entrypoint python \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -e CLOAKAI_NETWORK=cloakai-internal \
      cloakai/probe:latest -m tests.direct_use
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

IMAGE = os.environ.get("AGENT_IMAGE", "cloakai/dev:latest")


def _cleanup() -> None:
    """Remove any container this harness left behind.

    Best-effort and unconditional. A harness that is interrupted, times out, or
    fails an assertion partway through still has containers running, and orphaned
    MCP containers are exactly what this file's own --rm assertion exists to
    prevent. Better to remove a container that might still be wanted than to
    leave one that never will be.
    """
    subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=cloakai.call=1"],
        capture_output=True, text=True, check=False,
    )
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=cloakai.call=1"],
        capture_output=True, text=True, check=False,
    )
    for cid in (listed.stdout or "").split():
        subprocess.run(["docker", "rm", "-f", cid],
                       capture_output=True, check=False)


async def main() -> int:
    # This is the whole interface. No gateway, no manifest, no proxy hop of ours.
    command = "docker"
    args = ["run", "--rm", "-i", "--network",
            os.environ.get("CLOAKAI_NETWORK", "cloakai-internal"), IMAGE, "mcp"]

    params = StdioServerParameters(command=command, args=args, env=dict(os.environ))

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected to {IMAGE}: {init.server_info.name}")

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools/list -> {names}")
            if not names:
                print("FAIL: the image served MCP but exposed no tools")
                return 1
            for tool in tools.tools:
                print(f"  {tool.name}: {(tool.description or '')[:70]}")

    print("\nPASS: the image is a working MCP server with no gateway involved")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
