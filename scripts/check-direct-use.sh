#!/usr/bin/env bash
# Verify the central claim: an agent image is usable with NO gateway involved.
#
#     docker run --rm <image> mcp
#
# This is the property the whole architecture rests on — that the image is the
# product, with nothing else running. It has been verified by hand several times
# and by nothing automatic, which is precisely how plugins/dev/mcp.json came to
# point at a service that was never in compose while every test stayed
# green. So it runs here now.
#
# The dev image needs no credential to list tools, so this is unconditional.
set -uo pipefail

# Anything this script starts goes away when it finishes, INCLUDING when it is
# interrupted or a check fails partway. Without this, a failed run left MCP
# containers running indefinitely, because the mcp shape's whole job is to sit
# there waiting for stdin.
cleanup() {
  local ids
  ids=$(docker ps -aq --filter "label=cloakai.call=1" 2>/dev/null || true)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    docker rm -f $ids >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

cd "$(dirname "$0")/.."
ROOT="$PWD"

# A test-only MCP client. Not part of the product: the product is the agent
# images. This used to be the gateway's image, and the gateway is gone; the ability
# to prove it is unnecessary had to outlive it.
HARNESS="${HARNESS:-cloakai/probe:latest}"
NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}"
pass=0
fail=0

ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass + 1)); }
no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail + 1)); }

docker image inspect "$HARNESS" >/dev/null 2>&1 || {
  echo "  the harness image $HARNESS is missing." >&2
  echo "  It is only an MCP client here, not part of the product:" >&2
  echo "    docker build -f tests/probe.Dockerfile -t $HARNESS ." >&2
  fail=$((fail + 1))
  echo; echo "  $pass passed, $fail failed"; exit 1
}

# ---------------------------------------------------------------- dev image
echo "  dev image over stdio, no gateway"
# The repo is mounted because tests/direct_use.py now builds its command line with
# the CLI rather than hardcoding one — a test that assembles its own docker argv
# can silently disagree with the product.
out=$(timeout 300 docker run --rm --entrypoint python \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$ROOT:/srv:ro" \
        -e REPO=/srv \
        "$HARNESS" -m tests.direct_use 2>&1)

if grep -q "PASS: the image is a working MCP server" <<<"$out"; then
  ok "cloakai/dev:mcp speaks MCP with no gateway"
  grep -oE "tools/list -> \[[^]]*\]" <<<"$out" | head -1 | sed 's/^/       /'
else
  no "cloakai/dev:mcp did not serve over stdio"
  tail -12 <<<"$out" | sed 's/^/       /'
fi

echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]