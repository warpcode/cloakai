"""The gateway's entire brain: every agent is a tool.

Three operations, in order:

  1. `tool_defs` lists the agents as tools.
  2. `build_argv` turns a call into a `docker run` command line.
  3. `call_agent` runs it and returns what the agent printed.

There is no policy here. The image, the isolation flags and the resource limits
all arrive in dist/gateway.json, generated from each agent's own agent.json. So
an agent brings its own Dockerfile, its own tooling and its own dependencies, and
adding an agent to the gateway means adding a plugin directory and recompiling.

Two deliberate omissions:

  * No `-v`. A call mounts no host directory. An agent that needs files is
    responsible for getting them with the tools in its own image, which is the
    whole point of giving each agent its own.
  * No `--env` of anything caller-supplied. Whatever the client sends becomes the
    prompt and nothing else, so a caller cannot smuggle configuration into a
    container the manifest has already constrained.

This module is deliberately free of MCP and of Docker imports at module scope so
the command-line construction can be tested without either.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

NETWORK_PLACEHOLDER = "NETWORK_PLACEHOLDER"

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "dist" / "gateway.json"

#: The manifest, agents/isolation-flags and compose all carry this sentinel and
#: resolve it the same way, so CLOAKAI_NETWORK overrides one stack consistently.
DEFAULT_NETWORK = "cloakai-internal"

#: How long a single call may run. The reaper is the backstop for a container
#: that outlives its call; this is the frontstop.
DEFAULT_TIMEOUT = 900

#: The schema every agent tool exposes. One required string: an autonomous agent
#: decides for itself what commands to run, so there is nothing else to pass.
PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The task for the agent. It has no access to your files; "
                           "say what it needs in the prompt.",
        }
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


class GatewayError(RuntimeError):
    """Raised for a caller mistake: unknown agent, or a missing prompt."""


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Read the generated tool surface.

    Raises GatewayError rather than letting a JSON or KeyError escape, because
    every caller of this function is an MCP handler that must answer with text.
    """
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GatewayError(
            f"no gateway manifest at {path}; run scripts/compile.sh"
        ) from exc
    except json.JSONDecodeError as exc:
        raise GatewayError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(manifest.get("agents"), dict) or not manifest["agents"]:
        raise GatewayError(f"{path} declares no agents")
    return manifest


def agents(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest["agents"]


def tool_defs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool surface: one tool per agent, named after it.

    The description is the agent's own, verbatim. That text is the only thing a
    client model sees when deciding which agent to call, so it is written in
    agent.json for that purpose rather than generated here.
    """
    return [
        {
            "name": name,
            "description": spec.get("description", ""),
            "inputSchema": PROMPT_SCHEMA,
        }
        for name, spec in sorted(agents(manifest).items())
    ]


def mint_key(
    budget: str = "1.00",
    network: str | None = None,
    master: str | None = None,
) -> str | None:
    """Ask the proxy for a virtual key scoped to this call.

    The key is budgeted and single-use in effect: it dies with the container.
    This is the only credential the gateway handles, and it is minted by the
    gateway — never forwarded from a caller, because a caller-supplied key would
    let one agent spend another's budget.

    Returns None when the proxy is not reachable, so the caller can decide
    whether to continue rather than dying on an exception.
    """
    network = network or os.environ.get("CLOAKAI_NETWORK") or DEFAULT_NETWORK
    master = master or os.environ.get("LITELLM_MASTER_KEY", "sk-cloakai-dev-not-a-secret")
    payload = json.dumps({
        "models": ["default"],
        "max_budget": budget,
        "budget_duration": "24h",
        "key_alias": f"gateway-{uuid.uuid4().hex[:12]}",
    })
    try:
        done = subprocess.run(
            ["docker", "run", "--rm", "--network", network,
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


def build_env(spec: dict[str, Any], key: str) -> list[str]:
    """The agent's declared environment, with the call's proxy key substituted.

    The gateway does not know what any variable means. It replaces the three
    placeholders and passes the result on, which is the whole extent of its
    involvement with an agent's runtime configuration.
    """
    base_url = spec.get("base_url") or "http://litellm:4000/v1"
    model = spec.get("model") or "default"
    out: list[str] = []
    for name, template in sorted((spec.get("env") or {}).items()):
        value = (str(template)
                 .replace("{{key}}", key)
                 .replace("{{base_url}}", base_url)
                 .replace("{{model}}", model))
        out += ["--env", f"{name}={value}"]
    return out


def build_argv(
    manifest: dict[str, Any],
    agent_name: str,
    prompt: str,
    call_id: str | None = None,
    started: int | None = None,
    key: str | None = None,
) -> list[str]:
    """The `docker run` command line for one call.

    Pure: takes the epoch, the call id and the proxy key so a test can assert
    the exact line. `key` is the gateway's own minted key, never a caller's.
    """
    spec = agents(manifest).get(agent_name)
    if spec is None:
        known = ", ".join(sorted(agents(manifest))) or "none"
        raise GatewayError(f"unknown agent '{agent_name}'; the gateway offers: {known}")

    image = spec.get("image") or ""
    if not image:
        raise GatewayError(f"agent '{agent_name}' declares no image")
    entrypoint = spec.get("entrypoint") or "run"

    network = os.environ.get("CLOAKAI_NETWORK") or DEFAULT_NETWORK
    call_id = call_id or uuid.uuid4().hex[:12]
    started = int(time.time()) if started is None else started

    env = build_env(spec, key) if key else []

    return [
        "docker", "run", "--rm", "--init",
        "--name", f"cloakai-{call_id}",
        # These three labels are the whole reaper contract. The reaper removes a
        # container that outlives its call, and it can only find one by label.
        "--label", "cloakai.call=1",
        "--label", f"cloakai.started={started}",
        "--label", f"cloakai.instance={agent_name}",
        # The manifest's flags, verbatim and unexamined, except for the network
        # sentinel, which is the one value resolved here so CLOAKAI_NETWORK keeps
        # working exactly as it does for run.sh, isolation-tests.sh and compose.
        *[
            str(f).replace(NETWORK_PLACEHOLDER, network)
            for f in spec.get("flags", [])
        ],
        *env,
        image, entrypoint, prompt,
    ]


def call_agent(
    manifest: dict[str, Any],
    agent_name: str,
    prompt: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Run one call and return its output.

    A failed call is a normal outcome, not an exception: the client gets the
    agent's own error text back and decides what to do. Only caller mistakes —
    an unknown agent, an empty prompt — raise.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise GatewayError("'prompt' is required and must be a non-empty string")

    # Validate agent presence before minting keys to fail fast securely on unknown agents
    # and avoid unhandled KeyError or wasteful API proxy key generation.
    spec = agents(manifest).get(agent_name)
    if spec is None:
        known = ", ".join(sorted(agents(manifest))) or "none"
        raise GatewayError(f"unknown agent '{agent_name}'; the gateway offers: {known}")

    key = mint_key()
    if key is None and (spec.get("env") or {}):
        return (f"[{agent_name}] the model proxy did not answer, so no key could be "
                "minted and this agent has no model endpoint. Is the stack up? "
                "docker compose -f infra/compose.yml up -d")

    argv = build_argv(manifest, agent_name, prompt, key=key)
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"[{agent_name}] timed out after {timeout}s"

    if done.returncode == 0:
        return done.stdout.strip() or f"[{agent_name}] returned no output"

    detail = (done.stderr or done.stdout or "").strip()
    return f"[{agent_name}] failed (exit {done.returncode}):\n{detail}"


def call_tool(manifest: dict[str, Any], name: str, arguments: dict[str, Any]) -> str:
    """MCP-shaped entry point: a tool name and its arguments."""
    return call_agent(manifest, name, (arguments or {}).get("prompt", ""))
