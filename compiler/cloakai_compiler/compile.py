"""Compiler: plugins/<name>/ -> dist/<name>/ + clients/.

Four rules, one mechanical branch:

  1. Conformant clients get plugin.json, skills/ and mcp.json byte-for-byte unchanged.
  2. Clients using a namespace with the root manifest get components generated in place.
  3. Clients needing their own manifest get a complete, independently installable tree.
  4. Config-only clients get a few lines of MCP config.

Determinism is a property of the code, not a cleanup pass: sorted iteration everywhere,
no timestamps, no absolute paths, stable key order, LF newlines, trailing newline.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .clients import (
    AGENT_MANIFEST, ANTIGRAVITY_SCHEMA, BY_ID, MCP_SCHEMA, NAMESPACE,
    PLUGIN_SCHEMA, TARGETS,
)
from . import validate as V


# ---------------------------------------------------------------- helpers

def write_json(path: Path, doc: Any) -> None:
    """Stable, diffable JSON: 2-space indent, insertion key order, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def agent_doc(plugin_root: Path) -> dict:
    path = plugin_root / NAMESPACE / AGENT_MANIFEST
    doc = V.load_json(path)
    if not isinstance(doc, dict):
        V._fail(str(path), "must be a JSON object")
    return doc


def endpoint_url(mcp: dict) -> str | None:
    """The first remote MCP endpoint in the source config, if any."""
    for entry in mcp.get("mcpServers", {}).values():
        url = entry.get("url")
        if isinstance(url, str):
            return url
    return None


# ---------------------------------------------------------------- rule 1

def emit_conformant(plugin_root: Path, mcp: dict) -> list[Path]:
    """Copy, never rewrite. If you find yourself editing these files, the client
    belongs in rule 2 or 3 instead."""
    out_root = Path("dist")
    written = []
    dst = out_root / plugin_root.name
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("plugin.json", "mcp.json"):
        src = plugin_root / name
        target = dst / name
        target.write_bytes(src.read_bytes())
        written.append(target)
    if (plugin_root / "skills").is_dir():
        copy_tree(plugin_root / "skills", dst / "skills")
        written.append(dst / "skills")
    return written


# ---------------------------------------------------------------- rule 2

def emit_namespace(plugin_root: Path, mcp: dict, target: dict, agent: dict) -> list[Path]:
    """Generate the client-specific component dirs inside the client's own namespace."""
    written = []
    base = Path("dist") / plugin_root.name / target["out"]
    base.mkdir(parents=True, exist_ok=True)
    written.append(base)

    # Skills are identical in every target that supports the Agent Skills format.
    if (plugin_root / "skills").is_dir():
        copy_tree(plugin_root / "skills", base / "skills")
        written.append(base / "skills")

    # An agent definition per source agent. Namespace clients read these in place,
    # so the body is the instructions and the description is the blurb.
    agents_dir = base / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    name = agent.get("name") or plugin_root.name
    body = render_agent_body(plugin_root, agent,
                             frontmatter=build_frontmatter(agent, target["frontmatter"]))
    (agents_dir / f"{name}.md").write_text(body, encoding="utf-8", newline="\n")
    written.append(agents_dir / f"{name}.md")

    # MCP config, only for targets whose namespace holds one.
    if target["mcp_container"]:
        write_json(base / target["mcp_container"], mcp_config_for(target, mcp))
        written.append(base / target["mcp_container"])

    return written


# ---------------------------------------------------------------- rule 3

def emit_tree(plugin_root: Path, mcp: dict, target: dict, agent: dict) -> list[Path]:
    """A complete, independently installable plugin tree."""
    written = []
    base = Path("dist") / plugin_root.name / target["out"]
    base.mkdir(parents=True, exist_ok=True)
    written.append(base)

    if (plugin_root / "skills").is_dir():
        copy_tree(plugin_root / "skills", base / "skills")
        written.append(base / "skills")

    if target["id"] == "antigravity":
        manifest = antigravity_manifest(plugin_root)
        V.check_antigravity_manifest(manifest, str(base / "plugin.json"))
        write_json(base / "plugin.json", manifest)
        written.append(base / "plugin.json")

        write_json(base / "mcp_config.json", mcp_config_for(target, mcp))
        written.append(base / "mcp_config.json")

        name = agent.get("name") or plugin_root.name
        agents_dir = base / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        fm = build_frontmatter(agent, target["frontmatter"])
        V.check_agy_agent_frontmatter(fm, str(agents_dir / f"{name}.md"))
        (agents_dir / f"{name}.md").write_text(
            render_agent_body(plugin_root, agent, frontmatter=fm), encoding="utf-8", newline="\n")
        written.append(agents_dir / f"{name}.md")

    elif target["id"] == "claude-code":
        # Claude Code's manifest lives at .claude-plugin/plugin.json, not the root.
        # The agent's own description wins over the plugin's: what the user invokes
        # is the agent, and keeping one description means editing agent.json is enough.
        manifest = {
            "$schema": "https://json.schemastore.org/claude-code-plugin.json",
            "name": agent.get("name") or plugin_name(plugin_root),
            "description": agent.get("description") or description_of(plugin_root),
            "version": version_of(plugin_root),
        }
        write_json(base / "plugin.json", manifest)
        written.append(base / "plugin.json")

        write_json(base / ".mcp.json", mcp_config_for(target, mcp))
        written.append(base / ".mcp.json")

        agents_dir = base / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / f"{agent.get('name', 'agent')}.md").write_text(
            render_agent_body(plugin_root, agent), encoding="utf-8", newline="\n")
        written.append(agents_dir / f"{agent.get('name', 'agent')}.md")

    return written


# ---------------------------------------------------------------- rule 4

def emit_config(plugin_root: Path, mcp: dict, target: dict, agent: dict) -> list[Path]:
    """A few lines of MCP config for a client that has no plugin format."""
    written = []
    out = Path("clients")
    out.mkdir(parents=True, exist_ok=True)

    if target["id"] == "opencode":
        # Key is "mcp", NOT "mcpServers". Type is "remote", NOT "sse" —
        # opencode tries Streamable HTTP then falls back to SSE on its own.
        servers = {}
        for sname, entry in mcp.get("mcpServers", {}).items():
            cfg = {"type": "remote", "url": entry.get("url"), "enabled": True, "oauth": False}
            if entry.get("headers"):
                cfg["headers"] = entry["headers"]
            servers[sname] = cfg
        # opencode has a real agent concept, so emit one. `description` is required
        # by opencode; mode defaults to "all" when unspecified.
        agent_cfg = {"description": agent.get("description", "")}
        if agent.get("mode"):
            agent_cfg["mode"] = agent["mode"]
        if agent.get("model"):
            agent_cfg["model"] = agent["model"]
        if agent.get("permission"):
            agent_cfg["permission"] = agent["permission"]

        doc = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": servers,
        }
        if agent_cfg["description"]:
            doc["agent"] = {agent.get("name", "agent"): agent_cfg}
        write_json(out / "opencode.json", doc)
        written.append(out / "opencode.json")

    elif target["id"] == "gemini-cli":
        write_json(out / "mcp_config.json", mcp_config_for(target, mcp))
        written.append(out / "mcp_config.json")

    return written


# ---------------------------------------------------------------- shared

def mcp_config_for(target: dict, mcp: dict) -> dict:
    """Rewrite the source mcp.json into this client's shape."""
    url_field = target["url_field"]
    servers = {}
    for sname, entry in sorted(mcp.get("mcpServers", {}).items()):
        out = {}
        if entry.get("type") == "stdio":
            out["type"] = "stdio"
            out["command"] = entry["command"]
            if entry.get("args"):
                out["args"] = list(entry["args"])
            if entry.get("env"):
                out["env"] = dict(entry["env"])
            if entry.get("cwd"):
                out["cwd"] = entry["cwd"]
        else:
            # agy uses serverUrl and does not recognise url or httpUrl.
            out["type"] = entry.get("type", "streamable-http")
            out[url_field] = entry.get("url")
            if entry.get("headers"):
                out["headers"] = dict(entry["headers"])
        servers[sname] = out
    return {"$schema": MCP_SCHEMA, "mcpServers": servers}


def build_frontmatter(agent: dict, mapping: dict | None) -> dict | None:
    """Map agent.json onto a client's frontmatter schema via the clients table.

    Returns None when the client wants no frontmatter. Only keys present in the
    mapping are considered, so we can never emit a field the client does not
    define. Keys the source leaves at their default are omitted for the same
    reason — agy documents defaults for every optional field, and writing a
    default out by hand is a chance to be wrong.
    """
    if mapping is None:
        return None

    # name and description are required by every schema that has frontmatter at all,
    # and both are always present in agent.json.
    out = {"name": agent.get("name", ""), "description": agent.get("description", "")}

    for src_key, dest_key in mapping.items():
        if src_key in ("name", "description"):
            continue
        if src_key in agent:
            out[dest_key] = agent[src_key]

    return out


def render_agent_body(plugin_root: Path, agent: dict, frontmatter: dict | None = None) -> str:
    """Build an agent markdown file.

    frontmatter=None means emit none at all, which is correct for the namespace and
    config targets — they carry agent metadata elsewhere.
    """
    parts = []
    if frontmatter is not None:
        lines = ["---"]
        for k, v in frontmatter.items():
            if isinstance(v, list):
                if not v:
                    lines.append(f"{k}: []")
                else:
                    lines.append(f"{k}:")
                    lines.extend(f"  - {i}" for i in v)
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        parts.append("\n".join(lines))

    parts.append(f"# {agent.get('name', 'agent')}\n")
    parts.append(agent.get("description", "").strip())
    parts.append("\n")
    return "\n".join(parts)


def plugin_name(plugin_root: Path) -> str:
    return V.load_json(plugin_root / "plugin.json").get("name", plugin_root.name)


def description_of(plugin_root: Path) -> str:
    return V.load_json(plugin_root / "plugin.json").get("description", "")


def version_of(plugin_root: Path) -> str:
    return V.load_json(plugin_root / "plugin.json").get("version", "0.0.0")


def antigravity_manifest(plugin_root: Path) -> dict:
    """Reduce our rich manifest to agy's closed schema: $schema, name, description only."""
    src = V.load_json(plugin_root / "plugin.json")
    return {
        "$schema": ANTIGRAVITY_SCHEMA,
        "name": src["name"],
        "description": src.get("description", ""),
    }
