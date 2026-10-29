"""Benchmark script for testing performance of sequential vs concurrent session auditing."""

import time
from typing import Any
import unittest
from unittest.mock import MagicMock

import jules_client as jules
from jules_client import JulesClient, AsyncJulesClient


class TestAuditBenchmark(unittest.TestCase):

    def setUp(self) -> None:
        # Create 20 sample session dicts
        self.sessions = [
            {
                "id": f"session-{i}",
                "state": "IN_PROGRESS",
                "title": f"Task session {i}",
                "prompt": f"Fix bug {i}",
                "createTime": "2025-01-01T00:00:00Z",
                "updateTime": "2025-01-01T01:00:00Z",
            }
            for i in range(20)
        ]

    def test_benchmark_sync_audit_sessions(self) -> None:
        # Mock client
        client = JulesClient(api_key="test-key")

        def mock_audit_session(s: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.05)  # Simulate 50ms network delay per session fetch
            return {
                "id": s.get("id"),
                "state": s.get("state"),
                "assessment": "ACTIVE",
                "inactive_mins": 10.0,
                "pull_request_url": "",
                "title": s.get("title"),
            }

        client.audit_session = mock_audit_session  # type: ignore

        # 1. Sequential approach (current cli.py behavior)
        start_seq = time.perf_counter()
        seq_audits = []
        for s in self.sessions:
            if s.get("archived") is True:
                continue
            try:
                a = client.audit_session(s, stale_threshold_mins=60, max_age_days=30)
                seq_audits.append(a)
            except Exception:
                pass
        duration_seq = time.perf_counter() - start_seq

        # 2. Concurrent audit_sessions approach (if implemented on client)
        if hasattr(client, "audit_sessions"):
            start_conc = time.perf_counter()
            conc_audits = client.audit_sessions(self.sessions, stale_threshold_mins=60, max_age_days=30)
            duration_conc = time.perf_counter() - start_conc

            print(f"\n[Sync Benchmark Results]")
            print(f"Sequential Duration: {duration_seq:.4f} seconds")
            print(f"Concurrent Duration: {duration_conc:.4f} seconds")
            print(f"Speedup: {duration_seq / duration_conc:.2f}x")

            self.assertEqual(len(seq_audits), len(conc_audits))
            self.assertLess(duration_conc, duration_seq)
        else:
            print(f"\n[Sync Benchmark Sequential Baseline]: {duration_seq:.4f} seconds")


if __name__ == "__main__":
    unittest.main()
