"""A minimal Jules REST client. v1alpha, no SDK, no retries-with-jitter.

Deliberately tiny: four calls, a key in a header, and nothing else. Everything
the MCP layer needs is in here so the tool definitions above it stay about
protocol rather than about HTTP.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = "https://jules.googleapis.com/v1alpha"


class JulesError(RuntimeError):
    """A Jules API error, already reduced to a message safe to return."""


def api_key_present() -> bool:
    """Whether a key is set, without touching its value."""
    return bool(os.environ.get("JULES_API_KEY", "").strip())


def api_key() -> str:
    """Read the key from the environment.

    It is never logged, never echoed into a tool result, and never baked into the
    image. The container receives it per invocation. If it is missing, say so by
    name only — never print the value, not even its length.
    """
    key = os.environ.get("JULES_API_KEY", "").strip()
    if not key:
        raise JulesError(
            "JULES_API_KEY is not set. Pass it per invocation, for example "
            'JULES_API_KEY=$(...) docker run --rm -e JULES_API_KEY cloakai/jules mcp'
        )
    return key


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{BASE}/{path.lstrip('/')}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("X-Goog-Api-Key", api_key())
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        # Surface Google's message, never our own request, which carries the key
        # in a header. The body of an error is safe; the request is not.
        detail = exc.read().decode(errors="replace")[:500]
        raise JulesError(f"Jules API {exc.code} on {method} {path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise JulesError(f"cannot reach Jules: {exc.reason}") from None
    return json.loads(raw) if raw.strip() else {}


def list_sources() -> list[dict]:
    return _request("GET", "sources").get("sources", [])


def create_session(
    prompt: str,
    source: str | None = None,
    branch: str = "main",
    title: str | None = None,
    require_plan_approval: bool | None = None,
) -> dict:
    """Create a session.

    `source` is optional and that is the whole point: omit it and the session has
    no repository at all, which is a plain sandboxed agent — the isolation model
    with nothing for us to build or enforce.
    """
    payload: dict = {"prompt": prompt}
    if source:
        clean = source if source.startswith("sources/") else f"sources/{source}"
        payload["sourceContext"] = {
            "source": clean,
            "githubRepoContext": {"startingBranch": branch},
        }
    if title:
        payload["title"] = title
    if require_plan_approval is not None:
        payload["requirePlanApproval"] = require_plan_approval
    return _request("POST", "sessions", payload)


def get_session(session_id: str) -> dict:
    return _request("GET", f"sessions/{session_id.strip().removeprefix('sessions/')}")


def send_message(session_id: str, prompt: str) -> dict:
    sid = session_id.strip().removeprefix("sessions/")
    return _request("POST", f"sessions/{sid}:sendMessage", {"prompt": prompt})


def approve_plan(session_id: str, plan_id: str | None = None) -> dict:
    sid = session_id.strip().removeprefix("sessions/")
    return _request("POST", f"sessions/{sid}:approvePlan", {})


def activities(session_id: str, page_size: int = 20) -> list[dict]:
    sid = session_id.strip().removeprefix("sessions/")
    return _request(
        "GET", f"sessions/{sid}/activities?pageSize={page_size}"
    ).get("activities", [])


def timeline(session_id: str, max_pages: int = 20) -> list[dict]:
    """Every activity, oldest first, following pagination to the end.

    The whole timeline is required, not a page of it: judging whether a session
    truly finished means looking for a `sessionCompleted` marker, and concluding
    "no marker" from the first page of a long session would report almost every
    finished session as unfinished.

    `max_pages` is a guard against an unbounded loop, not a silent truncation —
    if it trips, the caller is told the timeline is incomplete and must not treat
    a missing marker as meaningful.
    """
    sid = session_id.strip().removeprefix("sessions/")
    collected: list[dict] = []
    token = ""
    complete = True
    for _ in range(max_pages):
        page = f"sessions/{sid}/activities?pageSize=100"
        if token:
            page += f"&pageToken={token}"
        data = _request("GET", page)
        collected.extend(data.get("activities", []))
        token = data.get("nextPageToken") or ""
        if not token:
            break
    else:
        complete = False
    collected.sort(key=lambda a: a.get("createTime", ""))
    return collected, complete


def _verdict(session: dict[str, Any], timeline: list[dict], complete: bool) -> list[str]:
    """Decide whether a session is actually finished, and say why.

    `state: COMPLETED` does NOT mean finished. Jules sets it when the runner goes
    idle, so a session parked on an unapproved plan reports COMPLETED and looks
    exactly like one that finished cleanly. A client model reading only the state
    would treat a session waiting on a human as done and walk away.

    The distinction is in the timeline, not the state:

      * a `sessionCompleted` marker is the real "this finished" signal;
      * a `planGenerated` with no matching `planApproved` is a plan gate;
      * a trailing `userMessaged` with no later `agentMessaged` is a message the
        agent never answered.

    Field names taken from the live API, not guessed: `planGenerated.plan.id`,
    `planApproved.planId`, `userMessaged.userMessage`, `agentMessaged.agentMessage`,
    and `progressUpdated` also clears a pending plan.
    """
    state = session.get("state") or ""
    pending_plan = ""
    waiting_on_us = False
    completed_marker = False
    unanswered = 0

    for activity in timeline:
        if "planGenerated" in activity:
            pending_plan = (activity["planGenerated"] or {}).get("plan", {}).get("id", "")
        if "planApproved" in activity:
            pending_plan = ""
        if "progressUpdated" in activity:
            pending_plan = ""
        if "sessionCompleted" in activity:
            completed_marker = True
        if "userMessaged" in activity:
            unanswered += 1
        if "agentMessaged" in activity:
            # Any agent reply clears the backlog, same as the reference client.
            unanswered = 0
    waiting_on_us = unanswered > 0

    lines: list[str] = []

    if pending_plan:
        lines.append(
            f"  WAITING: plan '{pending_plan}' is generated but not approved. "
            f"Read it with jules_status, then jules_approve_plan."
        )
    if waiting_on_us:
        lines.append(
            f"  WAITING: {unanswered} message(s) from you have not been answered."
        )

    if state == "COMPLETED":
        if pending_plan or waiting_on_us:
            lines.append(
                "  state=COMPLETED means the runner went IDLE, not that the work "
                "finished. It is waiting on you."
            )
        elif completed_marker:
            lines.append("  finished: the session emitted a sessionCompleted marker.")
        elif complete:
            lines.append(
                "  no sessionCompleted marker, but nothing is pending. Treat as "
                "finished with low confidence; check activities for anything odd."
            )
        else:
            lines.append(
                "  the timeline was truncated, so a missing sessionCompleted marker "
                "means nothing here. Re-check with a higher activity limit."
            )
    return lines
