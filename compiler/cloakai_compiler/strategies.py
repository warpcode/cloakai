"""Per-client output strategies.

`clients.py` holds one row of metadata per client and names a strategy here.
That split is deliberate:

  - Adding another client of a shape that already exists is a ROW in
    clients.py, with no change to this file. That is the common case.
  - Adding a client with a genuinely new output SHAPE needs a new strategy
    here. Manifests cannot be described as data alone — agy reduces ours to
    three keys, Claude Code puts it at a different path with a different
    $schema — so this is new code, and pretending otherwise would be a lie.

The earlier version branched on `target["id"]` inside emit_tree/emit_config,
which made "adding a client is a row" untrue. This module is the fix: the
branch is a dispatch through the table, not a chain of per-client ifs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import validate as V
from .clients import ANTIGRAVITY_SCHEMA, MCP_SCHEMA


def write_json(path: Path, doc: Any) -> None:
    """Stable, diffable JSON: 2-space indent, insertion key order, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- manifest builders

def manifest_antigravity(src: dict) -> dict:
    """agy: closed schema, additionalProperties false. Only $schema, name, description."""
    return {
        "$schema": ANTIGRAVITY_SCHEMA,
        "name": src["name"],
        "description": src.get("description", ""),
    }


def manifest_claude_code(src: dict, agent: dict) -> dict:
    """Claude Code: the agent's description wins, because the agent is what you invoke."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-plugin.json",
        "name": agent.get("name") or src["name"],
        "description": agent.get("description") or src.get("description", ""),
        "version": src.get("version", "0.0.0"),
    }


# ---------------------------------------------------------------- strategies

def tree_antigravity(ctx) -> list[Path]:
    """Full plugin tree for agy."""
    base, written = _tree_base(ctx)
    manifest = manifest_antigravity(ctx.src_manifest)
    V.check_antigravity_manifest(manifest, str(base / "plugin.json"))
    write_json(base / "plugin.json", manifest)
    written.append(base / "plugin.json")

    write_json(base / "mcp_config.json", ctx.mcp_config())
    written.append(base / "mcp_config.json")

    name = ctx.agent_name
    agents_dir = base / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    fm = ctx.frontmatter()
    path = agents_dir / f"{name}.md"
    V.check_agy_agent_frontmatter(fm, str(path))
    path.write_text(ctx.render_agent(fm), encoding="utf-8", newline="\n")
    written.append(path)

    return written


def tree_claude_code(ctx) -> list[Path]:
    """Claude Code: manifest at .claude-plugin/plugin.json, not the root."""
    base, written = _tree_base(ctx)
    manifest = manifest_claude_code(ctx.src_manifest, ctx.agent)
    write_json(base / "plugin.json", manifest)
    written.append(base / "plugin.json")

    write_json(base / ".mcp.json", ctx.mcp_config())
    written.append(base / ".mcp.json")

    agents_dir = base / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{ctx.agent_name}.md"
    path.write_text(ctx.render_agent(None), encoding="utf-8", newline="\n")
    written.append(path)

    return written


def config_opencode(ctx) -> list[Path]:
    """opencode: key is `mcp` not `mcpServers`; type is `remote`, never `sse`.

    opencode tries Streamable HTTP and falls back to SSE on its own, so
    emitting an `sse` type would be wrong as well as unsupported.
    """
    servers = {}
    for name, entry in sorted(ctx.mcp.get("mcpServers", {}).items()):
        cfg = {"type": "remote", "url": entry.get("url"), "enabled": True, "oauth": False}
        if entry.get("headers"):
            cfg["headers"] = dict(entry["headers"])
        servers[name] = cfg

    doc = {"$schema": "https://opencode.ai/config.json", "mcp": servers}

    agent_cfg = {"description": ctx.agent.get("description", "")}
    if ctx.agent.get("mode"):
        agent_cfg["mode"] = ctx.agent["mode"]
    if ctx.agent.get("model"):
        agent_cfg["model"] = ctx.agent["model"]
    if ctx.agent.get("permission"):
        agent_cfg["permission"] = ctx.agent["permission"]
    if agent_cfg["description"]:
        doc["agent"] = {ctx.agent_name: agent_cfg}

    path = ctx.clients_root / "opencode.json"
    write_json(path, doc)
    return [path]


def config_gemini_cli(ctx) -> list[Path]:
    """Gemini CLI: same content as the source mcp.json, url rewritten to serverUrl."""
    path = ctx.clients_root / "mcp_config.json"
    write_json(path, ctx.mcp_config())
    return [path]


# ---------------------------------------------------------------- shared helpers

def _tree_base(ctx) -> tuple[Path, list[Path]]:
    base = ctx.out_root / ctx.plugin_name / ctx.target["out"]
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = [base]
    if ctx.skills_dir.is_dir():
        ctx.copy_tree(ctx.skills_dir, base / "skills")
        written.append(base / "skills")
    return base, written


#: strategy name -> callable. A client row names one of these keys.
STRATEGIES = {
    "tree:antigravity": tree_antigravity,
    "tree:claude-code": tree_claude_code,
    "config:opencode": config_opencode,
    "config:gemini-cli": config_gemini_cli,
}


def resolve(strategy: str):
    try:
        return STRATEGIES[strategy]
    except KeyError:
        raise KeyError(
            f"unknown strategy {strategy!r}; known: {sorted(STRATEGIES)}"
        ) from None
