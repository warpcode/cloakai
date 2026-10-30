"""Tests for the finished-or-parked verdict.

`state: COMPLETED` means the runner went idle, so a session waiting on a human
and a session that genuinely finished report the same state. The verdict is the
only thing standing between a client model and walking away from a session that
is parked on a plan gate, so it is tested against every branch.

No API and no key: these are synthetic timelines. Live coverage cannot include a
parked session without deliberately abandoning one, and the whole point of the
verdict is that sessions get abandoned by accident rather than on purpose.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Import the client directly, by directory. jules_client.py is stdlib-only on
# purpose: keeping the verdict logic out of server.py is what makes it testable
# here at all, since the mcp package only exists inside the image.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jules_client import (  # noqa: E402
    JulesError,
    _clean_sid,
    _verdict,
    _extract_session_pr_url,
    _process_activities,
    _extract_latest_activity_info,
    _determine_assessment,
    audit_session,
)


def act(**fields):
    return fields


class Verdict(unittest.TestCase):
    def text(self, session, timeline, complete=True):
        return "\n".join(_verdict(session, timeline, complete))

    def test_genuine_completion_is_stated_plainly(self):
        out = self.text({"state": "COMPLETED"},
                        [act(agentMessaged={}), act(sessionCompleted={})])
        self.assertIn("sessionCompleted marker", out)
        self.assertNotIn("WAITING", out)

    def test_idle_on_an_unapproved_plan_is_not_reported_as_done(self):
        # The exact failure this exists to prevent: state=COMPLETED, work
        # blocked, and a client that reads only the state walks away.
        out = self.text({"state": "COMPLETED"},
                        [act(planGenerated={"plan": {"id": "p-42"}})])
        self.assertIn("WAITING", out)
        self.assertIn("p-42", out)
        self.assertIn("jules_approve_plan", out)
        self.assertIn("went IDLE", out)
        self.assertNotIn("finished:", out)

    def test_approving_the_plan_clears_the_gate(self):
        out = self.text({"state": "COMPLETED"}, [
            act(planGenerated={"plan": {"id": "p-42"}}),
            act(planApproved={"planId": "p-42"}),
        ])
        self.assertNotIn("WAITING", out)

    def test_progress_also_clears_a_pending_plan(self):
        # progressUpdated clears it too; treating it as still pending would leave
        # a finished session looking blocked forever.
        out = self.text({"state": "COMPLETED"}, [
            act(planGenerated={"plan": {"id": "p-42"}}),
            act(progressUpdated={"title": "Step 1"}),
        ])
        self.assertNotIn("WAITING", out)

    def test_trailing_user_message_is_reported_as_unanswered(self):
        out = self.text({"state": "COMPLETED"}, [
            act(agentMessaged={"agentMessage": "done"}),
            act(userMessaged={"userMessage": "also update the tests"}),
        ])
        self.assertIn("WAITING", out)
        self.assertIn("not been answered", out)

    def test_an_agent_reply_clears_the_unanswered_backlog(self):
        out = self.text({"state": "COMPLETED"}, [
            act(userMessaged={"userMessage": "one"}),
            act(agentMessaged={"agentMessage": "reply"}),
            act(userMessaged={"userMessage": "two"}),
            act(agentMessaged={"agentMessage": "reply"}),
        ])
        self.assertNotIn("WAITING", out)

    def test_a_real_question_counts_as_unanswered(self):
        # A trailing question is the common case: the agent asked something and
        # is waiting. The message counter catches it without needing to detect a
        # question mark, which is the robust option.
        out = self.text({"state": "IN_PROGRESS"}, [
            act(userMessaged={"userMessage": "go"}),
            act(agentMessaged={"agentMessage": "Which database should I use?"}),
        ])
        self.assertNotIn("WAITING", out)  # answered by count; state still says running

    def test_completed_with_no_marker_is_low_confidence_not_finished(self):
        out = self.text({"state": "COMPLETED"}, [act(agentMessaged={})])
        self.assertIn("low confidence", out)
        self.assertNotIn("finished:", out)

    def test_truncated_timeline_does_not_claim_a_missing_marker_means_anything(self):
        # The dangerous inference is "no sessionCompleted marker, so unfinished".
        # If pagination stopped early that inference is worthless, so it must not
        # be made at all.
        out = self.text({"state": "COMPLETED"}, [act(agentMessaged={})], complete=False)
        self.assertIn("truncated", out)
        self.assertNotIn("low confidence", out)
        self.assertNotIn("finished:", out)

    def test_in_progress_never_claims_finished(self):
        out = self.text({"state": "IN_PROGRESS"}, [act(sessionCompleted={})])
        self.assertNotIn("finished:", out)

    def test_missing_state_is_not_treated_as_completed(self):
        out = self.text({}, [act(sessionCompleted={})])
        self.assertNotIn("went IDLE", out)


class CleanSessionId(unittest.TestCase):
    def test_clean_sid_valid_and_prefixed(self):
        self.assertEqual(_clean_sid("s123"), "s123")
        self.assertEqual(_clean_sid("sessions/s123"), "s123")
        self.assertEqual(_clean_sid("  sessions/s123  "), "s123")

    def test_clean_sid_escapes_path_traversal_and_special_chars(self):
        self.assertEqual(_clean_sid("../sources"), "..%2Fsources")
        self.assertEqual(_clean_sid("s123/cancel"), "s123%2Fcancel")
        self.assertEqual(_clean_sid("s123?param=val"), "s123%3Fparam%3Dval")

    def test_clean_sid_rejects_empty_inputs(self):
        with self.assertRaises(JulesError):
            _clean_sid("")
        with self.assertRaises(JulesError):
            _clean_sid("   ")
        with self.assertRaises(JulesError):
            _clean_sid("sessions/")


class AuditSessionTests(unittest.TestCase):
    def test_extract_session_pr_url(self):
        s1 = {"outputs": [{"pullRequest": {"url": "https://github.com/foo/bar/pull/1"}}]}
        s2 = {"outputs": [{"other": 123}]}
        s3 = {}
        self.assertEqual(_extract_session_pr_url(s1), "https://github.com/foo/bar/pull/1")
        self.assertEqual(_extract_session_pr_url(s2), "")
        self.assertEqual(_extract_session_pr_url(s3), "")

    def test_process_activities(self):
        activities_list = [
            act(id="a1", createTime="2025-01-01T00:01:00Z", planGenerated={"plan": {"id": "p1", "steps": [{"title": "s1"}]}}),
            act(id="a2", createTime="2025-01-01T00:02:00Z", planApproved={"planId": "p1"}),
            act(id="a3", createTime="2025-01-01T00:03:00Z", progressUpdated={"title": "Code reviewed and applied", "description": "review details"}),
            act(id="a4", createTime="2025-01-01T00:04:00Z", userMessaged={"userMessage": "looks good"}),
            act(id="a5", createTime="2025-01-01T00:05:00Z", agentMessaged={"agentMessage": "Would you like me to confirm?"}),
        ]
        res = _process_activities("Initial prompt", "2025-01-01T00:00:00Z", activities_list)
        self.assertEqual(res["user_message_count"], 2)  # initial prompt + a4
        self.assertEqual(res["agent_message_count"], 1)
        self.assertEqual(res["unanswered_user_messages"], 0)
        self.assertTrue(res["agent_awaiting_reply"])
        self.assertEqual(len(res["plans"]), 1)
        self.assertEqual(len(res["code_reviews"]), 1)

    def test_extract_latest_activity_info(self):
        self.assertEqual(_extract_latest_activity_info([], "2025-01-01T00:00:00Z"), ("2025-01-01T00:00:00Z", "none", ""))
        acts = [act(createTime="2025-01-01T00:05:00Z", agentMessaged={"agentMessage": "Hello world"})]
        t, typ, detail = _extract_latest_activity_info(acts, "2025-01-01T00:00:00Z")
        self.assertEqual(t, "2025-01-01T00:05:00Z")
        self.assertEqual(typ, "agentMessaged")
        self.assertEqual(detail, "Hello world")

    def test_determine_assessment(self):
        self.assertEqual(_determine_assessment(True, "", "COMPLETED", "", False, 0, 0, 60), "INACTIVE")
        self.assertEqual(_determine_assessment(False, "https://pr.url", "COMPLETED", "", False, 0, 0, 60), "COMPLETED_WITH_PR")
        self.assertEqual(_determine_assessment(False, "", "AWAITING_PLAN_APPROVAL", "p1", False, 0, 0, 60), "AWAITING_PLAN_APPROVAL")
        self.assertEqual(_determine_assessment(False, "", "IN_PROGRESS", "p1", False, 0, 0, 60), "AWAITING_PLAN_APPROVAL")
        self.assertEqual(_determine_assessment(False, "", "AWAITING_USER_FEEDBACK", "", True, 0, 0, 60), "AWAITING_USER_FEEDBACK")
        self.assertEqual(_determine_assessment(False, "", "IN_PROGRESS", "", True, 0, 0, 60), "AWAITING_USER_FEEDBACK")
        self.assertEqual(_determine_assessment(False, "", "IN_PROGRESS", "", False, 1, 16.0, 60), "STALLED")
        self.assertEqual(_determine_assessment(False, "", "IN_PROGRESS", "", False, 0, 70.0, 60), "STALLED")
        self.assertEqual(_determine_assessment(False, "", "IN_PROGRESS", "", False, 0, 10.0, 60), "ACTIVE")
        self.assertEqual(_determine_assessment(False, "", "COMPLETED", "", False, 1, 0, 60), "STALLED")
        self.assertEqual(_determine_assessment(False, "", "COMPLETED", "", False, 0, 0, 60), "COMPLETED_NO_OUTPUT")

    def test_full_audit_session(self):
        session_data = {
            "id": "s100",
            "title": "Fix bug in system",
            "state": "COMPLETED",
            "prompt": "Fix bug in system",
            "sourceContext": {"source": "sources/github.com/foo/bar", "githubRepoContext": {"startingBranch": "main"}},
            "createTime": "2025-01-01T00:00:00Z",
            "updateTime": "2025-01-01T01:00:00Z",
            "outputs": [{"pullRequest": {"url": "https://github.com/foo/bar/pull/42"}}],
            "activities": [
                act(id="a1", createTime="2025-01-01T00:10:00Z", sessionCompleted={}),
            ],
        }
        res = audit_session(session_data, max_age_days=0)
        self.assertEqual(res["id"], "s100")
        self.assertEqual(res["assessment"], "COMPLETED_WITH_PR")
        self.assertEqual(res["pull_request_url"], "https://github.com/foo/bar/pull/42")
        self.assertEqual(res["source"], "github.com/foo/bar")
        self.assertEqual(res["branch"], "main")


if __name__ == "__main__":
    unittest.main(verbosity=2)
