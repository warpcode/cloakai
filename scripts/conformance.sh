#!/usr/bin/env bash
# Client conformance: does a REAL client load what we generated?
#
# This exists because every other check in this project tests our own output
# against our own expectations. That cannot catch "the client silently drops it",
# which is the failure mode the whole compiler exists to avoid.
#
# OpenHands runs here because it is headless and scriptable. VS Code does not,
# and the reason is worth recording rather than rediscovering:
#
#   code-server ships the agent-plugins system (chat.pluginLocations,
#   agentPluginsHome and componentPaths are all present in its workbench), so a
#   containerised VS Code test is technically possible. But its workbench is
#   lazy-loaded and only starts on a browser connection, and the chat subsystem
#   that consumes the plugin only activates with the Copilot extension
#   authenticated. The blocker is a signed-in session, not an install — so a
#   container does not unblock it, and pretending otherwise would be a test that
#   silently never runs.
#
# Until then, VS Code is covered by static analysis of the shipped workbench
# bundle (docs/verification/phase-0.md Check 2), not by this.
set -uo pipefail

cd "$(dirname "$0")/.."

IMAGE="${CONFORMANCE_IMAGE:-cloakai/conformance-openhands}"
NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}"
PLUGIN="${PLUGIN:-dev}"
MCP_URL="${CLOAKAI_MCP_URL:-}"

pass=0; fail=0; skip=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
sk()  { printf '  \033[33m–\033[0m %s\n' "$*"; skip=$((skip+1)); }

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  sk "no internal network '$NETWORK' — docker compose -f infra/compose.yml up -d"
  exit 0
fi

# Build on first use. The image is the pinned OpenHands version, which is the
# whole point: an unpinned client answers a question about whatever happened to
# be installed, and the answer changes silently.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  printf '  building %s (first run only)\n' "$IMAGE"
  if ! docker build -q -t "$IMAGE" -f infra/conformance/Dockerfile infra/conformance >/dev/null 2>&1; then
    no "could not build the conformance image"
    exit 1
  fi
fi

# Only offer the live probe when the agent is actually up. Pointing it at a dead
# endpoint would report a client failure for what is really a missing service.
if [ -z "$MCP_URL" ]; then
  if docker ps --format '{{.Names}}' | grep -q dev-agent; then
    MCP_URL="http://dev-agent:8081/mcp"
  fi
fi

# Order matters: every flag must precede the image name, or docker passes it to
# the entrypoint as an argument. Appending `-e` after the image looked correct and
# silently turned the live probe into a permanent skip.
# The SOURCE skills are mounted too, so the "every source skill survived
# compilation" check compares against something real instead of comparing the
# generated list against itself.
args=(docker run --rm --network "$NETWORK"
      -v "$PWD/dist/$PLUGIN:/check/plugin:ro"
      -v "$PWD/plugins/$PLUGIN/skills:/check/source_skills:ro"
      -v "$PWD/infra/conformance/check_openhands.py:/check/check.py:ro")
[ -n "$MCP_URL" ] && args+=(-e "CLOAKAI_MCP_URL=$MCP_URL")
args+=(--entrypoint python3 "$IMAGE" /check/check.py)

out=$("${args[@]}" 2>&1)
rc=$?
printf '%s\n' "$out"

if [ $rc -eq 0 ]; then
  ok "OpenHands conformance passed"
else
  no "OpenHands conformance FAILED — a real client did not accept our output"
fi

# VS Code: recorded as unverified, never as a pass.
sk "VS Code: not verifiable headlessly (needs an authenticated Copilot session); see docs/phase-3.md"

printf '\n  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
