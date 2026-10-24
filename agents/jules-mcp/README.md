# jules-mcp

Command-line interface (CLI) and Model Context Protocol (MCP) server for Google Jules (`jules.googleapis.com/v1alpha`).

## Philosophy: CLI First, MCP Second

Whenever we create MCP servers, **they must be a CLI first, and an MCP second**.

```
                           ┌──────────────────────────────┐
                           │      Google Jules CLI        │
                           │           (jules)            │
                           └──────────────┬───────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
     ┌────────────────────────┐                     ┌────────────────────────┐
     │      CLI Commands      │                     │     MCP Subcommand     │
     │                        │                     │       (jules mcp)      │
     │  • sources             │                     └───────────┬────────────┘
     │  • sessions            │                                 │
     │  • status <id>         │                  ┌──────────────┴──────────────┐
     │  • audit               │                  ▼                             ▼
     │  • start "prompt"      │      ┌───────────────────────┐   ┌───────────────────────┐
     │  • approve-plan <id>   │      │   Stdio JSON-RPC 2.0  │   │  FastAPI HTTP & SSE   │
     │  • nudge <id>          │      │      (jules mcp)      │   │  (jules mcp --http)   │
     │  • archive / delete    │      └───────────────────────┘   └───────────────────────┘
     │  • patch <id> [-o ...] │
     │  • call <endpoint>     │
     └────────────────────────┘
```

The underlying business logic and client operations are exposed as first-class CLI commands for direct terminal and script use. The MCP server is a subcommand (`jules mcp` or `jules mcp --http`) that wraps the same typed handlers and models into JSON-RPC 2.0 endpoints for AI agents.

---

## Zero Git / GitHub Access (Strict API Isolation)

**`jules-mcp` has NO direct access to git or GitHub.**
It does not mount local git repositories, does not require `git` or `gh` binaries, and does not require SSH keys or `GH_TOKEN`. The only required credential is `JULES_API_KEY`.

### How Wedged PRs and Undelivered Patches are Handled

When running inside Docker or standalone as an MCP server, how does the agent recover wedged PRs or deliver undelivered changes?

```
┌─────────────────┐        1. jules_audit / jules_status         ┌───────────────────────┐
│                 ├─────────────────────────────────────────────►│                       │
│                 │◄─────────────────────────────────────────────┤                       │
│                 │        Timeline verdict (stalled runner)     │                       │
│                 │                                              │      Google Jules     │
│                 │        2. jules_nudge(pr_reminder)           │       MCP Server      │
│                 ├─────────────────────────────────────────────►│                       │
│    AI Agent     │◄─────────────────────────────────────────────┤ (Strict Jules API)    │
│  (Claude, Cursor│        Runner revived (native PR created)    │                       │
│   Antigravity)  │                                              │                       │
│                 │        3. jules_get_patch (if wedged)        │                       │
│                 ├─────────────────────────────────────────────►│                       │
│                 │◄─────────────────────────────────────────────┤                       │
│                 │        Raw unidiff patch + commit message    └───────────────────────┘
└────────┬────────┘
         │
         │ 4. Apply patch & open PR
         ▼
┌─────────────────────────┐
│ Host Git / GitHub MCP   │ (Has local repo & GH_TOKEN)
│  • git checkout -b tidy │
│  • git apply patch      │
│  • gh pr create         │
└─────────────────────────┘
```

1. **Detection**: The agent detects a stalled or undelivered session via `jules_audit` or `jules_status` (which performs timeline activity verdict analysis).
2. **Nudge First**: The agent sends `jules_nudge(session_id, template="pr_reminder")`. This wakes the cloud VM inside Google Jules where Jules natively pushes its branch and opens the PR (`AUTO_CREATE_PR`).
3. **Patch Extraction**: If the session is wedged ($\ge 2$ empty commits) or completed without opening a PR, the agent calls `jules_get_patch(session_id)`. Jules MCP extracts the raw `unidiffPatch` deliverable, suggested commit message, and source branch metadata directly from the session API outputs.
4. **Handoff to Host / GitHub Agent**: The calling agent or host environment (which has git/GitHub tools and `GH_TOKEN`) writes the patch, creates the tidy branch, and opens/squashes the PR.
5. **Session Archival**: The agent reversibly archives the old wedged session via `jules_archive_session(session_id)`.

> [!NOTE]
> Custom GitHub-level operations (`recover_wedged_pr`, `detect_empty_commits`, `pr_overlap_matrix`) belong in a dedicated GitHub MCP server (e.g. `github-reviews-mcp`), where `GH_TOKEN` is the only required environment variable.

---

## Directory Layout

```
agents/jules-mcp/
├── cli.py            # Primary CLI entrypoint (sources, status, audit, patch, mcp)
├── __main__.py       # Module runner dispatching directly to cli.main()
├── protocol.py       # Pydantic v2 schemas for JSON-RPC 2.0 & MCP (2024-11-05)
├── jules_client.py   # Async & sync Jules REST client with credential isolation
├── handlers.py       # Tool registry, argument validation, and markdown formatters
├── server.py         # FastAPI application and stdio runner
├── verify.py         # Stdio MCP verification script
├── probe.py          # Sandbox proof harness
└── tests/
    ├── test_cli.py                 # CLI argument parsing, flags, and safety gates
    ├── test_protocol.py            # Protocol serialization and schema extraction
    ├── test_handlers.py            # Tool dispatching and error mapping
    ├── test_server_integration.py  # FastAPI endpoint integration tests
    └── test_verdict.py             # Finished vs parked timeline classification
```

---

## 1. CLI Usage

Run directly via the `bin/jules` executable wrapper or `python -m agents.jules_mcp.cli`:

```bash
# List connected repository sources
jules sources

# Inspect active sessions (default: last 30 days)
jules sessions --repo owner/repo

# Check session status, timeline, and whether it is parked on a plan
jules status 1234567890

# Audit sessions for stuck runners and undelivered PRs
jules audit --stale-threshold-mins 60

# Start a new Jules task session
jules start "Refactor auth middleware to use JWT" --source github/owner/repo --branch main

# Approve a pending implementation plan
jules approve-plan 1234567890

# Nudge an idle session runner
jules nudge 1234567890 --template progress_check

# Extract git diff patches from session deliverables
jules patch 1234567890

# Save patch to file
jules patch 1234567890 -o /tmp/fix.patch

# Reversibly archive a session (hides from default listing)
jules archive 1234567890

# Irreversibly delete a session (requires explicit --confirm)
jules delete 1234567890 --confirm
```

---

## 2. MCP Server Usage

The MCP server is invoked via the `mcp` subcommand.

### Stdio Mode (Default)

Used by AI agent harnesses (Claude Desktop, Cursor, Antigravity, Docker):

```bash
# Direct CLI invocation
export JULES_API_KEY="..."
jules mcp

# Via Docker container
docker run --rm -i -e JULES_API_KEY cloakai/jules mcp
```

### HTTP & Server-Sent Events (SSE) Mode (FastAPI)

Used by networked MCP clients, web services, and supervision monitors:

```bash
# Start FastAPI HTTP/SSE server
jules mcp --http --host 0.0.0.0 --port 8081

# Via uvicorn directly
uvicorn agents.jules_mcp.server:app --host 0.0.0.0 --port 8081
```

#### HTTP Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/mcp` | `POST` | Stateless JSON-RPC 2.0 endpoint (compatible with curl & health probes) |
| `/sse` | `GET` | Server-Sent Events stream for MCP client connections |
| `/messages` | `POST` | Message post intake for active SSE sessions (`?sessionId=...`) |
| `/health` | `GET` | Readiness and credential check (`{"status": "ok", "api_key_configured": true}`) |
| `/tools` | `GET` | REST catalog of available MCP tools and input schemas |

```bash
# Check health
curl -s http://127.0.0.1:8081/health

# List available tools
curl -s http://127.0.0.1:8081/tools

# Send stateless JSON-RPC call
curl -s -X POST http://127.0.0.1:8081/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}'
```

---

## Tool Reference (15 Canonical Tools)

All tools communicate strictly with `jules.googleapis.com/v1alpha`:

| Tool | Aliases | Description |
|---|---|---|
| `jules_start` | `jules_create_session` | Start a Jules coding session. |
| `jules_send` | `jules_send_message` | Send guidance or steer an active session. |
| `jules_status` | — | Get session status, gate alerts, and recent activities. |
| `jules_approve_plan` | — | Approve a session's pending implementation plan. |
| `jules_sources` | `jules_list_sources` | List connected GitHub repositories. |
| `jules_get_source` | — | Get details and branches for a repository source. |
| `jules_list_sessions` | — | List task sessions with optional filters. |
| `jules_get_session` | — | Get full details and outputs for a session. |
| `jules_nudge` | — | Send a standardized nudge to revive an idle runner. |
| `jules_audit` | — | Audit sessions for stalled runners, pending plans, and deliverables. |
| `jules_archive_session` | `jules_unarchive_session` | Reversibly archive or restore a session. |
| `jules_delete_session` | — | Permanently delete a session (requires confirm=True). |
| `jules_get_activity` | — | Get details of a specific activity event. |
| `jules_get_patch` | `jules_deliver_patch` | Extract unidiff git patch deliverables from a session. |
| `jules_call` | — | Execute a raw GET call to the Jules v1alpha API. |

---

## Testing

Run the full test suite using `uv`:

```bash
uv run --with fastapi --with pydantic --with httpx --with pytest pytest agents/jules-mcp/
```

- `test_cli.py`: 9 tests verifying CLI argument parsing, flags, safety gates (`--confirm`), and `mcp` dispatch.
- `test_verdict.py`: 11 tests covering timeline parsing, plan gates, idle detection, and truncated timelines.
- `test_protocol.py`: 8 tests validating JSON-RPC formatting and Pydantic input schemas.
- `test_handlers.py`: 9 tests verifying tool execution, argument binding, and error mapping across all tools.
- `test_server_integration.py`: 8 tests covering `/mcp`, `/health`, `/tools`, and header authentication.
