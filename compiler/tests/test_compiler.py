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


def run_compiler(plugin: Path, cwd: Path, out: str = "dist", clients: str = "clients") -> subprocess.CompletedProcess:
    """Invoke the real entry point, so exit codes are the ones a user would see."""
    return subprocess.run(
        [sys.executable, "-m", "cloakai_compiler", str(plugin),
         "--out", out, "--clients-out", clients, "--quiet"],
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

    def test_agy_model_frontmatter_is_a_tier_not_an_object(self):
        """Regression: `model` in agent.json is not the same concept as agy's `model`.

        agent.json's model endpoint is `upstream`. agy's agent frontmatter `model` is a
        tier enum (inherit/flash/pro). When both were called `model`, the endpoint dict
        was rendered into the tier field.
        """
        self.compile()
        fm = V.parse_frontmatter(self.cwd / "dist/dev/google.antigravity/agents/dev.md")
        if "model" in fm:
            self.assertIn(fm["model"], {"inherit", "flash", "pro"},
                          f"agy model must be a tier enum, got {fm['model']!r}")

    def test_upstream_endpoint_reaches_agent_yaml(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        doc = json.loads(agent_path.read_text())
        doc["upstream"] = {"provider": "openai", "id": "some-model",
                           "base_url": "http://elsewhere:9999/v1"}
        agent_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        self.compile()
        text = (self.cwd / "dist/dev/agent.yaml").read_text()
        self.assertIn("http://elsewhere:9999/v1", text)
        self.assertIn("some-model", text)
        # and it must NOT have leaked into the agy agent frontmatter
        fm = V.parse_frontmatter(self.cwd / "dist/dev/google.antigravity/agents/dev.md")
        self.assertNotIn("elsewhere", str(fm))

    def test_entrypoint_script_matches_agent_json(self):
        """agent.json and the container entrypoint must not drift.

        The entrypoint script hardcodes the mcp invocation, and agent.json also
        records it. Nothing enforced that they agree, so agent.json could claim
        /agent.yaml (a path that does not exist in the image) while the image
        worked fine — the drift only surfaces when a gateway reads agent.json.
        """
        agent = json.loads(
            (self.plugin / "io.github.warpcode.cloakai" / "agent.json").read_text())
        script = (REPO / "agents" / "cloakai-entrypoint.sh").read_text(encoding="utf-8")

        # The script wraps the command across lines with backslash continuations,
        # so drop those and compare on whitespace-normalised text.
        flat = " ".join(script.replace("\\", " ").split())
        for mode, cmd in agent["entrypoints"].items():
            self.assertIn(" ".join(cmd.split()), flat,
                          f"entrypoint {mode!r} in agent.json does not appear in "
                          f"agents/cloakai-entrypoint.sh; one of them is stale")

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


# ------------------------------------------------------- output destination

class TestOutputDestination(Sandbox):
    """Regression: output root must be a parameter, never inferred from cwd.

    An earlier version cleaned REPO_ROOT/dist while writing to cwd/dist, so
    running the compiler from any other directory deleted the committed dist/
    tree. The test below asserts the two can never diverge again.
    """

    def test_does_not_touch_the_repo_checkout(self):
        canary_dist = REPO / "dist" / ".canary"
        canary_clients = REPO / "clients" / ".canary"
        canary_dist.parent.mkdir(parents=True, exist_ok=True)
        canary_clients.parent.mkdir(parents=True, exist_ok=True)
        canary_dist.write_text("do not delete me", encoding="utf-8")
        canary_clients.write_text("do not delete me", encoding="utf-8")
        self.addCleanup(lambda: (canary_dist.unlink(missing_ok=True),
                                 canary_clients.unlink(missing_ok=True)))

        # Run from a completely different cwd, as a user or CI might.
        r = run_compiler(self.plugin, self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertTrue(canary_dist.is_file(),
                        "compiler deleted the repo's dist/ while running from another cwd")
        self.assertTrue(canary_clients.is_file(),
                        "compiler deleted the repo's clients/ while running from another cwd")

    def test_output_lands_in_the_requested_directory(self):
        r = run_compiler(self.plugin, self.tmp, out="build-out", clients="build-config")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "build-out" / "dev" / "plugin.json").is_file())
        self.assertTrue((self.tmp / "build-config" / "opencode.json").is_file())
        self.assertFalse((self.tmp / "dist").exists(), "--out was not honoured")

    def test_absolute_out_is_honoured(self):
        elsewhere = self.tmp / "elsewhere"
        r = run_compiler(self.plugin, self.cwd, out=str(elsewhere / "d"),
                         clients=str(elsewhere / "c"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((elsewhere / "d" / "dev" / "plugin.json").is_file())
        self.assertTrue((elsewhere / "c" / "opencode.json").is_file())

    def test_default_cleans_but_keep_does_not(self):
        marker = self.tmp / "dist" / "STALE"

        # Default: a stale file is removed, so a rebuild cannot inherit it.
        (self.tmp / "dist").mkdir(parents=True, exist_ok=True)
        marker.write_text("stale", encoding="utf-8")
        r = run_compiler(self.plugin, self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(marker.exists(), "default run should clean stale output")

        # --keep: a pre-existing sibling file survives.
        marker.write_text("stale", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-m", "cloakai_compiler", str(self.plugin),
             "--out", "dist", "--clients-out", "clients", "--keep", "--quiet"],
            cwd=self.tmp, env={"PYTHONPATH": str(REPO / "compiler"), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(marker.exists(), "--keep should not have cleaned")


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

    def test_block_scalar_description_accepted(self):
        """`description: >` is ordinary YAML and must not be rejected."""
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace(
            "description: Review a change for correctness, security, and maintainability, then report findings ranked by severity with file:line references.",
            "description: >\n"
            "  Review a change for correctness, security, and maintainability,\n"
            "  then report findings ranked by severity with file:line references.",
        ), encoding="utf-8")
        r = self.compile()
        self.assertEqual(r.returncode, 0, f"block scalar rejected:\n{r.stderr}")

    def test_block_scalar_literal_pipe_accepted(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace(
            "description: Review a change for correctness, security, and maintainability, then report findings ranked by severity with file:line references.",
            "description: |\n"
            "  Line one of the description.\n"
            "  Line two of the description.",
        ), encoding="utf-8")
        r = self.compile()
        self.assertEqual(r.returncode, 0, f"literal block rejected:\n{r.stderr}")

    def test_block_scalar_preserves_content(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(
            "---\nname: code-review\ndescription: |\n  First line.\n  Second line.\n---\n\n# Body\n",
            encoding="utf-8")
        r = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr)
        fm = V.parse_frontmatter(p)
        self.assertEqual(fm["description"], "First line.\nSecond line.")

    def test_folded_block_scalar_joins_lines(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(
            "---\nname: code-review\ndescription: >\n  One two three.\n  Four five.\n---\n\n# Body\n",
            encoding="utf-8")
        fm = V.parse_frontmatter(p)
        self.assertEqual(fm["description"], "One two three.\nFour five.")

    def test_empty_block_scalar_rejected(self):
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text("---\nname: code-review\ndescription: >\n---\n\n# Body\n", encoding="utf-8")
        self.assertFailsNaming("block scalar has no content")

    def test_genuinely_nested_yaml_still_rejected(self):
        """Block scalars are supported; arbitrary nesting is not silently accepted."""
        p = self.plugin / "skills" / "code-review" / "SKILL.md"
        p.write_text(
            "---\nname: code-review\ndescription: ok\nmeta:\n  nested: true\n---\n\n# Body\n",
            encoding="utf-8")
        self.assertFailsNaming("nested frontmatter is not supported")

    def test_skill_symlink_escape(self):
        outside = self.tmp / "outside.md"
        outside.write_text("---\nname: x\n---\n", encoding="utf-8")
        link = self.plugin / "skills" / "code-review" / "extra.md"
        link.symlink_to(outside)
        self.assertFailsNaming("outside the plugin root")

    def test_dangling_symlink_escape(self):
        link = self.plugin / "skills" / "code-review" / "dangling.md"
        link.symlink_to("/nonexistent/path/outside/plugin")
        self.assertFailsNaming("outside the plugin root")

    def test_plugin_json_symlink_escape(self):
        (self.plugin / "plugin.json").unlink()
        (self.plugin / "plugin.json").symlink_to("/nonexistent/path/outside/plugin.json")
        self.assertFailsNaming("outside the plugin root")

    def test_mcp_json_symlink_escape(self):
        (self.plugin / "mcp.json").unlink()
        (self.plugin / "mcp.json").symlink_to("/nonexistent/path/outside/mcp.json")
        self.assertFailsNaming("outside the plugin root")

    def test_nested_skill_symlink_escape(self):
        subdir = self.plugin / "skills" / "code-review" / "subdir"
        subdir.mkdir()
        link = subdir / "nested_escape.md"
        link.symlink_to("/nonexistent/path/outside/plugin")
        self.assertFailsNaming("outside the plugin root")

    def test_symlink_loop_fails_validation(self):
        link1 = self.plugin / "skills" / "code-review" / "loop1.md"
        link2 = self.plugin / "skills" / "code-review" / "loop2.md"
        link1.symlink_to(link2)
        link2.symlink_to(link1)
        self.assertFailsNaming("cannot resolve path safely")

    def test_skill_dir_is_symlink(self):
        real = self.plugin / "skills" / "code-review"
        outside = self.tmp / "outside-skill"
        shutil.copytree(real, outside)
        shutil.rmtree(real)
        (self.plugin / "skills" / "code-review").symlink_to(outside)
        self.assertFailsNaming("symlink")

    def test_agent_json_symlink_escape(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        agent_path.unlink()
        agent_path.symlink_to("/nonexistent/path/outside/agent.json")
        self.assertFailsNaming("outside the plugin root")

    def test_agent_json_missing(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        agent_path.unlink()
        self.assertFailsNaming("missing")

    def test_agent_json_unparseable(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        agent_path.write_text("{ unparseable", encoding="utf-8")
        self.assertFailsNaming("unparseable")

    def test_agent_yaml_escapes_special_chars(self):
        agent_path = self.plugin / "io.github.warpcode.cloakai" / "agent.json"
        doc = json.loads(agent_path.read_text())
        doc["name"] = "agent\n  injected: true"
        doc["upstream"] = {
            "id": "model\n  injected: true",
            "base_url": "http://litellm:4000/v1\n  injected: true",
        }
        agent_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        r = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.cwd / "dist" / "dev" / "agent.yaml").read_text(encoding="utf-8")
        self.assertIn('"agent\\n  injected: true"', text)
        self.assertIn('"model\\n  injected: true"', text)
        self.assertIn('"http://litellm:4000/v1\\n  injected: true"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
