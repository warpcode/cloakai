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
import sys

sys.path.insert(0, os.environ.get("REPO", "/srv"))

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
    # Build the command line with the CLI itself, rather than spelling out a docker
    # argv here. This test previously hardcoded `--network cloakai-internal`, which
    # stopped existing when the compose stack went; the failure it produced was
    # "Connection closed" with nothing on stderr, because the container never
    # started. A test that assembles its own command line can disagree with the
    # product in exactly that way, silently.
    from cli import registry as reg

    catalog = reg.load()
    name = IMAGE.split("/")[-1].split(":")[0]
    if name not in reg.agents(catalog):
        name = reg.names(catalog)[0]
        print(f"note: {IMAGE} is not in the catalogue; testing {name} instead")

    args = reg.build_argv(catalog, name, "mcp")

    params = StdioServerParameters(command=args[0], args=args[1:], env=dict(os.environ))

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=90)
            print(f"connected to {name}: {init.server_info.name}")

            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
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
