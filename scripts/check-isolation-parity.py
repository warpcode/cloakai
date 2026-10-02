"""Compare the declared isolation in agent.json and the generated catalogue.

Three places once described an agent's isolation:

  agents/isolation-flags      the word list the shell scripts read
  plugins/*/.../agent.json     the source of truth
  dist/agents.json             what the CLI actually runs

infra/compose.yml was a fourth and is gone. This compares the remaining three
against the middle one, rather than against each other, so editing the flag file
and the generated output together cannot make a stale comparison pass.

Normalisation is the whole difficulty. The flag file writes
`--security-opt no-new-privileges` as two words; agent.json writes it as one token.
A raw token comparison reports a difference that is not there. And a generic "the
token after a flag is its value" heuristic turns `--read-only` into
`--read-only=--tmpfs`, because --read-only takes no value. So only the flags that
genuinely take a value are treated as doing so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Flags that take a value. Everything else is boolean or self-contained.
VALUE_FLAGS = {
    "--network", "--tmpfs", "--security-opt", "--cap-drop",
    "--pids-limit", "--memory", "--cpus",
}


def pairs(argv: list[str]) -> list[str]:
    """Normalise argv to sorted flag=value pairs (or bare flags)."""
    out: list[str] = []
    pending: str | None = None
    for token in argv:
        if pending is not None:
            out.append(f"{pending}={token}")
            pending = None
        elif token.startswith("--") and "=" not in token and token in VALUE_FLAGS:
            pending = token
        else:
            out.append(token)
    return sorted(out)


def read_flag_file(path: Path) -> list[str]:
    """The word list, parsed exactly as run.sh and the isolation tests parse it."""
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words += line.split()
    return words


def declared_flags(spec: dict) -> list[str]:
    """The flags agent.json declares, as tokens.

    --network is deliberately absent: it is a per-agent choice attached by the CLI,
    not a property of the shared flag file.
    """
    isolation = spec.get("isolation") or {}
    limits = spec.get("limits") or {}
    flags: list[str] = []
    if isolation.get("read_only"):
        flags.append("--read-only")
    for mount in isolation.get("tmpfs", []):
        flags += ["--tmpfs", str(mount)]
    for cap in isolation.get("cap_drop", []):
        flags.append(f"--cap-drop={cap}")
    if isolation.get("no_new_privileges"):
        flags.append("--security-opt=no-new-privileges")
    for key, flag in (("pids", "--pids-limit"), ("memory", "--memory"), ("cpus", "--cpus")):
        if limits.get(key):
            flags.append(f"{flag}={limits[key]}")
    return flags


def report(label: str, expected: list[str], actual: list[str]) -> bool:
    if expected == actual:
        print(f"  ok   {label}")
        return True
    print(f"  DIFF {label}")
    only_expected = [f for f in expected if f not in actual]
    only_actual = [f for f in actual if f not in expected]
    for flag in only_expected:
        print(f"         in the flag file but not declared: {flag}")
    for flag in only_actual:
        print(f"         declared but not in the flag file: {flag}")
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check-isolation-parity.py <isolation-flags> <agents.json>",
              file=sys.stderr)
        return 2

    flag_file = Path(argv[0])
    catalog_path = Path(argv[1])
    expected = pairs(read_flag_file(flag_file))
    failures = 0

    for source in sorted(Path("plugins").glob("*/io.github.warpcode.cloakai/agent.json")):
        plugin = source.parents[1].name
        declared = pairs(declared_flags(json.loads(source.read_text(encoding="utf-8"))))
        if not report(plugin, expected, declared):
            failures += 1

    if not catalog_path.exists():
        print(f"  DIFF {catalog_path} is not built — run scripts/compile.sh")
        return 1
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for name, spec in sorted(catalog.get("agents", {}).items()):
        if not report(f"catalogue:{name}", expected, pairs(spec.get("flags", []))):
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
