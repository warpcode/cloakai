#!/usr/bin/env bash
# Two modes off one image, so there is no bespoke per-mode image to maintain.
#
#   cloakai-entrypoint run-cmd <cmd...> run the exact command agent.json declares
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
  # `run` is NOT dispatched here. The caller passes the full command the agent
  # declares in agent.json, so this branch exists only to run it. Dispatching a
  # hardcoded `opencode run --auto` silently dropped `--model opencode/big-pickle`
  # and left the run hanging with no output, because a wrong model does not fail —
  # it waits. One source of truth: agent.json.
  run-cmd)
    exec "$@"
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
    # no port is published. See the "Using an image directly" section of README.md.
    exec docker-agent serve mcp /agent/agent.yaml \
      -a dev \
      --http --listen 0.0.0.0:8081 \
      --insecure-no-auth
    ;;

  shell)
    exec /bin/bash "$@"
    ;;

  *)
    echo "usage: cloakai-entrypoint {run-cmd|mcp|mcp-http|shell} [args...]" >&2
    exit 2
    ;;
esac
