"""Entry point. Validate the source, then emit every target.

Exits non-zero on any validation failure, naming the offending file.

The output root is an explicit parameter, never inferred from the process cwd.
An earlier version derived it from cwd while cleaning REPO_ROOT, so running the
compiler from any other directory deleted the committed dist/ tree. The two
destinations are now threaded through explicitly and cleaned together.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .clients import NAMESPACE, TARGETS
from .compile import (
    agent_doc, emit_conformant, emit_namespace, emit_runtime, emit_via_strategy,
)
from .context import Context
from . import validate as V

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = "dist"
CLIENTS_DIR = "clients"


def clean_output(dist_root: Path, clients_root: Path) -> None:
    """Remove generated trees so a rebuild cannot inherit a stale file.

    Only ever deletes the two paths it was given. It must not fall back to a
    repo-relative default, because that is how the earlier version destroyed a
    working tree.
    """
    for path in (dist_root, clients_root):
        if path.is_dir():
            shutil.rmtree(path)


def compile_plugin(plugin_root: Path, dist_root: Path, clients_root: Path) -> list[Path]:
    # ---- validate source before emitting anything (fail loudly, early) ----
    for file_name in ("plugin.json", "mcp.json"):
        path = plugin_root / file_name
        V.check_no_escape(plugin_root, path, str(path))

    plugin_json = V.load_json(plugin_root / "plugin.json")
    V.check_plugin_manifest(plugin_json, str(plugin_root / "plugin.json"))

    mcp_json = V.load_json(plugin_root / "mcp.json")
    V.check_mcp_config(mcp_json, plugin_json["$schema"], str(plugin_root / "mcp.json"))

    skills_dir = plugin_root / "skills"
    skills = V.check_skills(skills_dir, str(skills_dir))
    for name in skills:
        for descendant in (skills_dir / name).rglob("*"):
            V.check_no_escape(plugin_root, descendant, str(descendant))

    agent = agent_doc(plugin_root)
    agent_path = plugin_root / NAMESPACE / "agent.json"
    agent.setdefault("name", plugin_json["name"])
    V.check_no_escape(plugin_root, agent_path, str(agent_path))

    # ---- emit ----
    written: list[Path] = []
    written += emit_conformant(plugin_root, mcp_json, dist_root)

    for target in TARGETS:
        ctx = Context(plugin_root=plugin_root, out_root=dist_root, clients_root=clients_root,
                      target=target, src_manifest=plugin_json, mcp=mcp_json, agent=agent)
        if target["kind"] == "namespace":
            written += emit_namespace(ctx)
        else:
            written += emit_via_strategy(ctx)

    # Our own container artifacts. Not one of the four rules — no client reads
    # these — but they must derive from agent.json like everything else.
    written += emit_runtime(plugin_root, agent, dist_root)

    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compile a cloakai source plugin for every target client.")
    ap.add_argument("plugin", nargs="?", default="plugins/dev", help="source plugin directory")
    ap.add_argument("--out", default=DIST_DIR, help="directory for generated plugin trees")
    ap.add_argument("--clients-out", default=CLIENTS_DIR, help="directory for generated config files")
    ap.add_argument("--keep", action="store_true", help="do not clean the output directories first")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    # Relative output paths resolve against cwd; absolute ones are used as given.
    dist_root = Path(args.out).resolve()
    clients_root = Path(args.clients_out).resolve()

    plugin_root = Path(args.plugin).resolve()
    if not plugin_root.is_dir():
        print(f"error: {args.plugin} is not a directory", file=sys.stderr)
        return 2

    if not args.keep:
        clean_output(dist_root, clients_root)

    try:
        written = compile_plugin(plugin_root, dist_root, clients_root)
    except V.ValidationError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"compiled {plugin_root.name}: {len(written)} paths written")
        for path in written:
            if path.is_dir():
                continue
            try:
                shown = path.relative_to(Path.cwd())
            except ValueError:
                shown = path
            print(f"  {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
