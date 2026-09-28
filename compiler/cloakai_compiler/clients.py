"""Canonical clients table.

The thesis of cloakai is that per-client differences live in ONE place, so adding a
client is a row here rather than a code change scattered across the compiler.

Fields per target:

  rule          Which compiler rule emits this target (1 copy, 2 namespace, 3 tree, 4 config)
  out           Path under dist/ or clients/ to write
  kind          "conformant" | "namespace" | "tree" | "config"
  url_field     The remote-endpoint key this client uses for an MCP URL
  mcp_container The file name the MCP config is written under, if any
  strategy      names an entry in strategies.STRATEGIES. Rule 2 (kind=namespace)
                needs none: it is uniform. Rules 3 and 4 do, because a manifest
                cannot be described as data — agy reduces ours to three keys,
                Claude Code puts it at a different path. Adding a client of an
                EXISTING output shape is a row here and nothing else. A NEW shape
                needs a new strategy in strategies.py, and that is real new code.
  frontmatter   source agent.json key -> this client's frontmatter key, or None
                for no frontmatter. Only keys listed here are emitted, so we never
                write a field the client does not define.
"""

# Canonical schema identifiers.
AGENT_PLUGINS_VERSION = "1.0.0"
PLUGIN_SCHEMA = f"https://agent-plugins.org/schemas/{AGENT_PLUGINS_VERSION}/plugin.schema.json"
MCP_SCHEMA = f"https://agent-plugins.org/schemas/{AGENT_PLUGINS_VERSION}/mcp.schema.json"
ANTIGRAVITY_SCHEMA = "https://antigravity.google/schemas/v1/plugin.json"

# Our own extension namespace, and its manifest member file.
NAMESPACE = "io.github.warpcode.cloakai"
AGENT_MANIFEST = "agent.json"

# Order matters only for stable, reviewable diffs.
TARGETS = [
    {
        "id": "vscode",
        "rule": 2,
        "out": "com.github.copilot",
        "kind": "namespace",
        "strategy": None,
        "url_field": "url",
        "mcp_container": None,
        "frontmatter": None,
    },
    {
        "id": "openhands",
        "rule": 2,
        "out": "dev.openhands",
        "kind": "namespace",
        "strategy": None,
        "url_field": "url",
        "mcp_container": None,
        "frontmatter": None,
    },
    {
        "id": "antigravity",
        "rule": 3,
        "out": "google.antigravity",
        "kind": "tree",
        "url_field": "serverUrl",
        "mcp_container": "mcp_config.json",
        "strategy": "tree:antigravity",
        # agy registers an agent by its frontmatter `name`. Emitting the body with no
        # frontmatter validates clean and then silently loads nothing — the exact
        # failure mode Task 4 exists to prevent, reached the other way.
        # Only fields agy documents are mapped; see schemas/antigravity-agent-frontmatter.json.
        "frontmatter": {
            "name": "name",
            "description": "description",
            "tools": "tools",
            "main_agent": "mainAgent",
            "subagent": "subagent",
            "model": "model",
            "command_execution_policy": "commandExecutionPolicy",
        },
    },
    {
        "id": "claude-code",
        "rule": 3,
        "out": ".claude-plugin",
        "kind": "tree",
        "url_field": "url",
        "mcp_container": ".mcp.json",
        "strategy": "tree:claude-code",
        "frontmatter": None,
    },
    {
        "id": "opencode",
        "rule": 4,
        "out": "opencode.json",
        "kind": "config",
        "url_field": "url",
        "mcp_container": None,
        "strategy": "config:opencode",
        "frontmatter": None,
    },
    {
        "id": "gemini-cli",
        "rule": 4,
        "out": "mcp_config.json",
        "kind": "config",
        "url_field": "serverUrl",
        "mcp_container": "mcp_config.json",
        "strategy": "config:gemini-cli",
        "frontmatter": None,
    },
]

BY_ID = {t["id"]: t for t in TARGETS}
