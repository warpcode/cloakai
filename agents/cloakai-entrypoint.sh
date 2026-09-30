#!/usr/bin/env bash
# Two modes off one image, so there is no bespoke per-mode image to maintain.
#
#   cloakai-entrypoint run [args...]   the harness CLI, project dir already mounted
#   cloakai-entrypoint mcp             docker agent serve mcp over stdio
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
    # stdio, and that is the portable contract:
    #     docker run --rm <image> mcp
    # speaks MCP to any client with no port, no network and no gateway. Verified:
    # this writes ZERO bytes to stdout, which matters because on stdio stdout IS
    # the JSON-RPC channel. All the startup noise goes to stderr.
    #
    # This mode used to hardcode --http, which meant `mcp` could not serve stdio
    # at all AND printed "Tool safety policy: restricted" onto the JSON-RPC
    # channel, so every stdio client hung on initialize. HTTP mode emits its
    # startup lines on stdout precisely because stdout is not its channel.
    #
    # `-a dev` is redundant here (one agent per config) but is explicit, so adding
    # a second agent to agent.yaml cannot silently change which one is served.
    exec docker-agent serve mcp /agent/agent.yaml -a dev
    ;;

  mcp-http)
    # The networked variant, for callers that cannot spawn a process. Serves on a
    # port instead of stdio, and so is only reachable from inside the compose
    # network — never publish it.
    #
    # --insecure-no-auth because the bind is 0.0.0.0 and there is no auth; that
    # is acceptable only while the container is attached to cloakai-internal and
    # no port is published. See docs/gateway-design.md.
    exec docker-agent serve mcp /agent/agent.yaml \
      -a dev \
      --http --listen 0.0.0.0:8081 \
      --insecure-no-auth
    ;;

  shell)
    exec /bin/bash "$@"
    ;;

  *)
    echo "usage: cloakai-entrypoint {run|mcp|mcp-http|shell} [args...]" >&2
    exit 2
    ;;
esac
