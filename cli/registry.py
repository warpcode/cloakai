"""The agent catalogue, and the two ways to invoke an agent.

There are exactly two shapes, and they differ only in which entrypoint they use:

  `agents`  ephemeral. One task, exit, container gone. `--rm` on the docker run
            means destruction is not a cleanup step that can be skipped; the
            container cannot outlive the command because it never outlives the
            process.

  `mcp`     long-lived. Serves stdio until stdin closes, then goes. Each caller
            spawns its own container, so two clients using the same agent get two
            containers and cannot see each other.

Neither this module nor the CLI holds isolation policy. Every flag comes from
dist/agents.json, generated from the agent's own agent.json. The CLI decides which
entrypoint and what prompt; it cannot add a flag the plugin did not declare, and
it never mounts a host directory.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "dist" / "agents.json"

#: Longest an ephemeral run may take before the caller stops waiting.
DEFAULT_TIMEOUT = 900

#: The two shapes, named after the CLI subcommands that select them.
#:
#: These are SHAPE names, not entrypoint names. An agent's `run` entrypoint is the
#: ephemeral one, but the catalogue calls it `run` while the command is
#: `cloakai agents`, so the mapping happens once, here, rather than in three
#: places that have to agree.
SHAPES = {
    "agents": "run",
    "mcp": "mcp",
}


class CliError(RuntimeError):
    """A caller mistake, reported without a traceback."""


def catalog_path(explicit: str | None = None) -> Path:
    """The catalogue to dispatch from.

    CLOAKAI_CATALOG wins over the default so a caller can point at a catalogue
    built somewhere else, which is what an installed CLI needs.
    """
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("CLOAKAI_CATALOG") or DEFAULT_CATALOG)


def load(path: Path | str | None = None) -> dict[str, Any]:
    """Read the generated catalogue."""
    target = Path(path) if path else catalog_path()
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliError(
            f"no agent catalogue at {target}. Run scripts/compile.sh to generate it."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"{target} is not valid JSON: {exc}") from exc

    if not isinstance(doc.get("agents"), dict) or not doc["agents"]:
        raise CliError(f"{target} declares no agents")
    return doc


def agents(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["agents"]


def names(doc: dict[str, Any]) -> list[str]:
    return sorted(agents(doc))


def get(doc: dict[str, Any], name: str) -> dict[str, Any]:
    """One agent, or a legible error naming what does exist."""
    spec = agents(doc).get(name)
    if spec is None:
        known = ", ".join(names(doc)) or "none"
        raise CliError(f"unknown agent '{name}'. Available: {known}")
    return spec


def network(declared: str | None = None) -> str | None:
    """The Docker network to attach, or None for the default bridge.

    There is no cloakai-managed network any more. An agent that needs a specific
    one says so in agent.json and the caller creates it; an agent that does not,
    gets the default bridge, which has egress and needs nothing set up.

    Returning None is deliberate: passing `--network` for a network that does not
    exist makes docker fail with an error that names a network the user never
    asked for.
    """
    return declared or None


def resolve_flags(spec: dict[str, Any]) -> list[str]:
    """The agent's declared isolation flags, verbatim.

    The network is NOT here. It is attached separately by build_argv, and only when
    the agent declares one, so a default invocation names no network at all.
    """
    return [str(flag) for flag in spec.get("flags", [])]


def build_env(spec: dict[str, Any]) -> list[str]:
    """The agent's own env, with {{base_url}} and {{model}} substituted.

    Two placeholders. {{key}} is gone with the proxy: an agent either needs no
    credential, or the operator supplies one through the environment. The CLI
    never invents a credential, which is what keeps a caller's call from spending
    someone else's budget.
    """
    replacements = {
        "{{base_url}}": spec.get("base_url") or "",
        "{{model}}": spec.get("model") or "default",
    }
    out: list[str] = []
    for var, template in sorted((spec.get("env") or {}).items()):
        value = str(template)
        for token, actual in replacements.items():
            value = value.replace(token, actual)
        out += ["--env", f"{var}={value}"]
    return out


def build_argv(
    doc: dict[str, Any],
    name: str,
    shape: str,
    *,
    prompt: str | None = None,
    call_id: str | None = None,
    started: int | None = None,
    interactive: bool = True,
) -> list[str]:
    """The exact `docker run` line for one invocation.

    Pure: takes the clock and the id so a test can assert the whole line.

    Two shapes, one difference:

      agents   adds --rm, and the prompt as the final argument.
      mcp      adds -i and omits --rm only because the entrypoint serves stdio
               until stdin closes; the container still goes on exit. -i is what
               keeps that pipe open.
    """
    if shape not in SHAPES:
        raise CliError(f"unknown shape '{shape}'; expected one of {', '.join(SHAPES)}")

    spec = get(doc, name)
    image = spec.get("image") or ""
    if not image:
        raise CliError(f"agent '{name}' declares no image")

    # Shape name -> the entrypoint key that implements it. Keeping this in one
    # table is what stopped `cloakai agents` from looking for an entrypoint
    # literally called "agents".
    # `mcp` is passed as a mode name, because the image's entrypoint dispatches on
    # it. `agents` is passed the FULL COMMAND, because that is what has to run:
    # passing the key made the entrypoint fall back to a hardcoded
    # `opencode run --auto`, which silently dropped `--model opencode/big-pickle`
    # and then hung forever with no output, because a wrong model does not fail.
    entrypoints = spec.get("entrypoints") or {}
    if shape == "mcp":
        mode = "mcp"
        if not entrypoints.get("mcp"):
            raise CliError(
                f"agent '{name}' declares no mcp entrypoint, so it cannot serve "
                f"MCP. It has: {', '.join(sorted(entrypoints)) or 'none'}"
            )
    else:
        # `run-cmd`, NOT `run`. `run` is the shape name; sending it reached no case
        # arm in the entrypoint and printed the usage banner in 0.06s.
        mode = "run-cmd"
        # The command the agent declares. Validated here rather than trusted,
        # because a missing one would exec `run-cmd` with nothing after it and the
        # container would exit silently.
        if not entrypoints.get("run"):
            raise CliError(
                f"agent '{name}' declares no run command, so it cannot be invoked. "
                f"It has: {', '.join(sorted(entrypoints)) or 'none'}"
            )

    call_id = call_id or uuid.uuid4().hex[:12]
    started = int(time.time()) if started is None else started

    # --rm on BOTH shapes. It does not shorten the container's life: with `-i`
    # and an exec'd docker, the container lives until stdin closes, and --rm only
    # removes it on exit. Omitting it from the mcp shape was a mistake — it is the
    # long-lived shape, so it is the one MOST likely to be killed mid-call (a test
    # timeout, a Ctrl-C, a dropped client), and those are exactly the exits that
    # leaked containers. Three orphaned `cloakai-entrypoint mcp` containers sat on
    # this host for two days because of it.
    argv = ["docker", "run", "--rm"]
    # -i keeps stdin open, which the mcp shape needs to stay alive at all.
    if interactive:
        argv.append("-i")
    net = network(spec.get("network"))
    if net:
        argv += ["--network", net]

    argv += [
        "--name", f"cloakai-{shape}-{call_id}",
        "--label", "cloakai.call=1",
        "--label", f"cloakai.started={started}",
        "--label", f"cloakai.instance={name}",
        *resolve_flags(spec),
        *build_env(spec),
        image,
        mode,
    ]
    if shape == "agents":
        # The agent's command, split into tokens so nothing is re-parsed by a
        # shell. `mode` above is the entrypoint's dispatch name.
        argv += shlex.split(entrypoints.get("run", ""))
        argv.append(prompt or "")
    return argv
    if shape == "agents":
        argv.append(prompt or "")
    return argv


def run_agents(
    doc: dict[str, Any],
    name: str,
    prompt: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> int:
    """Ephemeral: one task, then the container is gone.

    Returns a process exit code, because that is what a CLI owes its caller.
    """
    if not prompt or not prompt.strip():
        raise CliError("a prompt is required: cloakai agents <name> \"prompt\"")

    get(doc, name)
    argv = build_argv(doc, name, "agents", prompt=prompt)
    # Caller-supplied environment, and ONLY that. `--env FOO=$FOO` forwards from
    # the caller's own environment rather than embedding a value in a command line
    # that ends up in `ps` and shell history.
    for var in sorted(env or os.environ if env is None else env):
        argv += ["--env", f"{var}={env[var]}"] if env else ["--env", var]
    try:
        done = subprocess.run(argv, check=False, timeout=timeout)
        return done.returncode
    except subprocess.TimeoutExpired:
        # The container is --rm, so it goes when this process does.
        print(f"error: '{name}' exceeded {timeout}s and was destroyed", file=sys.stderr)
        return 124
    except FileNotFoundError:
        print("error: docker is not on PATH", file=sys.stderr)
        return 127


def run_mcp(doc: dict[str, Any], name: str, *, env: dict[str, str] | None = None) -> int:
    """Long-lived: serve stdio until stdin closes, then destroy.

    `exec` semantics matter here. This must REPLACE the Python process, not
    supervise it: an extra process between the client and docker-agent means the
    client can close its pipe while we are still winding down, and stdin does not
    reach the container.
    """
    get(doc, name)
    argv = build_argv(doc, name, "mcp")
    for var in sorted(env or {}):
        argv += ["--env", f"{var}={env[var]}"]
    os.execvp("docker", argv)
    return 127  # only reached if execvp fails

# ---------------------------------------------------------------- doctor probes

def image_exists(image: str) -> bool:
    """Whether the image is present locally, without pulling anything."""
    if not image:
        return False
    return subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True, check=False,
    ).returncode == 0


def network_exists(name: str) -> bool:
    """Whether a network exists. Absence is a fact for doctor to report, not an error."""
    return subprocess.run(
        ["docker", "network", "inspect", name],
        capture_output=True, check=False,
    ).returncode == 0
