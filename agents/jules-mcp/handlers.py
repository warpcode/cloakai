"""Request handlers, tool dispatching, and response formatting for Jules MCP server."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine

from pydantic import ValidationError

try:
    from . import jules_client as jules
    from . import prompts
    from .jules_client import AsyncJulesClient, JulesError
    from .prompts import get_prompt, list_prompts
    from .protocol import (
        INTERNAL_ERROR,
        INVALID_PARAMS,
        METHOD_NOT_FOUND,
        PARSE_ERROR,
        CallToolResult,
        InitializeParams,
        InitializeResult,
        JSONRPCError,
        JSONRPCRequest,
        JSONRPCResponse,
        JulesApprovePlanParams,
        JulesArchiveSessionParams,
        JulesAuditParams,
        JulesCallParams,
        JulesDeleteSessionParams,
        JulesGetActivityParams,
        JulesGetPatchParams,
        JulesGetSessionParams,
        JulesGetSourceParams,
        JulesListSessionsParams,
        JulesNudgeParams,
        JulesSendParams,
        JulesSourcesParams,
        JulesStartParams,
        JulesStatusParams,
        ServerCapabilities,
        ServerInfo,
        TextContent,
        Tool,
        model_to_json_schema,
    )
except ImportError:
    import jules_client as jules
    import prompts
    from jules_client import AsyncJulesClient, JulesError
    from prompts import get_prompt, list_prompts
    from protocol import (
        INTERNAL_ERROR,
        INVALID_PARAMS,
        METHOD_NOT_FOUND,
        PARSE_ERROR,
        CallToolResult,
        InitializeParams,
        InitializeResult,
        JSONRPCError,
        JSONRPCRequest,
        JSONRPCResponse,
        JulesApprovePlanParams,
        JulesArchiveSessionParams,
        JulesAuditParams,
        JulesCallParams,
        JulesDeleteSessionParams,
        JulesGetActivityParams,
        JulesGetPatchParams,
        JulesGetSessionParams,
        JulesGetSourceParams,
        JulesListSessionsParams,
        JulesNudgeParams,
        JulesSendParams,
        JulesSourcesParams,
        JulesStartParams,
        JulesStatusParams,
        ServerCapabilities,
        ServerInfo,
        TextContent,
        Tool,
        model_to_json_schema,
    )

logger = logging.getLogger("jules_mcp.handlers")

SERVER_NAME = "cloakai-jules"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2024-11-05"

GATED = {"PLAN_PENDING", "AWAITING_PLAN_APPROVAL", "FEEDBACK_PENDING", "AWAITING_FEEDBACK"}

_ACTIVITY_FIELDS = (
    ("agentMessaged", "agent message"),
    ("planGenerated", "plan generated"),
    ("planApproved", "plan approved"),
    ("userMessaged", "you said"),
    ("progressUpdated", "progress"),
)


def _describe_activity(activity: dict[str, Any]) -> tuple[str, str]:
    """Human-readable label and preview for one activity."""
    for field, label in _ACTIVITY_FIELDS:
        if field not in activity:
            continue
        payload = activity[field] or {}
        text = (
            payload.get("agentMessage")
            or payload.get("prompt")
            or payload.get("message")
            or ""
        )
        text = " ".join(str(text).split())
        if len(text) > 160:
            text = text[:157] + "..."
        return label, text
    unknown = [
        k
        for k in sorted(activity)
        if k.endswith("d") and k not in ("id", "name", "createTime")
    ]
    return (unknown[0] if unknown else "activity"), ""


def _session_summary(session: dict[str, Any]) -> str:
    """Compact summary of a session including state and gate alerts."""
    name = session.get("name", "?")
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
            url = pr.get("url") or ""
            lines.append(f"  pull request: {url}")
        elif output.get("url"):
            lines.append(f"  output: {output['url']}")

    source = (session.get("sourceContext") or {}).get("source")
    lines.append(f"  source: {source or 'none (project-less sandbox)'}")
    return "\n".join(lines)


# ==============================================================================
# Tool Registry & Implementation Functions
# ==============================================================================

ToolHandler = Callable[[dict[str, Any], AsyncJulesClient], Coroutine[Any, Any, CallToolResult]]

TOOLS: dict[str, Tool] = {}
HANDLERS: dict[str, ToolHandler] = {}


def register_tool(
    name: str,
    description: str,
    param_model: type[Any],
    aliases: list[str] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to register an MCP tool and its handler."""

    def decorator(fn: ToolHandler) -> ToolHandler:
        schema = model_to_json_schema(param_model)
        tool_def = Tool(name=name, description=description, inputSchema=schema)
        TOOLS[name] = tool_def
        HANDLERS[name] = fn
        if aliases:
            for alias in aliases:
                HANDLERS[alias] = fn
        return fn

    return decorator


# 1. jules_start
@register_tool(
    name="jules_start",
    description="Start a Jules coding session.",
    param_model=JulesStartParams,
    aliases=["jules_create_session"],
)
async def handle_jules_start(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesStartParams.model_validate(args)
    try:
        session = await client.create_session(
            prompt=params.prompt,
            source=params.source,
            branch=params.branch,
            title=params.title,
            require_plan_approval=params.require_plan_approval,
            automation_mode=params.automation_mode,
        )
        return CallToolResult.text(_session_summary(session))
    except Exception as exc:
        logger.error("Error in jules_start: %s", exc)
        return CallToolResult.error(f"error: {exc}")


# 2. jules_send
@register_tool(
    name="jules_send",
    description="Send guidance or steer an active session.",
    param_model=JulesSendParams,
    aliases=["jules_send_message"],
)
async def handle_jules_send(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesSendParams.model_validate(args)
    try:
        await client.send_message(params.session_id, params.prompt)
        return CallToolResult.text(
            f"sent to {params.session_id}. It is running again; check with jules_status."
        )
    except Exception as exc:
        logger.error("Error in jules_send: %s", exc)
        return CallToolResult.error(f"error: {exc}")


# 3. jules_status
@register_tool(
    name="jules_status",
    description="Get session status, gate alerts, and recent activities.",
    param_model=JulesStatusParams,
)
async def handle_jules_status(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesStatusParams.model_validate(args)
    try:
        session = await client.get_session(params.session_id)
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")

    out = [_session_summary(session)]
    try:
        timeline, complete = await client.timeline(params.session_id)
        out.extend(jules._verdict(session, timeline, complete))
        for activity in timeline[-params.activity_limit :]:
            label, detail = _describe_activity(activity)
            out.append(f"  - {label}" + (f": {detail}" if detail else ""))
    except Exception as exc:
        out.append(f"  (activity timeline unavailable: {exc})")
    return CallToolResult.text("\n".join(out))


# 4. jules_approve_plan
@register_tool(
    name="jules_approve_plan",
    description="Approve a session's pending implementation plan.",
    param_model=JulesApprovePlanParams,
)
async def handle_jules_approve_plan(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesApprovePlanParams.model_validate(args)
    try:
        await client.approve_plan(params.session_id, params.plan_id)
        return CallToolResult.text(f"plan approved on {params.session_id}; the session is running again.")
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 5. jules_sources
@register_tool(
    name="jules_sources",
    description="List connected GitHub repositories.",
    param_model=JulesSourcesParams,
    aliases=["jules_list_sources"],
)
async def handle_jules_sources(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesSourcesParams.model_validate(args)
    try:
        sources = await client.list_sources(
            page_size=params.page_size,
            page_token=params.page_token,
            filter_expr=params.filter_expr,
        )
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")

    if not sources:
        return CallToolResult.text("no sources are connected to this Jules account.")
    lines = []
    for source in sorted(sources, key=lambda s: s.get("name", "")):
        repo = source.get("githubRepo") or {}
        branch = (repo.get("defaultBranch") or {}).get("displayName", "?")
        count = len(repo.get("branches") or [])
        name = source.get("id") or (source.get("name", "").removeprefix("sources/"))
        lines.append(f"- {name}  (default branch: {branch}, {count} branch(es))")
    return CallToolResult.text("\n".join(lines))


# 6. jules_get_source
@register_tool(
    name="jules_get_source",
    description="Get details and branches for a repository source.",
    param_model=JulesGetSourceParams,
)
async def handle_jules_get_source(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesGetSourceParams.model_validate(args)
    try:
        data = await client.get_source(params.source_id)
        return CallToolResult.text(json.dumps(data, indent=2))
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 7. jules_list_sessions
@register_tool(
    name="jules_list_sessions",
    description="List task sessions with optional filters.",
    param_model=JulesListSessionsParams,
)
async def handle_jules_list_sessions(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesListSessionsParams.model_validate(args)
    try:
        sessions = await client.list_sessions(
            page_size=params.page_size,
            page_token=params.page_token,
            filter_expr=params.filter_expr,
            repo=params.repo,
            max_age_days=params.max_age_days,
        )
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")

    if not sessions:
        return CallToolResult.text("No sessions found matching criteria.")

    lines = ["| Session ID | State | Title | Source |", "|---|---|---|---|"]
    for s in sessions:
        sid = s.get("id") or s.get("name", "").removeprefix("sessions/")
        state = s.get("state", "UNKNOWN")
        title = " ".join((s.get("title") or s.get("prompt") or "").split())[:60]
        source = (s.get("sourceContext") or {}).get("source", "").removeprefix("sources/")
        lines.append(f"| `{sid}` | {state} | {title} | {source} |")
    return CallToolResult.text("\n".join(lines))


# 8. jules_get_session
@register_tool(
    name="jules_get_session",
    description="Get full details and outputs for a session.",
    param_model=JulesGetSessionParams,
)
async def handle_jules_get_session(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesGetSessionParams.model_validate(args)
    try:
        session = await client.get_session(params.session_id)
        return CallToolResult.text(json.dumps(session, indent=2))
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 9. jules_nudge
@register_tool(
    name="jules_nudge",
    description="Send a standardized nudge to revive an idle runner.",
    param_model=JulesNudgeParams,
)
async def handle_jules_nudge(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesNudgeParams.model_validate(args)
    try:
        await client.nudge(params.session_id, template=params.template)
        return CallToolResult.text(
            f"Nudge '{params.template}' sent to session {params.session_id}. Runner revived."
        )
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 10. jules_audit
@register_tool(
    name="jules_audit",
    description="Audit sessions for stalled runners, pending plans, and deliverables.",
    param_model=JulesAuditParams,
)
async def handle_jules_audit(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesAuditParams.model_validate(args)
    try:
        sessions = await client.list_sessions(
            page_size=50 if params.all_pages else 20,
            repo=params.repo,
            max_age_days=params.max_age_days,
        )
        if not sessions:
            return CallToolResult.text("No active sessions found within audit window.")

        lines = [
            "## Jules Sessions Health Audit\n",
            "| Session ID | State | Assessment | Inactive (m) | PR | Title |",
            "|---|---|---|---|---|---|",
        ]
        for s in sessions:
            if s.get("archived") is True:
                continue
            audit = await client.audit_session(
                s,
                stale_threshold_mins=params.stale_threshold_mins,
                max_age_days=params.max_age_days,
            )
            sid = audit["id"]
            state = audit["state"]
            assess = audit["assessment"]
            inact = audit["inactive_mins"]
            pr = audit["pull_request_url"] or "-"
            title = audit["title"]
            lines.append(f"| `{sid}` | {state} | **{assess}** | {inact} | {pr} | {title} |")
        return CallToolResult.text("\n".join(lines))
    except Exception as exc:
        return CallToolResult.error(f"error during audit: {exc}")


# 11. jules_archive_session
@register_tool(
    name="jules_archive_session",
    description="Reversibly archive or restore a session.",
    param_model=JulesArchiveSessionParams,
    aliases=["jules_unarchive_session"],
)
async def handle_jules_archive_session(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesArchiveSessionParams.model_validate(args)
    try:
        if params.unarchive:
            await client.unarchive_session(params.session_id)
            return CallToolResult.text(f"Session {params.session_id} unarchived (restored to active listing).")
        else:
            await client.archive_session(params.session_id)
            return CallToolResult.text(f"Session {params.session_id} archived (reversible).")
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 12. jules_delete_session
@register_tool(
    name="jules_delete_session",
    description="Permanently delete a session (requires confirm=True).",
    param_model=JulesDeleteSessionParams,
)
async def handle_jules_delete_session(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesDeleteSessionParams.model_validate(args)
    try:
        await client.delete_session(params.session_id, confirm=params.confirm)
        return CallToolResult.text(f"Session {params.session_id} permanently deleted.")
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 13. jules_get_activity
@register_tool(
    name="jules_get_activity",
    description="Get details of a specific activity event.",
    param_model=JulesGetActivityParams,
)
async def handle_jules_get_activity(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesGetActivityParams.model_validate(args)
    try:
        activity = await client.get_activity(params.session_id, params.activity_id)
        return CallToolResult.text(json.dumps(activity, indent=2))
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# 14. jules_get_patch
@register_tool(
    name="jules_get_patch",
    description="Extract unidiff git patch deliverables from a session.",
    param_model=JulesGetPatchParams,
    aliases=["jules_deliver_patch"],
)
async def handle_jules_get_patch(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesGetPatchParams.model_validate(args)
    try:
        patch_info = await client.extract_patch(params.session_id)
        if not patch_info:
            return CallToolResult.error(f"No git patch found in session {params.session_id}.")

        return CallToolResult.text(
            f"### Undelivered Git Patch Deliverable\n"
            f"- **Session**: `{patch_info['session_id']}`\n"
            f"- **Source**: `{patch_info['source']}`\n"
            f"- **Suggested Commit Message**:\n```\n{patch_info['commit_message']}\n```\n\n"
            f"```diff\n{patch_info['patch']}\n```"
        )
    except Exception as exc:
        return CallToolResult.error(f"error extracting patch: {exc}")


# 15. jules_call
@register_tool(
    name="jules_call",
    description="Execute a raw GET call to the Jules v1alpha API.",
    param_model=JulesCallParams,
)
async def handle_jules_call(args: dict[str, Any], client: AsyncJulesClient) -> CallToolResult:
    params = JulesCallParams.model_validate(args)
    try:
        data = await client.call("GET", params.endpoint)
        return CallToolResult.text(json.dumps(data, indent=2))
    except Exception as exc:
        return CallToolResult.error(f"error: {exc}")


# ==============================================================================
# Central JSON-RPC 2.0 Dispatcher
# ==============================================================================


async def handle_jsonrpc(
    req: JSONRPCRequest,
    client: AsyncJulesClient,
) -> JSONRPCResponse:
    """Process an incoming JSON-RPC 2.0 request and return a JSON-RPC 2.0 response."""
    method = req.method
    params = req.params or {}

    try:
        if method == "initialize":
            _ = InitializeParams.model_validate(params)
            result = InitializeResult(
                protocolVersion=PROTOCOL_VERSION,
                capabilities=ServerCapabilities(
                    tools={"listChanged": False},
                    prompts={"listChanged": False},
                ),
                serverInfo=ServerInfo(name=SERVER_NAME, version=SERVER_VERSION),
            )
            return JSONRPCResponse.make_result(req.id, result.model_dump(by_alias=True))

        elif method in ("notifications/initialized", "initialized"):
            return JSONRPCResponse.make_result(req.id, {})

        elif method == "ping":
            return JSONRPCResponse.make_result(req.id, {})

        elif method == "tools/list":
            # Return list of registered tools
            tools_list = [t.model_dump(by_alias=True) for t in TOOLS.values()]
            return JSONRPCResponse.make_result(req.id, {"tools": tools_list})

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}
            if not tool_name or tool_name not in HANDLERS:
                return JSONRPCResponse.make_error(
                    req.id,
                    METHOD_NOT_FOUND,
                    f"Unknown tool: '{tool_name}'",
                )
            handler = HANDLERS[tool_name]
            result = await handler(tool_args, client)
            return JSONRPCResponse.make_result(req.id, result.model_dump(by_alias=True))

        elif method == "prompts/list":
            prompts_list = [p.model_dump(by_alias=True) for p in list_prompts()]
            return JSONRPCResponse.make_result(req.id, {"prompts": prompts_list})

        elif method == "prompts/get":
            prompt_name = params.get("name")
            prompt_args = params.get("arguments") or {}
            if not prompt_name:
                return JSONRPCResponse.make_error(
                    req.id,
                    INVALID_PARAMS,
                    "Missing required parameter 'name'",
                )
            prompt_result = get_prompt(prompt_name, prompt_args)
            if prompt_result is None:
                return JSONRPCResponse.make_error(
                    req.id,
                    METHOD_NOT_FOUND,
                    f"Unknown prompt: '{prompt_name}'",
                )
            return JSONRPCResponse.make_result(req.id, prompt_result.model_dump(by_alias=True))

        else:
            return JSONRPCResponse.make_error(
                req.id,
                METHOD_NOT_FOUND,
                f"Method not found: '{method}'",
            )

    except ValidationError as err:
        logger.warning("Validation error on %s: %s", method, err)
        return JSONRPCResponse.make_error(
            req.id,
            INVALID_PARAMS,
            f"Invalid parameters: {err}",
        )
    except JulesError as err:
        logger.error("Jules error on %s: %s", method, err)
        return JSONRPCResponse.make_error(
            req.id,
            INTERNAL_ERROR,
            str(err),
        )
    except Exception as exc:
        logger.exception("Unexpected error processing %s: %s", method, exc)
        return JSONRPCResponse.make_error(
            req.id,
            INTERNAL_ERROR,
            f"Internal error: {exc}",
        )
