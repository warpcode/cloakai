#!/usr/bin/env bash
# Run the agent in an isolated container.
#
# The flag set below is the MINIMAL working set, derived by escalating one flag at
# a time in docs/verification/phase-0.md Check 3. It is not cargo-culted. If you
# are adding a flag, add the comment saying which failure demanded it.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

IMAGE="${IMAGE:-cloakai/dev}"
NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}"
PROJECT_DIR="${CLOAKAI_PROJECT:-$PWD}"

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

exec docker run --rm "${TTY[@]}" \
  -v "$PROJECT_DIR:/workspace" -w /workspace \
  "${FLAGS[@]}" \
  -e OPENCODE_CONFIG_CONTENT="${OPENCODE_CONFIG_CONTENT:-{\}}" \
  "$IMAGE" run "$@"
