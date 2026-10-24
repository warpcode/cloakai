"""Prompt templates for Google Jules autonomous coding sessions.

Implements MCP prompt definitions (2024-11-05) for:
1. High-discipline task scaffolding (jules_task_scaffold)
2. Scheduled maintenance agent personas (bolt, sentinel, docs_auditor, test_smith, workflow_warden, ratchet)
3. Supervisory review and handoff prompts (plan_review, replacement_session, steer_nudge)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

try:
    from .protocol import (
        GetPromptResult,
        Prompt,
        PromptArgument,
        PromptMessage,
        TextContent,
    )
except ImportError:
    from protocol import (
        GetPromptResult,
        Prompt,
        PromptArgument,
        PromptMessage,
        TextContent,
    )


PROMPTS: Dict[str, Prompt] = {}
PROMPT_GENERATORS: Dict[str, Callable[[Dict[str, str]], GetPromptResult]] = {}


def register_prompt(
    name: str,
    description: str,
    arguments: Optional[List[PromptArgument]] = None,
) -> Callable[[Callable[[Dict[str, str]], str]], Callable[[Dict[str, str]], str]]:
    """Decorator to register an MCP prompt template."""
    args_list = arguments or []
    prompt_def = Prompt(name=name, description=description, arguments=args_list)
    PROMPTS[name] = prompt_def

    def decorator(fn: Callable[[Dict[str, str]], str]) -> Callable[[Dict[str, str]], str]:
        def generator(args: Dict[str, str]) -> GetPromptResult:
            content = fn(args)
            return GetPromptResult(
                description=description,
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=content),
                    )
                ],
            )

        PROMPT_GENERATORS[name] = generator
        return fn

    return decorator


# ==============================================================================
# Prompt Definitions
# ==============================================================================


@register_prompt(
    name="jules_task_scaffold",
    description="Scaffold a high-discipline task prompt for a Jules session.",
    arguments=[
        PromptArgument(name="goal", description="Goal or feature description.", required=True),
        PromptArgument(name="constraints", description="Optional repo-specific constraints.", required=False),
        PromptArgument(name="repo_conventions", description="Optional test/lint commands.", required=False),
    ],
)
def prompt_task_scaffold(args: Dict[str, str]) -> str:
    goal = args.get("goal", "").strip()
    constraints = args.get("constraints", "").strip()
    conventions = args.get("repo_conventions", "").strip()

    constraints_block = f"\nCONSTRAINTS:\n{constraints}\n" if constraints else ""
    conventions_block = f"\nVERIFICATION COMMANDS:\n{conventions}\n" if conventions else ""

    return f"""TASK GOAL:
{goal}
{constraints_block}{conventions_block}
OPERATING INSTRUCTIONS:
1. ORIENTATION & BASELINE:
   - Check repository conventions (README, AGENTS.md, CI workflows) to identify test and lint commands.
   - Run existing tests once before making edits to record any pre-existing failures.
2. SURGICAL CHANGE DISCIPLINE:
   - Make the minimal required changes to fulfill the goal. Do NOT refactor unrelated code.
   - Strictly NO REPO LITTER: Never create `.jules/` directories, journals, logs, or housekeeping files.
   - Only modify real code and tests.
3. VERIFICATION:
   - Add or update a regression test that fails without your change and passes with it.
   - Run the test suite and verify it passes. Never claim tests pass unless executed.
4. DELIVERY:
   - Commit changes as a clean, non-empty commit. The platform handles PR publication.
   - Structure your final summary with: Problem, Change, Verification, and Confidence.
"""


@register_prompt(
    name="jules_agent_bolt",
    description="Performance janitor prompt: audit hot paths and N+1 query loops.",
    arguments=[
        PromptArgument(name="focus_slice", description="Optional subdirectory to focus audit.", required=False),
        PromptArgument(name="min_impact", description="Minimum measurable speedup/allocation reduction.", required=False),
    ],
)
def prompt_agent_bolt(args: Dict[str, str]) -> str:
    focus = args.get("focus_slice", "entire codebase").strip()
    min_impact = args.get("min_impact", "measurable CPU/memory reduction on hot paths").strip()

    return f"""MISSION: Performance Audit (Bolt)
FOCUS AREA: {focus}
THRESHOLD: {min_impact}

OBJECTIVES:
1. Audit for algorithmic bottlenecks, N+1 query loops, unbounded data structures, or redundant memory allocations.
2. EVIDENCE REQUIRED: You must be able to state the exact file:line and provide benchmark or profiling proof of the bottleneck before making any change.
3. QUALITY GATE:
   - If no issue exceeds the impact threshold, DO NOTHING. A quiet run is a successful run.
   - Do NOT perform micro-optimizations or sacrifice readability for trivial gains.
4. RULES:
   - No repo litter (never create `.jules/` directories or benchmark artifact files in the repo).
   - Establish baseline performance first, verify speedup with a repeatable test or benchmark.
   - Structure final PR with: Problem, Algorithmic Improvement, Benchmark Verification, Risk.
"""


@register_prompt(
    name="jules_agent_sentinel",
    description="Security auditor prompt: scan for reachable vulnerabilities without secret leakage.",
    arguments=[
        PromptArgument(name="focus_slice", description="Optional subdirectory to focus audit.", required=False),
        PromptArgument(name="risk_class", description="Target risk category (e.g. injection, auth, SSRF).", required=False),
    ],
)
def prompt_agent_sentinel(args: Dict[str, str]) -> str:
    focus = args.get("focus_slice", "entire codebase").strip()
    risk = args.get("risk_class", "OWASP Top 10 / injection / auth / SSRF").strip()

    return f"""MISSION: Security Audit (Sentinel)
FOCUS AREA: {focus}
TARGET RISK: {risk}

OBJECTIVES:
1. Identify and fix REACHABLE security flaws (command injection, path traversal, unsafe deserialization, authorization bypasses).
2. REACHABILITY GATE:
   - Trace data-flow from an untrusted external input to a sensitive sink.
   - Do NOT report unreachable code, theoretical library warnings, or developer-only test utilities.
3. SAFE DISCLOSURE:
   - Never leak exploit payloads, private credentials, or secrets in the PR body.
   - Keep the fix surgical and minimal.
4. VERIFICATION:
   - Add a targeted regression test verifying that the exploit vector is blocked without breaking valid inputs.
   - Run the repository test suite to guarantee zero regression.
"""


@register_prompt(
    name="jules_agent_docs_auditor",
    description="Documentation janitor prompt: fix reader-blocking setup, env var, and API rot.",
    arguments=[
        PromptArgument(name="focus_slice", description="Optional docs directory or module.", required=False),
    ],
)
def prompt_agent_docs_auditor(args: Dict[str, str]) -> str:
    focus = args.get("focus_slice", "docs, README, and setup guides").strip()

    return f"""MISSION: Documentation Audit
FOCUS AREA: {focus}

OBJECTIVES:
1. Identify and fix reader-blocking documentation rot:
   - Broken quickstart or installation steps.
   - Missing, renamed, or obsolete environment variables.
   - API request/response shapes that contradict ground-truth code.
   - Outdated CLI command examples or flags.
2. GROUND TRUTH RULE:
   - Source code is ground truth. Compare doc claims directly against code, configs, and CLI parser definitions.
3. DO NOT SHIP:
   - Do NOT rewrite prose for style, tone, or subjective readability.
   - Only fix factual inaccuracies and broken references.
4. VERIFICATION:
   - If docs contain code snippets or shell commands, execute or test them to verify accuracy.
"""


@register_prompt(
    name="jules_agent_test_smith",
    description="Test writer prompt: add missing regression tests with break-the-code verification.",
    arguments=[
        PromptArgument(name="target_module", description="Target file or module to add tests for.", required=True),
    ],
)
def prompt_agent_test_smith(args: Dict[str, str]) -> str:
    target = args.get("target_module", "").strip()

    return f"""MISSION: Regression Test Writer (TestSmith)
TARGET MODULE: {target}

OBJECTIVES:
1. Add high-value unit or integration tests for untested edge cases, error conditions, and core business logic in `{target}`.
2. BREAK-THE-CODE SANITY CHECK (Mandatory):
   - Write your new tests.
   - Temporarily introduce an intentional defect in `{target}` and run the test to confirm it FAILS.
   - Revert the defect and run the test to confirm it PASSES.
   - This proves the test exercises real behavior and is not a tautology.
3. RULES:
   - Do not modify production code in `{target}` unless fixing a legitimate bug uncovered by your test.
   - Follow existing test framework conventions and mock patterns.
   - Ensure the entire suite remains green.
"""


@register_prompt(
    name="jules_agent_workflow_warden",
    description="CI hygiene prompt: audit GitHub Actions workflows for deprecated actions and missing concurrency.",
    arguments=[],
)
def prompt_agent_workflow_warden(args: Dict[str, str]) -> str:
    return """MISSION: GitHub Actions CI Audit (WorkflowWarden)
TARGET: .github/workflows/*.yml

OBJECTIVES:
1. Audit workflow files for:
   - Deprecated Node runtimes or deprecated runner images (e.g. ubuntu-20.04).
   - Missing `concurrency: group: ... cancel-in-progress: true` on PR workflows.
   - Redundant build/test matrices that waste CI compute.
   - Unpinned or vulnerable third-party GitHub Actions.
2. QUALITY GATES:
   - Do not alter build logic, required status check names, or secrets.
   - Validate YAML syntax and action input parameters before committing.
3. PR FORMAT:
   - Enumerate exact workflow changes, reasons for updates, and verification checks.
"""


@register_prompt(
    name="jules_agent_ratchet",
    description="Deprecation ratchet prompt: eliminate a specific deprecated API or lint rule violation.",
    arguments=[
        PromptArgument(name="rule_or_deprecation", description="Deprecated function, API, or lint rule.", required=True),
        PromptArgument(name="focus_slice", description="Optional subdirectory to focus on.", required=False),
    ],
)
def prompt_agent_ratchet(args: Dict[str, str]) -> str:
    target = args.get("rule_or_deprecation", "").strip()
    focus = args.get("focus_slice", "entire codebase").strip()

    return f"""MISSION: Deprecation / Lint Ratchet
TARGET: {target}
FOCUS AREA: {focus}

OBJECTIVES:
1. Systematically eliminate all usages of `{target}` in `{focus}` and replace them with the modern, supported equivalent.
2. VERIFICATION:
   - Run the linter or compiler to verify `{target}` violations are reduced to zero in the target area.
   - Run full regression test suite to ensure runtime compatibility.
3. CONSTRAINTS:
   - Keep diff strictly scoped to replacing `{target}`. Do not perform drive-by formatting.
"""


@register_prompt(
    name="jules_plan_review",
    description="Plan evaluation checklist prompt for human or AI supervisor before approving a plan.",
    arguments=[
        PromptArgument(name="session_id", description="Jules session ID being audited.", required=True),
        PromptArgument(name="plan_text", description="Plan steps proposed by Jules.", required=False),
    ],
)
def prompt_plan_review(args: Dict[str, str]) -> str:
    sid = args.get("session_id", "").strip()
    plan = args.get("plan_text", "(Inspect via jules_status)").strip()

    return f"""## Jules Plan Assessment: Session `{sid}`

PROPOSED PLAN:
{plan}

AUDIT CHECKLIST:
1. VALIDITY & PREMISE: Does the problem or bug actually exist in ground-truth code, or is Jules solving a hallucinated/misdiagnosed issue?
2. SURGICAL SCOPE: Does the plan stick strictly to the stated goal, or does it attempt unrequested refactoring, architecture redesigns, or package additions?
3. VERIFICATION INTEGRITY: Are regression tests included that will fail before the change and pass after?
4. SECRETS & PATHS: Does the plan respect sensitive file boundaries and avoid hardcoding credentials?

DECISION:
- If all checks pass: Approve plan via `jules_approve_plan(session_id="{sid}")`.
- If any check fails: Reject and steer via `jules_send(session_id="{sid}", prompt="<detailed feedback>")`.
"""


@register_prompt(
    name="jules_replacement_session",
    description="Handoff prompt for recovering a wedged Jules session starting from a clean tidy branch.",
    arguments=[
        PromptArgument(name="old_session_id", description="ID of the wedged or stalled session.", required=True),
        PromptArgument(name="tidy_branch", description="Clean squashed branch name (e.g. tidy/jules-<id>).", required=True),
        PromptArgument(name="remaining_goals", description="Unresolved review findings or remaining criteria.", required=True),
    ],
)
def prompt_replacement_session(args: Dict[str, str]) -> str:
    old_sid = args.get("old_session_id", "").strip()
    branch = args.get("tidy_branch", "").strip()
    remaining = args.get("remaining_goals", "").strip()

    return f"""REPLACEMENT TASK SPECIFICATION

CONTEXT:
A previous Jules session ({old_sid}) attempted this work but became wedged or failed to publish its PR.
The salvageable diff from that session has been squashed onto clean branch `{branch}`.

STARTING BRANCH: `{branch}`

WHAT IS ALREADY DONE:
The core implementation diff from session {old_sid} is already present on `{branch}`.
Do NOT reimplement or revert existing correct work.

WHAT REMAINS TO BE COMPLETED:
{remaining}

DELIVERY REQUIREMENT:
- Commit your final changes as a single clean, non-empty commit on top of `{branch}`.
- Do NOT push manually. The platform publishes the PR automatically under AUTO_CREATE_PR.
- Strictly avoid dummy commits or CI-bypass filler files.
"""


@register_prompt(
    name="jules_steer_nudge",
    description="Generate a standardized steering or nudge message for an idle session.",
    arguments=[
        PromptArgument(
            name="template",
            description="Template: progress_check, plan_stalled, or pr_reminder.",
            required=True,
        ),
        PromptArgument(name="session_id", description="Optional session ID for reference.", required=False),
    ],
)
def prompt_steer_nudge(args: Dict[str, str]) -> str:
    template = args.get("template", "progress_check").strip()
    sid = args.get("session_id", "").strip()
    sid_ref = f" on session {sid}" if sid else ""

    if template == "pr_reminder":
        return (
            f"Please ensure all required test verifications have passed{sid_ref}, "
            "and finalize your changes with a clean, non-empty git commit so the platform "
            "can publish the pull request under AUTO_CREATE_PR mode."
        )
    elif template == "plan_stalled":
        return (
            f"Your proposed implementation plan{sid_ref} has been approved. "
            "Please proceed with the code changes and regression testing as planned."
        )
    else:  # progress_check
        return (
            f"Checking in on current execution progress{sid_ref}. "
            "If your implementation and test suite verifications are complete, "
            "please provide a summary of your findings and commit your changes."
        )


# ==============================================================================
# Public API
# ==============================================================================


def list_prompts() -> List[Prompt]:
    """Return list of all registered MCP Prompt schemas."""
    return list(PROMPTS.values())


def get_prompt(name: str, arguments: Optional[Dict[str, str]] = None) -> Optional[GetPromptResult]:
    """Render an MCP prompt by name and argument dictionary."""
    generator = PROMPT_GENERATORS.get(name)
    if not generator:
        return None
    return generator(arguments or {})
