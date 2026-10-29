"""Benchmark for handle_jules_audit performance."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import handle_jules_audit
from jules_client import AsyncJulesClient


class TestBenchmarkAudit(unittest.TestCase):
    def test_audit_performance(self):
        mock_client = AsyncMock(spec=AsyncJulesClient)

        num_sessions = 20
        simulated_delay = 0.05  # 50ms per session audit call

        mock_sessions = [
            {
                "id": f"session-{i}",
                "state": "IN_PROGRESS",
                "title": f"Task session {i}",
                "archived": False,
            }
            for i in range(num_sessions)
        ]

        mock_client.list_sessions.return_value = mock_sessions

        async def mock_audit_session(session_data, stale_threshold_mins=60, max_age_days=30):
            await asyncio.sleep(simulated_delay)
            sid = session_data["id"]
            return {
                "id": sid,
                "state": session_data["state"],
                "assessment": "ACTIVE",
                "inactive_mins": 5.0,
                "pull_request_url": "",
                "title": session_data["title"],
            }

        mock_client.audit_session.side_effect = mock_audit_session

        start_time = time.perf_counter()
        result = asyncio.run(handle_jules_audit({}, mock_client))
        elapsed_time = time.perf_counter() - start_time

        print(f"\n[BENCHMARK] Audited {num_sessions} sessions with {simulated_delay*1000:.0f}ms delay each.")
        print(f"[BENCHMARK] Total execution time: {elapsed_time:.4f} seconds")

        self.assertFalse(result.isError)
        self.assertIn("session-0", result.content[0].text)
        self.assertIn("session-19", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
