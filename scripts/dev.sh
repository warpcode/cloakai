#!/usr/bin/env bash
# One word for each dev-stack operation.
#
# `up` waits for health and reports state. A stack that takes 30 seconds to become
# usable and says nothing is a bad developer experience, and "fire and forget"
# compose hides exactly the ordering bugs this project has already hit.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

COMPOSE_FILE="${COMPOSE_FILE:-infra/compose.yml}"
# --project-directory makes compose resolve the AGENT_PROJECT mount against the repo
# root rather than against infra/, where the compose file happens to live. Without
# it the `..` default works by accident and `AGENT_PROJECT=.` silently mounts infra/.
#
# It also changes resolution for EVERY other relative path, so the config mounts are
# passed as ABSOLUTE paths below. That is not belt and braces: a relative
# `./litellm.yaml` resolved to the repo root, docker created an empty directory at
# that path, and litellm came up with no model list and served 400s.
export CLOAKAI_LITELLM_CONFIG="${CLOAKAI_LITELLM_CONFIG:-$ROOT/infra/litellm.yaml}"
export CLOAKAI_MOCK_UPSTREAM="${CLOAKAI_MOCK_UPSTREAM:-$ROOT/infra/mock-upstream.py}"
COMPOSE=(docker compose --project-directory "$ROOT" -f "$COMPOSE_FILE")

# A missing config path does NOT fail the compose invocation. Docker creates an empty
# DIRECTORY at the mount source and starts the container anyway, which is how
# litellm came up serving 400s with an empty model list. Check it here instead.
for f in "$CLOAKAI_LITELLM_CONFIG" "$CLOAKAI_MOCK_UPSTREAM"; do
  if [ ! -f "$f" ]; then
    echo "error: $f does not exist, so it would be mounted as an empty directory." >&2
    echo "       set CLOAKAI_LITELLM_CONFIG / CLOAKAI_MOCK_UPSTREAM to real files." >&2
    exit 1
  fi
done
PROFILE="${COMPOSE_PROFILE:-test}"
WAIT_SECONDS="${DEV_UP_TIMEOUT:-180}"

dim=$'\033[2m'; b=$'\033[1m'; green=$'\033[32m'; red=$'\033[31m'; yellow=$'\033[33m'; off=$'\033[0m'

need_image() {
  if ! docker image inspect "${AGENT_IMAGE:-cloakai/dev:latest}" >/dev/null 2>&1; then
    echo "${red}error:${off} the agent image is not built." >&2
    echo "       run: ./scripts/build.sh" >&2
    exit 1
  fi
}

# Show every service with its state, and say plainly if anything is not healthy.
status() {
  local services
  services=$("${COMPOSE[@]}" --profile "$PROFILE" ps --services 2>/dev/null || true)
  [ -z "$services" ] && { echo "${dim}no services running${off}"; return; }

  local unhealthy=0
  printf '%b\n' "${b}service${off}"
  while read -r s; do
    [ -z "$s" ] && continue
    local line state
    line=$("${COMPOSE[@]}" ps "$s" --format '{{.Status}}' 2>/dev/null | head -1)
    state="${line:-absent}"
    case "$state" in
      *unhealthy*|*Exited*|*absent*|restarting*)
        printf '  %-16s %s%s%s\n' "$s" "$red" "$state" "$off"
        unhealthy=1
        ;;
      *healthy*)  printf '  %-16s %s%s%s\n' "$s" "$green" "$state" "$off" ;;
      *)          printf '  %-16s %s%s%s\n' "$s" "$state" "$off" ;;
    esac
  done <<< "$services"
  return $unhealthy
}

cmd_up() {
  need_image
  echo "${b}bringing up the stack${off} (profile: $PROFILE)"
  # The agent mounts the caller's project by default. Set AGENT_PROJECT to mount
  # somewhere else; see the README for why it is a variable and not a fixed path.
  "${COMPOSE[@]}" --profile "$PROFILE" up -d --wait --wait-timeout "$WAIT_SECONDS" "$@" || {
    echo
    echo "${red}the stack did not become healthy within ${WAIT_SECONDS}s${off}" >&2
    status || true
    echo
    echo "${dim}logs:${off}" >&2
    "${COMPOSE[@]}" --profile "$PROFILE" logs --tail 30 >&2 || true
    exit 1
  }
  echo
  # `status` returns non-zero when anything is unhealthy, and `set -e` is active,
  # so calling it bare would abort here and swallow the instructions below.
  status || true
  echo
  echo "${green}ready.${off} the agent is reachable on the internal network${off} as 'dev-agent'."
  echo "${dim}Nothing calls it automatically yet — there is no gateway. This is plumbing.${off}"
  echo
  echo "  agent   ${COMPOSE[*]} exec dev-agent <cmd>"
  echo "  logs    ./scripts/dev.sh logs"
}

cmd_down() {
  # --remove-orphans deliberately NOT used: it would remove containers belonging
  # to other compose projects that share the network.
  "${COMPOSE[@]}" --profile "$PROFILE" down "$@"
}

cmd_logs() {
  "${COMPOSE[@]}" --profile "$PROFILE" logs -f --tail 100 "$@"
}

cmd_shell() {
  need_image
  # `docker compose exec` and `docker exec` bypass the container ENTRYPOINT — they
  # exec a binary directly. So `exec dev-agent shell` looks for a binary literally
  # named "shell" and fails, even though `shell` is a valid MODE of the entrypoint.
  # The entrypoint has to be named explicitly.
  "${COMPOSE[@]}" exec dev-agent /usr/local/bin/cloakai-entrypoint shell "$@"
}

cmd_test() {
  # The isolation suite runs its own throwaway containers against the same
  # network, so it works whether or not the stack is up. Starting it here is a
  # convenience, not a requirement.
  "${COMPOSE[@]}" --profile "$PROFILE" up -d --wait --wait-timeout "$WAIT_SECONDS" || true
  echo
  echo "${b}isolation parity${off}"
  ./scripts/check-isolation-parity.sh || exit 1
  echo
  echo "${b}isolation${off}"
  # Run against the compose-managed network, which is the point: the same
  # assertions must hold whether the stack was started by hand or by a
  # supervisor. DEV_AGENT_SERVICE names the service so the suite can also verify
  # the compose container itself is isolated, not just a throwaway one.
  AGENT_SERVICE="${AGENT_SERVICE:-dev-agent}" \
    ./scripts/isolation-tests.sh
}

cmd_ps() { status; }

case "${1:-}" in
  up)     shift; cmd_up "$@" ;;
  down)   shift; cmd_down "$@" ;;
  logs)   shift; cmd_logs "$@" ;;
  shell)  shift; cmd_shell "$@" ;;
  test)   shift; cmd_test "$@" ;;
  ps|status) shift; cmd_ps "$@" ;;
  "")
    sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "Usage: ./scripts/dev.sh {up|down|logs|shell|test|ps} [args...]"
    exit 2
    ;;
  *)
    echo "unknown command: $1" >&2
    echo "usage: ./scripts/dev.sh {up|down|logs|shell|test|ps}" >&2
    exit 2
    ;;
esac
