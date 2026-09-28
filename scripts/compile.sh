#!/usr/bin/env bash
# Regenerate dist/ and clients/ from plugins/.
#
# Deterministic by construction: same input, byte-identical output. Running this
# twice must produce an empty diff — scripts/test.sh asserts exactly that.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHONPATH="$PWD/compiler" exec "${PYTHON:-python3}" -m cloakai_compiler "$@"
