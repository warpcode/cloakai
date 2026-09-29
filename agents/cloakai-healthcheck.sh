#!/usr/bin/env bash
# Health probe for the mcp service.
#
# A real round trip, not `--version`. The binary existing proves nothing about the
# service serving, and a health check that cannot detect a wedged MCP endpoint is
# worse than none: `depends_on: service_healthy` would then gate on a lie.
#
# run mode has no comparable probe — it is a one-shot process, not a service — so
# its check is the container simply staying up. That asymmetry is real and is
# documented rather than papered over.
set -euo pipefail

PORT="${CLOAKAI_MCP_PORT:-8081}"
URL="http://127.0.0.1:${PORT}/mcp"

# A streamable-HTTP initialize. No session id is required: docker-agent's server
# is stateless per request, so this is a complete exchange in one POST.
body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cloakai-healthcheck","version":"1"}}}'

out=$(curl -s -m 8 \
  -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d "$body" 2>/dev/null || true)

# The response is SSE-framed even on POST, so match the payload rather than the
# envelope. An empty body means the listener is up but not serving.
case "$out" in
  *'"serverInfo"'*) exit 0 ;;
  *)
    echo "unhealthy: no MCP initialize response from $URL" >&2
    echo "  body: ${out:0:200}" >&2
    exit 1
    ;;
esac
