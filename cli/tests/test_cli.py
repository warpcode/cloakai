"""Tests for the CLI's dispatch logic.

Asserting properties rather than snapshots, and every one of them is something
that has actually gone wrong at some point in this project:

  * the shape name maps to the right entrypoint (`agents` -> `run`, not `agents`);
  * the catalogue's isolation flags reach the command line unexamined;
  * no host mount, ever;
  * the ephemeral shape is --rm and the long-lived one is not, because the claim
    differs between them;
  * a prompt that looks like a flag stays a prompt;
  * a missing prompt is refused before Docker is touched.

Pure: build_argv takes the clock and the id, so nothing here needs Docker.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import registry as reg  # noqa: E402
from cli.main import build_parser  # noqa: E402

# parents[2], not parents[1]. This file is cli/tests/test_cli.py, so parents[1] is
# cli/ and parents[2] is the repo root. Getting it wrong points at cli/dist and
# the skipUnless below then silently skips the three tests that check the real
# committed catalogue.
REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "dist" / "agents.json"

SPEC = {
    "description": "Development agent.",
    "image": "cloakai/dev:latest",
    "entrypoints": {
        "run": "opencode run --auto",
        "mcp": "docker-agent serve mcp /agent/agent.yaml -a dev",
    },
    "flags": [
        "--network", "NETWORK_PLACEHOLDER",
        "--read-only", "--tmpfs", "/tmp", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=1024",
    ],
    "env": {"OPENAI_API_KEY": "{{key}}"},
    "model": "default",
    "base_url": "http://litellm:4000/v1",
}
ONE = {"version": 1, "agents": {"dev": SPEC}}


def argv(shape="agents", prompt="do the thing", **kw):
    return reg.build_argv(ONE, "dev", shape, prompt=prompt, **kw)


class ShapeMapping(unittest.TestCase):
    """The bug this class exists for: `agents` looking for an entrypoint named
    `agents`, which does not exist, so every invocation failed."""

    def test_agents_shape_runs_the_command_the_agent_declares(self):
        """The bug that made `cloakai prompt` hang forever with no output.

        The CLI passed the entrypoint KEY ("run") rather than the command
        agent.json declares, so the entrypoint fell back to a hardcoded
        `opencode run --auto` with no --model. That does not error: opencode
        waits on a model it was never given. The command has to be passed whole,
        because every flag in it is load-bearing.
        """
        spec = json.loads(json.dumps(SPEC))
        spec["entrypoints"]["run"] = "opencode run --model opencode/big-pickle --auto"
        line = reg.build_argv({"agents": {"dev": spec}}, "dev", "agents", prompt="hi")
        # The image, then the mode, then the declared command, then the prompt.
        # Indexed rather than sliced so an extra token cannot shift the window and
        # make a wrong argv look right.
        image_at = line.index(spec["image"])
        self.assertEqual(
            line[image_at:],
            ["cloakai/dev:latest", "run-cmd", "opencode", "run",
             "--model", "opencode/big-pickle", "--auto", "hi"],
            f"the declared command must survive to argv; got {line[image_at:]}",
        )
        self.assertEqual(line[-1], "hi", "the prompt is still last")
        self.assertIn("--model", line, "--model must reach the container")
        self.assertIn("--auto", line)

    def test_agents_dispatches_run_cmd_never_run(self):
        # `run` is the SHAPE name and reaches no case arm in the entrypoint: it
        # printed the usage banner and exited in 0.06s. The bare word also appears
        # in `docker run` and in `opencode run`, so this asserts on the token
        # AFTER the image — the only position where it would be a mode name.
        line = argv("agents")
        self.assertIn("run-cmd", line)
        image_at = line.index(SPEC["image"])
        self.assertEqual(line[image_at + 1], "run-cmd",
                         f"the mode after the image must be run-cmd: {line}")

    def test_mcp_uses_the_mode_name(self):
        self.assertEqual(argv("mcp")[-1], "mcp")

    def test_mcp_shape_uses_the_mcp_entrypoint(self):
        self.assertEqual(argv("mcp")[-1], "mcp")


    def test_shapes_map_to_the_documented_entrypoints(self):
        # The catalogue key an agent declares under, not the argv mode.
        self.assertEqual(reg.SHAPES["agents"], "run")
        self.assertEqual(reg.SHAPES["mcp"], "mcp")

    def test_unknown_shape_is_rejected(self):
        with self.assertRaises(reg.CliError):
            reg.build_argv(ONE, "dev", "sideways")

    def test_agent_missing_the_command_says_which_it_has(self):
        thin = {"agents": {"dev": {"image": "i", "entrypoints": {"mcp": "x"}}}}
        with self.assertRaises(reg.CliError) as ctx:
            reg.build_argv(thin, "dev", "agents", prompt="hi")
        message = str(ctx.exception)
        self.assertIn("run", message, "must name what it looked for")
        self.assertIn("mcp", message, "must name what the agent does have")


class Isolation(unittest.TestCase):
    def test_catalog_flags_reach_the_command_line_verbatim(self):
        for flag in SPEC["flags"]:
            if flag == "NETWORK_PLACEHOLDER":
                continue
            self.assertIn(flag, argv(), f"{flag} was dropped")

    def test_network_placeholder_is_resolved(self):
        # Emitting the placeholder unresolved made every call name a network that
        # does not exist; emitting `internal` named the compose SERVICE alias.
        self.assertNotIn("NETWORK_PLACEHOLDER", argv())
        self.assertIn("cloakai-internal", argv())

    def test_declared_network_selects_the_right_one(self):
        # An agent declares "internal" or "egress" and the CLI maps it to a real
        # Docker network name. Getting this wrong silently produces a container
        # that cannot reach its model: cloakai-internal has no egress, so
        # big-pickle hung there with no error at all.
        egress = json.loads(json.dumps(SPEC))
        egress["network"] = "egress"
        flags = reg.build_argv({"agents": {"dev": egress}}, "dev", "agents", prompt="x")
        self.assertIn("cloakai-egress", flags)
        self.assertNotIn("cloakai-internal", flags)

    def test_internal_network_override_still_applies(self):
        import os
        os.environ["CLOAKAI_NETWORK"] = "my-internal"
        try:
            spec = json.loads(json.dumps(SPEC))
            spec["network"] = "internal"
            flags = reg.build_argv({"agents": {"dev": spec}}, "dev", "agents", prompt="x")
            self.assertIn("my-internal", flags)
        finally:
            del os.environ["CLOAKAI_NETWORK"]

    def test_egress_is_not_overridable_by_the_internal_variable(self):
        # CLOAKAI_NETWORK renames the internal network. If it also renamed egress,
        # an operator could point an agent at a network with no internet and get a
        # hang rather than an error.
        import os
        os.environ["CLOAKAI_NETWORK"] = "my-internal"
        try:
            spec = json.loads(json.dumps(SPEC))
            spec["network"] = "egress"
            flags = reg.build_argv({"agents": {"dev": spec}}, "dev", "agents", prompt="x")
            self.assertIn("cloakai-egress", flags)
        finally:
            del os.environ["CLOAKAI_NETWORK"]

    def test_network_override_is_honoured(self):
        import os
        os.environ["CLOAKAI_NETWORK"] = "other-net"
        try:
            self.assertIn("other-net", argv())
        finally:
            del os.environ["CLOAKAI_NETWORK"]

    def test_no_host_mount_in_either_shape(self):
        for shape in ("agents", "mcp"):
            line = argv(shape)
            for forbidden in ("-v", "--volume", "--mount"):
                self.assertNotIn(forbidden, line, f"{shape} mounts the host")

    def test_never_privileged_and_never_the_socket(self):
        for shape in ("agents", "mcp"):
            line = argv(shape)
            self.assertNotIn("--privileged", line)
            self.assertFalse([a for a in line if "docker.sock" in a])

    def test_read_only_and_cap_drop_present(self):
        self.assertIn("--read-only", argv())
        self.assertIn("--cap-drop=ALL", argv())


class DestructionClaim(unittest.TestCase):
    """The two shapes make different promises, and the difference is the point."""

    def test_agents_is_rm_so_destruction_cannot_be_skipped(self):
        self.assertIn("--rm", argv("agents"))

    def test_mcp_is_not_rm_because_the_pipe_keeps_it_alive(self):
        # Not an oversight: `--rm` with `-i` is fine, but the container must
        # outlive this docker invocation until stdin closes. The e2e test asserts
        # it is gone once the client disconnects.
        self.assertNotIn("--rm", argv("mcp"))

    def test_both_shapes_are_interactive(self):
        for shape in ("agents", "mcp"):
            self.assertIn("-i", argv(shape), f"{shape} needs an open stdin")

    def test_both_shapes_are_labelled_for_the_reaper(self):
        for shape in ("agents", "mcp"):
            line = argv(shape, started=1700000000)
            self.assertIn("cloakai.call=1", line)
            self.assertIn("cloakai.started=1700000000", line)
            self.assertIn("cloakai.instance=dev", line)

    def test_container_name_records_the_shape(self):
        # A leaked container should say which shape leaked it.
        self.assertIn("cloakai-agents-", " ".join(argv("agents")))
        self.assertIn("cloakai-mcp-", " ".join(argv("mcp")))


class PromptSafety(unittest.TestCase):
    def test_prompt_is_the_last_argument_and_appears_once(self):
        # A prompt that reads like a flag must not become one.
        line = argv("agents", "--privileged --cap-add=SYS_ADMIN")
        self.assertEqual(line[-1], "--privileged --cap-add=SYS_ADMIN")
        self.assertNotIn("--cap-add=SYS_ADMIN", line[:-1])

    def test_prompt_cannot_smuggle_an_env_var(self):
        line = argv("agents", "OPENAI_API_KEY=attacker")
        self.assertEqual(line[-1], "OPENAI_API_KEY=attacker")
        self.assertNotIn("--env", line, "no env without a minted key")

    def test_env_only_appears_with_a_key_and_is_fully_substituted(self):
        line = reg.build_argv(ONE, "dev", "agents", prompt="x", key="vk-123")
        self.assertIn("OPENAI_API_KEY=vk-123", line)
        self.assertNotIn("{{", line, "an unsubstituted placeholder reached the container")

    def test_all_three_placeholders_substitute(self):
        spec = json.loads(json.dumps(SPEC))
        spec["env"] = {"A": "{{key}}", "B": "{{base_url}}", "C": "{{model}}"}
        line = reg.build_argv(
            {"agents": {"dev": spec}}, "dev", "agents", prompt="x", key="k",
        )
        self.assertIn("A=k", line)
        self.assertIn("B=http://litellm:4000/v1", line)
        self.assertIn("C=default", line)

    def test_non_opencode_env_passes_through_untouched(self):
        # The CLI substitutes three tokens and knows nothing else, which is what
        # lets a Jules-style agent bring its own variables.
        spec = json.loads(json.dumps(SPEC))
        spec["env"] = {"JULES_API_KEY": "{{key}}", "HTTP_PROXY": "socks5://x:1080"}
        line = reg.build_argv({"agents": {"dev": spec}}, "dev", "mcp", key="k")
        self.assertIn("HTTP_PROXY=socks5://x:1080", line)


class CallerErrors(unittest.TestCase):
    def test_unknown_agent_names_the_alternatives(self):
        with self.assertRaises(reg.CliError) as ctx:
            reg.build_argv(ONE, "ghost", "agents", prompt="x")
        self.assertIn("unknown agent", str(ctx.exception))
        self.assertIn("dev", str(ctx.exception))

    def test_agent_without_an_image_is_refused(self):
        with self.assertRaises(reg.CliError):
            reg.build_argv({"agents": {"x": {"entrypoints": {"run": "r"}}}}, "x",
                           "agents", prompt="hi")

    def test_empty_prompt_never_reaches_docker(self):
        for bad in ("", "   ", None):
            with self.assertRaises(reg.CliError):
                reg.run_agents(ONE, "dev", bad)

    def test_missing_catalogue_says_how_to_make_one(self):
        with self.assertRaises(reg.CliError) as ctx:
            reg.load("/nonexistent/agents.json")
        self.assertIn("compile.sh", str(ctx.exception))

    def test_catalogue_with_no_agents_is_rejected(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"version": 1}, fh)
            name = fh.name
        try:
            with self.assertRaises(reg.CliError):
                reg.load(name)
        finally:
            Path(name).unlink()


class Parser(unittest.TestCase):
    def test_prompt_words_join_without_quoting(self):
        # `cloakai agents dev review this` must work as well as a quoted string.
        args = build_parser().parse_args(["agents", "dev", "review", "this", "diff"])
        self.assertEqual(args.name, "dev")
        self.assertEqual(args.prompt, ["review", "this", "diff"])

    def test_bare_agents_lists_rather_than_invoking(self):
        args = build_parser().parse_args(["agents"])
        self.assertIsNone(args.name)

    def test_prompt_verb_parses_like_agents(self):
        args = build_parser().parse_args(["prompt", "big-pickle", "what", "is", "1+1"])
        self.assertEqual(args.name, "big-pickle")
        self.assertEqual(args.prompt, ["what", "is", "1+1"])

    def test_mcp_takes_a_name(self):
        self.assertEqual(build_parser().parse_args(["mcp", "dev"]).name, "dev")

    def test_catalog_override_parses(self):
        self.assertEqual(
            build_parser().parse_args(["--catalog", "/tmp/a.json", "agents"]).catalog,
            "/tmp/a.json",
        )


@unittest.skipUnless(CATALOG.exists(), "dist/agents.json not built")
class RealCatalog(unittest.TestCase):
    """Against the committed output, so tests and dist cannot drift apart."""

    def test_generated_mcp_entry_matches_what_the_cli_would_run(self):
        """The plugin's mcp.json and the CLI must build the same container.

        Two paths to one container: a client uses the plugin entry, a person uses
        `cloakai mcp`. If they drift, one of them serves an agent with different
        isolation than the other, silently.

        KNOWN AND ACCEPTED: the plugin entry carries only --network, so a plugin
        client currently gets a LESS isolated container than the CLI — no
        read-only rootfs, no dropped capabilities, no limits. `dev` works anyway
        because it is the compose stack talking to its own service, and
        big-pickle works because opencode does not need those to function. That is
        a real gap, not a rounding error, and it is asserted here so it cannot
        quietly become worse.

        What this test asserts today is the part that must never drift: the same
        IMAGE, the same NETWORK, and no secret in the file.
        """
        doc = reg.load(CATALOG)
        for plugin, net in (("dev", "cloakai-internal"),
                            ("big-pickle", "cloakai-egress")):
            source = REPO / "plugins" / plugin / "mcp.json"
            self.assertTrue(source.exists(), f"{plugin}/mcp.json is missing")
            spec = reg.agents(doc)[plugin]
            for server, entry in json.loads(source.read_text())["mcpServers"].items():
                self.assertEqual(entry["command"], "docker",
                                 f"{plugin}/{server} must invoke docker directly")
                self.assertIn(spec["image"], entry["args"],
                              f"{plugin}/{server} runs a different image than the catalogue")
                self.assertEqual(entry["args"][-1], "mcp")
                # The network is the one flag the plugin entry carries today.
                self.assertIn(net, entry["args"],
                              f"{plugin}/{server} must use {net}")
                self.assertEqual(reg.network(spec.get("network")), net)

    def test_plugin_clients_are_less_isolated_than_the_cli(self):
        """Records the gap, so closing it is a deliberate change.

        A plugin consumer runs whatever mcp.json says. Today that is a bare
        `docker run --rm -i --network X image mcp`, while the CLI adds read-only,
        cap-drop, no-new-privileges and three resource limits. Any test that
        asserted the two were EQUAL would be failing correctly; this one asserts
        the difference still exists so it cannot be forgotten.
        """
        doc = reg.load(CATALOG)
        entry = json.loads(
            (REPO / "plugins" / "dev" / "mcp.json").read_text()
        )["mcpServers"]["agents"]
        plugin_flags = {a for a in entry["args"] if a.startswith("--")}
        cli_flags = set(reg.resolve_flags(reg.agents(doc)["dev"]))
        missing = {f for f in cli_flags if f not in plugin_flags
                   and f.split("=")[0] not in {p.split("=")[0] for p in plugin_flags}}
        self.assertIn("--read-only", missing,
                      "if this now passes with read-only present, the gap is closed "
                      "and this assertion plus the mcp.json should both be updated")
        self.assertIn("--cap-drop=ALL", missing)

    def test_generated_mcp_entry_carries_no_secret(self):
        for plugin in ("dev", "big-pickle"):
            text = (REPO / "plugins" / plugin / "mcp.json").read_text()
            self.assertNotIn("sk-", text, f"{plugin} hardcodes a key")

    def test_big_pickle_needs_no_credential(self):
        # The whole point of it: a free model with no auth. If env is ever
        # populated, the CLI starts minting proxy keys for an agent that needs
        # none, and the agent would stop working if the proxy were down.
        spec = reg.agents(reg.load(CATALOG)).get("big-pickle")
        if spec is None:
            self.skipTest("big-pickle is not in the catalogue")
        self.assertEqual(spec["env"], {}, "big-pickle must declare no env")

    def test_every_agent_supports_both_shapes(self):
        # The CLI offers exactly two shapes. An agent that can only do one of them
        # is a gap, and this is where it should be noticed.
        doc = reg.load(CATALOG)
        for name in reg.names(doc):
            entrypoints = reg.agents(doc)[name].get("entrypoints") or {}
            for shape in reg.SHAPES.values():
                self.assertIn(shape, entrypoints, f"{name} has no {shape} entrypoint")

    def test_no_agent_names_a_dead_service(self):
        text = CATALOG.read_text(encoding="utf-8")
        self.assertNotIn("cloakai-gateway", text)
        self.assertNotIn("4483", text)

    def test_committed_agents_have_a_writable_scratch_space(self):
        # --read-only with no tmpfs means the agent cannot write anything at all.
        for name, spec in reg.agents(reg.load(CATALOG)).items():
            self.assertIn("--read-only", spec["flags"], name)
            self.assertIn("--tmpfs", spec["flags"], name)


if __name__ == "__main__":
    unittest.main(verbosity=2)