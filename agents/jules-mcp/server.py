"""Jules Model Context Protocol (MCP) server.

Provides dual-transport MCP serving:
1. HTTP / SSE via FastAPI (`/mcp`, `/sse`, `/messages`, `/health`, `/tools`).
2. Stdio JSON-RPC 2.0 transport for Docker container invocations (`docker run --rm -i ... cloakai/jules mcp`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

try:
    from . import jules_client as jules
    from .handlers import HANDLERS, PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, TOOLS, handle_jsonrpc
    from .jules_client import AsyncJulesClient, JulesError
    from .prompts import get_prompt, list_prompts
    from .protocol import (
        INTERNAL_ERROR,
        INVALID_REQUEST,
        PARSE_ERROR,
        JSONRPCError,
        JSONRPCRequest,
        JSONRPCResponse,
    )
except ImportError:
    import jules_client as jules
    from handlers import HANDLERS, PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, TOOLS, handle_jsonrpc
    from jules_client import AsyncJulesClient, JulesError
    from prompts import get_prompt, list_prompts
    from protocol import (
        INTERNAL_ERROR,
        INVALID_REQUEST,
        PARSE_ERROR,
        JSONRPCError,
        JSONRPCRequest,
        JSONRPCResponse,
    )

# Configure logging to sys.stderr so stdout remains a clean JSON-RPC channel
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("jules_mcp.server")

# Active SSE sessions: session_id -> asyncio.Queue[JSONRPCResponse]
_sse_sessions: dict[str, asyncio.Queue[JSONRPCResponse]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    if not jules.api_key_present():
        logger.warning(
            "JULES_API_KEY is not set in environment. "
            "Tools requiring authentication will fail until a key is configured."
        )
    else:
        logger.info("Jules MCP Server initialized with configured credentials.")
    yield
    # Cleanup any active SSE sessions
    _sse_sessions.clear()


app = FastAPI(
    title="Google Jules MCP Server",
    description="FastAPI-based Model Context Protocol (MCP) server for Google Jules cloud coding agents.",
    version=SERVER_VERSION,
    lifespan=lifespan,
)

# Security: Avoid overly permissive CORS with allow_credentials=True and wildcard origins.
# Origins can be configured via CLOAKAI_CORS_ORIGINS (comma-separated list).
_cors_origins_env = os.environ.get("CLOAKAI_CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def get_jules_client(
    x_goog_api_key: str | None = Header(default=None, alias="X-Goog-Api-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AsyncJulesClient:
    """Dependency injection for AsyncJulesClient with request-scoped or environment auth."""
    explicit_key: str | None = None
    if x_goog_api_key and x_goog_api_key.strip():
        explicit_key = x_goog_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        explicit_key = authorization[7:].strip()

    return AsyncJulesClient(api_key=explicit_key)


# ==============================================================================
# FastAPI Routes
# ==============================================================================


@app.get("/health", summary="Health check probe")
async def health() -> dict[str, Any]:
    """Healthcheck endpoint for container orchestration and uptime monitoring."""
    key_ready = jules.api_key_present()
    return {
        "status": "healthy",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "api_key_configured": key_ready,
        "tool_count": len(TOOLS),
        "prompt_count": len(list_prompts()),
    }


@app.get("/tools", summary="List exposed MCP tools")
async def list_tools() -> dict[str, Any]:
    """RESTful tool catalogue inspection."""
    return {
        "tools": [t.model_dump(by_alias=True) for t in TOOLS.values()]
    }


@app.get("/prompts", summary="List exposed MCP prompts")
async def list_prompts_endpoint() -> dict[str, Any]:
    """RESTful prompt catalogue inspection."""
    return {
        "prompts": [p.model_dump(by_alias=True) for p in list_prompts()]
    }


@app.post("/prompts/{name}", summary="Render an MCP prompt")
async def render_prompt_endpoint(
    name: str,
    args: dict[str, str] | None = None,
) -> JSONResponse:
    """Render a prompt template with provided arguments."""
    prompt_result = get_prompt(name, args or {})
    if prompt_result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown prompt template '{name}'",
        )
    return JSONResponse(status_code=200, content=prompt_result.model_dump(by_alias=True))


@app.post("/mcp", summary="Stateless JSON-RPC 2.0 MCP endpoint")
async def mcp_endpoint(
    request: Request,
    client: AsyncJulesClient = Depends(get_jules_client),
) -> JSONResponse:
    """Stateless MCP JSON-RPC 2.0 POST endpoint.

    Compatible with streamable HTTP clients, direct POST calls, and cloakai-healthcheck.sh.
    """
    try:
        raw_body = await request.body()
        if not raw_body.strip():
            err_resp = JSONRPCResponse.make_error(None, PARSE_ERROR, "Empty request body")
            return JSONResponse(status_code=400, content=err_resp.model_dump(by_alias=True, exclude_none=True))

        data = json.loads(raw_body)
    except Exception as exc:
        err_resp = JSONRPCResponse.make_error(None, PARSE_ERROR, f"Invalid JSON payload: {exc}")
        return JSONResponse(status_code=400, content=err_resp.model_dump(by_alias=True, exclude_none=True))

    try:
        rpc_req = JSONRPCRequest.model_validate(data)
    except Exception as exc:
        err_resp = JSONRPCResponse.make_error(data.get("id") if isinstance(data, dict) else None, INVALID_REQUEST, f"Invalid JSON-RPC 2.0 request: {exc}")
        return JSONResponse(status_code=400, content=err_resp.model_dump(by_alias=True, exclude_none=True))

    resp = await handle_jsonrpc(rpc_req, client)
    return JSONResponse(status_code=200, content=resp.model_dump(by_alias=True, exclude_none=True))


@app.get("/sse", summary="Server-Sent Events (SSE) stream")
async def sse_endpoint(request: Request) -> StreamingResponse:
    """Standard Model Context Protocol Server-Sent Events (SSE) transport."""
    session_id = uuid.uuid4().hex
    queue: asyncio.Queue[JSONRPCResponse] = asyncio.Queue()
    _sse_sessions[session_id] = queue

    logger.info("New SSE client session opened: %s", session_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # First event according to MCP SSE transport: endpoint location
            yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Wait for next outgoing response with periodic keepalive
                    response = await asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = json.dumps(response.model_dump(by_alias=True, exclude_none=True))
                    yield f"event: message\ndata: {payload}\n\n"
                    queue.task_done()
                except asyncio.TimeoutError:
                    # Keepalive ping comment
                    yield ": ping\n\n"
        finally:
            _sse_sessions.pop(session_id, None)
            logger.info("SSE client session closed: %s", session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages", summary="Intake endpoint for SSE sessions")
async def messages_endpoint(
    request: Request,
    sessionId: str = Query(..., description="Active SSE session ID"),
    client: AsyncJulesClient = Depends(get_jules_client),
) -> JSONResponse:
    """Message intake endpoint for clients connected via the /sse stream."""
    if sessionId not in _sse_sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSE session '{sessionId}' not found or expired.",
        )

    queue = _sse_sessions[sessionId]

    try:
        raw_body = await request.body()
        data = json.loads(raw_body)
        rpc_req = JSONRPCRequest.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON-RPC request: {exc}")

    # Process request asynchronously and dispatch response to the SSE stream
    async def process_and_send() -> None:
        response = await handle_jsonrpc(rpc_req, client)
        if rpc_req.id is not None:
            await queue.put(response)

    asyncio.create_task(process_and_send())
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "accepted"})


# ==============================================================================
# Stdio Transport Runner (Docker / CLI)
# ==============================================================================


async def run_stdio(client: AsyncJulesClient) -> int:
    """Serve JSON-RPC 2.0 messages over standard input and standard output."""
    logger.info("Serving Jules MCP over stdio...")
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line_bytes = await reader.readline()
        if not line_bytes:
            # End of input stream (stdin closed)
            logger.info("Stdin EOF reached. Terminating stdio server.")
            break

        line = line_bytes.decode("utf-8").strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except Exception as exc:
            err = JSONRPCResponse.make_error(None, PARSE_ERROR, f"Parse error: {exc}")
            sys.stdout.write(json.dumps(err.model_dump(by_alias=True, exclude_none=True)) + "\n")
            sys.stdout.flush()
            continue

        try:
            req = JSONRPCRequest.model_validate(data)
        except Exception as exc:
            err = JSONRPCResponse.make_error(
                data.get("id") if isinstance(data, dict) else None,
                INVALID_REQUEST,
                f"Invalid request: {exc}",
            )
            sys.stdout.write(json.dumps(err.model_dump(by_alias=True, exclude_none=True)) + "\n")
            sys.stdout.flush()
            continue

        resp = await handle_jsonrpc(req, client)

        # In JSON-RPC 2.0, notifications (requests with id=None) do not receive responses
        if req.id is not None:
            out = json.dumps(resp.model_dump(by_alias=True, exclude_none=True))
            sys.stdout.write(out + "\n")
            sys.stdout.flush()

    return 0


# ==============================================================================
# Main Entrypoint
# ==============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Jules Model Context Protocol (MCP) Server")
    parser.add_argument(
        "mode",
        nargs="?",
        default="mcp",
        choices=["mcp", "mcp-http", "run", "stdio"],
        help="Serving mode: 'mcp' (stdio, default), 'mcp-http' (FastAPI web server), or 'stdio'",
    )
    parser.add_argument("--host", default=os.environ.get("CLOAKAI_HOST", "0.0.0.0"), help="Host for HTTP server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CLOAKAI_MCP_PORT", os.environ.get("PORT", "8081"))),
        help="Port for HTTP server (default: 8081)",
    )
    parser.add_argument("--http", action="store_true", help="Force HTTP server mode")

    args = parser.parse_args()

    # Warn if JULES_API_KEY is not set when running in standalone mode
    if not jules.api_key_present():
        logger.warning(
            "JULES_API_KEY is not set in environment. "
            "Tools requiring authentication will fail until configured."
        )

    client = AsyncJulesClient()

    if args.http or args.mode == "mcp-http":
        try:
            import uvicorn
            logger.info("Starting Jules MCP FastAPI server on %s:%d...", args.host, args.port)
            uvicorn.run(app, host=args.host, port=args.port, log_config=None)
            return 0
        except ImportError:
            print("error: uvicorn is required for HTTP mode. Install with: pip install uvicorn", file=sys.stderr)
            return 1
    else:
        # Default to stdio mode
        return asyncio.run(run_stdio(client))


if __name__ == "__main__":
    sys.exit(main())
