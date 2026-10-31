"""Benchmark script for testing performance of sequential vs concurrent session auditing (sync and async).

Run standalone:
    PYTHONPATH=agents/jules-mcp python3 agents/jules-mcp/benchmark_audit.py
"""

import asyncio
import time
from typing import Any

from jules_client import AsyncJulesClient, JulesClient


def _make_sample_sessions(count: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "id": f"session-{i}",
            "state": "IN_PROGRESS",
            "title": f"Task session {i}",
            "prompt": f"Fix bug {i}",
            "createTime": "2025-01-01T00:00:00Z",
            "updateTime": "2025-01-01T01:00:00Z",
        }
        for i in range(count)
    ]


def run_sync_benchmark() -> None:
    sessions = _make_sample_sessions(20)
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

    # 1. Sequential approach (prior cli.py behavior)
    start_seq = time.perf_counter()
    seq_audits = []
    for s in sessions:
        if s.get("archived") is True:
            continue
        try:
            a = client.audit_session(s, stale_threshold_mins=60, max_age_days=30)
            seq_audits.append(a)
        except Exception:
            pass
    duration_seq = time.perf_counter() - start_seq

    # 2. Concurrent audit_sessions approach
    start_conc = time.perf_counter()
    conc_audits = client.audit_sessions(sessions, stale_threshold_mins=60, max_age_days=30)
    duration_conc = time.perf_counter() - start_conc

    print("[Sync Benchmark Results]")
    print(f"Sequential Duration: {duration_seq:.4f} seconds")
    print(f"Concurrent Duration: {duration_conc:.4f} seconds")
    print(f"Speedup: {duration_seq / duration_conc:.2f}x")
    assert len(seq_audits) == len(conc_audits)
    assert duration_conc < duration_seq


async def run_async_benchmark() -> None:
    sessions = _make_sample_sessions(20)
    client = AsyncJulesClient(api_key="test-key")

    async def mock_audit_session(s: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # Simulate 50ms network delay per session fetch
        return {
            "id": s.get("id"),
            "state": s.get("state"),
            "assessment": "ACTIVE",
            "inactive_mins": 10.0,
            "pull_request_url": "",
            "title": s.get("title"),
        }

    client.audit_session = mock_audit_session  # type: ignore

    # 1. Sequential approach (prior handlers.py behavior)
    start_seq = time.perf_counter()
    seq_audits = []
    for s in sessions:
        if s.get("archived") is True:
            continue
        try:
            a = await client.audit_session(s, stale_threshold_mins=60, max_age_days=30)
            seq_audits.append(a)
        except Exception:
            pass
    duration_seq = time.perf_counter() - start_seq

    # 2. Concurrent audit_sessions approach (optimized handlers.py behavior)
    start_conc = time.perf_counter()
    conc_audits = await client.audit_sessions(sessions, stale_threshold_mins=60, max_age_days=30)
    duration_conc = time.perf_counter() - start_conc

    print("[Async Benchmark Results]")
    print(f"Sequential Duration: {duration_seq:.4f} seconds")
    print(f"Concurrent Duration: {duration_conc:.4f} seconds")
    print(f"Speedup: {duration_seq / duration_conc:.2f}x")
    assert len(seq_audits) == len(conc_audits)
    assert duration_conc < duration_seq


def run_benchmark() -> None:
    run_sync_benchmark()
    print()
    asyncio.run(run_async_benchmark())


if __name__ == "__main__":
    run_benchmark()
