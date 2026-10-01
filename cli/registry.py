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
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "dist" / "agents.json"

#: Sentinel for the caller-chosen Docker network name, shared with
#: agents/isolation-flags, scripts/run.sh and the isolation tests.
NETWORK_PLACEHOLDER = "NETWORK_PLACEHOLDER"
DEFAULT_NETWORK = "cloakai-internal"

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


def network() -> str:
    return os.environ.get("CLOAKAI_NETWORK") or DEFAULT_NETWORK


def resolve_flags(spec: dict[str, Any]) -> list[str]:
    """The agent's declared flags, with only the network sentinel resolved."""
    return [
        str(flag).replace(NETWORK_PLACEHOLDER, network())
        for flag in spec.get("flags", [])
    ]


def build_env(spec: dict[str, Any], key: str | None) -> list[str]:
    """The agent's own env, with {{key}}/{{base_url}}/{{model}} substituted.

    Three placeholders, no more. The CLI does not know what any variable means,
    which is what lets a non-opencode agent bring its own without a code change
    here.
    """
    if not key:
        return []
    replacements = {
        "{{key}}": key,
        "{{base_url}}": spec.get("base_url") or "http://litellm:4000/v1",
        "{{model}}": spec.get("model") or "default",
    }
    out: list[str] = []
    for var, template in sorted((spec.get("env") or {}).items()):
        value = str(template)
        for token, actual in replacements.items():
            value = value.replace(token, actual)
        out += ["--env", f"{var}={value}"]
    return out


def mint_key(
    budget: str = "1.00",
    master: str | None = None,
) -> str | None:
    """Ask the proxy for a virtual key scoped to this one call.

    Minted here, never taken from the caller. A caller-supplied key would let one
    call spend another's budget, and the whole point of the proxy is that the
    container holds no real provider credential.

    Returns None if the proxy is not reachable, so the caller can decide whether to
    continue rather than dying on an exception.
    """
    master = master or os.environ.get(
        "LITELLM_MASTER_KEY", "sk-cloakai-dev-not-a-secret"
    )
    payload = json.dumps({
        "models": ["default"],
        "max_budget": budget,
        "budget_duration": "24h",
        "key_alias": f"cli-{uuid.uuid4().hex[:12]}",
    })
    try:
        done = subprocess.run(
            ["docker", "run", "--rm", "--network", network(),
             "curlimages/curl:latest", "-s", "-X", "POST",
             "http://litellm:4000/key/generate",
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {master}",
             "-d", payload],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return json.loads(done.stdout)["key"]
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, IndexError):
        return None


def build_argv(
    doc: dict[str, Any],
    name: str,
    shape: str,
    *,
    prompt: str | None = None,
    key: str | None = None,
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
    entry_key = SHAPES[shape]
    entrypoints = spec.get("entrypoints") or {}
    entry = entrypoints.get(entry_key)
    if not entry:
        available = ", ".join(sorted(entrypoints)) or "none"
        raise CliError(
            f"agent '{name}' has no '{entry_key}' entrypoint, so it cannot be used "
            f"as '{shape}'. It has: {available}"
        )

    call_id = call_id or uuid.uuid4().hex[:12]
    started = int(time.time()) if started is None else started

    argv = ["docker", "run"]
    # -i keeps stdin open, which the mcp shape needs to stay alive at all. The
    # agents shape reads nothing from stdin so it does not ask for it.
    if interactive:
        argv.append("-i")
    if shape == "agents":
        argv.append("--rm")
    argv += [
        "--name", f"cloakai-{shape}-{call_id}",
        "--label", "cloakai.call=1",
        "--label", f"cloakai.started={started}",
        "--label", f"cloakai.instance={name}",
        *resolve_flags(spec),
        *build_env(spec, key),
        image,
        entry_key,
    ]
    if shape == "agents":
        argv.append(prompt or "")
    return argv


def run_agents(
    doc: dict[str, Any],
    name: str,
    prompt: str,
    *,
    budget: str = "1.00",
    timeout: int = DEFAULT_TIMEOUT,
    passthrough_key: str | None = None,
) -> int:
    """Ephemeral: one task, then the container is gone.

    Returns a process exit code, because that is what a CLI owes its caller.
    """
    if not prompt or not prompt.strip():
        raise CliError("a prompt is required: cloakai agents <name> \"prompt\"")

    spec = get(doc, name)
    key = passthrough_key or mint_key(budget=budget)
    needs_key = bool(spec.get("env"))
    if key is None and needs_key:
        print(
            f"error: the model proxy did not answer, so no key could be minted and\n"
            f"       '{name}' has no model endpoint. Is the stack up?\n"
            f"         docker compose -f infra/compose.yml up -d",
            file=sys.stderr,
        )
        return 2

    argv = build_argv(doc, name, "agents", prompt=prompt, key=key)
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


def run_mcp(doc: dict[str, Any], name: str, *, passthrough_key: str | None = None) -> int:
    """Long-lived: serve stdio until stdin closes, then destroy.

    `exec` semantics matter here. This must REPLACE the Python process, not
    supervise it: an extra process between the client and docker-agent means the
    client can close its pipe while we are still winding down, and stdin does not
    reach the container.
    """
    spec = get(doc, name)
    key = passthrough_key or mint_key()
    if key is None and spec.get("env"):
        print(
            "error: the model proxy did not answer, so no key could be minted and\n"
            f"       '{name}' has no model endpoint. Is the stack up?",
            file=sys.stderr,
        )
        return 2

    argv = build_argv(doc, name, "mcp", key=key)
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


def docker_network_inspect(name: str) -> None:
    """Raise if the network is absent, naming the command that creates it."""
    done = subprocess.run(
        ["docker", "network", "inspect", name], capture_output=True, check=False,
    )
    if done.returncode != 0:
        raise CliError(f"docker network '{name}' does not exist")


def proxy_running() -> bool:
    """Whether the model proxy is up. Absence is reported, not raised."""
    done = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    return "litellm" in (done.stdout or "")
