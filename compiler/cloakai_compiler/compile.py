"""Compiler: plugins/<name>/ -> dist/<name>/ + clients/.

Four rules, one mechanical branch:

  1. Conformant clients get plugin.json, skills/ and mcp.json byte-for-byte unchanged.
  2. Clients using a namespace with the root manifest get components generated in place.
  3. Clients needing their own manifest get a complete, independently installable tree.
  4. Config-only clients get a few lines of MCP config.

Rules 2-4 dispatch through clients.py, which names a strategy in strategies.py.
There is no per-client `if` in this file. Adding a client of an existing output
shape is a row; a new shape is a strategy.

Determinism is a property of the code, not a cleanup pass: sorted iteration
everywhere, no timestamps, no absolute paths, stable key order, LF newlines,
trailing newline.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .clients import NAMESPACE
from .strategies import resolve, write_json


# ---------------------------------------------------------------- rule 1

def emit_conformant(plugin_root: Path, mcp: dict, out_root: Path) -> list[Path]:
    """Copy, never rewrite. If you find yourself editing these files, the client
    belongs in rule 2 or 3 instead."""
    written: list[Path] = []
    dst = out_root / plugin_root.name
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("plugin.json", "mcp.json"):
        target = dst / name
        target.write_bytes((plugin_root / name).read_bytes())
        written.append(target)
    if (plugin_root / "skills").is_dir():
        shutil.copytree(plugin_root / "skills", dst / "skills", symlinks=False, dirs_exist_ok=True)
        written.append(dst / "skills")
    return written


# ---------------------------------------------------------------- rule 2

def emit_namespace(ctx) -> list[Path]:
    """Generate the client-specific component dirs inside the client's own namespace."""
    base = ctx.out_root / ctx.plugin_name / ctx.target["out"]
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = [base]

    # Skills are identical in every target that supports the Agent Skills format.
    if ctx.skills_dir.is_dir():
        ctx.copy_tree(ctx.skills_dir, base / "skills")
        written.append(base / "skills")

    agents_dir = base / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{ctx.agent_name}.md"
    path.write_text(ctx.render_agent(ctx.frontmatter()), encoding="utf-8", newline="\n")
    written.append(path)

    return written


# ---------------------------------------------------------------- rules 3 and 4

def emit_via_strategy(ctx) -> list[Path]:
    ctx.clients_root.mkdir(parents=True, exist_ok=True)
    return resolve(ctx.target["strategy"])(ctx)


# ---------------------------------------------------------------- shared

def build_frontmatter(agent: dict, mapping: dict | None) -> dict:
    """Map agent.json onto a client's frontmatter schema via the clients table.

    Only keys present in the mapping are considered, so we can never emit a field
    the client does not define. Keys the source leaves at their documented default
    are omitted for the same reason — writing a default out by hand is a chance
    to be wrong, and agy rejects nothing for it but a future version might.
    """
    if mapping is None:
        return {}

    # name and description are required by every schema that has frontmatter.
    out: dict[str, Any] = {"name": agent.get("name", ""), "description": agent.get("description", "")}

    for src_key, dest_key in mapping.items():
        if src_key in ("name", "description"):
            continue
        if src_key in agent:
            out[dest_key] = agent[src_key]

    return out


def render_agent_body(plugin_root: Path, agent: dict, frontmatter: dict | None = None) -> str:
    """Build an agent markdown file.

    frontmatter=None or {} means emit none at all, which is correct for the
    namespace and config targets — they carry agent metadata elsewhere.
    """
    parts: list[str] = []
    if frontmatter:
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


def agent_doc(plugin_root: Path) -> dict:
    from . import validate as V
    path = plugin_root / NAMESPACE / "agent.json"
    doc = V.load_json(path)
    if not isinstance(doc, dict):
        V._fail(str(path), "must be a JSON object")
    return doc


__all__ = [
    "agent_doc", "build_frontmatter", "emit_conformant", "emit_namespace",
    "emit_runtime", "emit_via_strategy", "render_agent_body", "write_json",
]


# ---------------------------------------------------------------- our own runtime

def emit_runtime(plugin_root: Path, agent: dict, out_root: Path) -> list[Path]:
    """Emit the artifacts only cloakai's own container consumes.

    Not one of the four rules, because no client reads these. `agent.yaml` is the
    docker-agent config for the container's mcp mode; `instructions.md` is the
    system prompt it points at. Both derive from agent.json so that changing the
    runtime still means editing exactly one file.
    """
    written: list[Path] = []
    base = out_root / plugin_root.name
    base.mkdir(parents=True, exist_ok=True)

    instructions = base / "instructions.md"
    instructions.write_text(render_instructions(plugin_root, agent), encoding="utf-8", newline="\n")
    written.append(instructions)

    name = agent.get("name") or plugin_root.name
    # The endpoint comes from agent.json so the proxy address is not hardcoded here.
    # It is `upstream`, NOT `model`: agy's agent frontmatter has its own `model` field
    # meaning a tier (inherit/flash/pro), and reusing the key rendered this dict into
    # an enum. A unit test caught it, which is the argument for having one.
    upstream = agent.get("upstream") or {}
    proxy = upstream.get("base_url") or "http://litellm:4000/v1"
    model_id = upstream.get("id") or "default"

    agent_yaml = f"""\
# GENERATED from plugins/{plugin_root.name}/{NAMESPACE}/agent.json by scripts/compile.sh.
# Do not edit: edit agent.json and recompile.
#
# Config version 16 and `agents` as a MAPPING keyed by agent name are both required.
# A list fails with "sequence was used where mapping is expected". A model must
# resolve, either here or via a provider credential, or the config is rejected.
#
# instruction_file is RELATIVE to this file. An absolute path is rejected outright
# with "must be a local relative path inside the config directory", which is easy
# to hit because the file lives at /agent/agent.yaml inside the image.
# See docs/verification/phase-0.md Check 1.
version: "16"
models:
  proxy-model:
    provider: openai
    model: {yaml_scalar(model_id)}
    base_url: {yaml_scalar(proxy)}
agents:
  {yaml_scalar(name)}:
    description: {yaml_scalar(agent.get("description", ""))}
    instruction_file: instructions.md
    model: proxy-model
"""
    path = base / "agent.yaml"
    path.write_text(agent_yaml, encoding="utf-8", newline="\n")
    written.append(path)

    return written


def yaml_scalar(value: str) -> str:
    """Quote a scalar so it cannot break the document.

    YAML 1.2 double-quoted scalars use JSON-compatible escapes, so json.dumps
    gives a correct result for any input, newlines and backslashes included.
    The previous hand-rolled version escaped only backslashes and quotes while
    its docstring promised to handle newlines.
    """
    return json.dumps(value, ensure_ascii=False)


def render_instructions(plugin_root: Path, agent: dict) -> str:
    """Compose the system prompt from the same skills every client gets.

    The bodies are read from plugins/, not from dist/, because a SKILL.md is
    identical in every target by design and re-reading the source keeps this a
    pure function of the source tree.
    """
    parts = [f"# {agent.get('name', 'agent')}", "", agent.get("description", "").strip(), ""]

    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        parts.append("## Skills")
        parts.append("")
        for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            parts.append(f"### {entry.name}")
            parts.append("")
            parts.append(f"Source: skills/{entry.name}/SKILL.md")
            parts.append("")
            parts.append(skill_md.read_text(encoding="utf-8").strip())
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"
