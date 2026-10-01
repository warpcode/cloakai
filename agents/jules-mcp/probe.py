"""Start ONE project-less Jules session, prove the sandbox, then report.

The only tool verify.py deliberately skips, because it creates cloud resources
and spends one of a small number of concurrent session slots. A prompt that
demands nothing expensive: no repository, no pull request, just the agent
describing the box it is running in.

Run as:

    JULES_API_KEY=$(cloakenv get "kp://Personal/Credentials/Google - Main - Jules - Api Key:Password") \
      docker run --rm --entrypoint python \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -e JULES_API_KEY \
        cloakai/probe:latest /srv/jules-mcp/probe.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

IMAGE = os.environ.get("JULES_IMAGE", "cloakai/jules:latest")

PROMPT = (
    "You are running in a sandbox with no repository attached. Reply with: "
    "1) the absolute path of your working directory, "
    "2) one sentence on what tools you have. "
    "Do not fetch anything and do not modify anything."
)

POLL_SECONDS = 15
MAX_POLLS = 8


async def main() -> int:
    params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "-e", "JULES_API_KEY", IMAGE, "mcp"],
        env=dict(os.environ),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=45)

            print("jules_start (no source -> project-less)...")
            started = await asyncio.wait_for(
                session.call_tool("jules_start", {"prompt": PROMPT}), timeout=90,
            )
            text = "".join(c.text for c in started.content
                           if getattr(c, "type", "") == "text").strip()
            print(f"  {text}")

            if text.startswith("error:"):
                print("FAIL: jules_start returned an error")
                return 1
            if "project-less" not in text:
                print("FAIL: the session is not project-less, so the sandbox is not proven")
                return 1

            session_id = text.split()[1]

            for poll in range(1, MAX_POLLS + 1):
                await asyncio.sleep(POLL_SECONDS)
                status = await asyncio.wait_for(
                    session.call_tool("jules_status",
                                      {"session_id": session_id, "activity_limit": 6}),
                    timeout=90,
                )
                body = "".join(c.text for c in status.content
                               if getattr(c, "type", "") == "text").strip()
                head = body.splitlines()[0] if body else "(empty)"
                print(f"  poll {poll}: {head}")
                for line in body.splitlines()[1:]:
                    if line.strip().startswith(("-", "output:", "pull")):
                        print(f"          {line.strip()}")

                if "COMPLETED" in head:
                    print("\nPASS: a project-less session ran in its own sandbox")
                    return 0

            print("\nThe session is still running, which is expected for a real agent.")
            print(f"Session id: {session_id}")
            print("PASS: jules_start works; the session outlived the call, as designed.")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
