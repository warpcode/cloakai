"""Does a real OpenHands load the skills we generated?

Run inside the pinned conformance image, with the generated plugin mounted at
/check/plugin. Exit non-zero if anything fails to load.

This is deliberately strict. OpenHands validates skill names in strict mode, and
the same class of bug is the reason the epic warned about VS Code silently
skipping skills whose frontmatter `name` is not plain kebab-case. A check that
loaded permissively would pass on output that a real client drops.

It also asserts the negative: a deliberately malformed skill must be REJECTED.
A loader that accepts everything would make every positive assertion meaningless.
"""

import os
import sys
from pathlib import Path

from openhands.sdk.skills.skill import Skill
from openhands.sdk.skills.utils import find_skill_md_directories

PLUGIN = Path("/check/plugin")
SKILLS = PLUGIN / "skills"

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def bad(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")
    failures.append(msg)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# ---------------------------------------------------------------- discovery
section("discovery")

found = find_skill_md_directories(SKILLS)
found_names = sorted(p.parent.name for p in found)

if not found:
    bad(f"no SKILL.md found under {SKILLS}")
    print("\n\033[31mthe check cannot run: nothing to load.\033[0m")
    sys.exit(1)

ok(f"discovered {len(found)} skills: {', '.join(found_names)}")

# The generated tree must agree with the source: no skill lost in compilation.
source = sorted(p.name for p in (PLUGIN.parent / "skills").iterdir() if p.is_dir()) \
    if (PLUGIN.parent / "skills").is_dir() else found_names
if sorted(found_names) != sorted(source):
    bad(f"generated skills differ from the source: {found_names} vs {source}")
else:
    ok("every source skill survived compilation")

# ---------------------------------------------------------------- strict load
section("strict load (the mode a real client uses)")

loaded = {}
for path in found:
    try:
        skill = Skill.load(path, strict=True)
    except Exception as exc:  # noqa: BLE001 — report whatever the loader says
        bad(f"{path.parent.name}: {type(exc).__name__}: {exc}")
        continue
    loaded[skill.name] = skill
    ok(f"{skill.name!r} loaded strictly (content {len(skill.content or '')} chars)")

for name, skill in sorted(loaded.items()):
    if not (skill.description or "").strip():
        bad(f"{name!r} loaded with an EMPTY description — a client cannot route to it")
    if name != name.strip().lower() or " " in name:
        bad(f"{name!r} is not a plain identifier")

# ---------------------------------------------------------------- the negative
section("negative control (a loader that accepts everything proves nothing)")

# Built in a writable COPY, not in the mount. The mount is deliberately
# read-only so the check cannot modify what it is checking, and that turned out
# to matter: writing the probe directly into it fails with EROFS, which is the
# mount doing its job.
import shutil
import tempfile

with tempfile.TemporaryDirectory() as td:
    sandbox = Path(td) / "skills"
    shutil.copytree(SKILLS, sandbox)

    bad_skill = sandbox / "zz-invalid-name-probe"
    bad_skill.mkdir(parents=True, exist_ok=True)
    (bad_skill / "SKILL.md").write_text(
        "---\n"
        "name: Not_Kebab_Case\n"
        "description: Deliberately malformed, to prove the loader rejects it.\n"
        "---\n\n"
        "probe\n",
        encoding="utf-8",
    )

    try:
        Skill.load(bad_skill / "SKILL.md", strict=True)
        bad("a skill with a non-kebab-case name was ACCEPTED in strict mode — "
            "the strictness check is not doing anything")
    except Exception as exc:  # noqa: BLE001
        ok(f"non-kebab-case name correctly rejected ({type(exc).__name__})")

# ---------------------------------------------------------------- mcp config
section("mcp configuration")

if (PLUGIN / "mcp.json").is_file():
    import json
    doc = json.loads((PLUGIN / "mcp.json").read_text())
    servers = doc.get("mcpServers", {})
    if servers:
        ok(f"mcp.json declares {len(servers)} server(s): {', '.join(sorted(servers))}")
    else:
        ok("mcp.json is present and valid (no servers declared)")
else:
    ok("no mcp.json in the generated tree (expected for a pure skills check)")

# ---------------------------------------------------------------- live mcp
# The other half of the acceptance bar, and the one that used to be unverified:
# a real conformant client must SEE TOOLS, not just load files.
#
# Skipped unless CLOAKAI_MCP_URL is set, because it needs the agent container up
# on the internal network. A skip is reported as a skip, never as a pass.
section("live MCP (tools/list from a real client)")

mcp_url = os.environ.get("CLOAKAI_MCP_URL")
if not mcp_url:
    print("  \033[33m–\033[0m CLOAKAI_MCP_URL not set; start the stack and re-run "
          "(./scripts/dev.sh up)")
else:
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def probe() -> tuple[bool, str]:
        try:
            async with streamablehttp_client(mcp_url) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    res = await session.list_tools()
                    return True, ", ".join(t.name for t in res.tools)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    reached, detail = asyncio.run(probe())
    if reached and detail:
        ok(f"tools/list returned: {detail}")
    elif reached:
        bad("tools/list returned NO tools — the plugin's MCP entry produced nothing")
    else:
        bad(f"could not reach the MCP endpoint: {detail}")

# ---------------------------------------------------------------- verdict
print()
if failures:
    print(f"\033[31m{len(failures)} check(s) failed.\033[0m")
    sys.exit(1)
print("\033[32mOpenHands loaded the generated skills.\033[0m")
