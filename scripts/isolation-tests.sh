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
# Defaulted rather than left empty: invoking this script directly used to skip
# Test 0 entirely, because an empty service name can never match. The "not
# running" branch below still skips cleanly when the stack is down.
AGENT_SERVICE="${AGENT_SERVICE:-dev-agent}"
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
#
# This test previously wrote its markers to /tmp and reported "two concurrent
# invocations cannot see each other's files". That was FALSE as a statement about
# the system: run.sh and compose both bind-mount a host directory at /workspace,
# so two invocations on the same project DID share files. Demonstrated:
#
#     B READS: [A private data]
#     === what survived === B CLOBBERS
#
# It was green because it only exercised tmpfs, which was never the requirement.
# The requirement is that an invocation has NO shared working directory BY DEFAULT.
#
# /tmp is where the markers go because it is the only writable location in a
# default invocation — which is the point. 5b asserts the absence of a mount, and
# 5c proves that mount is what creates sharing, so 5 is not passing trivially.

WS=$(mktemp -d)
# mktemp -d is 0700, and cap-drop=ALL takes CAP_DAC_OVERRIDE away from the
# container's root, so it CANNOT write into a 0700 directory. Any host directory
# mounted into an isolated container must be writable by the container's uid.
chmod 777 "$WS"
A="probe-a-$$"; B="probe-b-$$"
docker rm -f "$A" "$B" >/dev/null 2>&1

launch() { # name, [extra docker args...]
  local n="$1"; shift
  docker run -d --rm --name "$n" "${ISOLATION_FLAGS[@]}" "$@" "$IMAGE" \
    shell -c "echo $n > /tmp/$n.marker; sleep 20" >/dev/null 2>&1
}

launch "$A" &
pa=$!
launch "$B" &
pb=$!
sleep 4

# POSITIVE CONTROL: both alive and self-readable. Without it, "B could not see A"
# is indistinguishable from "neither container started".
self_a=$(docker exec "$A" cat "/tmp/$A.marker" 2>/dev/null || true)
self_b=$(docker exec "$B" cat "/tmp/$B.marker" 2>/dev/null || true)

if [ -z "$self_a" ] || [ -z "$self_b" ]; then
  no "5. COULD NOT RUN — a='$self_a' b='$self_b' (both containers must be up and self-readable first)"
else
  saw=$(docker exec "$B" cat "/tmp/$A.marker" 2>/dev/null || true)
  if [ -z "$saw" ]; then
    ok "5. two default invocations cannot see each other's files"
  else
    no "5. ONE RUN SAW THE OTHER'S FILES — there is shared writable state"
  fi
fi

# 5b — the assertion that would have caught the original bug. It fails the moment
# a volume flag reappears in the default invocation.
if docker inspect "$A" --format '{{range .Mounts}}{{.Destination}} {{end}}' 2>/dev/null | grep -qE '/workspace'; then
  no "5b. THE DEFAULT INVOCATION MOUNTS A WORKSPACE — the shared directory is back"
else
  ok "5b. the default invocation mounts no working directory (workspace is opt-in)"
fi

docker rm -f "$A" "$B" >/dev/null 2>&1
wait $pa $pb 2>/dev/null

# 5c — the control for 5. If adding an explicit workspace does NOT produce sharing,
# then 5 is passing for some other reason and proves nothing.
C="probe-c-$$"; D="probe-d-$$"
docker rm -f "$C" "$D" >/dev/null 2>&1
# The markers go into /workspace here, NOT /tmp. A workspace mount does not affect
# /tmp, so writing to /tmp and expecting sharing would fail for the wrong reason —
# which is exactly what the first version of this control did.
docker run -d --rm --name "$C" "${ISOLATION_FLAGS[@]}" -v "$WS:/workspace" "$IMAGE" \
  shell -c "echo C-wrote-here > /workspace/$C.marker; sleep 20" >/dev/null 2>&1 &
pc=$!
docker run -d --rm --name "$D" "${ISOLATION_FLAGS[@]}" -v "$WS:/workspace" "$IMAGE" \
  shell -c "sleep 5; echo D-wrote-here > /workspace/$D.marker; sleep 15" >/dev/null 2>&1 &
pd=$!
sleep 9
shared=$(docker exec "$D" cat "/workspace/$C.marker" 2>/dev/null || true)
if [ -n "$shared" ]; then
  ok "5c. CONTROL: with a workspace explicitly mounted, invocations DO share it"
  echo "     ^ which is what makes 5 meaningful, and why 5b is the real assertion"
else
  no "5c. CONTROL FAILED — mounting a workspace produced no sharing, so test 5 is passing for the wrong reason"
fi
docker rm -f "$C" "$D" >/dev/null 2>&1
wait $pc $pd 2>/dev/null
rm -rf "$WS"

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
      "$IMAGE" run-cmd opencode run --auto "reply with exactly PONG" 2>&1 | tail -6)

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
# Let compose own BOTH the container and the network. Creating the network by hand
# first made compose warn that it had not created it, and meant an interrupted run
# left the network and a temp file behind.
#
# --project-name pins the container name: compose otherwise derives it from the
# directory, and this file lives in a mktemp dir, so the name was unpredictable.
# The trap covers SIGINT, so an aborted test run still cleans up.
cleanup_oom() {
  docker compose -p cloakai-oomtest -f "$OOM_FILE" down --remove-orphans >/dev/null 2>&1 || true
  rm -f "$OOM_FILE"
}
trap cleanup_oom EXIT INT TERM

timeout 180 docker compose -p cloakai-oomtest -f "$OOM_FILE" up --abort-on-container-exit >/dev/null 2>&1
oom_state=$(docker inspect cloakai-oomtest-oom-victim-1 --format '{{.State.OOMKilled}}/{{.State.ExitCode}}' 2>/dev/null || echo "missing")
cleanup_oom

case "$oom_state" in
  true/137) ok "9. mem_limit is enforced — the container was OOM-killed at the limit, not the host" ;;
  *)        no "9. MEMORY LIMIT NOT ENFORCED (state=$oom_state) — resource exhaustion would become a host problem" ;;
esac

head_ "summary"
printf '  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
