"""Emit context: everything a strategy needs, with no module globals.

A strategy is handed this and nothing else, so it cannot reach back into the
compiler's internals or depend on the process cwd.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .clients import MCP_SCHEMA


class Context:
    def __init__(self, *, plugin_root: Path, out_root: Path, clients_root: Path,
                 target: dict, src_manifest: dict, mcp: dict, agent: dict) -> None:
        self.plugin_root = plugin_root
        self.plugin_name = plugin_root.name
        self.out_root = out_root
        self.clients_root = clients_root
        self.target = target
        self.src_manifest = src_manifest
        self.mcp = mcp
        self.agent = agent

    @property
    def skills_dir(self) -> Path:
        return self.plugin_root / "skills"

    @property
    def agent_name(self) -> str:
        return self.agent.get("name") or self.src_manifest.get("name") or self.plugin_name

    def copy_tree(self, src: Path, dst: Path) -> None:
        shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)

    def mcp_config(self) -> dict:
        """Rewrite the source mcp.json into this client's shape."""
        url_field = self.target["url_field"]
        servers = {}
        for name, entry in sorted(self.mcp.get("mcpServers", {}).items()):
            if entry.get("type") == "stdio":
                # agy writes a stdio server as command + args + disabled, with no
                # `type` at all. Taken from what `agy mcp add` actually produced
                # rather than from the plugin schema, because agy is the consumer.
                out = {"command": entry["command"]}
                if entry.get("args"):
                    out["args"] = list(entry["args"])
                if entry.get("env"):
                    out["env"] = dict(entry["env"])
                if entry.get("cwd"):
                    out["cwd"] = entry["cwd"]
                out["disabled"] = False
            else:
                # agy uses serverUrl and does not recognise url or httpUrl.
                out = {"type": entry.get("type", "streamable-http"), url_field: entry.get("url")}
                if entry.get("headers"):
                    out["headers"] = dict(entry["headers"])
            servers[name] = out
        return {"$schema": MCP_SCHEMA, "mcpServers": servers}

    def frontmatter(self) -> dict:
        from .compile import build_frontmatter
        return build_frontmatter(self.agent, self.target["frontmatter"])

    def render_agent(self, frontmatter: dict | None) -> str:
        from .compile import render_agent_body
        return render_agent_body(self.plugin_root, self.agent, frontmatter=frontmatter)
