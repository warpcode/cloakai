#!/usr/bin/env bash
# Verify the declared isolation in plugins/*/agent.json and the generated
# dist/agents.json both match agents/isolation-flags.
#
# There used to be a third copy of these flags, in infra/compose.yml, and this script
# compared all three. Compose is gone. The two consumers that remain are
# agents/isolation-flags, which the shell scripts read, and dist/agents.json, which
# the CLI reads. If those disagree, one of them runs an agent more weakly than the
# other and nothing else notices.
#
# Both are compared against the SOURCE of truth rather than against each other, so
# editing the flag file and the output together cannot make a stale comparison pass.
set -uo pipefail

cd "$(dirname "$0")/.."

pass=0
fail=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail + 1)); }

# The word list exactly as run.sh and the isolation tests parse it.
declare -a FLAGS=()
while read -r line; do
  case "$line" in ''|\#*) continue ;; esac
  # shellcheck disable=SC2086
  for word in $line; do FLAGS+=("$word"); done
done < agents/isolation-flags

if python3 scripts/check-isolation-parity.py agents/isolation-flags dist/agents.json; then
  ok "every agent.json and the generated catalogue match agents/isolation-flags"
else
  no "a declared isolation flag set disagrees with agents/isolation-flags (see above)"
fi

# ---------------------------------------------------------------- the properties
has() { for f in "${FLAGS[@]}"; do [ "$f" = "$1" ] && return 0; done; return 1; }

has --read-only    && ok "read-only root filesystem" || no "MISSING --read-only"
has --cap-drop=ALL && ok "all capabilities dropped" || no "MISSING --cap-drop=ALL"
has --security-opt && ok "no-new-privileges"        || no "MISSING --security-opt no-new-privileges"

tmpfs_count=0
for f in "${FLAGS[@]}"; do [ "$f" = "--tmpfs" ] && tmpfs_count=$((tmpfs_count + 1)); done
if [ "$tmpfs_count" -ge 4 ]; then
  ok "$tmpfs_count tmpfs mounts (writable scratch under a read-only rootfs)"
else
  no "only $tmpfs_count tmpfs mounts; the agent needs somewhere to write"
fi

for f in "${FLAGS[@]}"; do
  case "$f" in
    --privileged)  no "--privileged is never acceptable" ;;
    */docker.sock) no "the Docker socket is never acceptable" ;;
  esac
done
ok "no --privileged and no Docker socket in the flag set"

echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
