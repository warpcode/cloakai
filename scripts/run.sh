#!/usr/bin/env bash
# Run the agent in an isolated container.
#
# The flag set below is the MINIMAL working set, derived by escalating one flag at
# a time in docs/verification/phase-0.md Check 3. It is not cargo-culted. If you
# are adding a flag, add the comment saying which failure demanded it.
set -euo pipefail

# The caller's directory must be captured BEFORE we cd to the repo root.
# Doing it afterwards made PROJECT_DIR resolve to the cloakai checkout itself, so
# running this from any other project silently mounted cloakai's own source into
# /workspace and ran the agent on the wrong tree.
PROJECT_DIR="${CLOAKAI_PROJECT:-$PWD}"

cd "$(dirname "$0")/.."
ROOT="$PWD"

IMAGE="${IMAGE:-cloakai/dev}"
NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}"

# A missing network must fail loudly. Docker would otherwise attach the container
# to the default bridge, which HAS internet — turning a deliberate isolation
# boundary into an open one without saying anything.
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  cat >&2 <<EOF
error: Docker network '$NETWORK' does not exist.

The agent container is placed on it so it has no route off this machine.
Falling back to the default bridge would give the agent full internet access,
so this stops instead.

Create it with:  docker network create --internal $NETWORK
Or bring up the whole topology:  docker compose -f infra/compose.yml up -d
EOF
  exit 1
fi

# The flag set lives in one file, shared with scripts/isolation-tests.sh, so the
# thing that is tested and the thing that is used cannot drift apart. The reason
# for every flag is documented there.
#
# The project bind mount is the ONE intentional exception to "no host mounts". It
# is scoped to the project directory by choice and by the discipline of the
# person running it — not by anything the spec enforces. Do not "harden" this away
# by accident, and do not add a second mount.
#
# No --privileged. No -v /var/run/docker.sock. The socket is root-equivalent on the
# host and would let the agent start sibling containers, which is exactly what the
# isolation exists to prevent.
FLAGS=()
while IFS= read -r line; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  # Word-split each line: "--tmpfs /tmp" is TWO arguments, not one. Reading the
  # file line-at-a-time into an array would pass it as a single bogus argument and
  # silently drop the whole flag set — which is exactly what happened once.
  for word in $line; do
    FLAGS+=("${word//NETWORK_PLACEHOLDER/$NETWORK}")
  done
done < agents/isolation-flags

# Allocate a TTY only when we actually have one. `-it` unconditionally makes the
# script unusable from a pipe, a script, or CI, which is exactly where you want to
# run an agent to check it still works.
TTY=()
if [ -t 0 ] && [ -t 1 ]; then
  TTY=(-it)
else
  TTY=(-i)
  echo "note: stdin is not a terminal, running without a TTY." >&2
fi

# Model access goes through the proxy. Without this the agent has no reachable
# endpoint at all, because the container cannot reach the internet to discover
# one — so a bare `./scripts/run.sh "prompt"` failed on missing credentials.
#
# A caller-supplied OPENCODE_CONFIG_CONTENT always wins. Otherwise we mint a
# scoped virtual key here, exactly as the isolation tests do, and point the
# harness at the proxy. The agent never sees a provider credential.
CONFIG_ARGS=()
if [ -n "${OPENCODE_CONFIG_CONTENT:-}" ]; then
  CONFIG_ARGS=(-e "OPENCODE_CONFIG_CONTENT=$OPENCODE_CONFIG_CONTENT")
  if [ -n "${OPENCODE_API_KEY:-}" ]; then
    CONFIG_ARGS+=(-e "OPENCODE_API_KEY=$OPENCODE_API_KEY")
  fi
else
  VK=""
  if docker ps --format '{{.Names}}' | grep -q litellm; then
    VK=$(docker run --rm --network "$NETWORK" curlimages/curl:latest -s \
           -X POST http://litellm:4000/key/generate \
           -H 'Content-Type: application/json' \
           -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-sk-cloakai-dev-not-a-secret}" \
           -d "{\"models\":[\"default\"],\"max_budget\":${CLOAKAI_BUDGET:-1.00},\"budget_duration\":\"24h\",\"key_alias\":\"run-$$-$(date +%s)\"}" \
           2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])" 2>/dev/null || true)
  fi

  if [ -n "$VK" ]; then
    CONFIG_ARGS=(-e "OPENCODE_API_KEY=$VK" -e "OPENCODE_DISABLE_MODELS_FETCH=1")
    CONFIG_ARGS+=(-e "OPENCODE_CONFIG_CONTENT=$(CLOAKAI_VK="$VK" python3 -c '
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
}))')")
    echo "using a scoped virtual key from the proxy (max ${CLOAKAI_BUDGET:-1.00}/24h)" >&2
  else
    echo "warning: the proxy is not running, so no scoped key could be minted." >&2
    echo "         The agent will have no model endpoint — the container cannot" >&2
    echo "         reach the internet to find one. Start it with:" >&2
    echo "           docker compose -f $ROOT/infra/compose.yml up -d" >&2
    # An explicitly empty config is better than a half-formed one: it fails
    # immediately and legibly rather than hanging on a discovery request.
    CONFIG_ARGS=(-e "OPENCODE_CONFIG_CONTENT={}")
  fi
fi

exec docker run --rm "${TTY[@]}" \
  -v "$PROJECT_DIR:/workspace" -w /workspace \
  "${FLAGS[@]}" \
  "${CONFIG_ARGS[@]}" \
  "$IMAGE" run "$@"
