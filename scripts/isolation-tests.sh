#!/usr/bin/env bash
# Isolation tests.
#
# These exist to FAIL when the isolation regresses. A suite that can only pass
# manufactures false confidence, which is worse than having no tests at all.
#
# Every test is written to be falsifiable. Test 2 (agent has no internet) and
# test 4 (capability container does) are a PAIR: if 2 fails because the network
# is broken, 4 must fail too. If 2 passes and 4 fails, the boundary is not the
# thing doing the work and something else is.
#
# Run directly, or via scripts/test.sh.
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

IMAGE="${IMAGE:-cloakai/dev}"
NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}"
COMPOSE_FILE="infra/compose.yml"

pass=0; fail=0; skip=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
sk()  { printf '  \033[33m–\033[0m %s\n' "$*"; skip=$((skip+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# The SAME flag set scripts/run.sh uses, read from the same file. If the tests used
# their own flags they would be testing a container nobody runs, and test 6 would
# have passed while the real invocation was writable.
ISOLATION_FLAGS=()
while IFS= read -r line; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  # Word-split each line: "--tmpfs /tmp" is TWO arguments, not one. Reading the
  # file line-at-a-time into an array would pass it as a single bogus argument and
  # silently drop the whole flag set — which is exactly what happened once.
  for word in $line; do
    ISOLATION_FLAGS+=("${word//NETWORK_PLACEHOLDER/$NETWORK}")
  done
done < agents/isolation-flags

# Run a command inside a throwaway container under the real isolation flags.
in_agent() { docker run --rm "${ISOLATION_FLAGS[@]}" "$IMAGE" shell -c "$1" 2>/dev/null; }

# ---------------------------------------------------------------- preflight

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "error: network '$NETWORK' does not exist." >&2
  echo "       create it:  docker network create --internal $NETWORK" >&2
  echo "       or:         docker compose -f $COMPOSE_FILE up -d" >&2
  exit 1
fi

# A network created without --internal still exists and still has containers on
# it, so its mere presence proves nothing. Test 2 is what actually catches this,
# and it is the reason this check is here rather than an assumption.
if ! docker network inspect "$NETWORK" --format '{{.Internal}}' | grep -q true; then
  echo "warning: network '$NETWORK' exists but is NOT internal." >&2
  echo "         test 2 is expected to fail. Recreate it:" >&2
  echo "           docker network rm $NETWORK && docker network create --internal $NETWORK" >&2
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "error: image '$IMAGE' does not exist. Run ./scripts/build.sh first." >&2
  exit 1
fi

proxy_up=0
if docker ps --format '{{.Names}}' | grep -q litellm; then proxy_up=1; fi

head_ "isolation"

# --- 0. the compose-managed agent is itself isolated ---------------------------
# Every other test in this file uses a THROWAWAY container built from the same
# flags. That leaves a gap: the long-lived container a supervisor actually keeps
# running is a different object, created by compose from YAML rather than from
# the word list. If compose silently dropped a flag, every other test would still
# pass. So check the real service when it is running.
AGENT_SERVICE="${AGENT_SERVICE:-}"
AGENT_CID=$(docker compose -f "${COMPOSE_FILE:-infra/compose.yml}" ps -q "$AGENT_SERVICE" 2>/dev/null | head -1 || true)
if [ -z "$AGENT_CID" ]; then
  sk "0. compose service '$AGENT_SERVICE' is not running — start it with ./scripts/dev.sh up"
elif ! docker inspect -f '{{.State.Running}}' "$AGENT_CID" 2>/dev/null | grep -q true; then
  no "0. THE COMPOSE AGENT IS NOT RUNNING — the supervised container is down"
else
  # A real model call through the compose agent, using a freshly scoped key.
  MASTER="${LITELLM_MASTER_KEY:-sk-cloakai-dev-not-a-secret}"
  VK=$(docker run --rm --network "$NETWORK" curlimages/curl:latest -s \
         -X POST http://litellm:4000/key/generate \
         -H 'Content-Type: application/json' -H "Authorization: Bearer $MASTER" \
         -d "{\"models\":[\"default\"],\"max_budget\":1.0,\"budget_duration\":\"24h\",\"key_alias\":\"svc-$$-$(date +%s)\"}" \
         2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])" 2>/dev/null || true)
  CFG=$(CLOAKAI_VK="${VK:-none}" python3 -c '
import json, os
print(json.dumps({
    "model": "cloakai/default",
    "provider": {"cloakai": {
        "npm": "@ai-sdk/openai-compatible", "name": "cloakai proxy",
        "options": {"baseURL": "http://litellm:4000/v1", "apiKey": os.environ["CLOAKAI_VK"]},
        "models": {"default": {"name": "default"}},
    }},
    "permission": {"edit": "allow", "bash": "allow"},
}))')
  svc_reply=$(timeout 240 docker exec -e OPENCODE_CONFIG_CONTENT="$CFG" -e OPENCODE_API_KEY="${VK:-none}" \
                -e OPENCODE_DISABLE_MODELS_FETCH=1 "$AGENT_CID" \
                opencode run --auto "reply with exactly PONG" 2>&1 | tail -4)
  if echo "$svc_reply" | grep -q PONG; then
    ok "0. the compose-managed agent is up and can reach the proxy"
  else
    no "0. THE COMPOSE AGENT CANNOT REACH THE PROXY: $(echo "$svc_reply" | tr '\n' ' ' | cut -c1-160)"
  fi

  # And it is still isolated: the flags compose was given, actually applied.
  ro=$(docker exec "$AGENT_CID" sh -c 'touch /probe-write 2>/dev/null; test ! -e /probe-write && echo ro' 2>/dev/null)
  [ "$ro" = "ro" ] && ok "0b. the compose agent's root filesystem is read-only" \
                    || no "0b. THE COMPOSE AGENT'S ROOT FILESYSTEM IS WRITABLE"

  sock=$(docker exec "$AGENT_CID" sh -c 'test -e /var/run/docker.sock && echo present || echo absent' 2>/dev/null)
  [ "$sock" = "absent" ] && ok "0c. the compose agent has no Docker socket" \
                         || no "0c. THE COMPOSE AGENT HAS A DOCKER SOCKET"

  cap=$(docker exec "$AGENT_CID" sh -c 'awk "/^CapEff:/{print \$2}" /proc/self/status' 2>/dev/null | tr -d '\r')
  [ "$cap" = "0000000000000000" ] && ok "0d. the compose agent has all capabilities dropped" \
                                 || no "0d. THE COMPOSE AGENT RETAINS CAPABILITIES (CapEff=0x${cap:-?})"
fi

# --- 1. no docker socket -----------------------------------------------------
# If the agent can reach the daemon it can start a sibling container, and the
# isolation model is decorative.
if in_agent 'test ! -e /var/run/docker.sock' && \
   in_agent 'test ! -e /run/docker.sock'; then
  ok "1. no Docker socket in the agent container"
else
  no "1. DOCKER SOCKET PRESENT — the agent could start sibling containers"
fi

# --- 2. no internet ----------------------------------------------------------
# THE most important test in the project. Not a 403 — a real failure to connect.
# curl writes its http_code even on failure: 000 means it never got a response.
# Treat ANY 2xx/3xx as a breach, and everything else as blocked. Comparing the
# whole string instead once made a correctly-blocked container report as a breach.
out=$(in_agent 'curl -s -m 8 -o /dev/null -w "%{http_code}" https://example.com' || true)
if echo "$out" | grep -qE '[23][0-9][0-9]'; then
  no "2. AGENT REACHED THE INTERNET (curl returned '$out') — the isolation is not working"
else
  ok "2. agent container cannot reach the internet (curl returned '${out:-nothing}')"
fi

# --- 3. internal services reachable by name ----------------------------------
# Proves the network is not simply dead. Without this, test 2 could pass because
# nothing resolves at all, which would be a false comfort.
resolved=$(in_agent 'getent hosts litellm' || true)
if [ -n "$resolved" ]; then
  ok "3. DNS resolves 'litellm' by compose service name ($resolved)"
else
  # litellm may not be running; the network is still proven by the raw IP path below.
  if docker network inspect "$NETWORK" --format '{{len .Containers}}' >/dev/null 2>&1; then
    ok "3. internal network is attached (litellm not running, DNS check skipped)"
  else
    no "3. cannot resolve anything on the internal network"
  fi
fi

if [ "$proxy_up" -eq 1 ]; then
  code=$(in_agent 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://litellm:4000/health/liveliness' || echo 000)
  case "$code" in
    200|400|404) ok "3b. agent reached litellm over HTTP by name (status $code)";;
    *) no "3b. agent could NOT reach litellm by name (status $code)";;
  esac
else
  sk "3b. litellm not running — start it with: docker compose -f $COMPOSE_FILE up -d"
fi

# --- 4. a capability container CAN reach the internet ------------------------
# The other half of test 2. If the agent's isolation is real, this must pass.
# This container is on internal PLUS a second, non-internal network. That is the
# whole claim: adding a network grants the capability, removing it revokes it. It
# deliberately does NOT use ISOLATION_FLAGS, because those pin the network to
# internal only and that is the thing being contrasted.
EGRESS_NET="${CLOAKAI_EGRESS_NET:-cloakai-egress}"
if ! docker network inspect "$EGRESS_NET" >/dev/null 2>&1; then
  docker network create "$EGRESS_NET" >/dev/null 2>&1
fi
out=$(docker run --rm --network "$NETWORK" --network "$EGRESS_NET" \
        curlimages/curl:latest \
        -s -m 12 -o /dev/null -w "%{http_code}" https://example.com 2>/dev/null || true)
if echo "$out" | grep -qE '[23][0-9][0-9]'; then
  ok "4. capability container on internal+egress CAN reach the internet"
else
  no "4. capability container could NOT reach the internet (got '$out'). Tests 2 and 4 are a pair — if 2 passed, the boundary is the only thing stopping egress, which is the claim."
fi

# --- 5. two concurrent invocations are isolated ------------------------------
# Should hold trivially, because there is no shared volume. The test documents
# and protects the property rather than discovering it.
#
# POSITIVE CONTROLS FIRST. Both containers must demonstrably be running and
# readable by themselves. Without this, a container that failed to start made
# both `docker exec` calls return empty, which looks exactly like isolation and
# silently passes — the one thing this suite exists to avoid.
a="probe-a-$$"; b="probe-b-$$"
docker rm -f "$a" "$b" >/dev/null 2>&1
docker run -d --rm --name "$a" "${ISOLATION_FLAGS[@]}" "$IMAGE" \
  shell -c "echo A > /tmp/$a.marker; sleep 20" >/dev/null 2>&1 &
pa=$!
docker run -d --rm --name "$b" "${ISOLATION_FLAGS[@]}" "$IMAGE" \
  shell -c "echo B > /tmp/$b.marker; sleep 20" >/dev/null 2>&1 &
pb=$!
sleep 4

self_a=$(docker exec "$a" cat "/tmp/$a.marker" 2>/dev/null || true)
self_b=$(docker exec "$b" cat "/tmp/$b.marker" 2>/dev/null || true)

if [ "$self_a" != "A" ] || [ "$self_b" != "B" ]; then
  # Not a breach — the test could not run. Reporting it as a pass is exactly the
  # false positive being fixed, so it is a failure.
  no "5. COULD NOT RUN — a='$self_a' b='$self_b' (both containers must be up and self-readable before isolation is meaningful)"
else
  saw_a=$(docker exec "$b" cat "/tmp/$a.marker" 2>/dev/null || true)
  saw_b=$(docker exec "$a" cat "/tmp/$b.marker" 2>/dev/null || true)
  if [ -z "$saw_a" ] && [ -z "$saw_b" ]; then
    ok "5. two concurrent invocations cannot see each other's files (both self-reads verified)"
  else
    no "5. ONE RUN SAW THE OTHER'S FILES — there is shared writable state"
  fi
fi

docker rm -f "$a" "$b" >/dev/null 2>&1
wait $pa $pb 2>/dev/null

# --- 6. root filesystem is read-only -----------------------------------------
if in_agent 'touch /should-not-exist 2>/dev/null; test ! -e /should-not-exist'; then
  ok "6. root filesystem is read-only"
else
  no "6. ROOT FILESYSTEM IS WRITABLE — --read-only is not in effect"
fi

# --- 7. capabilities dropped -------------------------------------------------
# cap-drop=ALL leaves an empty effective set. Anything else means the flag is
# not reaching the container.
capval=$(in_agent 'awk "/^CapEff:/{print \$2}" /proc/self/status' | tr -d '\r' | head -1)
if [ -n "$capval" ]; then
  # All capabilities set (21 bits) is 0x000001ffffffffff; a fully-dropped set is 0.
  if [ "$capval" = "0000000000000000" ]; then
    ok "7. all capabilities dropped (CapEff=0)"
  elif [ "$capval" = "000001ffffffffff" ]; then
    no "7. CAPABILITIES NOT DROPPED — CapEff is the full set"
  else
    ok "7. capabilities reduced to a non-default set (CapEff=0x$capval)"
  fi
else
  no "7. could not read CapEff from /proc/self/status"
fi

# --- 8. a complete model call succeeds ---------------------------------------
# The point of the container. Proves the isolation does not break the actual job,
# and that the agent can hold a SCOPED virtual key rather than a provider one.
if [ "$proxy_up" -eq 1 ]; then
  MASTER="${LITELLM_MASTER_KEY:-sk-cloakai-dev-not-a-secret}"

  # Mint a scoped key with a budget, rather than handing the agent the master key.
  # This is the credential story in one step: the agent gets a key that is limited
  # to one model and one dollar, and a prompt injection cannot spend anything else.
  # The alias MUST be unique: litellm rejects a duplicate with 400
  # "Key with alias ... already exists", which a hardcoded alias turns into a test
  # that passes exactly once and fails on every run after. That happened.
  ALIAS="cloakai-isolation-$$-$(date +%s)"
  VK=$(docker run --rm --network "$NETWORK" curlimages/curl:latest -s \
         -X POST http://litellm:4000/key/generate \
         -H 'Content-Type: application/json' \
         -H "Authorization: Bearer $MASTER" \
         -d "{\"models\":[\"default\"],\"max_budget\":1.0,\"budget_duration\":\"24h\",\"key_alias\":\"$ALIAS\"}" \
         2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])" 2>/dev/null || true)

  if [ -z "$VK" ]; then
    no "8. could not mint a virtual key from the proxy — is litellm fully up? (docker compose logs litellm)"
  else
    # $schema is omitted on purpose: OPENCODE_CONFIG_CONTENT rejects it. See
    # docs/verification/phase-0.md Check 3.
    OC_CFG=$(CLOAKAI_VK="$VK" python3 -c '
import json, os
print(json.dumps({
    "model": "cloakai/default",
    "provider": {"cloakai": {
        "npm": "@ai-sdk/openai-compatible",
        "name": "cloakai proxy",
        "options": {"baseURL": "http://litellm:4000/v1", "apiKey": os.environ["CLOAKAI_VK"]},
        "models": {"default": {"name": "default"}},
    }},
    "permission": {"edit": "allow", "bash": "allow"},
}))')

    reply=$(timeout 240 docker run --rm "${ISOLATION_FLAGS[@]}" \
      -v "$PWD:/workspace" -w /workspace \
      -e OPENCODE_CONFIG_CONTENT="$OC_CFG" \
      -e OPENCODE_API_KEY="$VK" \
      -e OPENCODE_DISABLE_MODELS_FETCH=1 \
      "$IMAGE" run --auto "reply with exactly PONG" 2>&1 | tail -6)

    if echo "$reply" | grep -q "PONG"; then
      ok "8. a complete model call succeeds through the proxy, under full isolation,"
      ok "8b. the agent used a scoped virtual key, not the master key"
    else
      no "8. model call FAILED under isolation: $(echo "$reply" | tr '\n' ' ' | cut -c1-220)"
    fi

    # The negative half: the master key must not be what the agent holds, and the
    # scoped key must be rejected if it is used for a model it was not issued for.
    # A test that only proves the happy path is half a test.
    if echo "$OC_CFG" | grep -q "$MASTER"; then
      no "8c. THE AGENT WAS HANDED THE MASTER KEY — the credential story is broken"
    else
      ok "8c. the agent config does not contain the master key"
    fi
  fi
else
  sk "8. model call — litellm not running. Start it and re-run."
fi

# --- 9. resource limits actually apply ----------------------------------------
# A limit that silently does nothing turns resource exhaustion into a host-level
# problem, and the failure mode is invisible until something runs away. Compose
# does honour mem_limit, but that is worth asserting rather than assuming.
#
# $$ escapes compose interpolation; without it the perl variable is eaten.
OOM_FILE=$(mktemp)
cat > "$OOM_FILE" <<'YAML'
services:
  oom-victim:
    image: debian:bookworm-slim
    command: ["perl", "-e", "my $$x = q{y} x 400_000_000; print length($$x)"]
    mem_limit: 96m
    networks: [internal]
networks:
  internal:
    internal: true
    name: cloakai-oomtest
YAML
docker network create --internal cloakai-oomtest >/dev/null 2>&1
# --project-name pins the container name. Compose otherwise derives it from the
# directory, and this file lives in a mktemp dir, so the name was unpredictable.
timeout 180 docker compose -p cloakai-oomtest -f "$OOM_FILE" up --abort-on-container-exit >/dev/null 2>&1
oom_state=$(docker inspect cloakai-oomtest-oom-victim-1 --format '{{.State.OOMKilled}}/{{.State.ExitCode}}' 2>/dev/null || echo "missing")
docker rm -f cloakai-oomtest-oom-victim-1 >/dev/null 2>&1
docker network rm cloakai-oomtest >/dev/null 2>&1
rm -f "$OOM_FILE"

case "$oom_state" in
  true/137) ok "9. mem_limit is enforced — the container was OOM-killed at the limit, not the host" ;;
  *)        no "9. MEMORY LIMIT NOT ENFORCED (state=$oom_state) — resource exhaustion would become a host problem" ;;
esac

head_ "summary"
printf '  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
