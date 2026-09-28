#!/usr/bin/env bash
# Install the compiled plugin trees into each target client.
#
# Idempotent: re-running reports what it skipped rather than piling up copies.
# Nothing here talks to a gateway, so this works before any container exists.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PLUGIN="${PLUGIN:-dev}"
DIST="$ROOT/dist/$PLUGIN"
CLIENTS="$ROOT/clients"

dry=0
targets=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1 ;;
    --only=*) targets+=("${arg#*=}") ;;
    -h|--help)
      sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
      echo
      echo "Usage: ./scripts/install.sh [--dry-run] [--only=<target>]"
      echo "  targets: $(grep -o '"id": "[a-z-]*"' compiler/cloakai_compiler/clients.py | sed 's/.*: "//;s/"//' | tr '\n' ' ')"
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

wants() {
  [ ${#targets[@]} -eq 0 ] && return 0
  local t
  for t in "${targets[@]}"; do [ "$t" = "$1" ] && return 0; done
  return 1
}

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[33m–\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*" >&2; }

run() { if [ "$dry" -eq 1 ]; then say "would run: $*"; else "$@"; fi; }

[ -d "$DIST" ] || { echo "error: $DIST missing. Run ./scripts/compile.sh first." >&2; exit 1; }

echo "installing $PLUGIN from $DIST"
echo

# ------------------------------------------------------------------ agy
if wants antigravity; then
  if command -v agy >/dev/null 2>&1; then
    if agy plugin list 2>/dev/null | grep -q "\"name\": \"$PLUGIN\""; then
      skip "agy: $PLUGIN already installed"
    else
      run agy plugin install "$DIST/google.antigravity"
      ok "agy: installed google.antigravity"
    fi
  else
    skip "agy: not on PATH — copy $DIST/google.antigravity into ~/.gemini/config/plugins/"
  fi
fi

# ------------------------------------------------------------------ opencode
if wants opencode; then
  cfg="$CLIENTS/opencode.json"
  if [ -f "$cfg" ]; then
    if [ "$dry" -eq 1 ]; then
      say "would merge $cfg into your opencode config"
    else
      say "merge this into your opencode config (~/.config/opencode/opencode.json):"
      sed 's/^/    /' "$cfg"
    fi
  else
    skip "opencode: $cfg not built"
  fi
fi

# ------------------------------------------------------------------ VS Code
if wants vscode; then
  say "add to your VS Code settings.json:"
  printf '    "chat.pluginLocations": { "cloakai": %s }\n' "\"$DIST\"" | sed 's/^/  /'
fi

# ------------------------------------------------------------------ OpenHands
if wants openhands; then
  say "OpenHands: add $DIST as a local plugin location (namespace dev.openhands)"
fi

# ------------------------------------------------------------------ Claude Code
if wants claude-code; then
  if [ -d "$DIST/.claude-plugin" ]; then
    skip "claude-code: copy $DIST/.claude-plugin into your project, or merge $DIST/.claude-plugin/.mcp.json"
  fi
fi

echo
echo "done. The MCP endpoint is a gateway that does not exist yet, so tools will not"
echo "resolve until Phase 2/3. That is expected in this phase — see docs/verification/phase-0.md."
