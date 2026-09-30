#!/usr/bin/env bash
# Build the agent image.
#
# Compiles first, because the image ships the GENERATED dist/dev rather than
# plugins/dev. That way the image and a locally installed plugin cannot drift.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

IMAGE="${IMAGE:-cloakai/dev}"
PLUGIN="${PLUGIN:-dev}"

echo "compiling $PLUGIN"
./scripts/compile.sh --quiet

echo "building $IMAGE from the compiled output"
# Context is the repo root because the Dockerfile needs both dist/ and the
# entrypoint script. dockerignore keeps .git and local clutter out of the context.
docker build \
  -f agents/dev.Dockerfile \
  -t "$IMAGE" \
  --build-arg "PLUGIN=$PLUGIN" \
  "$ROOT"

echo
echo "built $IMAGE"
echo "  run mode:  ./scripts/run.sh <prompt>"
echo "  mcp (stdio): docker run --rm $IMAGE mcp        <- the portable contract"
echo "  mcp-http:    docker run --rm -d --name cloakai-mcp --network cloakai-internal $IMAGE mcp-http"
echo

# Jules is a separate image with the same contract. It is not built by default
# because it is not part of the agent runtime: it wraps a remote API, and it is
# the only image here that needs internet access. Opt in with JULES=1.
if [ "${JULES:-0}" = "1" ]; then
  echo "building cloakai/jules"
  docker build -f agents/jules.Dockerfile -t "${JULES_IMAGE:-cloakai/jules}" "$ROOT"
  echo "  built ${JULES_IMAGE:-cloakai/jules}"
  echo "  mcp (stdio): docker run --rm -e JULES_API_KEY ${JULES_IMAGE:-cloakai/jules} mcp"
fi
