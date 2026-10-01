"""`cloakai` — invoke an agent, or serve one as an MCP server.

    cloakai agents                     list the agents you can invoke
    cloakai prompt big-pickle "what is 1+1"   one task, then destroyed
    cloakai agents dev "review this"    the same thing, spelled out
    cloakai mcp dev                    serve stdio until stdin closes, then destroyed
    cloakai show dev                   what the catalogue says about one agent
    cloakai doctor                     is anything actually runnable

Two shapes, matching the two ways an agent is used:

  `agents` is ephemeral. It handles one task and is destroyed. `--rm` on the
  docker run means destruction is not a cleanup step that can be forgotten.

  `mcp` is long-lived but still disposable. It serves stdio until stdin closes and
  is destroyed then. Every caller spawns its own container, so two clients using
  the same agent are isolated from each other by having separate containers.

There is no `cloakai serve`, and no shared daemon. A CLI is per-invocation, so
every shape it offers is ephemeral by construction; a long-running one would need
something to hold it, and nothing here does that on purpose.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import registry as reg

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 1


def _cmd_agents(args: argparse.Namespace) -> int:
    if args.name:
        return _run_agents(args)
    return _list_agents(args)


def _list_agents(args: argparse.Namespace) -> int:
    doc = reg.load(args.catalog)
    if args.json:
        import json
        print(json.dumps(reg.agents(doc), indent=2, sort_keys=True))
        return EXIT_OK

    for name in reg.names(doc):
        spec = reg.agents(doc)[name]
        shapes = ",".join(sorted(spec.get("entrypoints") or {}))
        first = (spec.get("description") or "").strip().split("\n")[0]
        print(f"{name}  [{spec.get('image', '?')}]  ({shapes})")
        if first:
            print(f"    {first}")
    if not reg.names(doc):
        print("no agents in the catalogue")
    return EXIT_OK


def _run_agents(args: argparse.Namespace) -> int:
    doc = reg.load(args.catalog)
    # Everything after the agent name is the prompt, so a caller never has to
    # quote around their own quoting.
    prompt = " ".join(args.prompt).strip()
    code = reg.run_agents(
        doc, args.name, prompt,
        budget=args.budget, timeout=args.timeout,
        passthrough_key=args.key,
    )
    return EXIT_OK if code == 0 else (code if code not in (0,) else EXIT_FAILED)


def _cmd_prompt(args: argparse.Namespace) -> int:
    """`cloakai prompt <name> "text"` — the same ephemeral call as `agents`."""
    return _run_agents(args)


def _cmd_mcp(args: argparse.Namespace) -> int:
    doc = reg.load(args.catalog)
    return reg.run_mcp(doc, args.name, passthrough_key=args.key)


def _cmd_show(args: argparse.Namespace) -> int:
    import json
    doc = reg.load(args.catalog)
    print(json.dumps(reg.get(doc, args.name), indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report whether anything is actually runnable, and say what is missing.

    A CLI that fails at invocation time with a Docker error teaches the user
    nothing. This names each prerequisite and whether it is satisfied.
    """
    problems: list[str] = []

    try:
        doc = reg.load(args.catalog)
        print(f"catalogue   ok   {reg.catalog_path(args.catalog)}")
        print(f"agents      ok   {', '.join(reg.names(doc))}")
    except reg.CliError as exc:
        print(f"catalogue   FAIL {exc}")
        problems.append("catalogue")
        doc = None

    if reg.shutil.which("docker") is None:
        print("docker      FAIL not on PATH")
        problems.append("docker")
    else:
        print("docker      ok")

    if doc is not None:
        for name in reg.names(doc):
            spec = reg.agents(doc)[name]
            image = spec.get("image", "")
            found = reg.image_exists(image)
            print(f"image {name:<8}{'ok  ' if found else 'MISSING'} {image}")
            if not found:
                problems.append(f"image:{name}")

    try:
        reg.docker_network_inspect(reg.network())
        print(f"network     ok   {reg.network()}")
    except Exception:
        print(f"network     MISSING {reg.network()} "
              f"— docker network create --internal {reg.network()}")
        problems.append("network")

    if reg.proxy_running():
        print("proxy       ok   litellm is up")
    else:
        print("proxy       MISSING docker compose -f infra/compose.yml up -d")
        problems.append("proxy")

    if problems:
        print(f"\n{len(problems)} problem(s): {', '.join(problems)}")
        return EXIT_FAILED
    print("\neverything needed is present")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloakai",
        description="Invoke an isolated agent, or serve one over MCP stdio.",
    )
    parser.add_argument(
        "--catalog", help="path to agents.json (default: dist/agents.json)",
    )
    sub = parser.add_subparsers(dest="command")

    agents = sub.add_parser("agents", help="list agents, or run one task ephemerally")
    agents.add_argument("name", nargs="?", help="agent to invoke; omit to list")
    agents.add_argument(
        "prompt", nargs="*", help="the task; everything after the name is used",
    )
    agents.add_argument("--budget", default="1.00", help="proxy spend cap for this call")
    agents.add_argument("--timeout", type=int, default=reg.DEFAULT_TIMEOUT)
    agents.add_argument("--json", action="store_true", help="emit JSON when listing")
    agents.add_argument("--key", help="use this key instead of minting one")
    agents.set_defaults(func=_cmd_agents)

    # `prompt` is the short verb for the ephemeral shape. It is not a third shape
    # and not an alias that can drift: it dispatches to exactly what
    # `cloakai agents` does.
    prompt = sub.add_parser(
        "prompt", help='ask an agent something: cloakai prompt <name> "text"',
    )
    prompt.add_argument("name")
    prompt.add_argument("prompt", nargs="*")
    prompt.add_argument("--budget", default="1.00", help="ignored by agents needing no key")
    prompt.add_argument("--timeout", type=int, default=reg.DEFAULT_TIMEOUT)
    prompt.add_argument("--key")
    prompt.set_defaults(func=_cmd_prompt)

    mcp = sub.add_parser("mcp", help="serve an agent as a stdio MCP server")
    mcp.add_argument("name", help="agent to serve")
    mcp.add_argument("--key", help="use this key instead of minting one")
    mcp.set_defaults(func=_cmd_mcp)

    show = sub.add_parser("show", help="print one agent's catalogue entry as JSON")
    show.add_argument("name")
    show.set_defaults(func=_cmd_show)

    doctor = sub.add_parser("doctor", help="check that everything needed is present")
    doctor.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except reg.CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # The container is --rm, so it is already gone.
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())