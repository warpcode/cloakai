"""Jules as an MCP server over stdio.

The whole point of this image is that it obeys the same contract as every other
cloakai image:

    docker run --rm cloakai/jules mcp

No gateway, no port, no network of ours. Jules is an API-driven autonomous agent
system where every session is its own sandboxed agent, so this image is a thin
adapter: four HTTP calls wrapped as four tools.

Why four tools and not one, when the gateway model is one tool per agent? Because
a Jules session is not a `docker run`. It outlives the call that created it, it
can park on a plan-approval gate for 20-30 minutes, and its own API documents that
`COMPLETED` does not mean terminal. A single blocking tool would have to either
time out or report "done" for a session that is actually waiting for a human.

So `start` returns a session id and the client drives it from there — which is
what "interact through API messaging" means anyway.

Two deliberate constraints:

  * The API key is read per invocation and never logged, never echoed, and never
    baked into the image. JulesError carries Google's response, never our request,
    because our request carries the key in a header.
  * The key is never passed into a session's environmentVariables, which the Jules
    API supports, because that would push the credential into the sandbox and into
    Google's session log.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import jules_client as jules
from .jules_client import _verdict

mcp = MCPServer("cloakai-jules")

#: State values Jules reports that mean "still waiting on a human", not "finished".
#: The API is explicit that idle is not terminal, so a client that reads `COMPLETED`
#: as done will strand a session that is parked on a plan gate.
GATED = {"PLAN_PENDING", "AWAITING_PLAN_APPROVAL", "FEEDBACK_PENDING", "AWAITING_FEEDBACK"}


def _fail(exc: Exception) -> str:
    return f"error: {exc}"


def _session_summary(session: dict[str, Any]) -> str:
    """A compact, honest view of a session.

    Reports the state AND whether it is parked, because those are different and
    conflating them is how a session gets abandoned mid-plan.
    """
    name = session.get("name", "?")
    # The create response has no state field yet — the session exists but has not
    # been scheduled. Printing "UNKNOWN" there reads like an error, and is the
    # first thing a client sees, so say what it actually means.
    state = session.get("state") or "STARTING"
    lines = [f"session {name.removeprefix('sessions/')}  state={state}"]

    if state in GATED or state == "STARTING":
        lines.append(
            "  PARKED: waiting on a human. Approve the plan or send guidance, "
            "then check again. Do not treat this as finished."
        )

    outputs = session.get("outputs") or []
    for output in outputs:
        pr = output.get("pullRequest")
        if pr:
            url = pr.get("url") or (f"{pr.get('url', '').rsplit('/pull/', 1)}"
                                    if pr.get("url") else "")
            lines.append(f"  pull request: {url}")
        elif output.get("url"):
            lines.append(f"  output: {output['url']}")

    source = (session.get("sourceContext") or {}).get("source")
    lines.append(f"  source: {source or 'none (project-less sandbox)'}")
    return "\n".join(lines)


#: Activities are a union type keyed by a present field, not a field called
#: "type". Guessing `type`/`kind` printed the literal word "activity" for every
#: row, which told the caller nothing at all.
_ACTIVITY_FIELDS = (
    ("agentMessaged", "agent message"),
    ("planGenerated", "plan generated"),
    ("planApproved", "plan approved"),
    ("userMessaged", "you said"),
    ("progressUpdated", "progress"),
)


def _describe(activity: dict[str, Any]) -> tuple[str, str]:
    """A human label and a short detail for one activity."""
    for field, label in _ACTIVITY_FIELDS:
        if field not in activity:
            continue
        payload = activity[field] or {}
        text = (payload.get("agentMessage") or payload.get("prompt")
                or payload.get("message") or "")
        text = " ".join(str(text).split())
        if len(text) > 160:
            text = text[:157] + "..."
        return label, text
    # An unrecognised activity is still worth showing by its own key, so a new
    # Jules event type is visible rather than silently dropped.
    unknown = [k for k in sorted(activity)
               if k.endswith("d") and k not in ("id", "name", "createTime")]
    return (unknown[0] if unknown else "activity"), ""


@mcp.tool(
    name="jules_start",
    description="Start a Jules session, which is its own sandboxed agent. Returns "
                "a session id to drive it with jules_send and jules_status. Omit "
                "`source` for a project-less sandbox with no repository; pass a "
                "repo like 'github/owner/name' to work on one. Each session counts "
                "against a small per-account concurrency limit, so start one and "
                "reuse it rather than starting several.",
)
def jules_start(
    prompt: str,
    source: str | None = None,
    branch: str = "main",
    title: str | None = None,
    require_plan_approval: bool = False,
) -> str:
    try:
        session = jules.create_session(
            prompt, source=source, branch=branch, title=title,
            require_plan_approval=require_plan_approval,
        )
    except Exception as exc:
        return _fail(exc)
    return _session_summary(session)


@mcp.tool(
    name="jules_send",
    description="Send a message to an existing Jules session: answer a question, "
                "steer the work, or ask for progress. Resumes a parked session.",
)
def jules_send(session_id: str, prompt: str) -> str:
    try:
        jules.send_message(session_id, prompt)
    except Exception as exc:
        return _fail(exc)
    return f"sent to {session_id}. It is running again; check with jules_status."


@mcp.tool(
    name="jules_status",
    description="Report a Jules session's state, whether it is parked waiting on a "
                "human, any pull request, and its recent activity. Note that Jules "
                "reports COMPLETED when a runner goes idle, so a session can look "
                "finished and still be waiting for you.",
)
def jules_status(session_id: str, activity_limit: int = 10) -> str:
    try:
        session = jules.get_session(session_id)
    except Exception as exc:
        return _fail(exc)
    out = [_session_summary(session)]

    # The full timeline first, because the verdict needs it. A page of it cannot
    # tell "finished" from "never looked far enough back".
    try:
        timeline, complete = jules.timeline(session_id)
        out.extend(_verdict(session, timeline, complete))
        for activity in timeline[-activity_limit:]:
            label, detail = _describe(activity)
            out.append(f"  - {label}" + (f": {detail}" if detail else ""))
    except Exception as exc:
        # Status is still useful without the timeline, so this degrades rather
        # than discarding the part that worked.
        out.append(f"  (activity timeline unavailable: {exc})")
    return "\n".join(out)


@mcp.tool(
    name="jules_approve_plan",
    description="Approve a Jules session's pending implementation plan. Only use "
                "after reading it with jules_status; approving resumes the run.",
)
def jules_approve_plan(session_id: str, plan_id: str | None = None) -> str:
    try:
        jules.approve_plan(session_id, plan_id)
    except Exception as exc:
        return _fail(exc)
    return f"plan approved on {session_id}; the session is running again."


@mcp.tool(
    name="jules_sources",
    description="List the GitHub repositories Jules is authorised to work on.",
)
def jules_sources() -> str:
    try:
        sources = jules.list_sources()
    except Exception as exc:
        return _fail(exc)
    if not sources:
        return "no sources are connected to this Jules account."
    lines = []
    for source in sorted(sources, key=lambda s: s.get("name", "")):
        # The branch is nested and wrapped in an object: a flat "defaultBranch"
        # guess printed "?" for every repo, which reads like missing data rather
        # than a wrong field name.
        repo = source.get("githubRepo") or {}
        branch = (repo.get("defaultBranch") or {}).get("displayName", "?")
        count = len(repo.get("branches") or [])
        name = source.get("id") or (source.get("name", "").removeprefix("sources/"))
        lines.append(f"- {name}  (default branch: {branch}, {count} branch(es))")
    return "\n".join(lines)


def main() -> int:
    if not jules.api_key_present():
        # Fail at startup rather than on first call: a server that lists tools it
        # cannot serve is worse than one that refuses to start.
        print(
            "error: JULES_API_KEY is not set. Pass it per invocation:\n"
            "  docker run --rm -e JULES_API_KEY cloakai/jules mcp",
            file=sys.stderr,
        )
        return 2
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
