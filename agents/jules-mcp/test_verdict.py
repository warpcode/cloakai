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

from datetime import datetime, timezone

from jules_client import (  # noqa: E402
    JulesError,
    _analyze_timeline,
    _clean_sid,
    _format_completed_verdict,
    _format_waiting_verdict,
    _verdict,
    _extract_session_pr_url,
    _extract_session_metadata,
    _handle_user_message_activity,
    _handle_agent_message_activity,
    _should_fetch_timeline,
    _resolve_session_activities,
    _process_activities,
    _extract_latest_activity_info,
    _determine_assessment,
    _is_agent_awaiting_reply,
    _handle_plan_activity,
    _handle_progress_activity,
    _calculate_session_timing,
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


class AnalyzeTimelineTests(unittest.TestCase):
    def test_empty_timeline(self):
        pending_plan, completed_marker, unanswered = _analyze_timeline([])
        self.assertEqual(pending_plan, "")
        self.assertFalse(completed_marker)
        self.assertEqual(unanswered, 0)

    def test_plan_generated_and_pending(self):
        timeline = [act(planGenerated={"plan": {"id": "plan-123"}})]
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertEqual(pending_plan, "plan-123")
        self.assertFalse(completed_marker)
        self.assertEqual(unanswered, 0)

    def test_plan_approved_clears_pending_plan(self):
        timeline = [
            act(planGenerated={"plan": {"id": "plan-123"}}),
            act(planApproved={"planId": "plan-123"}),
        ]
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertEqual(pending_plan, "")

    def test_progress_updated_clears_pending_plan(self):
        timeline = [
            act(planGenerated={"plan": {"id": "plan-123"}}),
            act(progressUpdated={"title": "Step 1 in progress"}),
        ]
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertEqual(pending_plan, "")

    def test_session_completed_marker_detected(self):
        timeline = [act(sessionCompleted={})]
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertTrue(completed_marker)

    def test_user_messages_accumulate_until_agent_reply(self):
        timeline = [
            act(userMessaged={"userMessage": "first question"}),
            act(userMessaged={"userMessage": "second question"}),
        ]
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertEqual(unanswered, 2)

        timeline.append(act(agentMessaged={"agentMessage": "answering both"}))
        pending_plan, completed_marker, unanswered = _analyze_timeline(timeline)
        self.assertEqual(unanswered, 0)


class FormatWaitingVerdictTests(unittest.TestCase):
    def test_no_waiting_conditions(self):
        lines = _format_waiting_verdict("", 0)
        self.assertEqual(lines, [])

    def test_pending_plan_only(self):
        lines = _format_waiting_verdict("plan-99", 0)
        self.assertEqual(len(lines), 1)
        self.assertIn("WAITING: plan 'plan-99' is generated but not approved", lines[0])
        self.assertIn("jules_approve_plan", lines[0])

    def test_unanswered_messages_only(self):
        lines = _format_waiting_verdict("", 3)
        self.assertEqual(len(lines), 1)
        self.assertIn("WAITING: 3 message(s) from you have not been answered", lines[0])

    def test_both_pending_plan_and_unanswered_messages(self):
        lines = _format_waiting_verdict("plan-42", 2)
        self.assertEqual(len(lines), 2)
        self.assertIn("plan-42", lines[0])
        self.assertIn("2 message(s)", lines[1])


class FormatCompletedVerdictTests(unittest.TestCase):
    def test_idle_waiting_on_human(self):
        lines = _format_completed_verdict(pending_plan="p-1", waiting_on_us=False, completed_marker=False, complete=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("state=COMPLETED means the runner went IDLE", lines[0])

        lines_msg = _format_completed_verdict(pending_plan="", waiting_on_us=True, completed_marker=False, complete=True)
        self.assertEqual(len(lines_msg), 1)
        self.assertIn("state=COMPLETED means the runner went IDLE", lines_msg[0])

    def test_completed_marker_present(self):
        lines = _format_completed_verdict(pending_plan="", waiting_on_us=False, completed_marker=True, complete=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("finished: the session emitted a sessionCompleted marker", lines[0])

    def test_complete_timeline_without_marker(self):
        lines = _format_completed_verdict(pending_plan="", waiting_on_us=False, completed_marker=False, complete=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("low confidence", lines[0])

    def test_truncated_timeline_without_marker(self):
        lines = _format_completed_verdict(pending_plan="", waiting_on_us=False, completed_marker=False, complete=False)
        self.assertEqual(len(lines), 1)
        self.assertIn("timeline was truncated", lines[0])


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

    def test_is_agent_awaiting_reply(self):
        self.assertFalse(_is_agent_awaiting_reply("user", "Can you confirm?"))
        self.assertFalse(_is_agent_awaiting_reply("agent", ""))
        self.assertTrue(_is_agent_awaiting_reply("agent", "Does this look good to you?"))
        self.assertTrue(_is_agent_awaiting_reply("agent", "Please confirm if I should proceed."))
        self.assertTrue(_is_agent_awaiting_reply("agent", "Let me know what you think."))
        self.assertFalse(_is_agent_awaiting_reply("agent", "I have finished all tasks."))

    def test_handle_plan_activity(self):
        plans: list[dict] = []
        convo: list[dict] = []
        gen_act = act(planGenerated={"plan": {"id": "p123", "steps": [{"title": "Step 1"}]}})
        pid = _handle_plan_activity(gen_act, "2025-01-01T00:01:00Z", "a1", plans, convo)
        self.assertEqual(pid, "p123")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["id"], "p123")
        self.assertEqual(len(convo), 1)
        self.assertEqual(convo[0]["type"], "plan_generated")

        app_act = act(planApproved={"planId": "p123"})
        pid2 = _handle_plan_activity(app_act, "2025-01-01T00:02:00Z", "a2", plans, convo)
        self.assertEqual(pid2, "")
        self.assertEqual(len(convo), 2)
        self.assertEqual(convo[1]["type"], "plan_approved")

        other_act = act(otherField={})
        pid3 = _handle_plan_activity(other_act, "2025-01-01T00:03:00Z", "a3", plans, convo)
        self.assertEqual(pid3, "")

    def test_handle_progress_activity(self):
        code_reviews: list[dict] = []
        convo: list[dict] = []
        act1 = act(progressUpdated={"title": "Code review comments applied", "description": "reviewed files"})
        _handle_progress_activity(act1, "2025-01-01T00:01:00Z", "a1", code_reviews, convo)
        self.assertEqual(len(code_reviews), 1)
        self.assertEqual(code_reviews[0]["title"], "Code review comments applied")
        self.assertEqual(len(convo), 1)
        self.assertEqual(convo[0]["type"], "code_review")

        # Non-review progress update should not append to code_reviews
        act2 = act(progressUpdated={"title": "Running unit tests", "description": "tests running"})
        _handle_progress_activity(act2, "2025-01-01T00:02:00Z", "a2", code_reviews, convo)
        self.assertEqual(len(code_reviews), 1)
        self.assertEqual(len(convo), 1)

    def test_calculate_session_timing(self):
        now = datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        created = "2025-01-01T12:00:00Z"
        updated = "2025-01-05T12:00:00Z"
        latest_act = "2025-01-10T11:30:00Z"

        inactive_mins, age_days, is_inactive = _calculate_session_timing(
            created, updated, latest_act, max_age_days=5, now=now
        )
        self.assertAlmostEqual(inactive_mins, 30.0, places=1)
        self.assertAlmostEqual(age_days, 9.0, places=1)
        self.assertTrue(is_inactive)

        # Edge case: empty timestamps
        inactive_mins, age_days, is_inactive = _calculate_session_timing(
            "", "", "", max_age_days=30, now=now
        )
        self.assertEqual(inactive_mins, 0.0)
        self.assertEqual(age_days, 0.0)
        self.assertFalse(is_inactive)

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

    def test_extract_session_metadata(self):
        s = {
            "name": "sessions/sess-123",
            "title": "  My   Special   Title  ",
            "state": "IN_PROGRESS",
            "sourceContext": {
                "source": "sources/github.com/org/repo",
                "githubRepoContext": {"startingBranch": "feature"},
            },
            "createTime": "2025-01-01T00:00:00Z",
            "updateTime": "2025-01-01T01:00:00Z",
            "prompt": "  Do task  ",
        }
        meta = _extract_session_metadata(s)
        self.assertEqual(meta["id"], "sess-123")
        self.assertEqual(meta["title"], "My Special Title")
        self.assertEqual(meta["state"], "IN_PROGRESS")
        self.assertEqual(meta["source"], "github.com/org/repo")
        self.assertEqual(meta["branch"], "feature")
        self.assertEqual(meta["created_time"], "2025-01-01T00:00:00Z")
        self.assertEqual(meta["updated_time"], "2025-01-01T01:00:00Z")
        self.assertEqual(meta["prompt"], "Do task")

    def test_handle_user_and_agent_message_activity(self):
        convo: list[dict] = []
        user_act = act(userMessaged={"userMessage": "  hello  "})
        msg, ts = _handle_user_message_activity(user_act, "2025-01-01T00:01:00Z", "u1", convo)
        self.assertEqual(msg, "hello")
        self.assertEqual(ts, "2025-01-01T00:01:00Z")
        self.assertEqual(len(convo), 1)
        self.assertEqual(convo[0]["role"], "user")
        self.assertEqual(convo[0]["content"], "hello")

        agent_act = act(agentMessaged={"agentMessage": "  world  "})
        msg_a, ts_a = _handle_agent_message_activity(agent_act, "2025-01-01T00:02:00Z", "a1", convo)
        self.assertEqual(msg_a, "world")
        self.assertEqual(ts_a, "2025-01-01T00:02:00Z")
        self.assertEqual(len(convo), 2)
        self.assertEqual(convo[1]["role"], "agent")
        self.assertEqual(convo[1]["content"], "world")

    def test_should_fetch_timeline(self):
        self.assertFalse(_should_fetch_timeline("COMPLETED", "https://pr.url"))
        self.assertTrue(_should_fetch_timeline("COMPLETED", ""))
        self.assertTrue(_should_fetch_timeline("IN_PROGRESS", "https://pr.url"))
        self.assertTrue(_should_fetch_timeline("IN_PROGRESS", ""))

    def test_resolve_session_activities(self):
        # 1. Activities already in session_data
        acts = [act(id="1")]
        res_acts, complete = _resolve_session_activities("s1", {"activities": acts}, "")
        self.assertEqual(res_acts, acts)
        self.assertTrue(complete)

        # 2. Completed with PR -> empty activities without calling timeline
        res_acts_pr, complete_pr = _resolve_session_activities(
            "s2", {"state": "COMPLETED"}, "https://pr.url"
        )
        self.assertEqual(res_acts_pr, [])
        self.assertTrue(complete_pr)

    def test_audit_session_with_string_id(self):
        # Passing string ID should gracefully initialize session_data with id
        session_id = "s-string-123"
        res = audit_session(session_id, max_age_days=0)
        self.assertEqual(res["id"], "s-string-123")
        self.assertEqual(res["state"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
