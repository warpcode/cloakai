"""Jules REST client (v1alpha).

Provides both synchronous and asynchronous interfaces to the Google Jules REST API,
supporting source inspection, session lifecycle, timeline analysis, plan approval,
messaging, nudges, patch extraction, and diagnostic health auditing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://jules.googleapis.com/v1alpha"

NUDGE_TEMPLATES: dict[str, str] = {
    "progress_check": (
        "Could you provide a progress update on the task? "
        "If you are stuck or waiting for input, please let me know."
    ),
    "plan_stalled": (
        "Please provide an implementation plan or progress update on the pending task."
    ),
    "pr_reminder": (
        "Task execution appears complete. "
        "Please ensure changes are committed and a pull request is created."
    ),
}


class JulesError(RuntimeError):
    """A Jules API error, already reduced to a message safe to return."""


def api_key_present(key: str | None = None) -> bool:
    """Whether a key is set, without touching its value."""
    if key and key.strip():
        return True
    return bool(os.environ.get("JULES_API_KEY", "").strip())


def api_key(key: str | None = None) -> str:
    """Read the key from the environment or explicit argument.

    It is never logged, never echoed into a tool result, and never baked into the
    image. The container receives it per invocation. If it is missing, say so by
    name only — never print the value, not even its length.
    """
    resolved = (key or os.environ.get("JULES_API_KEY", "")).strip()
    if not resolved:
        raise JulesError(
            "JULES_API_KEY is not set. Pass it per invocation, for example "
            'JULES_API_KEY=$(...) docker run --rm -e JULES_API_KEY cloakai/jules mcp'
        )
    return resolved


def parse_iso_datetime(iso_str: str) -> datetime:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime."""
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def call(
    method: str,
    endpoint: str,
    payload: dict | None = None,
    query_params: dict | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Execute a restricted REST call relative to the v1alpha endpoint.

    Absolute URLs are rejected to guarantee the API key cannot be leaked to another host.
    Google's response body is surfaced in errors; the request itself is never surfaced.
    """
    if endpoint.startswith(("http://", "https://", "//")):
        raise JulesError("API endpoint must be relative to the configured Jules URL.")

    clean_endpoint = endpoint.strip().lstrip("/")
    url = f"{base_url.rstrip('/')}/{clean_endpoint}"

    if query_params:
        filtered = {k: v for k, v in query_params.items() if v is not None and v != ""}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method.upper())
    request.add_header("X-Goog-Api-Key", api_key(key))
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Surface Google's message, never our own request, which carries the key
        # in a header. The body of an error is safe; the request is not.
        detail = exc.read().decode(errors="replace")[:500]
        raise JulesError(f"Jules API {exc.code} on {method} {endpoint}: {detail}") from None
    except urllib.error.URLError as exc:
        raise JulesError(f"cannot reach Jules: {exc.reason}") from None

    return json.loads(raw) if raw.strip() else {}


def _request(
    method: str,
    path: str,
    payload: dict | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    return call(method, path, payload=payload, key=key, base_url=base_url)


def list_sources(
    page_size: int | None = None,
    page_token: str | None = None,
    filter_expr: str | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> list[dict]:
    params: dict[str, Any] = {}
    if page_size is not None:
        params["pageSize"] = page_size
    if page_token is not None:
        params["pageToken"] = page_token
    if filter_expr is not None:
        params["filter"] = filter_expr
    res = call("GET", "sources", query_params=params, key=key, base_url=base_url)
    return res.get("sources", [])


def get_source(
    source_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    clean = source_id.strip()
    if not clean.startswith("sources/"):
        clean = f"sources/{clean}"
    return call("GET", clean, key=key, base_url=base_url)


def list_sessions(
    page_size: int | None = None,
    page_token: str | None = None,
    filter_expr: str | None = None,
    repo: str | None = None,
    max_age_days: int | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> list[dict]:
    params: dict[str, Any] = {}
    if page_size is not None:
        params["pageSize"] = page_size
    if page_token is not None:
        params["pageToken"] = page_token
    if filter_expr is not None:
        params["filter"] = filter_expr
    res = call("GET", "sessions", query_params=params, key=key, base_url=base_url)
    sessions: list[dict] = res.get("sessions", [])

    if repo and sessions:
        clean_repo = repo.strip().removeprefix("sources/").removeprefix("github/").lower()
        sessions = [
            s for s in sessions
            if clean_repo in (s.get("sourceContext") or {}).get("source", "").removeprefix("sources/").removeprefix("github/").lower()
        ]

    if max_age_days is not None and max_age_days > 0 and sessions:
        now = datetime.now(timezone.utc)
        filtered = []
        for s in sessions:
            ts = s.get("createTime") or s.get("updateTime")
            if ts:
                try:
                    dt = parse_iso_datetime(ts)
                    age_days = (now - dt).total_seconds() / 86400.0
                    if age_days > max_age_days:
                        continue
                except Exception:
                    pass
            filtered.append(s)
        sessions = filtered

    return sessions


def _clean_sid(session_id: str) -> str:
    """Sanitize and URL-encode session_id to prevent API path traversal or injection."""
    sid = str(session_id or "").strip().removeprefix("sessions/").strip()
    if not sid:
        raise JulesError("invalid session_id: empty")
    return urllib.parse.quote(sid, safe="")


def get_session(
    session_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    sid = _clean_sid(session_id)
    return call("GET", f"sessions/{sid}", key=key, base_url=base_url)


def create_session(
    prompt: str,
    source: str | None = None,
    branch: str = "main",
    title: str | None = None,
    require_plan_approval: bool | None = None,
    automation_mode: str = "AUTO_CREATE_PR",
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Create a session.

    `source` is optional and that is the whole point: omit it and the session has
    no repository at all, which is a plain sandboxed agent — the isolation model
    with nothing for us to build or enforce.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if automation_mode:
        payload["automationMode"] = automation_mode
    if source:
        clean = source.strip()
        if not clean.startswith("sources/"):
            clean = f"sources/{clean}"
        payload["sourceContext"] = {
            "source": clean,
            "githubRepoContext": {"startingBranch": branch},
        }
    if title:
        payload["title"] = title
    if require_plan_approval is not None:
        payload["requirePlanApproval"] = require_plan_approval
    return call("POST", "sessions", payload=payload, key=key, base_url=base_url)


def approve_plan(
    session_id: str,
    plan_id: str | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    sid = _clean_sid(session_id)
    payload: dict[str, Any] = {}
    if plan_id:
        payload["planId"] = plan_id
    return call("POST", f"sessions/{sid}:approvePlan", payload=payload, key=key, base_url=base_url)


def send_message(
    session_id: str,
    prompt: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    sid = _clean_sid(session_id)
    return call("POST", f"sessions/{sid}:sendMessage", payload={"prompt": prompt}, key=key, base_url=base_url)


def nudge(
    session_id: str,
    template: str = "progress_check",
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Send a standardized nudge message to an idle or parked session."""
    if template not in NUDGE_TEMPLATES:
        available = ", ".join(sorted(NUDGE_TEMPLATES.keys()))
        raise JulesError(f"Unknown nudge template: '{template}'. Available templates: {available}")
    return send_message(session_id, NUDGE_TEMPLATES[template], key=key, base_url=base_url)


def archive_session(
    session_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Archive a Jules session (reversible).

    Sets the session's `archived` flag and hides it from default listings.
    This is the non-destructive preference for 'archive', 'tidy', 'hide', or 'close out'.
    """
    sid = _clean_sid(session_id)
    return call("POST", f"sessions/{sid}:archive", payload={}, key=key, base_url=base_url)


def unarchive_session(
    session_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Restore a previously archived Jules session to the active listing."""
    sid = _clean_sid(session_id)
    return call("POST", f"sessions/{sid}:unarchive", payload={}, key=key, base_url=base_url)


def delete_session(
    session_id: str,
    confirm: bool = False,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Permanently delete a Jules session.

    IRREVERSIBLE. Requires confirm=True to avoid accidental deletion.
    """
    sid = _clean_sid(session_id)
    if not confirm:
        raise JulesError(
            f"Refusing to permanently delete session {sid}. Deletion is IRREVERSIBLE. "
            "To archive or close out a session, use archive_session instead. "
            "Pass confirm=True if permanent deletion is explicitly intended."
        )
    return call("DELETE", f"sessions/{sid}", key=key, base_url=base_url)


def list_activities(
    session_id: str,
    page_size: int = 20,
    page_token: str | None = None,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    sid = _clean_sid(session_id)
    params: dict[str, Any] = {"pageSize": page_size}
    if page_token:
        params["pageToken"] = page_token
    return call("GET", f"sessions/{sid}/activities", query_params=params, key=key, base_url=base_url)


def activities(
    session_id: str,
    page_size: int = 20,
    key: str | None = None,
    base_url: str = BASE,
) -> list[dict]:
    return list_activities(session_id, page_size=page_size, key=key, base_url=base_url).get("activities", [])


def timeline(
    session_id: str,
    max_pages: int = 20,
    key: str | None = None,
    base_url: str = BASE,
) -> tuple[list[dict], bool]:
    """Every activity, oldest first, following pagination to the end.

    The whole timeline is required, not a page of it: judging whether a session
    truly finished means looking for a `sessionCompleted` marker, and concluding
    "no marker" from the first page of a long session would report almost every
    finished session as unfinished.

    `max_pages` is a guard against an unbounded loop, not a silent truncation —
    if it trips, the caller is told the timeline is incomplete and must not treat
    a missing marker as meaningful.
    """
    sid = _clean_sid(session_id)
    collected: list[dict] = []
    token = ""
    complete = True
    for _ in range(max_pages):
        params: dict[str, Any] = {"pageSize": 100}
        if token:
            params["pageToken"] = token
        data = call("GET", f"sessions/{sid}/activities", query_params=params, key=key, base_url=base_url)
        collected.extend(data.get("activities", []))
        token = data.get("nextPageToken") or ""
        if not token:
            break
    else:
        complete = False
    collected.sort(key=lambda a: a.get("createTime", ""))
    return collected, complete


def get_activity(
    session_id: str,
    activity_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict:
    """Retrieve details of a single activity.

    Supports exact match or prefix match from the full activity list.
    """
    sid = _clean_sid(session_id)
    aid = activity_id.strip().split("/")[-1]
    if len(aid) < 32:
        all_acts, _ = timeline(sid, key=key, base_url=base_url)
        matches = [
            a for a in all_acts
            if (a.get("id") or a.get("name", "").split("/")[-1]).startswith(aid)
        ]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            raise JulesError(f"Ambiguous activity ID prefix '{aid}' matches {len(matches)} activities.")
        elif not matches:
            raise JulesError(f"No activity matching prefix '{aid}' found in session {sid}.")
    return call("GET", f"sessions/{sid}/activities/{aid}", key=key, base_url=base_url)


def extract_patch(
    session_id: str,
    key: str | None = None,
    base_url: str = BASE,
) -> dict[str, Any] | None:
    """Extract unidiffPatch from session outputs or activities.

    Returns {"patch": str, "source": str, "commit_message": str, "session_id": str} or None.
    """
    sid = _clean_sid(session_id)
    session_data = get_session(sid, key=key, base_url=base_url)
    source = (session_data.get("sourceContext") or {}).get("source", "")
    commit_msg = "auto(jules): deliver undelivered patch"
    patch = None

    for out in session_data.get("outputs", []):
        cs = out.get("changeSet") or {}
        git_patch = cs.get("gitPatch") or {}
        if git_patch.get("unidiffPatch"):
            patch = git_patch["unidiffPatch"]
            commit_msg = cs.get("suggestedCommitMessage") or commit_msg
            break

    if not patch:
        try:
            activities_list, _ = timeline(sid, key=key, base_url=base_url)
            for act in reversed(activities_list):
                for art in act.get("artifacts", []):
                    cs = art.get("changeSet") or {}
                    git_patch = cs.get("gitPatch") or {}
                    if git_patch.get("unidiffPatch"):
                        patch = git_patch["unidiffPatch"]
                        commit_msg = cs.get("suggestedCommitMessage") or commit_msg
                        break
                if patch:
                    break
        except Exception:
            pass

    if not patch:
        return None

    return {
        "patch": patch,
        "source": source,
        "commit_message": commit_msg,
        "session_id": sid,
    }


def _verdict(session: dict[str, Any], timeline: list[dict], complete: bool = True) -> list[str]:
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


def audit_session(
    session_data: dict[str, Any],
    stale_threshold_mins: int = 60,
    max_age_days: int = 30,
    key: str | None = None,
    base_url: str = BASE,
) -> dict[str, Any]:
    """Evaluate session state, activities, and return diagnostic metrics including assessment and verdict."""
    sid = session_data.get("id") or session_data.get("name", "").removeprefix("sessions/")
    if "state" not in session_data and sid:
        try:
            session_data = get_session(sid, key=key, base_url=base_url)
        except Exception:
            pass

    raw_title = session_data.get("title") or session_data.get("prompt", "")
    title = " ".join(raw_title.split())[:60]
    state = session_data.get("state", "UNKNOWN")
    source_ctx = session_data.get("sourceContext") or {}
    source_name = source_ctx.get("source", "").removeprefix("sources/")
    branch = (source_ctx.get("githubRepoContext") or {}).get("startingBranch", "")

    created_time = session_data.get("createTime", "")
    updated_time = session_data.get("updateTime", "")

    pr_url = ""
    for out in session_data.get("outputs", []):
        if "pullRequest" in out:
            pr_url = (out["pullRequest"] or {}).get("url", "")
            break

    activities_list: list[dict[str, Any]] = []
    is_timeline_complete = True
    if "activities" in session_data and isinstance(session_data["activities"], list):
        activities_list = session_data["activities"]
    elif sid:
        if state == "COMPLETED" and pr_url:
            activities_list = []
            is_timeline_complete = True
        else:
            try:
                activities_list, is_timeline_complete = timeline(sid, key=key, base_url=base_url)
            except Exception:
                activities_list = []
                is_timeline_complete = False

    now = datetime.now(timezone.utc)
    prompt = session_data.get("prompt", "").strip()

    conversation_history: list[dict[str, Any]] = []
    code_reviews: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    pending_plan_id = ""
    user_message_count = 0
    agent_message_count = 0
    unanswered_user_messages = 0
    last_user_message = ""
    last_user_time = ""
    last_agent_message = ""
    last_agent_time = ""

    if prompt:
        conversation_history.append({
            "role": "user",
            "type": "prompt",
            "time": created_time,
            "id": "initial-prompt",
            "content": prompt,
        })
        user_message_count += 1
        unanswered_user_messages += 1
        last_user_message = prompt
        last_user_time = created_time

    for a in activities_list:
        t = a.get("createTime", "")
        aid = a.get("id", "")

        if "planGenerated" in a:
            p = (a["planGenerated"] or {}).get("plan", {})
            pid = p.get("id", "")
            steps = [s.get("title", "") for s in p.get("steps", [])]
            plans.append({"id": pid, "time": t, "steps": steps})
            pending_plan_id = pid
            conversation_history.append({
                "role": "agent",
                "type": "plan_generated",
                "time": t,
                "id": aid,
                "content": f"Generated plan '{pid}' with {len(steps)} steps.",
                "plan": p,
            })

        if "planApproved" in a:
            pid = (a["planApproved"] or {}).get("planId", "")
            pending_plan_id = ""
            conversation_history.append({
                "role": "user",
                "type": "plan_approved",
                "time": t,
                "id": aid,
                "content": f"Plan '{pid}' approved.",
            })

        if "progressUpdated" in a:
            pending_plan_id = ""
            pu = a["progressUpdated"] or {}
            pu_title = pu.get("title", "")
            pu_desc = pu.get("description", "")
            if "reviewed" in pu_title.lower() or "review" in pu_desc.lower():
                code_reviews.append({"time": t, "title": pu_title, "description": pu_desc})
                conversation_history.append({
                    "role": "agent",
                    "type": "code_review",
                    "time": t,
                    "id": aid,
                    "content": f"Code review: {pu_title}",
                    "review": pu,
                })

        if "userMessaged" in a:
            user_msg = (a["userMessaged"] or {}).get("userMessage", "").strip()
            user_message_count += 1
            unanswered_user_messages += 1
            last_user_message = user_msg
            last_user_time = t
            conversation_history.append({
                "role": "user",
                "type": "message",
                "time": t,
                "id": aid,
                "content": user_msg,
            })

        if "agentMessaged" in a:
            agent_msg = (a["agentMessaged"] or {}).get("agentMessage", "").strip()
            agent_message_count += 1
            unanswered_user_messages = 0
            last_agent_message = agent_msg
            last_agent_time = t
            conversation_history.append({
                "role": "agent",
                "type": "message",
                "time": t,
                "id": aid,
                "content": agent_msg,
            })

        if "sessionCompleted" in a:
            conversation_history.append({
                "role": "system",
                "type": "session_completed",
                "time": t,
                "id": aid,
                "content": "Session completed.",
            })

    last_speaker = conversation_history[-1]["role"] if conversation_history else "none"
    agent_awaiting_reply = False
    if last_speaker == "agent" and last_agent_message:
        lower_msg = last_agent_message.lower()
        if any(q in lower_msg for q in ["confirm", "approve", "proceed", "look good", "?", "let me know", "feedback"]):
            agent_awaiting_reply = True

    latest_activity_time = updated_time
    latest_type = "none"
    latest_detail = ""
    if activities_list:
        latest = activities_list[-1]
        latest_activity_time = latest.get("createTime", updated_time)
        type_keys = [k for k in latest.keys() if k not in ("id", "name", "createTime", "originator")]
        latest_type = type_keys[0] if type_keys else "unknown"
        if "agentMessaged" in latest:
            latest_detail = (latest["agentMessaged"] or {}).get("agentMessage", "")[:120].strip()
        elif "userMessaged" in latest:
            latest_detail = f"User: {(latest['userMessaged'] or {}).get('userMessage', '')[:100].strip()}"
        elif "planGenerated" in latest:
            p = (latest["planGenerated"] or {}).get("plan", {})
            latest_detail = f"Plan: {len(p.get('steps', []))} steps"
        elif "progressUpdated" in latest:
            latest_detail = (latest["progressUpdated"] or {}).get("title", "")
        elif "sessionCompleted" in latest:
            latest_detail = "Session completed"

    inactive_mins = 0.0
    if latest_activity_time:
        try:
            act_dt = parse_iso_datetime(latest_activity_time)
            inactive_mins = max(0.0, (now - act_dt).total_seconds() / 60.0)
        except Exception:
            pass

    created_str = created_time or updated_time
    age_days = 0.0
    if created_str:
        try:
            c_dt = parse_iso_datetime(created_str)
            age_days = max(0.0, (now - c_dt).total_seconds() / 86400.0)
        except Exception:
            pass

    is_inactive = bool(max_age_days is not None and max_age_days > 0 and age_days > max_age_days)

    if is_inactive:
        assessment = "INACTIVE"
    elif pr_url:
        assessment = "COMPLETED_WITH_PR"
    elif state == "AWAITING_PLAN_APPROVAL" or (pending_plan_id and state not in ("FAILED",)):
        assessment = "AWAITING_PLAN_APPROVAL"
    elif state == "AWAITING_USER_FEEDBACK" or (agent_awaiting_reply and state not in ("FAILED",)):
        assessment = "AWAITING_USER_FEEDBACK"
    elif state == "IN_PROGRESS":
        if unanswered_user_messages > 0 and inactive_mins >= 15:
            assessment = "STALLED"
        elif inactive_mins >= stale_threshold_mins:
            assessment = "STALLED"
        else:
            assessment = "ACTIVE"
    elif state == "COMPLETED":
        if unanswered_user_messages > 0:
            assessment = "STALLED"
        else:
            assessment = "COMPLETED_NO_OUTPUT"
    else:
        assessment = state

    verdict_lines = _verdict(session_data, activities_list, is_timeline_complete)

    return {
        "id": sid,
        "title": title,
        "state": state,
        "assessment": assessment,
        "verdict": verdict_lines,
        "source": source_name,
        "branch": branch,
        "created_time": created_time,
        "updated_time": updated_time,
        "latest_activity_time": latest_activity_time,
        "inactive_mins": round(inactive_mins, 1),
        "age_days": round(age_days, 1),
        "is_inactive": is_inactive,
        "activity_count": len(activities_list),
        "latest_type": latest_type,
        "latest_detail": latest_detail,
        "pending_plan_id": pending_plan_id,
        "pull_request_url": pr_url,
        "conversation_history": conversation_history,
        "user_message_count": user_message_count,
        "agent_message_count": agent_message_count,
        "unanswered_user_messages": unanswered_user_messages,
        "agent_awaiting_reply": agent_awaiting_reply,
        "last_speaker": last_speaker,
        "last_user_message": last_user_message,
        "last_user_time": last_user_time,
        "last_agent_message": last_agent_message,
        "last_agent_time": last_agent_time,
        "code_reviews": code_reviews,
        "plans": plans,
    }


def audit_sessions(
    sessions: list[dict[str, Any]],
    stale_threshold_mins: int = 60,
    max_age_days: int = 30,
    key: str | None = None,
    base_url: str = BASE,
    max_workers: int = 10,
) -> list[dict[str, Any]]:
    """Audit multiple sessions concurrently."""
    active_sessions = [s for s in sessions if s.get("archived") is not True]
    if not active_sessions:
        return []

    from concurrent.futures import ThreadPoolExecutor

    def _audit(s: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return audit_session(
                s,
                stale_threshold_mins=stale_threshold_mins,
                max_age_days=max_age_days,
                key=key,
                base_url=base_url,
            )
        except Exception:
            return None

    workers = min(max_workers, len(active_sessions))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(_audit, active_sessions))

    return [r for r in results if r is not None]


class JulesClient:
    """Synchronous Jules REST API client (v1alpha)."""

    def __init__(self, api_key: str | None = None, base_url: str = BASE) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")

    def api_key_present(self) -> bool:
        return api_key_present(self._key)

    def api_key(self) -> str:
        return api_key(self._key)

    def call(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        query_params: dict | None = None,
    ) -> dict:
        return call(method, endpoint, payload=payload, query_params=query_params, key=self._key, base_url=self.base_url)

    def list_sources(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter_expr: str | None = None,
    ) -> list[dict]:
        return list_sources(page_size=page_size, page_token=page_token, filter_expr=filter_expr, key=self._key, base_url=self.base_url)

    def get_source(self, source_id: str) -> dict:
        return get_source(source_id, key=self._key, base_url=self.base_url)

    def list_sessions(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter_expr: str | None = None,
        repo: str | None = None,
        max_age_days: int | None = None,
    ) -> list[dict]:
        return list_sessions(
            page_size=page_size,
            page_token=page_token,
            filter_expr=filter_expr,
            repo=repo,
            max_age_days=max_age_days,
            key=self._key,
            base_url=self.base_url,
        )

    def get_session(self, session_id: str) -> dict:
        return get_session(session_id, key=self._key, base_url=self.base_url)

    def create_session(
        self,
        prompt: str,
        source: str | None = None,
        branch: str = "main",
        title: str | None = None,
        require_plan_approval: bool | None = None,
        automation_mode: str = "AUTO_CREATE_PR",
    ) -> dict:
        return create_session(
            prompt=prompt,
            source=source,
            branch=branch,
            title=title,
            require_plan_approval=require_plan_approval,
            automation_mode=automation_mode,
            key=self._key,
            base_url=self.base_url,
        )

    def approve_plan(self, session_id: str, plan_id: str | None = None) -> dict:
        return approve_plan(session_id, plan_id=plan_id, key=self._key, base_url=self.base_url)

    def send_message(self, session_id: str, prompt: str) -> dict:
        return send_message(session_id, prompt, key=self._key, base_url=self.base_url)

    def nudge(self, session_id: str, template: str = "progress_check") -> dict:
        return nudge(session_id, template=template, key=self._key, base_url=self.base_url)

    def archive_session(self, session_id: str) -> dict:
        return archive_session(session_id, key=self._key, base_url=self.base_url)

    def unarchive_session(self, session_id: str) -> dict:
        return unarchive_session(session_id, key=self._key, base_url=self.base_url)

    def delete_session(self, session_id: str, confirm: bool = False) -> dict:
        return delete_session(session_id, confirm=confirm, key=self._key, base_url=self.base_url)

    def list_activities(
        self,
        session_id: str,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict:
        return list_activities(session_id, page_size=page_size, page_token=page_token, key=self._key, base_url=self.base_url)

    def activities(self, session_id: str, page_size: int = 20) -> list[dict]:
        return activities(session_id, page_size=page_size, key=self._key, base_url=self.base_url)

    def get_activity(self, session_id: str, activity_id: str) -> dict:
        return get_activity(session_id, activity_id, key=self._key, base_url=self.base_url)

    def timeline(self, session_id: str, max_pages: int = 20) -> tuple[list[dict], bool]:
        return timeline(session_id, max_pages=max_pages, key=self._key, base_url=self.base_url)

    def extract_patch(self, session_id: str) -> dict[str, Any] | None:
        return extract_patch(session_id, key=self._key, base_url=self.base_url)

    def audit_session(
        self,
        session_data: dict[str, Any],
        stale_threshold_mins: int = 60,
        max_age_days: int = 30,
    ) -> dict[str, Any]:
        return audit_session(
            session_data,
            stale_threshold_mins=stale_threshold_mins,
            max_age_days=max_age_days,
            key=self._key,
            base_url=self.base_url,
        )

    def audit_sessions(
        self,
        sessions: list[dict[str, Any]],
        stale_threshold_mins: int = 60,
        max_age_days: int = 30,
        max_workers: int = 10,
    ) -> list[dict[str, Any]]:
        active_sessions = [s for s in sessions if s.get("archived") is not True]
        if not active_sessions:
            return []

        from concurrent.futures import ThreadPoolExecutor

        def _audit(s: dict[str, Any]) -> dict[str, Any] | None:
            try:
                return self.audit_session(
                    s,
                    stale_threshold_mins=stale_threshold_mins,
                    max_age_days=max_age_days,
                )
            except Exception:
                return None

        workers = min(max_workers, len(active_sessions))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_audit, active_sessions))

        return [r for r in results if r is not None]


class AsyncJulesClient:
    """Asynchronous Jules REST client for FastAPI or async event loops.

    Uses httpx.AsyncClient if available; falls back to non-blocking execution via
    asyncio.to_thread wrapping urllib.request without blocking the event loop.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE,
        http_client: Any | None = None,
    ) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._sync_client = JulesClient(api_key=api_key, base_url=base_url)
        self._http_client = http_client
        self._owned_http_client = False

    def api_key_present(self) -> bool:
        return self._sync_client.api_key_present()

    def api_key(self) -> str:
        return self._sync_client.api_key()

    async def __aenter__(self) -> AsyncJulesClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._http_client is not None and hasattr(self._http_client, "aclose"):
            await self._http_client.aclose()
            if self._owned_http_client:
                self._http_client = None

    async def call(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        query_params: dict | None = None,
    ) -> dict:
        if endpoint.startswith(("http://", "https://", "//")):
            raise JulesError("API endpoint must be relative to the configured Jules URL.")

        try:
            import httpx  # type: ignore

            if self._http_client is None:
                self._http_client = httpx.AsyncClient()
                self._owned_http_client = True

            if isinstance(self._http_client, httpx.AsyncClient):
                clean_endpoint = endpoint.strip().lstrip("/")
                url = f"{self.base_url}/{clean_endpoint}"
                filtered = {k: v for k, v in (query_params or {}).items() if v is not None and v != ""}
                headers = {
                    "X-Goog-Api-Key": self.api_key(),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                resp = await self._http_client.request(
                    method=method.upper(),
                    url=url,
                    json=payload if payload is not None else None,
                    params=filtered if filtered else None,
                    headers=headers,
                    timeout=60.0,
                )
                if resp.status_code >= 400:
                    detail = resp.text[:500]
                    raise JulesError(f"Jules API {resp.status_code} on {method} {endpoint}: {detail}")
                return resp.json() if resp.text.strip() else {}
        except ImportError:
            pass

        return await asyncio.to_thread(self._sync_client.call, method, endpoint, payload, query_params)

    async def list_sources(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter_expr: str | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_token is not None:
            params["pageToken"] = page_token
        if filter_expr is not None:
            params["filter"] = filter_expr
        res = await self.call("GET", "sources", query_params=params)
        return res.get("sources", [])

    async def get_source(self, source_id: str) -> dict:
        clean = source_id.strip()
        if not clean.startswith("sources/"):
            clean = f"sources/{clean}"
        return await self.call("GET", clean)

    async def list_sessions(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter_expr: str | None = None,
        repo: str | None = None,
        max_age_days: int | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_token is not None:
            params["pageToken"] = page_token
        if filter_expr is not None:
            params["filter"] = filter_expr
        res = await self.call("GET", "sessions", query_params=params)
        sessions: list[dict] = res.get("sessions", [])

        if repo and sessions:
            clean_repo = repo.strip().removeprefix("sources/").removeprefix("github/").lower()
            sessions = [
                s for s in sessions
                if clean_repo in (s.get("sourceContext") or {}).get("source", "").removeprefix("sources/").removeprefix("github/").lower()
            ]

        if max_age_days is not None and max_age_days > 0 and sessions:
            now = datetime.now(timezone.utc)
            filtered = []
            for s in sessions:
                ts = s.get("createTime") or s.get("updateTime")
                if ts:
                    try:
                        dt = parse_iso_datetime(ts)
                        age_days = (now - dt).total_seconds() / 86400.0
                        if age_days > max_age_days:
                            continue
                    except Exception:
                        pass
                filtered.append(s)
            sessions = filtered

        return sessions

    async def get_session(self, session_id: str) -> dict:
        sid = _clean_sid(session_id)
        return await self.call("GET", f"sessions/{sid}")

    async def create_session(
        self,
        prompt: str,
        source: str | None = None,
        branch: str = "main",
        title: str | None = None,
        require_plan_approval: bool | None = None,
        automation_mode: str = "AUTO_CREATE_PR",
    ) -> dict:
        payload: dict[str, Any] = {"prompt": prompt}
        if automation_mode:
            payload["automationMode"] = automation_mode
        if source:
            clean = source.strip()
            if not clean.startswith("sources/"):
                clean = f"sources/{clean}"
            payload["sourceContext"] = {
                "source": clean,
                "githubRepoContext": {"startingBranch": branch},
            }
        if title:
            payload["title"] = title
        if require_plan_approval is not None:
            payload["requirePlanApproval"] = require_plan_approval
        return await self.call("POST", "sessions", payload=payload)

    async def approve_plan(self, session_id: str, plan_id: str | None = None) -> dict:
        sid = _clean_sid(session_id)
        payload: dict[str, Any] = {}
        if plan_id:
            payload["planId"] = plan_id
        return await self.call("POST", f"sessions/{sid}:approvePlan", payload=payload)

    async def send_message(self, session_id: str, prompt: str) -> dict:
        sid = _clean_sid(session_id)
        return await self.call("POST", f"sessions/{sid}:sendMessage", payload={"prompt": prompt})

    async def nudge(self, session_id: str, template: str = "progress_check") -> dict:
        if template not in NUDGE_TEMPLATES:
            available = ", ".join(sorted(NUDGE_TEMPLATES.keys()))
            raise JulesError(f"Unknown nudge template: '{template}'. Available templates: {available}")
        return await self.send_message(session_id, NUDGE_TEMPLATES[template])

    async def archive_session(self, session_id: str) -> dict:
        sid = _clean_sid(session_id)
        return await self.call("POST", f"sessions/{sid}:archive", payload={})

    async def unarchive_session(self, session_id: str) -> dict:
        sid = _clean_sid(session_id)
        return await self.call("POST", f"sessions/{sid}:unarchive", payload={})

    async def delete_session(self, session_id: str, confirm: bool = False) -> dict:
        sid = _clean_sid(session_id)
        if not confirm:
            raise JulesError(
                f"Refusing to permanently delete session {sid}. Deletion is IRREVERSIBLE. "
                "To archive or close out a session, use archive_session instead. "
                "Pass confirm=True if permanent deletion is explicitly intended."
            )
        return await self.call("DELETE", f"sessions/{sid}")

    async def list_activities(
        self,
        session_id: str,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict:
        sid = _clean_sid(session_id)
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return await self.call("GET", f"sessions/{sid}/activities", query_params=params)

    async def activities(self, session_id: str, page_size: int = 20) -> list[dict]:
        res = await self.list_activities(session_id, page_size=page_size)
        return res.get("activities", [])

    async def get_activity(self, session_id: str, activity_id: str) -> dict:
        sid = _clean_sid(session_id)
        aid = activity_id.strip().split("/")[-1]
        if len(aid) < 32:
            all_acts, _ = await self.timeline(sid)
            matches = [
                a for a in all_acts
                if (a.get("id") or a.get("name", "").split("/")[-1]).startswith(aid)
            ]
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                raise JulesError(f"Ambiguous activity ID prefix '{aid}' matches {len(matches)} activities.")
            elif not matches:
                raise JulesError(f"No activity matching prefix '{aid}' found in session {sid}.")
        return await self.call("GET", f"sessions/{sid}/activities/{aid}")

    async def timeline(self, session_id: str, max_pages: int = 20) -> tuple[list[dict], bool]:
        sid = _clean_sid(session_id)
        collected: list[dict] = []
        token = ""
        complete = True
        for _ in range(max_pages):
            params: dict[str, Any] = {"pageSize": 100}
            if token:
                params["pageToken"] = token
            data = await self.call("GET", f"sessions/{sid}/activities", query_params=params)
            collected.extend(data.get("activities", []))
            token = data.get("nextPageToken") or ""
            if not token:
                break
        else:
            complete = False
        collected.sort(key=lambda a: a.get("createTime", ""))
        return collected, complete

    async def extract_patch(self, session_id: str) -> dict[str, Any] | None:
        sid = _clean_sid(session_id)
        session_data = await self.get_session(sid)
        source = (session_data.get("sourceContext") or {}).get("source", "")
        commit_msg = "auto(jules): deliver undelivered patch"
        patch = None

        for out in session_data.get("outputs", []):
            cs = out.get("changeSet") or {}
            git_patch = cs.get("gitPatch") or {}
            if git_patch.get("unidiffPatch"):
                patch = git_patch["unidiffPatch"]
                commit_msg = cs.get("suggestedCommitMessage") or commit_msg
                break

        if not patch:
            try:
                activities_list, _ = await self.timeline(sid)
                for act in reversed(activities_list):
                    for art in act.get("artifacts", []):
                        cs = art.get("changeSet") or {}
                        git_patch = cs.get("gitPatch") or {}
                        if git_patch.get("unidiffPatch"):
                            patch = git_patch["unidiffPatch"]
                            commit_msg = cs.get("suggestedCommitMessage") or commit_msg
                            break
                    if patch:
                        break
            except Exception:
                pass

        if not patch:
            return None

        return {
            "patch": patch,
            "source": source,
            "commit_message": commit_msg,
            "session_id": sid,
        }

    async def audit_session(
        self,
        session_data: dict[str, Any],
        stale_threshold_mins: int = 60,
        max_age_days: int = 30,
    ) -> dict[str, Any]:
        sid = session_data.get("id") or session_data.get("name", "").removeprefix("sessions/")
        if "state" not in session_data and sid:
            try:
                session_data = await self.get_session(sid)
            except Exception:
                pass

        raw_title = session_data.get("title") or session_data.get("prompt", "")
        title = " ".join(raw_title.split())[:60]
        state = session_data.get("state", "UNKNOWN")
        source_ctx = session_data.get("sourceContext") or {}
        source_name = source_ctx.get("source", "").removeprefix("sources/")
        branch = (source_ctx.get("githubRepoContext") or {}).get("startingBranch", "")

        created_time = session_data.get("createTime", "")
        updated_time = session_data.get("updateTime", "")

        pr_url = ""
        for out in session_data.get("outputs", []):
            if "pullRequest" in out:
                pr_url = (out["pullRequest"] or {}).get("url", "")
                break

        activities_list: list[dict[str, Any]] = []
        is_timeline_complete = True
        if "activities" in session_data and isinstance(session_data["activities"], list):
            activities_list = session_data["activities"]
        elif sid:
            if state == "COMPLETED" and pr_url:
                activities_list = []
                is_timeline_complete = True
            else:
                try:
                    activities_list, is_timeline_complete = await self.timeline(sid)
                except Exception:
                    activities_list = []
                    is_timeline_complete = False

        now = datetime.now(timezone.utc)
        prompt = session_data.get("prompt", "").strip()

        conversation_history: list[dict[str, Any]] = []
        code_reviews: list[dict[str, Any]] = []
        plans: list[dict[str, Any]] = []
        pending_plan_id = ""
        user_message_count = 0
        agent_message_count = 0
        unanswered_user_messages = 0
        last_user_message = ""
        last_user_time = ""
        last_agent_message = ""
        last_agent_time = ""

        if prompt:
            conversation_history.append({
                "role": "user",
                "type": "prompt",
                "time": created_time,
                "id": "initial-prompt",
                "content": prompt,
            })
            user_message_count += 1
            unanswered_user_messages += 1
            last_user_message = prompt
            last_user_time = created_time

        for a in activities_list:
            t = a.get("createTime", "")
            aid = a.get("id", "")

            if "planGenerated" in a:
                p = (a["planGenerated"] or {}).get("plan", {})
                pid = p.get("id", "")
                steps = [s.get("title", "") for s in p.get("steps", [])]
                plans.append({"id": pid, "time": t, "steps": steps})
                pending_plan_id = pid
                conversation_history.append({
                    "role": "agent",
                    "type": "plan_generated",
                    "time": t,
                    "id": aid,
                    "content": f"Generated plan '{pid}' with {len(steps)} steps.",
                    "plan": p,
                })

            if "planApproved" in a:
                pid = (a["planApproved"] or {}).get("planId", "")
                pending_plan_id = ""
                conversation_history.append({
                    "role": "user",
                    "type": "plan_approved",
                    "time": t,
                    "id": aid,
                    "content": f"Plan '{pid}' approved.",
                })

            if "progressUpdated" in a:
                pending_plan_id = ""
                pu = a["progressUpdated"] or {}
                pu_title = pu.get("title", "")
                pu_desc = pu.get("description", "")
                if "reviewed" in pu_title.lower() or "review" in pu_desc.lower():
                    code_reviews.append({"time": t, "title": pu_title, "description": pu_desc})
                    conversation_history.append({
                        "role": "agent",
                        "type": "code_review",
                        "time": t,
                        "id": aid,
                        "content": f"Code review: {pu_title}",
                        "review": pu,
                    })

            if "userMessaged" in a:
                user_msg = (a["userMessaged"] or {}).get("userMessage", "").strip()
                user_message_count += 1
                unanswered_user_messages += 1
                last_user_message = user_msg
                last_user_time = t
                conversation_history.append({
                    "role": "user",
                    "type": "message",
                    "time": t,
                    "id": aid,
                    "content": user_msg,
                })

            if "agentMessaged" in a:
                agent_msg = (a["agentMessaged"] or {}).get("agentMessage", "").strip()
                agent_message_count += 1
                unanswered_user_messages = 0
                last_agent_message = agent_msg
                last_agent_time = t
                conversation_history.append({
                    "role": "agent",
                    "type": "message",
                    "time": t,
                    "id": aid,
                    "content": agent_msg,
                })

            if "sessionCompleted" in a:
                conversation_history.append({
                    "role": "system",
                    "type": "session_completed",
                    "time": t,
                    "id": aid,
                    "content": "Session completed.",
                })

        last_speaker = conversation_history[-1]["role"] if conversation_history else "none"
        agent_awaiting_reply = False
        if last_speaker == "agent" and last_agent_message:
            lower_msg = last_agent_message.lower()
            if any(q in lower_msg for q in ["confirm", "approve", "proceed", "look good", "?", "let me know", "feedback"]):
                agent_awaiting_reply = True

        latest_activity_time = updated_time
        latest_type = "none"
        latest_detail = ""
        if activities_list:
            latest = activities_list[-1]
            latest_activity_time = latest.get("createTime", updated_time)
            type_keys = [k for k in latest.keys() if k not in ("id", "name", "createTime", "originator")]
            latest_type = type_keys[0] if type_keys else "unknown"
            if "agentMessaged" in latest:
                latest_detail = (latest["agentMessaged"] or {}).get("agentMessage", "")[:120].strip()
            elif "userMessaged" in latest:
                latest_detail = f"User: {(latest['userMessaged'] or {}).get('userMessage', '')[:100].strip()}"
            elif "planGenerated" in latest:
                p = (latest["planGenerated"] or {}).get("plan", {})
                latest_detail = f"Plan: {len(p.get('steps', []))} steps"
            elif "progressUpdated" in latest:
                latest_detail = (latest["progressUpdated"] or {}).get("title", "")
            elif "sessionCompleted" in latest:
                latest_detail = "Session completed"

        inactive_mins = 0.0
        if latest_activity_time:
            try:
                act_dt = parse_iso_datetime(latest_activity_time)
                inactive_mins = max(0.0, (now - act_dt).total_seconds() / 60.0)
            except Exception:
                pass

        created_str = created_time or updated_time
        age_days = 0.0
        if created_str:
            try:
                c_dt = parse_iso_datetime(created_str)
                age_days = max(0.0, (now - c_dt).total_seconds() / 86400.0)
            except Exception:
                pass

        is_inactive = bool(max_age_days is not None and max_age_days > 0 and age_days > max_age_days)

        if is_inactive:
            assessment = "INACTIVE"
        elif pr_url:
            assessment = "COMPLETED_WITH_PR"
        elif state == "AWAITING_PLAN_APPROVAL" or (pending_plan_id and state not in ("FAILED",)):
            assessment = "AWAITING_PLAN_APPROVAL"
        elif state == "AWAITING_USER_FEEDBACK" or (agent_awaiting_reply and state not in ("FAILED",)):
            assessment = "AWAITING_USER_FEEDBACK"
        elif state == "IN_PROGRESS":
            if unanswered_user_messages > 0 and inactive_mins >= 15:
                assessment = "STALLED"
            elif inactive_mins >= stale_threshold_mins:
                assessment = "STALLED"
            else:
                assessment = "ACTIVE"
        elif state == "COMPLETED":
            if unanswered_user_messages > 0:
                assessment = "STALLED"
            else:
                assessment = "COMPLETED_NO_OUTPUT"
        else:
            assessment = state

        verdict_lines = _verdict(session_data, activities_list, is_timeline_complete)

        return {
            "id": sid,
            "title": title,
            "state": state,
            "assessment": assessment,
            "verdict": verdict_lines,
            "source": source_name,
            "branch": branch,
            "created_time": created_time,
            "updated_time": updated_time,
            "latest_activity_time": latest_activity_time,
            "inactive_mins": round(inactive_mins, 1),
            "age_days": round(age_days, 1),
            "is_inactive": is_inactive,
            "activity_count": len(activities_list),
            "latest_type": latest_type,
            "latest_detail": latest_detail,
            "pending_plan_id": pending_plan_id,
            "pull_request_url": pr_url,
            "conversation_history": conversation_history,
            "user_message_count": user_message_count,
            "agent_message_count": agent_message_count,
            "unanswered_user_messages": unanswered_user_messages,
            "agent_awaiting_reply": agent_awaiting_reply,
            "last_speaker": last_speaker,
            "last_user_message": last_user_message,
            "last_user_time": last_user_time,
            "last_agent_message": last_agent_message,
            "last_agent_time": last_agent_time,
            "code_reviews": code_reviews,
            "plans": plans,
        }

    async def audit_sessions(
        self,
        sessions: list[dict[str, Any]],
        stale_threshold_mins: int = 60,
        max_age_days: int = 30,
        max_concurrency: int = 10,
    ) -> list[dict[str, Any]]:
        active_sessions = [s for s in sessions if s.get("archived") is not True]
        if not active_sessions:
            return []

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def _audit(s: dict[str, Any]) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    return await self.audit_session(
                        s,
                        stale_threshold_mins=stale_threshold_mins,
                        max_age_days=max_age_days,
                    )
                except Exception:
                    return None

        results = await asyncio.gather(*[_audit(s) for s in active_sessions])
        return [r for r in results if r is not None]
