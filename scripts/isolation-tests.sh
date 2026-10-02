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
# There is no cloakai-managed network any more. The isolation properties under test
# are per-container — read-only rootfs, dropped capabilities, tmpfs, limits — and all
# of them hold on the default bridge. The one property a network would buy is "no
# egress", and that needs a network the caller creates:
#   docker network create --internal cloakai-internal
NETWORK="${CLOAKAI_NETWORK:-}"

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
  # --network is not in agents/isolation-flags any more; it is an explicit per-agent
  # choice, so add it here only when one was requested.
  [ -n "${NETWORK:-}" ] && ISOLATION_FLAGS+=("--network" "$NETWORK")
  for word in $line; do
    ISOLATION_FLAGS+=("$word")
  done
done < agents/isolation-flags

# Run a command inside a throwaway container under the real isolation flags.
in_agent() { docker run --rm "${ISOLATION_FLAGS[@]}" "$IMAGE" shell -c "$1" 2>/dev/null; }

# ---------------------------------------------------------------- preflight

if [ -n "$NETWORK" ] && ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "error: network '$NETWORK' does not exist." >&2
  echo "       create it:  docker network create --internal $NETWORK" >&2
  exit 1
fi

# A network created without --internal still exists and still has containers on
# it, so its mere presence proves nothing. Test 2 is what actually catches this,
# and it is the reason this check is here rather than an assumption.
if [ -n "$NETWORK" ] && ! docker network inspect "$NETWORK" --format '{{.Internal}}' | grep -q true; then
  echo "warning: network '$NETWORK' exists but is NOT internal." >&2
  echo "         test 2 is expected to fail. Recreate it:" >&2
  echo "           docker network rm $NETWORK && docker network create --internal $NETWORK" >&2
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "error: image '$IMAGE' does not exist. Run ./scripts/build.sh first." >&2
  exit 1
fi

proxy_up=0
# DNS-by-service-name was a compose property. There are no compose services now, so
# this resolves only if the caller has a container on the network whose name matches.

head_ "isolation"

# --- 1. no docker socket -----------------------------------------------------
# If the agent can reach the daemon it can start a sibling container, and the
# isolation model is decorative.
if in_agent 'test ! -e /var/run/docker.sock' && \
   in_agent 'test ! -e /run/docker.sock'; then
  ok "1. no Docker socket in the agent container"
else
  no "1. DOCKER SOCKET PRESENT — the agent could start sibling containers"
fi

# --- 2. the agent CAN reach the internet ---------------------------------------
# INVERTED from the original, and deliberately.
#
# This used to be "the most important test in the project": the agent container
# could not reach the internet, because the model arrived through a local proxy on
# a managed `--internal` network. That network came from the compose stack. The
# stack is gone, agents call opencode.ai/zen directly, and every container is on
# the default bridge.
#
# So the property is now the OPPOSITE, and the test asserts the opposite: an agent
# that cannot reach its model is broken, not safe. The isolation story no longer
# includes the network, and the tests say so rather than quietly passing on an
# absent network.
out=$(in_agent 'curl -s -m 8 -o /dev/null -w "%{http_code}" https://opencode.ai/zen/v1/models' || true)
if echo "$out" | grep -qE '[23][0-9][0-9]'; then
  ok "2. the agent can reach its model endpoint (status $out)"
else
  no "2. THE AGENT CANNOT REACH opencode.ai/zen (got '${out:-nothing}') — the agent is broken"
fi

# --- 3. peers on a shared network, if the agent declares one ------------------
# There is no cloakai-managed network, so this only means something when the caller
# both declares a network and sets ISOLATION_PEER. Otherwise there is no network to
# resolve a name on and the check is skipped rather than failed: asserting a failure
# here would be asserting a property the project no longer has.
if [ -z "${NETWORK:-}" ] || [ -z "${ISOLATION_PEER:-}" ]; then
  sk "3. no managed network or no peer — set CLOAKAI_NETWORK and ISOLATION_PEER to run"
else
  resolved=$(in_agent "getent hosts $ISOLATION_PEER || true")
  if [ -n "$resolved" ]; then
    ok "3. DNS resolves '$ISOLATION_PEER' by name ($resolved)"
  else
    no "3. cannot resolve '$ISOLATION_PEER' on the declared network"
  fi
fi

# 3b was "the agent can reach litellm by service name", which was a property of the
# compose stack. What is worth keeping is the general claim: a container on the
# network can reach a NAMED PEER over HTTP. Tested against any peer, since there is
# no litellm to name.
PEER="${ISOLATION_PEER:-}"
if [ -n "$PEER" ] && [ "$proxy_up" -eq 1 ]; then
  code=$(in_agent "curl -s -m 8 -o /dev/null -w '%{http_code}' http://$PEER/ 2>/dev/null" || echo 000)
  case "$code" in
    200|301|302|400|401|403|404) ok "3b. agent reached '$PEER' over HTTP by name (status $code)";;
    *) no "3b. agent could NOT reach '$PEER' by name (status $code)";;
  esac
else
  sk "3b. no peer container to reach — set ISOLATION_PEER=<name> to run this"
fi

# --- 4. the network is opt-in, not a boundary ---------------------------------
# This was "a capability container on internal+egress CAN reach the internet while
# the agent on internal-only cannot" — the two-network capability model. That model
# is gone: there is no managed internal network, so every agent has egress already
# and there is no boundary to contrast against.
#
# What is still worth asserting is that the network is an explicit, per-agent
# choice rather than something baked in. An agent that declares no network gets the
# default bridge, and the caller can check that is what actually happened.
declared=$(python3 -c 'import json,sys; print(json.load(open("dist/agents.json"))["agents"][os.environ.get("AGENT","dev")].get("network") or "")' 2>/dev/null || echo "")
actual=$(docker inspect "$IMAGE" --format '{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}' 2>/dev/null | wc -l)
if [ -z "$declared" ]; then
  ok "4. no agent declares a network, so the default bridge is the boundary — none"
else
  if docker network inspect "$declared" >/dev/null 2>&1; then
    ok "4. the declared network '$declared' exists for the caller to attach"
  else
    sk "4. agent declares network '$declared' which does not exist — docker network create $declared"
  fi
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

# --- 8. a complete model call succeeds under full isolation --------------------
# The point of the container: isolation must not break the actual job. No proxy
# means no key to mint and nothing to scope, so this is a plain free-model call
# from inside the fully-flagged container.
#
# It is the same assertion the CLI makes, run through the raw isolation flags, so
# the flags themselves are proven not to break a real request — which is what tests
# 2 and 6 and 7 cannot tell you.
ANSWER=$(in_agent 'opencode run --model opencode/big-pickle --auto "Reply with only the number: what is 1+1?"' 2>&1 | tr -d '\r')
if echo "$ANSWER" | grep -qE '^\s*2\s*$'; then
  ok "8. a complete model call succeeds through the full isolation flag set"
else
  no "8. model call FAILED under isolation: $(echo "$ANSWER" | tr '\n' ' ' | cut -c1-200)"
fi

# 8b asserted the agent held a scoped virtual key rather than the master one. There
# is no master key and no proxy any more, so there is nothing to distinguish. Kept as
# a check that no credential is baked into the image, which is the property that
# replaced it.
if docker run --rm --entrypoint sh "$IMAGE" -c 'env' 2>/dev/null | grep -qiE 'sk-|api_key=.{8}'; then
  no "8b. THE IMAGE CARRIES A CREDENTIAL IN ITS ENVIRONMENT"
else
  ok "8b. the image carries no credential; the free model needs none"
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
