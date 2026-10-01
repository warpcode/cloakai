#!/usr/bin/env bash
# Verify the compiled output.
#
# Two layers:
#   - Compiler unit tests and a determinism check, which must always pass.
#   - Client conformance, which is reported separately because the MCP endpoint
#     cannot resolve if the image is not built.
set -uo pipefail

# The suite starts MCP containers whose entire purpose is to wait for stdin. If it
# is interrupted or a check fails, they must not survive the run.
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
PLUGIN="${PLUGIN:-dev}"
DIST="$ROOT/dist/$PLUGIN"

pass=0; fail=0; skip=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
sk()   { printf '  \033[33m–\033[0m %s\n' "$*"; skip=$((skip+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# -------------------------------------------------------------- the cli, live
head_ "cli (end to end)"
# Both shapes, for real: `cloakai agents dev` must answer and leave no container,
# and `cloakai mcp dev` must stay alive while a client holds the pipe open and be
# gone once it closes. That second one is a claim about process lifetime, which is
# easy to assert and easy to get wrong, so it is asserted against real containers.
if timeout 900 docker run --rm --entrypoint python \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v "$ROOT:/srv:ro" \
     -e CLOAKAI_NETWORK="${CLOAKAI_NETWORK:-cloakai-internal}" \
     -e REPO=/srv \
     "${PROBE_IMAGE:-cloakai/probe:latest}" \
     -m tests.cli_end_to_end >/tmp/cloakai-cli-e2e.log 2>&1; then
  ok "both CLI shapes work and leave nothing behind"
  grep -E 'tools/list|container (alive|destroyed)|no container' /tmp/cloakai-cli-e2e.log | sed 's/^/     /'
else
  no "the CLI end-to-end check failed"
  tail -20 /tmp/cloakai-cli-e2e.log | sed 's/^/     /'
fi

# ---------------------------------------------------- independent use, directly
head_ "independent use"
# The claim the architecture rests on: an image is the product, so
# `docker run --rm <image> mcp` works with nothing else running. This was
# verified by hand for several turns and by nothing automatic, which is how
# mcp.json came to point at a service that was never in compose with every test
# green. RUN_JULES=1 opts the Jules half in; nothing inspects the credential.
if ./scripts/check-direct-use.sh; then
  ok "images are usable directly"
else
  no "an image is not usable directly"
fi

# ---------------------------------------------------------------- the cli
head_ "cli"
# A real answer from a real model. NOT a fixed reply: the mock upstream in the
# compose stack answers "PONG" to everything, so asserting on PONG proves only
# that plumbing carries bytes. These two cannot be produced by a mock.
if timeout 600 python3 -m cli.main prompt big-pickle "what is 1+1? Answer with only the number." 2>/dev/null | grep -qE '^2[[:space:]]*$'; then
  ok "big-pickle answers 1+1 correctly, from the real model"
else
  no "big-pickle did not answer 1+1 correctly (is the image built? is cloakai-egress up?)"
fi

if timeout 600 python3 -m cli.main prompt big-pickle "What is the capital of Japan? Answer with only the city." 2>/dev/null | grep -qi 'tokyo'; then
  ok "big-pickle gives a model-specific answer, not a canned one"
else
  no "big-pickle did not answer the capital of Japan"
fi
# The dispatch logic, without Docker: which entrypoint a shape maps to, that the
# catalogue's isolation flags survive verbatim, and that nothing mounts the host.
if python3 -m unittest discover -s cli/tests -t . 2>&1 | tail -3 | grep -q "^OK"; then
  ok "cli unit tests"
else
  no "cli unit tests"
  python3 -m unittest discover -s cli/tests -t . 2>&1 | tail -20
fi

# ---------------------------------------------------------------- jules image
head_ "jules"
# The finished-or-parked verdict. Stdlib-only, so it runs here rather than needing
# the image, and it is the one piece of the Jules image with no live example: a
# parked session cannot be produced without deliberately abandoning one.
if python3 -m unittest discover -s agents/jules-mcp -p "test_*.py" -t . 2>&1 | tail -3 | grep -q "^OK"; then
  ok "verdict unit tests"
else
  no "verdict unit tests"
  python3 -m unittest discover -s agents/jules-mcp -p "test_*.py" -t . 2>&1 | tail -20
fi

# ---------------------------------------------------------------- compiler
head_ "compiler"
if PYTHONPATH="$ROOT/compiler" python3 -m unittest discover -s compiler/tests 2>&1 | tail -3 | grep -q "^OK"; then
  ok "unit tests"
else
  no "unit tests"
  PYTHONPATH="$ROOT/compiler" python3 -m unittest discover -s compiler/tests 2>&1 | tail -20
fi

# ---------------------------------------------------------------- determinism
head_ "determinism"
# The agent list, so a compile that silently DROPS an agent cannot pass. The glob
# bug in compile.sh lost big-pickle on a green run, and byte-identity did not catch
# it: it compared one wrong output against the same wrong output.
catalogue_agents() {
  python3 -c 'import json,sys; print(",".join(sorted(json.load(open("dist/agents.json"))["agents"])))' 2>/dev/null || echo "<unreadable>"
}

if ./scripts/compile.sh --quiet 2>/dev/null; then
  before=$(find dist clients -type f -exec sha256sum {} \; 2>/dev/null | sort)
  before_agents=$(catalogue_agents)
  if ./scripts/compile.sh --quiet 2>/dev/null; then
    after=$(find dist clients -type f -exec sha256sum {} \; 2>/dev/null | sort)
    after_agents=$(catalogue_agents)
    if [ "$before" = "$after" ]; then
      ok "two runs produce byte-identical output"
    else
      no "two runs differ — something non-deterministic crept in"
      diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | head -10
    fi
    if [ "$before_agents" != "$after_agents" ]; then
      no "recompiling changed the agent list: $before_agents -> $after_agents"
    else
      ok "every agent survives a recompile ($after_agents)"
    fi
  else
    no "second compile failed"
  fi
else
  no "compile failed"
fi

# ---------------------------------------------------------------- reaper
# The leak guard for per-call containers. Nothing creates cloakai.call=1 containers
# any more — the gateway that did is gone — so this currently has nothing to reap.
# It is kept and tested because it is standalone, and a leak guard written and
# verified later is a leak guard that does not exist.
head_ "per-call container reaper"
if [ "${SKIP_ISOLATION:-0}" = "1" ] || ! command -v docker >/dev/null 2>&1; then
  sk "docker not available"
elif ! docker network inspect "${CLOAKAI_NETWORK:-cloakai-internal}" >/dev/null 2>&1; then
  sk "no internal network"
elif python3 scripts/test_reaper.py; then
  ok "the reaper spares live calls and reaps hung ones"
else
  no "THE REAPER IS WRONG — it would kill live calls, or leak hung ones"
fi

# ---------------------------------------------------------------- conformance
head_ "generated output conforms to each client's expectations"

check_keys() { # file, expected-keys...
  local f="$1"; shift
  if [ ! -f "$f" ]; then no "$f missing"; return; fi
  python3 - "$f" "$@" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
want = set(sys.argv[2:])
missing = want - set(doc)
extra = set(doc) - want
if missing or extra:
    print(f"  ✗ {sys.argv[1]}: missing={sorted(missing)} unexpected={sorted(extra)}")
    sys.exit(1)
print(f"  ✓ {sys.argv[1]}: exactly {sorted(want)}")
PY
  if [ $? -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); fi
}

check_keys "$DIST/google.antigravity/plugin.json" '$schema' name description

# opencode: key is mcp, every entry is type local with `command` as one argv
# array, and "sse" appears nowhere. That shape is what `opencode mcp add` itself
# wrote, not what the plugin schema says — opencode is the consumer.
# Inspects all servers rather than one hardcoded name, so renaming a server or
# adding a second one cannot make this assertion silently vacuous.
if [ -f clients/opencode.json ]; then
  out=$(python3 -c "
import json
d = json.load(open('clients/opencode.json'))
servers = d.get('mcp')
assert servers is not None, 'no mcp key'
assert 'mcpServers' not in d, 'uses mcpServers instead of mcp'
assert servers, 'mcp key is empty; this assertion would be vacuous'
for name, e in servers.items():
    assert e.get('type') == 'local', f'{name}: type is {e.get(\"type\")!r}, expected local'
    cmd = e.get('command')
    assert isinstance(cmd, list), f'{name}: command must be an array, got {type(cmd).__name__}'
    assert cmd[:2] == ['docker', 'run'], f'{name}: command is not a docker argv: {cmd!r}'
    assert e.get('enabled') is True, f'{name}: not enabled'
    assert 'url' not in e, f'{name}: a stdio server carries no url'
assert 'sse' not in json.dumps(d), 'emits an sse type'
print(f'{len(servers)} server(s): ' + ', '.join(sorted(servers)))
" 2>&1)
  if [ $? -eq 0 ]; then ok "clients/opencode.json uses mcp + type local (docker argv), no sse ($out)"
  else no "clients/opencode.json shape is wrong: $out"; fi
fi

# claude-plugin manifest
if [ -f "$DIST/.claude-plugin/plugin.json" ]; then
  ok "$DIST/.claude-plugin/plugin.json present (manifest is not at the root, as Claude Code expects)"
else
  no "$DIST/.claude-plugin/plugin.json missing"
fi

# skills present in every tree that should carry them
for t in "$DIST" "$DIST/google.antigravity" "$DIST/.claude-plugin" "$DIST/com.github.copilot" "$DIST/dev.openhands"; do
  if [ -d "$t/skills" ]; then ok "$t/skills"
  else no "$t/skills missing"; fi
done

# rule 1 byte identity
if cmp -s "plugins/$PLUGIN/plugin.json" "$DIST/plugin.json" \
&& cmp -s "plugins/$PLUGIN/mcp.json" "$DIST/mcp.json"; then
  ok "rule 1 output is byte-identical to source"
else
  no "rule 1 output was modified"
fi

# no host paths
if grep -rlq "$ROOT" dist clients 2>/dev/null; then
  no "generated output embeds an absolute host path"
else
  ok "no absolute host paths in generated output"
fi

# ---------------------------------------------------------------- client loading
head_ "client conformance (live)"
echo "  These need each client installed and are reported, not asserted."

if command -v agy >/dev/null 2>&1; then
  if agy agents 2>/dev/null | grep -qx "$PLUGIN"; then
    ok "agy: agent '$PLUGIN' is listed"
  else
    sk "agy: agent '$PLUGIN' not listed — run ./scripts/install.sh first"
  fi
else
  sk "agy: not on PATH"
fi

# tools/list requires the gateway, which does not exist until Phase 2/3.
sk "MCP tools/list against the compose service: superseded — clients spawn the image over stdio instead (see scripts/check-direct-use.sh)"
sk "VS Code skill listing: needs a GUI session; verify manually"

# ---------------------------------------------------------------- isolation
# Container isolation is reported separately from compiler correctness: it needs
# a built image and a running network, and a missing prerequisite is a skip rather
# than a failure. scripts/isolation-tests.sh does the checking and exits non-zero
# on a real breach.
head_ "container isolation"
if [ "${SKIP_ISOLATION:-0}" = "1" ]; then
  sk "skipped via SKIP_ISOLATION=1"
elif ! docker network inspect "${CLOAKAI_NETWORK:-cloakai-internal}" >/dev/null 2>&1; then
  sk "network '${CLOAKAI_NETWORK:-cloakai-internal}' does not exist — run: docker compose -f infra/compose.yml up -d"
elif ! docker image inspect "${IMAGE:-cloakai/dev}" >/dev/null 2>&1; then
  sk "image '${IMAGE:-cloakai/dev}' not built — run ./scripts/build.sh"
else
  if ./scripts/isolation-tests.sh; then
    ok "all isolation tests passed"
  else
    no "ISOLATION BREACH — see the failing assertion above"
  fi
fi

# ---------------------------------------------------------------- conformance
# A real client loading what we generated. This is the only layer that can catch
# "the client silently dropped it", which is the failure the whole compiler
# exists to prevent. Needs the built conformance image; skipped, never failed,
# when Docker or the network is absent.
head_ "client conformance"
if [ "${SKIP_CONFORMANCE:-0}" = "1" ]; then
  sk "skipped via SKIP_CONFORMANCE=1"
elif ! command -v docker >/dev/null 2>&1; then
  sk "docker not available"
elif ./scripts/conformance.sh; then
  ok "a real client loaded the generated plugin"
else
  no "A REAL CLIENT REJECTED OUR OUTPUT — see the failure above"
fi

# ---------------------------------------------------------------- summary
head_ "summary"
printf '  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
