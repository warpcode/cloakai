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
