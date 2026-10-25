import time
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import jules_client

def mock_call(method, endpoint, payload=None, query_params=None, key=None, base_url=jules_client.BASE):
    page_token = (query_params or {}).get("pageToken", "")
    page_num = int(page_token) if page_token.isdigit() else 0
    next_num = page_num + 1
    next_token = str(next_num) if next_num < 20 else ""

    activities = [
        {
            "id": f"act_{page_num}_{i}",
            "createTime": f"2025-01-01T{page_num:02d}:{i:02d}:00Z",
            "progressUpdated": {"title": f"Activity {page_num}-{i}"}
        }
        for i in range(100)
    ]
    return {
        "activities": activities,
        "nextPageToken": next_token
    }

def run_benchmark(iterations=1000):
    with patch("jules_client.call", side_effect=mock_call):
        start = time.perf_counter()
        for _ in range(iterations):
            acts, complete = jules_client.timeline("test-session", max_pages=20, key="dummy")
            assert len(acts) == 2000
            assert complete is True
        elapsed = time.perf_counter() - start
        print(f"Benchmark run ({iterations} iterations): {elapsed:.4f} seconds ({elapsed/iterations*1000:.4f} ms/op)")
        return elapsed

if __name__ == "__main__":
    run_benchmark()
