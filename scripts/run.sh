#!/usr/bin/env bash
# Run the agent in an isolated container.
#
# The flag set below is the MINIMAL working set, derived by escalating one flag at
# a time in docs/verification/phase-0.md Check 3. It is not cargo-culted. If you
# are adding a flag, add the comment saying which failure demanded it.
set -euo pipefail

# A workspace is OPT-IN. Most agents need no files at all, and mounting a shared
# host directory is the one thing that breaks the isolation guarantee: two
# invocations on the same project would read and overwrite each other's files.
# Demonstrated, and asserted by isolation test 5b/5c.
#
# Pass --workspace [PATH] to opt in. The default is no mount at all.
WORKSPACE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --workspace)
      WORKSPACE="${2:-$PWD}"; shift 2 ;;
    --workspace=*)
      WORKSPACE="${1#*=}"; shift ;;
    *)
      break ;;
  esac
done
[ -n "$WORKSPACE" ] || WORKSPACE="${CLOAKAI_PROJECT:-}"

# The caller's directory must be captured BEFORE we cd to the repo root.
# Doing it afterwards made PROJECT_DIR resolve to the cloakai checkout itself, so
# running this from any other project silently mounted cloakai's own source into
# /workspace and ran the agent on the wrong tree.
PROJECT_DIR="${WORKSPACE:-$PWD}"

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
    FLAGS+=("$word")
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
# No key is minted and no proxy is configured. There is no proxy: the agent calls
# the endpoint its agent.json names, and the free model needs no credential.
#
# The previous version minted a budgeted virtual key from LiteLLM and injected
# OPENCODE_CONFIG_CONTENT. That was a real credential-scoping story and it is gone
# with the stack. An agent that DOES need a credential gets it from the caller's
# environment, forwarded by name so no value lands in argv where `ps` can read it.
if [ -n "${OPENCODE_CONFIG_CONTENT:-}" ]; then
  CONFIG_ARGS=(-e "OPENCODE_CONFIG_CONTENT=$OPENCODE_CONFIG_CONTENT")
  if [ -n "${OPENCODE_API_KEY:-}" ]; then
    CONFIG_ARGS+=(-e "OPENCODE_API_KEY=$OPENCODE_API_KEY")
  fi
else
  CONFIG_ARGS=(-e "OPENCODE_DISABLE_MODELS_FETCH=1")
fi

# The mount is added only when a workspace was requested. It is never on by
# default — see the note above and isolation test 5b.
MOUNT=()
if [ -n "$WORKSPACE" ]; then
  if [ ! -d "$WORKSPACE" ]; then
    echo "error: --workspace '$WORKSPACE' is not a directory" >&2
    exit 1
  fi
  # With cap-drop=ALL the container's root loses CAP_DAC_OVERRIDE, so it cannot
  # write into a directory it lacks permission for. Warn rather than fail, since
  # a read-only workspace is a legitimate choice.
  if [ ! -w "$WORKSPACE" ]; then
    echo "warning: '$WORKSPACE' is not writable by uid $(id -u); the agent may not be able to edit files there." >&2
  fi
  MOUNT=(-v "$PROJECT_DIR:/workspace")
  echo "note: mounting $PROJECT_DIR at /workspace (opted in)." >&2
else
  echo "note: no workspace mounted; the agent cannot see any host files." >&2
fi

exec docker run --rm "${TTY[@]}" \
  "${MOUNT[@]}" \
  -w "${WORKSPACE:+/workspace}" \
  "${FLAGS[@]}" \
  "${CONFIG_ARGS[@]}" \
  "$IMAGE" run "$@"
