"""Validation. Fails loudly, names the offending file.

Task 4 of #13 exists because VS Code silently skips malformed skills. A plugin that
half-works is worse than one that refuses to build, so every check here raises
ValidationError with a path and a fix rather than warning and continuing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .clients import ANTIGRAVITY_SCHEMA, MCP_SCHEMA, PLUGIN_SCHEMA

PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
ANTIGRAVITY_NAME_RE = re.compile(r"^[a-zA-Z0-9-_]+$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CWD_RE = re.compile(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))")
STDIO_CMD_RE = re.compile(r"^[A-Za-z0-9._-]+$")

PLUGIN_TOP_LEVEL = {
    "$schema", "name", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "extensions",
}
AUTHOR_KEYS = {"name", "email", "url"}

MCP_TOP_LEVEL = {"$schema", "mcpServers"}
STDIO_KEYS = {"type", "command", "args", "env", "cwd"}
REMOTE_KEYS = {"type", "url", "headers"}
RESERVED_ENV = {"PLUGIN_ROOT", "PLUGIN_DATA"}

REMOTE_TYPES = {"streamable-http", "sse"}


class ValidationError(Exception):
    """A validation failure that must stop the build."""


def _fail(where: str, message: str) -> None:
    raise ValidationError(f"{where}: {message}")


def load_json(path: Path) -> Any:
    if not path.is_file():
        _fail(str(path), "missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(str(path), f"unparseable JSON at line {e.lineno} col {e.colno}: {e.msg}")


def check_plugin_manifest(doc: Any, where: str) -> dict:
    if not isinstance(doc, dict):
        _fail(where, "must be a JSON object")

    unknown = set(doc) - PLUGIN_TOP_LEVEL
    if unknown:
        _fail(where, f"unknown top-level field(s): {', '.join(sorted(unknown))}. "
                     f"Permitted: {', '.join(sorted(PLUGIN_TOP_LEVEL))}")

    if doc.get("$schema") != PLUGIN_SCHEMA:
        _fail(where, f"$schema must be exactly {PLUGIN_SCHEMA!r}, got {doc.get('$schema')!r}")

    name = doc.get("name")
    if not isinstance(name, str) or not name:
        _fail(where, "'name' is required")
    if len(name) > 64:
        _fail(where, f"'name' is {len(name)} characters, max 64")
    if not PLUGIN_NAME_RE.match(name):
        _fail(where, f"'name' {name!r} is invalid: must match {PLUGIN_NAME_RE.pattern} "
                     f"(lowercase [a-z0-9.-] only, alphanumeric at both ends, no '--', no '..')")

    if "author" in doc:
        author = doc["author"]
        if not isinstance(author, dict):
            _fail(where, "'author' must be an object")
        extra = set(author) - AUTHOR_KEYS
        if extra:
            _fail(where, f"'author' may contain only {sorted(AUTHOR_KEYS)}, "
                         f"found unknown: {sorted(extra)}")
        for k, v in author.items():
            if not isinstance(v, str):
                _fail(where, f"'author.{k}' must be a string, got {type(v).__name__}")

    if "keywords" in doc and not isinstance(doc["keywords"], list):
        _fail(where, "'keywords' must be an array")

    if "extensions" in doc and not isinstance(doc["extensions"], dict):
        _fail(where, "'extensions' must be an object")

    return doc


_SCHEMA_VERSION_RE = re.compile(r"/schemas/([0-9]+\.[0-9]+\.[0-9]+)/")


def schema_version(uri: str | None) -> str | None:
    """Extract the release from a canonical schema identifier.

    The spec requires the MCP config to target the same Agent Plugins version as the
    manifest, not the same URL — plugin.schema.json and mcp.schema.json are different
    documents that must name the same release.
    """
    if not isinstance(uri, str):
        return None
    m = _SCHEMA_VERSION_RE.search(uri)
    return m.group(1) if m else None


def check_mcp_config(doc: Any, plugin_schema: str, where: str) -> dict:
    if not isinstance(doc, dict):
        _fail(where, "must be a JSON object")

    unknown = set(doc) - MCP_TOP_LEVEL
    if unknown:
        _fail(where, f"unknown top-level field(s): {', '.join(sorted(unknown))}. "
                     f"Top level must be exactly $schema and mcpServers")

    if doc.get("$schema") != MCP_SCHEMA:
        _fail(where, f"$schema must be exactly {MCP_SCHEMA!r}, got {doc.get('$schema')!r}")

    if schema_version(doc.get("$schema")) is None:
        _fail(where, f"$schema {doc.get('$schema')!r} does not name a recognisable "
                     f"Agent Plugins release (expected /schemas/<major.minor.patch>/)")

    # A version mismatch is silent from a user's perspective: skills still load, tools do not.
    # Compare the VERSION, not the URL — the two canonical URLs legitimately differ
    # (plugin.schema.json vs mcp.schema.json) but must name the same release.
    mcp_version = schema_version(doc.get("$schema"))
    plugin_version = schema_version(plugin_schema)
    if mcp_version != plugin_version:
        _fail(where, f"$schema declares version {mcp_version!r} but plugin.json declares "
                     f"{plugin_version!r}. Skills will still load and MCP tools will not.")

    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        _fail(where, "'mcpServers' is required and must be an object")
    if not servers:
        return doc

    for name, entry in servers.items():
        check_server_entry(entry, f"{where} mcpServers.{name}")

    return doc


def check_server_entry(entry: Any, where: str) -> None:
    if not isinstance(entry, dict):
        _fail(where, "must be an object")

    stype = entry.get("type")
    if stype not in {"stdio", *REMOTE_TYPES}:
        _fail(where, f"'type' must be one of stdio, streamable-http, sse; got {stype!r}")

    allowed = STDIO_KEYS if stype == "stdio" else REMOTE_KEYS
    unknown = set(entry) - allowed
    if unknown:
        _fail(where, f"unknown field(s) for a {stype} server: {sorted(unknown)}. "
                     f"Permitted: {sorted(allowed)}")

    if stype == "stdio":
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            _fail(where, "'command' is required and must be a non-empty string")
        # Bare name, or a plugin-relative path.
        if not STDIO_CMD_RE.match(command) and not command.startswith("./"):
            _fail(where, f"'command' {command!r} must be a bare executable name or a "
                         f"'./'-prefixed plugin-relative path")
        if "args" in entry:
            if not isinstance(entry["args"], list) or not all(isinstance(a, str) for a in entry["args"]):
                _fail(where, "'args' must be an array of strings")
        if "env" in entry:
            env = entry["env"]
            if not isinstance(env, dict):
                _fail(where, "'env' must be an object")
            for k, v in env.items():
                if k in RESERVED_ENV:
                    _fail(where, f"env key {k!r} is reserved and invalidates the server entry")
                if not isinstance(v, str):
                    _fail(where, f"env.{k} must be a string")
        if "cwd" in entry and not CWD_RE.match(entry["cwd"]):
            _fail(where, f"'cwd' {entry['cwd']!r} must be './'-relative, ${{PLUGIN_ROOT}}-rooted, "
                         f"or ${{PLUGIN_DATA}}-rooted")
    else:
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            _fail(where, "'url' is required and must be a non-empty string")
        check_remote_url(url, where)
        if "headers" in entry:
            headers = entry["headers"]
            if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
                _fail(where, "'headers' must be an object of string values")
            _fail(where, "headers carry credentials in package data; do not put secrets here")


def check_remote_url(url: str, where: str) -> None:
    if not (url.startswith("http://") or url.startswith("https://")):
        _fail(where, f"'url' must be absolute, got {url!r}")
    if "#" in url:
        _fail(where, f"'url' must not contain a fragment, got {url!r}")
    authority = url.split("://", 1)[1].split("/", 1)[0]
    if "@" in authority:
        _fail(where, f"'url' must not contain user info, got {url!r}")


def check_skills(skills_dir: Path, where: str) -> list[str]:
    """Return skill directory names in stable order. Raises on any malformed skill."""
    if not skills_dir.is_dir():
        return []

    names = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            _fail(str(entry), "is a symlink; skills must not resolve outside the plugin root")
        if not entry.is_dir():
            _fail(str(entry), "entries under skills/ must be directories")
        if entry.name.startswith("."):
            continue

        skill_md = entry / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            _fail(str(skill_md), "missing or not a regular file; every skill needs skills/<name>/SKILL.md")

        fm = parse_frontmatter(skill_md)
        declared = fm.get("name")
        if not isinstance(declared, str) or not declared:
            _fail(str(skill_md), "frontmatter 'name' is required")
        if not KEBAB_RE.match(declared):
            # The silent-failure trap: VS Code skips these without warning.
            _fail(str(skill_md),
                  f"frontmatter 'name' {declared!r} must be plain kebab-case with no namespace "
                  f"prefix and no dots (pattern {KEBAB_RE.pattern}). Clients silently skip "
                  f"skills with invalid names.")
        if declared != entry.name:
            _fail(str(skill_md),
                  f"frontmatter 'name' {declared!r} does not match its directory name "
                  f"{entry.name!r}. Clients silently skip mismatched skills.")
        if not isinstance(fm.get("description"), str) or not fm["description"].strip():
            _fail(str(skill_md), "frontmatter 'description' is required and must be a non-empty string")

        names.append(entry.name)

    return names


def parse_frontmatter(path: Path) -> dict:
    """Minimal YAML frontmatter reader.

    Deliberately not a full YAML parser. It supports the subset frontmatters
    actually use — flat scalar keys plus block scalars — and refuses anything
    else rather than half-parsing it, because a mis-parsed `name` is a skill that
    silently never loads.

    Block scalars matter: `description: >` and `description: |` are ordinary YAML
    and common in skill frontmatter. Rejecting them would reject valid skills.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        _fail(str(path), "must start with YAML frontmatter delimited by ---")

    end = text.find("\n---", 3)
    if end == -1:
        _fail(str(path), "frontmatter is not closed with ---")

    out: dict[str, Any] = {}
    lines = text[3:end].splitlines()

    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = i + 2
        line = raw.strip()

        if not line or line.startswith("#"):
            i += 1
            continue

        if raw[:1] in (" ", "\t"):
            # An indented line with no preceding block scalar is genuinely nested
            # YAML, which this reader does not support.
            _fail(str(path), f"line {lineno}: nested frontmatter is not supported; "
                             f"only flat scalar keys and block scalars (>, |) are accepted")

        if ":" not in line:
            _fail(str(path), f"line {lineno}: expected 'key: value', got {raw!r}")

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Block scalar: collect the indented continuation lines.
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            block, i = _read_block(lines, i + 1, str(path))
            out[key] = block
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value == "[]":
            value = []
        out[key] = value
        i += 1

    return out


def _read_block(lines: list[str], start: int, where: str) -> tuple[str, int]:
    """Read an indented block scalar body, returning (text, next_index).

    Folded (>) joins lines with spaces and unwraps paragraphs; literal (|) keeps
    the line breaks. Chomping indicators (-/+) control the trailing newline, which
    we normalise away because nothing downstream cares and determinism does.
    """
    body: list[str] = []
    i = start
    while i < len(lines):
        raw = lines[i]
        if raw.strip() and raw[:1] not in (" ", "\t"):
            break
        body.append(raw)
        i += 1

    if not any(b.strip() for b in body):
        _fail(where, "block scalar has no content")

    # Drop the common indentation, then the leading/trailing blank lines.
    indents = [len(b) - len(b.lstrip()) for b in body if b.strip()]
    margin = min(indents)
    trimmed = [b[margin:] if b.strip() else "" for b in body]
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    return "\n".join(trimmed), i


def check_no_escape(plugin_root: Path, candidate: Path, where: str, root_resolved: Path | None = None) -> None:
    """Reject any path that resolves outside the plugin root, following symlinks.

    Accepts optional root_resolved to avoid repeatedly resolving plugin_root in loops.
    """
    try:
        resolved = candidate.resolve(strict=False)
        root = root_resolved if root_resolved is not None else plugin_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        _fail(where, f"cannot resolve path safely: {exc}")
    if not resolved.is_relative_to(root):
        _fail(where, f"resolves to {resolved} which is outside the plugin root {root}")


def check_antigravity_manifest(doc: Any, where: str) -> None:
    permitted = {"$schema", "name", "description"}
    unknown = set(doc) - permitted
    if unknown:
        _fail(where, f"agy plugin.json is closed (additionalProperties: false) and permits only "
                     f"{sorted(permitted)}; found {sorted(unknown)}")
    if set(doc) != permitted:
        _fail(where, f"agy plugin.json must contain exactly {sorted(permitted)}")
    if doc["$schema"] != ANTIGRAVITY_SCHEMA:
        _fail(where, f"$schema must be {ANTIGRAVITY_SCHEMA!r}, got {doc['$schema']!r}")
    if not ANTIGRAVITY_NAME_RE.match(doc.get("name", "")):
        _fail(where, f"name {doc.get('name')!r} must match {ANTIGRAVITY_NAME_RE.pattern} "
                     f"(uppercase and underscore permitted — unlike the Agent Plugins rule)")


AGENT_FRONTMATTER = {
    "name", "description", "tools", "mainAgent", "subagent", "model",
    "commandExecutionPolicy", "mcpServers", "skills", "plugins",
}
RULE_FRONTMATTER = {"trigger", "description", "globs"}
RULE_TRIGGERS = {"always_on", "model_decision", "glob", "manual"}


def check_agy_agent_frontmatter(fm: dict, where: str) -> None:
    unknown = set(fm) - AGENT_FRONTMATTER
    if unknown:
        _fail(where, f"unknown frontmatter field(s) {sorted(unknown)}; agy agents accept only "
                     f"{sorted(AGENT_FRONTMATTER)} (schema: schemas/antigravity-agent-frontmatter.json)")
    for req in ("name", "description"):
        if req not in fm:
            _fail(where, f"'{req}' is required")


def check_agy_rule_frontmatter(fm: dict, where: str) -> None:
    unknown = set(fm) - RULE_FRONTMATTER
    if unknown:
        _fail(where, f"unknown frontmatter field(s) {sorted(unknown)}; agy rules accept only "
                     f"{sorted(RULE_FRONTMATTER)} (schema: schemas/antigravity-rule-frontmatter.json)")
    if "trigger" not in fm:
        _fail(where, "'trigger' is required; without it agy silently discards the rule")
    if fm["trigger"] not in RULE_TRIGGERS:
        _fail(where, f"trigger {fm['trigger']!r} is not one of {sorted(RULE_TRIGGERS)}. "
                     f"An unrecognised value causes silent discard.")
