#!/usr/bin/env bash
# Two modes off one image, so there is no bespoke per-mode image to maintain.
#
#   cloakai-entrypoint run [args...]   the harness CLI, project dir already mounted
#   cloakai-entrypoint mcp             docker agent serve mcp over HTTP
#
# The mcp invocation carries --insecure-no-auth because the container is reached by
# IP on an `internal: true` network. A non-loopback --listen is refused outright
# without either that or --auth-token. On an internal network the network is the
# capability grant, so there is nothing off-host for the listener to expose.
set -euo pipefail

mode="${1:-run}"
[ $# -gt 0 ] && shift || true

case "$mode" in
  run)
    exec opencode run --auto "$@"
    ;;

  mcp)
    # `-a dev` is redundant here (one agent per config) but is explicit, so adding
    # a second agent to agent.yaml cannot silently change which one is served.
    exec docker-agent serve mcp /agent/agent.yaml \
      -a dev \
      --http --listen 0.0.0.0:8081 \
      --insecure-no-auth
    ;;

  shell)
    exec /bin/bash "$@"
    ;;

  *)
    echo "usage: cloakai-entrypoint {run|mcp|shell} [args...]" >&2
    exit 2
    ;;
esac
