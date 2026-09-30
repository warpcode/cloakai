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

from jules_client import _verdict  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
