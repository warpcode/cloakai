"""Tests for the gateway dispatcher.

These assert the properties that matter and that are easy to regress:

  * one tool per agent, described by the agent's own text;
  * the manifest's isolation flags reach the command line verbatim;
  * the command line never carries a host mount, an env var, or the docker socket;
  * every call is labelled so the reaper can find it;
  * a caller's prompt becomes the last argument and nothing else.

No Docker required: build_argv and tool_defs are pure.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway.dispatch import (  # noqa: E402
    GatewayError, PROMPT_SCHEMA, build_argv, call_agent, load_manifest, tool_defs,
)

# parents[2], not parents[1]: this file is gateway/tests/test_dispatch.py, so
# parents[1] is gateway/ and parents[2] is the repo root. Getting this wrong
# pointed at gateway/dist/gateway.json, which does not exist, and skipUnless
# evaluated it at import time — so the two RealManifest tests skipped silently
# and the most valuable coverage quietly did not run.
REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "dist" / "gateway.json"


def manifest_for(**agents):
    return {"version": 1, "agents": agents}


ONE = manifest_for(dev={
    "description": "Development agent.",
    "image": "cloakai/dev:latest",
    "entrypoint": "run",
    "limits": {"cpus": "2", "memory": "4g", "pids": 1024},
    "isolation": {"network": "internal", "read_only": True, "cap_drop": ["ALL"]},
    "flags": ["--network", "internal", "--read-only", "--cap-drop=ALL",
              "--pids-limit=1024"],
})


class ToolSurface(unittest.TestCase):
    def test_one_tool_per_agent(self):
        man = manifest_for(
            dev={"description": "d", "image": "i", "entrypoint": "run", "flags": []},
            audit={"description": "a", "image": "i2", "entrypoint": "run", "flags": []},
        )
        self.assertEqual([t["name"] for t in tool_defs(man)], ["audit", "dev"])

    def test_description_is_the_agents_own_text(self):
        man = manifest_for(dev={
            "description": "Reviews diffs and writes release notes.",
            "image": "i", "entrypoint": "run", "flags": [],
        })
        self.assertEqual(tool_defs(man)[0]["description"],
                         "Reviews diffs and writes release notes.")

    def test_tool_takes_exactly_one_required_prompt(self):
        tool = tool_defs(ONE)[0]
        self.assertEqual(tool["inputSchema"], PROMPT_SCHEMA)
        self.assertEqual(tool["inputSchema"]["required"], ["prompt"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_missing_manifest_says_how_to_make_one(self):
        with self.assertRaises(GatewayError) as ctx:
            load_manifest("/nonexistent/gateway.json")
        self.assertIn("compile.sh", str(ctx.exception))

    def test_manifest_with_no_agents_is_rejected(self):
        with self.assertRaises(GatewayError):
            load_manifest_text('{"version":1}')

    def test_unparseable_manifest_is_rejected(self):
        with self.assertRaises(GatewayError):
            load_manifest_text("{not json")


def load_manifest_text(raw):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(raw)
        name = fh.name
    try:
        return load_manifest(name)
    finally:
        Path(name).unlink()


class CommandLine(unittest.TestCase):
    def argv(self, prompt="do the thing", **kw):
        return build_argv(ONE, "dev", prompt, **kw)

    def test_manifest_flags_reach_the_command_line_verbatim(self):
        argv = self.argv()
        for flag in ONE["agents"]["dev"]["flags"]:
            self.assertIn(flag, argv, f"{flag} was dropped")

    def test_image_entrypoint_prompt_are_the_tail(self):
        argv = self.argv("review the diff")
        self.assertEqual(argv[-3:], ["cloakai/dev:latest", "run", "review the diff"])

    def test_prompt_is_the_final_argument_and_appears_once(self):
        # A prompt that looks like a flag must not become one.
        argv = self.argv("--privileged --cap-add=SYS_ADMIN")
        self.assertEqual(argv[-1], "--privileged --cap-add=SYS_ADMIN")
        self.assertNotIn("--cap-add=SYS_ADMIN", argv[:-1])

    def test_no_host_mount_ever(self):
        # The isolation model in one assertion: a call has no working directory,
        # so two calls cannot collide over one.
        argv = self.argv()
        self.assertNotIn("-v", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--mount", argv)
        self.assertFalse([a for a in argv if ":" in a and "/" in a
                          and not a.startswith("cloakai/")],
                         f"something that looks like a mount in {argv}")

    def test_no_docker_socket(self):
        argv = self.argv()
        self.assertFalse([a for a in argv if "docker.sock" in a],
                         "the docker socket lets an agent start siblings")

    def test_env_comes_only_from_the_manifest_with_a_minted_key(self):
        # The agent needs a model endpoint, so env IS passed — but only the agent's
        # own declared variables with the gateway's minted key substituted in. A
        # caller has no path to set an env var: the prompt is the only input, and
        # call_agent never forwards arguments into the environment.
        self.assertNotIn("--env", self.argv())  # no key, no env
        man = json.loads(json.dumps(ONE))
        man["agents"]["dev"]["env"] = {"SOME_TOOL_TOKEN": "{{key}}"}
        man["agents"]["dev"]["base_url"] = "http://litellm:4000/v1"
        argv = build_argv(man, "dev", "x", key="vk-123")
        self.assertIn("SOME_TOOL_TOKEN=vk-123", argv)
        self.assertEqual(argv.count("--env"), 1, "env must come only from the manifest")

    def test_a_prompt_containing_env_syntax_stays_the_prompt(self):
        # Proves the caller cannot smuggle a variable through the prompt either.
        argv = build_argv(ONE, "dev", "SOME_TOOL_TOKEN=attacker")
        self.assertEqual(argv[-1], "SOME_TOOL_TOKEN=attacker")
        self.assertNotIn("--env", argv)

    def test_never_privileged(self):
        argv = self.argv()
        self.assertNotIn("--privileged", argv)

    def test_container_is_removed_and_init_reaps_zombies(self):
        argv = self.argv()
        self.assertIn("--rm", argv)
        self.assertIn("--init", argv)

    def test_reaper_labels_present_and_correct(self):
        argv = self.argv()
        self.assertIn("cloakai.call=1", argv)
        self.assertIn("cloakai.started=1700000000", self.argv(started=1700000000))
        self.assertIn("cloakai.instance=dev", argv)

    def test_container_name_carries_the_call_id(self):
        argv = build_argv(ONE, "dev", "x", call_id="abc123")
        self.assertIn("cloakai-abc123", argv)

    def test_unknown_agent_is_a_caller_error(self):
        with self.assertRaises(GatewayError) as ctx:
            build_argv(ONE, "nope", "x")
        self.assertIn("unknown agent", str(ctx.exception))
        self.assertIn("dev", str(ctx.exception))  # tells the caller what exists

    def test_agent_without_an_image_is_rejected_not_run(self):
        with self.assertRaises(GatewayError):
            build_argv(manifest_for(broken={"entrypoint": "run"}), "broken", "x")


class PromptHandling(unittest.TestCase):
    def test_empty_prompt_is_refused_before_docker_runs(self):
        # Without this guard an empty prompt would start a real container to do nothing.
        for bad in ("", "   ", None, 123):
            with self.assertRaises(GatewayError):
                call_agent(ONE, "dev", bad)

    def test_unknown_agent_raises_rather_than_starting_a_container(self):
        with self.assertRaises(GatewayError):
            call_agent(ONE, "ghost", "hello")


class RealManifest(unittest.TestCase):
    """Against the committed output, so the tests and dist cannot drift apart."""

    @unittest.skipUnless(MANIFEST.exists(), "dist/gateway.json not built")
    def test_committed_manifest_builds_a_sane_command_line(self):
        man = load_manifest(MANIFEST)
        tools = tool_defs(man)
        self.assertTrue(tools)
        for tool in tools:
            self.assertTrue(tool["description"].strip(), f"{tool['name']} has no description")
            argv = build_argv(man, tool["name"], "hello")
            self.assertIn("--read-only", argv)
            self.assertIn("--cap-drop=ALL", argv)
            self.assertNotIn("-v", argv)

    @unittest.skipUnless(MANIFEST.exists(), "dist/gateway.json not built")
    def test_every_agent_has_a_writable_scratch_space(self):
        # --read-only with no tmpfs means the agent cannot write anything at all,
        # which makes it useless. Each agent's Dockerfile supplies its own, so the
        # manifest must carry them too or the command line is incomplete.
        man = load_manifest(MANIFEST)
        for name, spec in man["agents"].items():
            self.assertIn("--read-only", spec["flags"], f"{name} is read-only")
            self.assertIn("--tmpfs", spec["flags"], f"{name} has no writable scratch space")


if __name__ == "__main__":
    unittest.main(verbosity=2)
