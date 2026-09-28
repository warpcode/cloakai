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
echo "  mcp mode:  docker run --rm -d --name cloakai-mcp --network cloakai-internal $IMAGE mcp"
