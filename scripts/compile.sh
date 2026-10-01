#!/usr/bin/env bash
# Regenerate dist/ and clients/ from plugins/.
#
# Deterministic by construction: same input, byte-identical output. Running this
# twice must produce an empty diff — scripts/test.sh asserts exactly that.
set -euo pipefail

cd "$(dirname "$0")/.."

# Every plugin directory is compiled by default, so adding an agent is a matter of
# adding plugins/<name>/ and rebuilding — nothing to register anywhere.
#
# Tested for a non-flag ARGUMENT rather than for `$# -eq 0`, because
# `./scripts/compile.sh --quiet` passes a flag and no plugin, and the earlier
# `$# -eq 0` test compiled only plugins/dev in that case — silently dropping every
# other agent from the catalogue. scripts/test.sh calls it with --quiet, so this
# is the exact invocation that broke, and it broke the real build rather than a
# test: `cloakai prompt big-pickle` reported "unknown agent" after a green run.
have_plugin=0
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *) have_plugin=1 ;;
  esac
done
if [ "$have_plugin" -eq 0 ]; then
  set -- plugins/*
fi

PYTHONPATH="$PWD/compiler" exec "${PYTHON:-python3}" -m cloakai_compiler "$@"
