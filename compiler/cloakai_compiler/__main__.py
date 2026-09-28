"""Entry point. Validate the source, then emit every target.

Exits non-zero on any validation failure, naming the offending file.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .clients import NAMESPACE, TARGETS
from .compile import (
    agent_doc, emit_config, emit_conformant, emit_namespace, emit_tree,
)
from . import validate as V

REPO_ROOT = Path(__file__).resolve().parents[2]


def clean_output() -> None:
    """Remove generated trees so a rebuild cannot inherit a stale file."""
    for path in (REPO_ROOT / "dist", REPO_ROOT / "clients"):
        if path.exists():
            shutil.rmtree(path)


def compile_plugin(plugin_root: Path) -> list[Path]:
    # ---- validate source before emitting anything (fail loudly, early) ----
    plugin_json = V.load_json(plugin_root / "plugin.json")
    V.check_plugin_manifest(plugin_json, str(plugin_root / "plugin.json"))

    mcp_json = V.load_json(plugin_root / "mcp.json")
    V.check_mcp_config(mcp_json, plugin_json["$schema"], str(plugin_root / "mcp.json"))

    skills_dir = plugin_root / "skills"
    skills = V.check_skills(skills_dir, str(skills_dir))
    for name in skills:
        skill_md = skills_dir / name / "SKILL.md"
        for child in (skills_dir / name).iterdir():
            V.check_no_escape(plugin_root, child, str(child))
        V.check_no_escape(plugin_root, skill_md, str(skill_md))

    agent = agent_doc(plugin_root)
    agent_path = plugin_root / NAMESPACE / "agent.json"
    agent.setdefault("name", plugin_json["name"])
    V.check_no_escape(plugin_root, agent_path, str(agent_path))

    # ---- emit ----
    written: list[Path] = []
    written += emit_conformant(plugin_root, mcp_json)

    for target in TARGETS:
        if target["kind"] == "namespace":
            written += emit_namespace(plugin_root, mcp_json, target, agent)
        elif target["kind"] == "tree":
            written += emit_tree(plugin_root, mcp_json, target, agent)
        elif target["kind"] == "config":
            written += emit_config(plugin_root, mcp_json, target, agent)

    return [REPO_ROOT / w for w in written]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compile a cloakai source plugin for every target client.")
    ap.add_argument("plugin", nargs="?", default="plugins/dev", help="source plugin directory")
    ap.add_argument("--keep", action="store_true", help="do not clean dist/ and clients/ first")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    plugin_root = (REPO_ROOT / args.plugin).resolve()
    if not plugin_root.is_dir():
        print(f"error: {args.plugin} is not a directory", file=sys.stderr)
        return 2

    if not args.keep:
        clean_output()

    try:
        written = compile_plugin(plugin_root)
    except V.ValidationError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"compiled {plugin_root.name}: {len(written)} paths written")
        for path in written:
            if path.is_dir():
                continue
            try:
                shown = path.relative_to(REPO_ROOT)
            except ValueError:
                shown = path
            print(f"  {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
