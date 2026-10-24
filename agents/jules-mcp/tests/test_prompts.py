"""Tests for MCP prompt templates in jules-mcp."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prompts import get_prompt, list_prompts


class TestJulesPrompts(unittest.TestCase):
    def test_list_prompts(self):
        prompts = list_prompts()
        self.assertEqual(len(prompts), 10)
        names = {p.name for p in prompts}
        expected = {
            "jules_task_scaffold",
            "jules_agent_bolt",
            "jules_agent_sentinel",
            "jules_agent_docs_auditor",
            "jules_agent_test_smith",
            "jules_agent_workflow_warden",
            "jules_agent_ratchet",
            "jules_plan_review",
            "jules_replacement_session",
            "jules_steer_nudge",
        }
        self.assertEqual(names, expected)
        for p in prompts:
            self.assertTrue(bool(p.description))
            self.assertIsInstance(p.arguments, list)

    def test_get_unknown_prompt(self):
        result = get_prompt("non_existent_prompt")
        self.assertIsNone(result)

    def test_prompt_task_scaffold(self):
        result = get_prompt(
            "jules_task_scaffold",
            {
                "goal": "Refactor auth middleware",
                "constraints": "Zero third-party deps",
                "repo_conventions": "pytest -v",
            },
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("Refactor auth middleware", text)
        self.assertIn("Zero third-party deps", text)
        self.assertIn("pytest -v", text)
        self.assertIn("Strictly NO REPO LITTER", text)

    def test_prompt_bolt(self):
        result = get_prompt(
            "jules_agent_bolt",
            {"focus_slice": "services/cache", "min_impact": "20% latency reduction"},
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("services/cache", text)
        self.assertIn("20% latency reduction", text)
        self.assertIn("N+1 query loops", text)

    def test_prompt_sentinel(self):
        result = get_prompt(
            "jules_agent_sentinel",
            {"focus_slice": "api/v1", "risk_class": "Command Injection"},
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("api/v1", text)
        self.assertIn("Command Injection", text)
        self.assertIn("SAFE DISCLOSURE", text)

    def test_prompt_docs_auditor(self):
        result = get_prompt("jules_agent_docs_auditor", {"focus_slice": "docs/"})
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("docs/", text)
        self.assertIn("MISSION: Documentation Audit", text)

    def test_prompt_test_smith(self):
        result = get_prompt("jules_agent_test_smith", {"target_module": "core/signer.py"})
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("core/signer.py", text)
        self.assertIn("BREAK-THE-CODE SANITY CHECK", text)

    def test_prompt_workflow_warden(self):
        result = get_prompt("jules_agent_workflow_warden", {})
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn(".github/workflows", text)
        self.assertIn("cancel-in-progress", text)

    def test_prompt_ratchet(self):
        result = get_prompt(
            "jules_agent_ratchet",
            {"rule_or_deprecation": "datetime.utcnow()", "focus_slice": "backend/"},
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("datetime.utcnow()", text)
        self.assertIn("backend/", text)

    def test_prompt_plan_review(self):
        result = get_prompt(
            "jules_plan_review",
            {"session_id": "sess_99", "plan_text": "1. Edit auth.py\n2. Run tests"},
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("sess_99", text)
        self.assertIn("Edit auth.py", text)
        self.assertIn("AUDIT CHECKLIST", text)
        self.assertIn("VALIDITY & PREMISE", text)

    def test_prompt_replacement_session(self):
        result = get_prompt(
            "jules_replacement_session",
            {
                "old_session_id": "sess_stalled_1",
                "tidy_branch": "tidy/jules-sess-1",
                "remaining_goals": "Fix flaky test in test_runner.py",
            },
        )
        self.assertIsNotNone(result)
        text = result.messages[0].content.text
        self.assertIn("sess_stalled_1", text)
        self.assertIn("tidy/jules-sess-1", text)
        self.assertIn("Fix flaky test in test_runner.py", text)

    def test_prompt_steer_nudge_templates(self):
        # pr_reminder
        res_pr = get_prompt("jules_steer_nudge", {"template": "pr_reminder", "session_id": "s1"})
        self.assertIn("AUTO_CREATE_PR", res_pr.messages[0].content.text)
        self.assertIn("session s1", res_pr.messages[0].content.text)

        # plan_stalled
        res_plan = get_prompt("jules_steer_nudge", {"template": "plan_stalled", "session_id": "s2"})
        self.assertIn("approved", res_plan.messages[0].content.text)

        # progress_check (default)
        res_prog = get_prompt("jules_steer_nudge", {"template": "progress_check"})
        self.assertIn("Checking in on current execution progress", res_prog.messages[0].content.text)


if __name__ == "__main__":
    unittest.main()
