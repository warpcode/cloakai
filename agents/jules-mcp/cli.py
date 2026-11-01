"""Command-line interface (CLI) for Google Jules and its MCP server.

CLI first, MCP second:
  jules sources                              # list connected GitHub repos
  jules sessions                             # list recent task sessions
  jules session <id>                         # view session details
  jules start <prompt> [--source ...]        # start a new coding task
  jules send <id> <message>                  # steer an active session
  jules status <id>                          # inspect honest status and timeline
  jules approve-plan <id>                    # approve pending implementation plan
  jules nudge <id> [template]                # send progress check or PR reminder
  jules audit [--days 30]                    # audit sessions for stalled runners
  jules archive <id> [--unarchive]           # reversibly archive or restore
  jules delete <id> --confirm                # permanently delete session
  jules patch <id>                           # extract unidiff patch deliverables
  jules prompts                              # list registered MCP prompt templates
  jules prompt <name> [KEY=VALUE ...]        # render prompt template to stdout
  jules mcp                                  # serve as stdio MCP server (default)
  jules mcp --http [--port 8081]             # serve as FastAPI HTTP/SSE MCP server
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add agents/jules-mcp to path if run directly
parent_dir = str(Path(__file__).resolve().parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    from . import jules_client as jules
    from .handlers import _describe_activity, _session_summary
    from .jules_client import JulesClient, JulesError
    from .prompts import get_prompt, list_prompts
except ImportError:
    import jules_client as jules
    from handlers import _describe_activity, _session_summary
    from jules_client import JulesClient, JulesError
    from prompts import get_prompt, list_prompts


def _get_client(api_key: str | None = None) -> JulesClient:
    """Instantiate a JulesClient, failing cleanly if no key is configured."""
    if not jules.api_key_present(api_key):
        sys.stderr.write(
            "error: JULES_API_KEY is not set.\n"
            "Configure JULES_API_KEY in the environment or pass --api-key.\n"
        )
        sys.exit(2)
    return JulesClient(api_key=api_key)


# ==============================================================================
# CLI Command Handlers
# ==============================================================================


def cmd_sources(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        sources = client.list_sources(
            page_size=args.page_size,
            page_token=args.page_token,
            filter_expr=args.filter,
        )
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        print(json.dumps(sources, indent=2))
        return 0

    if not sources:
        print("No sources are connected to this Jules account.")
        return 0

    print("| Source ID | Default Branch | Branches |")
    print("|---|---|---|")
    for s in sorted(sources, key=lambda x: x.get("name", "")):
        repo = s.get("githubRepo") or {}
        branch = (repo.get("defaultBranch") or {}).get("displayName", "?")
        count = len(repo.get("branches") or [])
        name = s.get("id") or s.get("name", "").removeprefix("sources/")
        print(f"| `{name}` | {branch} | {count} |")
    return 0


def cmd_source(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        data = client.get_source(args.source_id)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    repo = data.get("githubRepo") or {}
    owner = repo.get("owner", "?")
    name = repo.get("repo", "?")
    default_branch = (repo.get("defaultBranch") or {}).get("displayName", "?")
    branches = [b.get("displayName", "") for b in repo.get("branches") or []]

    print(f"Source: {data.get('name', args.source_id)}")
    print(f"Repository: {owner}/{name}")
    print(f"Default Branch: {default_branch}")
    print(f"Active Branches ({len(branches)}): {', '.join(branches) if branches else 'none'}")
    return 0


def cmd_sessions(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        sessions = client.list_sessions(
            page_size=args.page_size,
            page_token=args.page_token,
            filter_expr=args.filter,
            repo=args.repo,
            max_age_days=args.max_age_days,
        )
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        print(json.dumps(sessions, indent=2))
        return 0

    if not sessions:
        print("No sessions found matching criteria.")
        return 0

    print("| Session ID | State | Title | Source |")
    print("|---|---|---|---|")
    for s in sessions:
        sid = s.get("id") or s.get("name", "").removeprefix("sessions/")
        state = s.get("state", "UNKNOWN")
        title = " ".join((s.get("title") or s.get("prompt") or "").split())[:60]
        source = (s.get("sourceContext") or {}).get("source", "").removeprefix("sources/")
        print(f"| `{sid}` | {state} | {title} | {source} |")
    return 0


def cmd_session(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        session = client.get_session(args.session_id)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        print(json.dumps(session, indent=2))
        return 0

    print(_session_summary(session))
    return 0


def cmd_start(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        session = client.create_session(
            prompt=args.prompt,
            source=args.source,
            branch=args.branch,
            title=args.title,
            require_plan_approval=args.require_approval,
            automation_mode=args.automation_mode,
        )
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        print(json.dumps(session, indent=2))
        return 0

    print(_session_summary(session))
    sid = session.get("name", "").removeprefix("sessions/")
    print(f"\nSession created. Check status with:\n  jules status {sid}")
    return 0


def cmd_send(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        client.send_message(args.session_id, args.message)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    print(f"Message sent to session {args.session_id}. Runner resumed.")
    return 0


def cmd_status(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        session = client.get_session(args.session_id)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if args.json:
        try:
            timeline_data, complete = client.timeline(args.session_id)
            session["timeline"] = timeline_data
            session["timeline_complete"] = complete
            session["verdict"] = jules._verdict(session, timeline_data, complete)
        except Exception as exc:
            session["timeline_error"] = str(exc)
        print(json.dumps(session, indent=2))
        return 0

    print(_session_summary(session))
    try:
        timeline_data, complete = client.timeline(args.session_id)
        verdict_lines = jules._verdict(session, timeline_data, complete)
        if verdict_lines:
            for line in verdict_lines:
                print(line)

        print(f"\nRecent activities (last {args.activity_limit}):")
        for activity in timeline_data[-args.activity_limit :]:
            label, detail = _describe_activity(activity)
            print(f"  - {label}" + (f": {detail}" if detail else ""))
    except Exception as exc:
        print(f"  (activity timeline unavailable: {exc})")
    return 0


def cmd_approve_plan(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        client.approve_plan(args.session_id, plan_id=args.plan_id)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    print(f"Plan approved on session {args.session_id}. Runner resumed.")
    return 0


def cmd_nudge(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        client.nudge(args.session_id, template=args.template)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    print(f"Nudge template '{args.template}' sent to session {args.session_id}.")
    return 0


def cmd_audit(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        sessions = client.list_sessions(
            page_size=50 if args.all_pages else 20,
            repo=args.repo,
            max_age_days=args.max_age_days,
        )
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if not sessions:
        if args.json:
            print("[]")
        else:
            print("No active sessions found within audit window.")
        return 0

    audits = client.audit_sessions(
        sessions,
        stale_threshold_mins=args.stale_threshold_mins,
        max_age_days=args.max_age_days,
    )

    if not audits:
        if args.json:
            print("[]")
        else:
            print("No active sessions found within audit window.")
        return 0

    if args.json:
        print(json.dumps(audits, indent=2))
        return 0

    print("## Jules Sessions Health Audit\n")
    print("| Session ID | State | Assessment | Inactive (m) | PR | Title |")
    print("|---|---|---|---|---|---|")
    for a in audits:
        sid = a["id"]
        state = a["state"]
        assess = a["assessment"]
        inact = a["inactive_mins"]
        pr = a["pull_request_url"] or "-"
        title = a["title"]
        print(f"| `{sid}` | {state} | **{assess}** | {inact} | {pr} | {title} |")
    return 0


def cmd_archive(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        if args.unarchive:
            client.unarchive_session(args.session_id)
            print(f"Session {args.session_id} unarchived (restored to active listing).")
        else:
            client.archive_session(args.session_id)
            print(f"Session {args.session_id} archived (reversible).")
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1
    return 0


def cmd_delete(client: JulesClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        sys.stderr.write(
            f"error: Refusing to permanently delete session {args.session_id}.\n"
            "Deletion is IRREVERSIBLE. To archive/hide a session, use 'jules archive <id>'.\n"
            "Pass --confirm if permanent deletion is explicitly intended.\n"
        )
        return 1

    try:
        client.delete_session(args.session_id, confirm=True)
        print(f"Session {args.session_id} permanently deleted.")
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1
    return 0


def cmd_patch(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        patch_info = client.extract_patch(args.session_id)
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1

    if not patch_info:
        sys.stderr.write(f"No git patch found in session {args.session_id}.\n")
        return 1

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(patch_info["patch"], encoding="utf-8")
        print(f"Extracted patch saved to {out_path}")
        print(f"Suggested commit message: {patch_info['commit_message']}")
    else:
        print(f"### Undelivered Git Patch Deliverable ({patch_info['session_id']})")
        print(f"Source: {patch_info['source']}")
        print(f"Suggested Commit Message:\n{patch_info['commit_message']}\n")
        print(patch_info["patch"])
    return 0


def cmd_call(client: JulesClient, args: argparse.Namespace) -> int:
    try:
        data = client.call("GET", args.endpoint)
        print(json.dumps(data, indent=2))
    except JulesError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serve as an MCP server: stdio by default, or HTTP/SSE with --http."""
    if not jules.api_key_present(getattr(args, "api_key", None)):
        sys.stderr.write(
            "warning: JULES_API_KEY is not set. Tools requiring API calls will fail until configured.\n"
        )

    if args.http:
        try:
            import uvicorn
            from server import app
            sys.stderr.write(f"Starting Jules MCP FastAPI server on {args.host}:{args.port}...\n")
            uvicorn.run(app, host=args.host, port=args.port, log_config=None)
            return 0
        except ImportError:
            sys.stderr.write("error: uvicorn is required for HTTP mode. Install with: pip install uvicorn\n")
            return 1
    else:
        # Default: stdio MCP transport
        import asyncio
        from jules_client import AsyncJulesClient
        from server import run_stdio

        client = AsyncJulesClient(api_key=getattr(args, "api_key", None))
        return asyncio.run(run_stdio(client))


def cmd_prompts(args: argparse.Namespace) -> int:
    prompts_list = list_prompts()
    if args.json:
        print(json.dumps([p.model_dump(by_alias=True) for p in prompts_list], indent=2))
        return 0

    if not prompts_list:
        print("No prompts registered.")
        return 0

    print("| Prompt Name | Arguments | Description |")
    print("|---|---|---|")
    for p in prompts_list:
        req_args = [a.name for a in p.arguments if a.required]
        opt_args = [f"[{a.name}]" for a in p.arguments if not a.required]
        all_args = " ".join(req_args + opt_args) or "-"
        print(f"| `{p.name}` | `{all_args}` | {p.description} |")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    prompt_name = args.name
    prompt_args: dict[str, str] = {}
    for item in args.prompt_args:
        if "=" in item:
            k, v = item.split("=", 1)
            prompt_args[k.strip()] = v.strip()
        else:
            sys.stderr.write(f"warning: ignoring argument without '=': {item}\n")

    prompt_result = get_prompt(prompt_name, prompt_args)
    if prompt_result is None:
        sys.stderr.write(f"error: Unknown prompt '{prompt_name}'. Use 'jules prompts' to list available prompts.\n")
        return 1

    if args.json:
        print(json.dumps(prompt_result.model_dump(by_alias=True), indent=2))
        return 0

    for msg in prompt_result.messages:
        print(msg.content.text)
    return 0


# ==============================================================================
# Parser Configuration
# ==============================================================================


def _add_sources_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register source and sources subcommands."""
    p_sources = subparsers.add_parser("sources", help="List connected GitHub repositories")
    p_sources.add_argument("--page-size", type=int, default=20)
    p_sources.add_argument("--page-token", default=None)
    p_sources.add_argument("--filter", default=None)
    p_sources.add_argument("--json", action="store_true")

    p_source = subparsers.add_parser("source", help="Get details for a specific repository source")
    p_source.add_argument("source_id", help="Source ID (e.g. github/owner/repo or sources/github/owner/repo)")
    p_source.add_argument("--json", action="store_true")


def _add_session_query_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register query and status subcommands for sessions."""
    # sessions
    p_sessions = subparsers.add_parser("sessions", help="List Jules task sessions")
    p_sessions.add_argument("--page-size", type=int, default=20)
    p_sessions.add_argument("--page-token", default=None)
    p_sessions.add_argument("--filter", default=None)
    p_sessions.add_argument("--repo", default=None, help="Filter by repository slug")
    p_sessions.add_argument("--max-age-days", type=int, default=None)
    p_sessions.add_argument("--json", action="store_true")

    # session
    p_session = subparsers.add_parser("session", help="Get full details for a session")
    p_session.add_argument("session_id", help="Jules session ID")
    p_session.add_argument("--json", action="store_true")

    # status
    p_status = subparsers.add_parser("status", help="Inspect session status, gate alerts, and activity timeline")
    p_status.add_argument("session_id", help="Jules session ID")
    p_status.add_argument("--activity-limit", type=int, default=10, help="Number of recent activities to show")
    p_status.add_argument("--json", action="store_true")

    # audit
    p_audit = subparsers.add_parser("audit", help="Audit sessions for stalled runners and deliverables")
    p_audit.add_argument("--max-age-days", "--days", type=int, default=30, dest="max_age_days")
    p_audit.add_argument("--stale-threshold-mins", type=int, default=60)
    p_audit.add_argument("--repo", default=None)
    p_audit.add_argument("--all-pages", action="store_true")
    p_audit.add_argument("--json", action="store_true")


def _add_session_lifecycle_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register lifecycle and task control subcommands for sessions."""
    # start / create-session
    for cmd_name in ("start", "create-session"):
        p_start = subparsers.add_parser(cmd_name, help="Start a new Jules task session")
        p_start.add_argument("prompt", help="Instruction or task goal for Jules")
        p_start.add_argument("--source", default=None, help="Target repository (e.g. github/owner/repo). Omit for sandbox.")
        p_start.add_argument("--branch", default="main", help="Target branch (default: main)")
        p_start.add_argument("--title", default=None, help="Optional short title")
        p_start.add_argument("--require-approval", action="store_true", help="Halt for human plan approval")
        p_start.add_argument("--automation-mode", default="AUTO_CREATE_PR", help="Execution mode (default: AUTO_CREATE_PR)")
        p_start.add_argument("--json", action="store_true")

    # send / send-message
    for cmd_name in ("send", "send-message"):
        p_send = subparsers.add_parser(cmd_name, help="Send guidance or steering message to a session")
        p_send.add_argument("session_id", help="Target session ID")
        p_send.add_argument("message", help="Instruction or feedback text")

    # approve-plan
    p_approve = subparsers.add_parser("approve-plan", help="Approve a pending implementation plan")
    p_approve.add_argument("session_id", help="Target session ID")
    p_approve.add_argument("--plan-id", default=None, help="Optional plan ID to confirm")

    # nudge
    p_nudge = subparsers.add_parser("nudge", help="Send a standardized nudge template to an idle session")
    p_nudge.add_argument("session_id", help="Target session ID")
    p_nudge.add_argument(
        "template",
        nargs="?",
        default="progress_check",
        choices=["progress_check", "plan_stalled", "pr_reminder"],
        help="Nudge template (default: progress_check)",
    )

    # archive / archive-session
    for cmd_name in ("archive", "archive-session"):
        p_arch = subparsers.add_parser(cmd_name, help="Reversibly archive or restore a session")
        p_arch.add_argument("session_id", help="Target session ID")
        p_arch.add_argument("--unarchive", action="store_true", help="Restore session to active listings")

    # delete / delete-session
    for cmd_name in ("delete", "delete-session"):
        p_del = subparsers.add_parser(cmd_name, help="Permanently delete a session (requires --confirm)")
        p_del.add_argument("session_id", help="Target session ID")
        p_del.add_argument("--confirm", action="store_true", help="Confirm permanent, irreversible deletion")

    # patch / extract-patch
    for cmd_name in ("patch", "extract-patch"):
        p_patch = subparsers.add_parser(cmd_name, help="Extract undelivered git patches from session outputs")
        p_patch.add_argument("session_id", help="Target session ID")
        p_patch.add_argument("--output", "-o", default=None, help="Save patch to specified file path")


def _add_prompt_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register prompt-related subcommands."""
    p_prompts = subparsers.add_parser("prompts", help="List registered MCP prompt templates")
    p_prompts.add_argument("--json", action="store_true", help="Output JSON schemas")

    p_prompt = subparsers.add_parser("prompt", help="Render an MCP prompt template")
    p_prompt.add_argument("name", help="Name of prompt template (e.g. jules_task_scaffold, jules_agent_bolt)")
    p_prompt.add_argument(
        "prompt_args",
        nargs="*",
        metavar="KEY=VALUE",
        help="Prompt arguments in KEY=VALUE format (e.g. goal='Add caching' repo='owner/repo')",
    )
    p_prompt.add_argument("--json", action="store_true", help="Output raw JSON GetPromptResult")


def _add_mcp_and_utility_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register API call and MCP subcommands."""
    p_call = subparsers.add_parser("call", help="Direct relative GET call to Jules v1alpha API")
    p_call.add_argument("endpoint", help="Relative endpoint path (e.g. sources, sessions)")

    p_mcp = subparsers.add_parser(
        "mcp",
        help="Serve as a Model Context Protocol (MCP) server (stdio by default, HTTP/SSE with --http)",
    )
    p_mcp.add_argument("--http", action="store_true", help="Serve over FastAPI HTTP/SSE instead of stdio")
    p_mcp.add_argument("--stdio", action="store_true", help="Serve over stdio JSON-RPC 2.0 (default)")
    p_mcp.add_argument("--host", default=os.environ.get("CLOAKAI_HOST", "0.0.0.0"), help="Host to bind for HTTP mode")
    p_mcp.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CLOAKAI_MCP_PORT", os.environ.get("PORT", "8081"))),
        help="Port to bind for HTTP mode (default: 8081)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the main argument parser for the Jules CLI."""
    parser = argparse.ArgumentParser(
        prog="jules",
        description="Google Jules CLI and Model Context Protocol (MCP) server.",
    )
    parser.add_argument("--api-key", default=None, help="Explicit Jules API key (default: $JULES_API_KEY)")

    subparsers = parser.add_subparsers(dest="subcommand", title="Available Commands", required=True)

    _add_sources_commands(subparsers)
    _add_session_query_commands(subparsers)
    _add_session_lifecycle_commands(subparsers)
    _add_prompt_commands(subparsers)
    _add_mcp_and_utility_commands(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "mcp":
        return cmd_mcp(args)
    if args.subcommand == "prompts":
        return cmd_prompts(args)
    if args.subcommand == "prompt":
        return cmd_prompt(args)

    client = _get_client(args.api_key)

    dispatch = {
        "sources": cmd_sources,
        "source": cmd_source,
        "sessions": cmd_sessions,
        "session": cmd_session,
        "start": cmd_start,
        "create-session": cmd_start,
        "send": cmd_send,
        "send-message": cmd_send,
        "status": cmd_status,
        "approve-plan": cmd_approve_plan,
        "nudge": cmd_nudge,
        "audit": cmd_audit,
        "archive": cmd_archive,
        "archive-session": cmd_archive,
        "delete": cmd_delete,
        "delete-session": cmd_delete,
        "patch": cmd_patch,
        "extract-patch": cmd_patch,
        "call": cmd_call,
    }

    handler = dispatch.get(args.subcommand)
    if not handler:
        sys.stderr.write(f"Unknown subcommand: {args.subcommand}\n")
        return 1

    return handler(client, args)


if __name__ == "__main__":
    sys.exit(main())
