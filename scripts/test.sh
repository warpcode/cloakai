#!/usr/bin/env bash
# Verify the compiled output.
#
# Two layers:
#   - Compiler unit tests and a determinism check, which must always pass.
#   - Client conformance, which is reported separately because the MCP endpoint
#     cannot resolve until the gateway exists.
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PLUGIN="${PLUGIN:-dev}"
DIST="$ROOT/dist/$PLUGIN"

pass=0; fail=0; skip=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
sk()   { printf '  \033[33m–\033[0m %s\n' "$*"; skip=$((skip+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

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
if ./scripts/compile.sh --quiet 2>/dev/null; then
  before=$(find dist clients -type f -exec sha256sum {} \; 2>/dev/null | sort)
  if ./scripts/compile.sh --quiet 2>/dev/null; then
    after=$(find dist clients -type f -exec sha256sum {} \; 2>/dev/null | sort)
    if [ "$before" = "$after" ]; then
      ok "two runs produce byte-identical output"
    else
      no "two runs differ — something non-deterministic crept in"
      diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | head -10
    fi
  else
    no "second compile failed"
  fi
else
  no "compile failed"
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

# opencode: key is mcp, every entry is type remote, and "sse" appears nowhere.
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
    assert e.get('type') == 'remote', f'{name}: type is {e.get(\"type\")!r}, expected remote'
    assert e.get('url'), f'{name}: no url'
    assert e.get('enabled') is True, f'{name}: not enabled'
assert 'sse' not in json.dumps(d), 'emits an sse type'
print(f'{len(servers)} server(s): ' + ', '.join(sorted(servers)))
" 2>&1)
  if [ $? -eq 0 ]; then ok "clients/opencode.json uses mcp + type remote, no sse ($out)"
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
sk "MCP tools/list: EXPECTED TO FAIL — gateway not built yet (Phase 2/3)"
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

# ---------------------------------------------------------------- summary
head_ "summary"
printf '  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
