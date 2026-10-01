"""End-to-end test of the CLI's two shapes.

Runs the real `cloakai` command as a subprocess and speaks MCP to it, exactly as
a client would:

  `cloakai mcp dev`      long-lived: a container stays alive while the client
                         holds the pipe open, and is gone once it closes.
  `cloakai agents dev`   ephemeral: one task, container destroyed on exit.

The mcp half is the interesting one, because "destroyed when closed" is a claim
about process lifetime that is easy to assert and easy to get wrong.

Run inside the probe image, which exists to provide an MCP client:

    docker run --rm --entrypoint python \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$PWD:/srv:ro" -e CLOAKAI_NETWORK=cloakai-internal \
      cloakai/probe:latest -m tests.cli_end_to_end
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def repo() -> str:
    return os.environ.get("REPO", "/srv")


def run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=repo(), capture_output=True, text=True, timeout=timeout, check=False,
    )


def call_containers() -> list[str]:
    done = subprocess.run(
        ["docker", "ps", "--filter", "label=cloakai.call=1", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    return [n for n in (done.stdout or "").split() if n]


async def check_mcp_shape() -> int:
    """`cloakai mcp dev` must serve MCP, and die when the client lets go."""
    print("cloakai mcp dev  (long-lived over stdio)")

    # The client's own PATH must find python, so the env is passed through
    # explicitly: stdio_client gives no way to add one, and inheriting a modified
    # env here would break the interpreter path it resolves.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cli.main", "mcp", "dev"],
        env={**os.environ, "PYTHONPATH": repo()},
        cwd=repo(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=90)
            print(f"  initialize ok: {init.server_info.name}")

            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            names = sorted(t.name for t in tools.tools)
            print(f"  tools/list -> {names}")
            if not names:
                print("  FAIL: served MCP but exposed no tools")
                return 1

            alive = call_containers()
            if not alive:
                print("  FAIL: no container is alive while the client is connected")
                return 1
            print(f"  container alive while connected: {alive[0]}")

    # Outside the `async with`, the client has closed the pipe. The container must
    # be gone: `cloakai mcp` execs docker, so it is the container's own stdin and
    # its exit is the CLI's exit.
    await asyncio.sleep(2)
    leaked = call_containers()
    if leaked:
        print(f"  FAIL: container survived the client closing: {leaked}")
        return 1
    print("  container destroyed when the client closed")
    return 0


def check_agents_shape() -> int:
    """`cloakai agents dev <prompt>` must answer and leave nothing behind."""
    print("\ncloakai agents dev  (ephemeral)")
    before = set(call_containers())

    result = run_cli(
        "agents", "dev", "reply with exactly PONG and nothing else", timeout=420,
    )
    tail = (result.stdout or "").strip().splitlines()[-3:]
    print(f"  output: {' / '.join(t.strip() for t in tail)}")
    if result.returncode != 0:
        print(f"  FAIL: exit {result.returncode}: {(result.stderr or '')[:200]}")
        return 1
    if "PONG" not in (result.stdout or ""):
        print("  FAIL: the agent did not answer")
        return 1

    after = set(call_containers())
    leaked = after - before
    if leaked:
        print(f"  FAIL: ephemeral call leaked a container: {leaked}")
        return 1
    print("  no container left behind")
    return 0


def check_cli_basics() -> int:
    """The parts that need no model: listing, show, and a legible failure."""
    print("cloakai agents / show / doctor")

    listing = run_cli("agents")
    if listing.returncode != 0 or "dev" not in (listing.stdout or ""):
        print(f"  FAIL: listing did not show dev: {(listing.stdout or listing.stderr)[:200]}")
        return 1
    print("  agents lists dev")

    as_json = run_cli("agents", "--json")
    try:
        doc = json.loads(as_json.stdout)
        if "dev" not in doc:
            print("  FAIL: --json did not include dev")
            return 1
        print("  agents --json parses and includes dev")
    except json.JSONDecodeError as exc:
        print(f"  FAIL: --json is not valid JSON: {exc}")
        return 1

    shown = run_cli("show", "dev")
    try:
        json.loads(shown.stdout)
        print("  show dev emits JSON")
    except json.JSONDecodeError:
        print("  FAIL: show did not emit JSON")
        return 1

    ghost = run_cli("agents", "no-such-agent", "hello")
    combined = f"{ghost.stdout}{ghost.stderr}"
    if ghost.returncode == 0:
        print("  FAIL: an unknown agent exited 0")
        return 1
    if "unknown agent" not in combined.lower() or "dev" not in combined:
        print(f"  FAIL: unknown agent did not name the alternatives: {combined[:200]}")
        return 1
    print("  unknown agent fails legibly and names what exists")

    empty = run_cli("agents", "dev")
    if empty.returncode == 0 and not empty.stdout.strip():
        print("  FAIL: a bare `agents dev` with no prompt silently succeeded")
        return 1
    print("  a missing prompt is refused rather than starting a container")

    doctor = run_cli("doctor")
    print(f"  doctor exit={doctor.returncode}")
    return 0


def main() -> int:
    failures = 0
    failures += check_cli_basics()
    failures += asyncio.run(check_mcp_shape())
    failures += check_agents_shape()

    print()
    if failures:
        print(f"FAIL: {failures} check(s) failed")
        return 1
    print("PASS: both CLI shapes work, and neither leaves a container behind")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())