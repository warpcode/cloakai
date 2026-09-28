"""Compiler tests.

Two kinds:
  - Determinism and byte-identity, which are properties of the whole build.
  - One case per Task 4 lint rule, asserting each exits non-zero and names the file.

Run with: PYTHONPATH=compiler python3 -m unittest discover -s compiler/tests -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "compiler"))

from cloakai_compiler import validate as V  # noqa: E402
from cloakai_compiler.clients import ANTIGRAVITY_SCHEMA, MCP_SCHEMA, PLUGIN_SCHEMA  # noqa: E402


def run_compiler(plugin: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke the real entry point, so exit codes are the ones a user would see."""
    return subprocess.run(
        [sys.executable, "-m", "cloakai_compiler", str(plugin), "--quiet"],
        cwd=cwd, env={"PYTHONPATH": str(REPO / "compiler"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )


class Sandbox(unittest.TestCase):
    """Each test gets a throwaway copy of the repo's plugin and an empty output dir."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugin = self.tmp / "plugins" / "dev"
        shutil.copytree(REPO / "plugins" / "dev", self.plugin)
        self.cwd = self.tmp

    def write(self, rel: str, doc) -> None:
        path = self.plugin / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def compile(self) -> subprocess.CompletedProcess:
        return run_compiler(self.plugin, self.cwd)

    def assertFailsNaming(self, needle: str):
        r = self.compile()
        self.assertNotEqual(r.returncode, 0, f"expected failure, got 0\nstdout={r.stdout}")
        self.assertIn(needle, r.stderr, f"stderr did not mention {needle!r}:\n{r.stderr}")
        return r


# ------------------------------------------------------------------ happy path

class TestBuild(Sandbox):
    def test_builds_cleanly(self):
        r = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in ("dist/dev/plugin.json", "dist/dev/mcp.json",
                  "dist/dev/google.antigravity/plugin.json",
                  "dist/dev/.claude-plugin/plugin.json", "clients/opencode.json"):
            self.assertTrue((self.cwd / p).is_file(), f"missing {p}")

    def test_rule1_is_byte_identical(self):
        self.compile()
        for name in ("plugin.json", "mcp.json", "skills/code-review/SKILL.md"):
            src = (self.plugin / name).read_bytes()
            dst = (self.cwd / "dist" / "dev" / name).read_bytes()
            self.assertEqual(src, dst, f"rule 1 must copy {name} unchanged")

    def test_agy_manifest_is_reduced_to_three_keys(self):
        self.compile()
        doc = json.loads((self.cwd / "dist/dev/google.antigravity/plugin.json").read_text())
        self.assertEqual(set(doc), {"$schema", "name", "description"})
        self.assertEqual(doc["$schema"], ANTIGRAVITY_SCHEMA)

    def test_agy_agent_has_name_frontmatter(self):
        """Regression: agy registers an agent by frontmatter `name`.

        Emitting the body with no frontmatter passes `agy plugin validate` and then
        loads nothing — the silent half-works failure Task 4 exists to prevent,
        reached from the other direction.
        """
        self.compile()
        text = (self.cwd / "dist/dev/google.antigravity/agents/dev.md").read_text()
        self.assertTrue(text.startswith("---\n"), "agy agent must open with frontmatter")
        fm = V.parse_frontmatter(self.cwd / "dist/dev/google.antigravity/agents/dev.md")
        self.assertEqual(fm.get("name"), "dev")
        self.assertIn("description", fm)
        # Only fields agy documents.
        self.assertEqual(set(fm) - V.AGENT_FRONTMATTER, set())

    def test_agy_agent_omits_unset_optionals(self):
        """Optional fields left at agy's default are not written out."""
        self.compile()
        fm = V.parse_frontmatter(self.cwd / "dist/dev/google.antigravity/agents/dev.md")
        for defaulted in ("mainAgent", "subagent", "model", "commandExecutionPolicy",
                          "mcpServers", "skills", "plugins"):
            self.assertNotIn(defaulted, fm,
                             f"{defaulted} is unset in agent.json and has a documented default; "
                             f"writing it out is a chance to be wrong")

    def test_agy_agent_maps_optional_fields_when_set(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        doc = json.loads(agent_path.read_text())
        doc["model"] = "pro"
        doc["command_execution_policy"] = "sandbox"
        doc["main_agent"] = False
        agent_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        self.compile()
        fm = V.parse_frontmatter(self.cwd / "dist/dev/google.antigravity/agents/dev.md")
        self.assertEqual(fm["model"], "pro")
        self.assertEqual(fm["commandExecutionPolicy"], "sandbox")
        self.assertEqual(fm["mainAgent"], "false")

    def test_namespace_agents_emit_no_frontmatter(self):
        """Namespace and config targets carry agent metadata elsewhere."""
        self.compile()
        for p in ("dist/dev/com.github.copilot/agents/dev.md",
                  "dist/dev/dev.openhands/agents/dev.md"):
            self.assertFalse((self.cwd / p).read_text().startswith("---"),
                             f"{p} should carry no frontmatter")

    def test_agy_mcp_uses_serverurl_only(self):
        self.compile()
        text = (self.cwd / "dist/dev/google.antigravity/mcp_config.json").read_text()
        self.assertIn('"serverUrl"', text)
        self.assertNotIn('"url"', text)
        self.assertNotIn('"httpUrl"', text)

    def test_opencode_config_shape(self):
        self.compile()
        doc = json.loads((self.cwd / "clients/opencode.json").read_text())
        self.assertIn("mcp", doc)
        self.assertNotIn("mcpServers", doc, "opencode uses the mcp key, not mcpServers")
        entry = doc["mcp"]["agents"]
        self.assertEqual(entry["type"], "remote")
        self.assertNotEqual(entry["type"], "sse", "there is no sse type in opencode")
        self.assertTrue(entry["enabled"])

    def test_no_absolute_host_paths(self):
        self.compile()
        for path in list((self.cwd / "dist").rglob("*")) + list((self.cwd / "clients").rglob("*")):
            if path.is_file():
                self.assertNotIn(str(self.tmp), path.read_text(encoding="utf-8"),
                                 f"{path} embeds a host path")

    def test_deterministic_across_runs(self):
        self.compile()
        first = {p.relative_to(self.cwd): p.read_bytes()
                 for p in sorted((self.cwd / "dist").rglob("*")) if p.is_file()}
        first.update({p.relative_to(self.cwd): p.read_bytes()
                      for p in sorted((self.cwd / "clients").rglob("*")) if p.is_file()})
        self.compile()
        second = {p.relative_to(self.cwd): p.read_bytes()
                  for p in sorted((self.cwd / "dist").rglob("*")) if p.is_file()}
        second.update({p.relative_to(self.cwd): p.read_bytes()
                       for p in sorted((self.cwd / "clients").rglob("*")) if p.is_file()})
        self.assertEqual(first, second, "second compile produced a different result")


# ------------------------------------------------------------------ the thesis

class TestThesis(Sandbox):
    TARGET_OUTPUTS = [
        "dist/dev/com.github.copilot/agents/dev.md",
        "dist/dev/dev.openhands/agents/dev.md",
        "dist/dev/google.antigravity/agents/dev.md",
        "dist/dev/.claude-plugin/plugin.json",
        "clients/opencode.json",
    ]

    def test_editing_agent_json_changes_every_target(self):
        """The thesis: one edit, no other file touched, every target updates."""
        self.compile()
        before = {t: (self.cwd / t).read_bytes() for t in self.TARGET_OUTPUTS}

        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        doc = json.loads(agent_path.read_text())
        doc["description"] = "A different description, changed only in agent.json."
        doc["limits"] = {"cpus": "4", "memory": "8g", "pids": 2048}
        agent_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        self.compile()
        after = {t: (self.cwd / t).read_bytes() for t in self.TARGET_OUTPUTS}

        for target in self.TARGET_OUTPUTS:
            self.assertNotEqual(before[target], after[target],
                                f"{target} did not change after editing agent.json alone")

    def test_rule1_output_does_not_depend_on_agent_json(self):
        """The flip side: passthrough must NOT move, or rule 1 is not passthrough."""
        self.compile()
        before = (self.cwd / "dist/dev/plugin.json").read_bytes()
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        doc = json.loads(agent_path.read_text())
        doc["description"] = "Changed."
        agent_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        self.compile()
        self.assertEqual(before, (self.cwd / "dist/dev/plugin.json").read_bytes())


# ------------------------------------------------------------------ Task 4 lint

class TestLint(Sandbox):
    def test_unknown_top_level_field(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "dev", "bogus": 1})
        self.assertFailsNaming("unknown top-level field")

    def test_missing_plugin_json(self):
        (self.plugin / "plugin.json").unlink()
        self.assertFailsNaming("plugin.json")

    def test_unparseable_plugin_json(self):
        (self.plugin / "plugin.json").write_text("{ nope", encoding="utf-8")
        self.assertFailsNaming("unparseable")

    def test_wrong_schema_id(self):
        self.write("plugin.json", {"$schema": "https://example.com/x.json", "name": "dev"})
        self.assertFailsNaming("$schema must be exactly")

    def test_bad_plugin_name_charset(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "Dev_Agent"})
        self.assertFailsNaming("is invalid")

    def test_plugin_name_double_dash(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "de--v"})
        self.assertFailsNaming("is invalid")

    def test_plugin_name_dotdot(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "de..v"})
        self.assertFailsNaming("is invalid")

    def test_plugin_name_trailing_dot(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "dev."})
        self.assertFailsNaming("is invalid")

    def test_plugin_name_too_long(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "a" * 65})
        self.assertFailsNaming("max 64")

    def test_author_extra_key(self):
        self.write("plugin.json", {"$schema": PLUGIN_SCHEMA, "name": "dev",
                                   "author": {"name": "x", "role": "y"}})
        self.assertFailsNaming("author")

    def test_mcp_extra_toplevel(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA, "mcpServers": {}, "extra": 1})
        self.assertFailsNaming("unknown top-level field")

    def test_mcp_foreign_schema_version_rejected(self):
        """A 2.0.0 identifier is caught by the strict-URL guard, which fires first."""
        self.write("mcp.json", {"$schema":
                                "https://agent-plugins.org/schemas/2.0.0/mcp.schema.json",
                                "mcpServers": {}})
        self.assertFailsNaming("$schema must be exactly")

    def test_mcp_schema_version_mismatch_check_itself(self):
        """Defence in depth: the version comparison is a separate guard.

        It is unreachable through a real build today because only 1.0.0 exists and
        the strict-URL check rejects anything else first. Tested directly so it stays
        correct when 1.1.0 lands.
        """
        doc = {"$schema": MCP_SCHEMA, "mcpServers": {}}
        future = "https://agent-plugins.org/schemas/1.1.0/mcp.schema.json"
        with self.assertRaises(V.ValidationError) as ctx:
            V.check_mcp_config(doc, future, "mcp.json")
        self.assertIn("but plugin.json declares", str(ctx.exception))

    def test_schema_version_extraction(self):
        self.assertEqual(V.schema_version(PLUGIN_SCHEMA), "1.0.0")
        self.assertEqual(V.schema_version(MCP_SCHEMA), "1.0.0")
        self.assertIsNone(V.schema_version("https://example.com/x.json"))
        self.assertIsNone(V.schema_version(None))

    def test_mcp_server_unknown_type(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "grpc", "url": "http://x/mcp"}}})
        self.assertFailsNaming("'type' must be one of")

    def test_remote_server_extra_field(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "sse", "url": "http://x/mcp",
                                                     "cwd": "./here"}}})
        self.assertFailsNaming("unknown field")

    def test_remote_url_not_absolute(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "sse", "url": "/mcp"}}})
        self.assertFailsNaming("must be absolute")

    def test_remote_url_with_userinfo(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "sse", "url": "http://u:p@x/mcp"}}})
        self.assertFailsNaming("user info")

    def test_remote_url_with_fragment(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "sse", "url": "http://x/mcp#f"}}})
        self.assertFailsNaming("fragment")

    def test_stdio_command_not_bare_or_relative(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "stdio", "command": "/usr/bin/thing"}}})
        self.assertFailsNaming("bare executable name")

    def test_stdio_reserved_env_key(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "stdio", "command": "x",
                                                     "env": {"PLUGIN_ROOT": "/tmp"}}}})
        self.assertFailsNaming("reserved")

    def test_stdio_bad_cwd(self):
        self.write("mcp.json", {"$schema": MCP_SCHEMA,
                                "mcpServers": {"a": {"type": "stdio", "command": "x",
                                                     "cwd": "/etc"}}})
        self.assertFailsNaming("'cwd'")

    def test_skill_name_not_kebab(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(p.read_text().replace("name: code-review", "name: code_review"), encoding="utf-8")
        self.assertFailsNaming("kebab-case")

    def test_skill_name_namespaced(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(p.read_text().replace("name: code-review", "name: acme.code-review"),
                     encoding="utf-8")
        self.assertFailsNaming("no namespace prefix")

    def test_skill_name_dir_mismatch(self):
        (self.plugin / "skills" / "code-review" / "SKILL.md").rename(
            self.plugin / "skills" / "code-review" / "SKILL.md")
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(p.read_text().replace("name: code-review", "name: reviewing"), encoding="utf-8")
        self.assertFailsNaming("does not match its directory name")

    def test_skill_without_skill_md(self):
        (self.plugin / "skills" / "code-review" / "SKILL.md").unlink()
        self.assertFailsNaming("SKILL.md")

    def test_stray_file_under_skills(self):
        (self.plugin / "skills" / "notes.md").write_text("loose", encoding="utf-8")
        self.assertFailsNaming("must be directories")

    def test_skill_symlink_escape(self):
        outside = self.tmp / "outside.md"
        outside.write_text("---\nname: x\n---\n", encoding="utf-8")
        link = self.plugin / "skills" / "code-review" / "extra.md"
        link.symlink_to(outside)
        self.assertFailsNaming("outside the plugin root")

    def test_skill_dir_is_symlink(self):
        real = self.plugin / "skills" / "code-review"
        outside = self.tmp / "outside-skill"
        shutil.copytree(real, outside)
        shutil.rmtree(real)
        (self.plugin / "skills" / "code-review").symlink_to(outside)
        self.assertFailsNaming("symlink")


if __name__ == "__main__":
    unittest.main(verbosity=2)
