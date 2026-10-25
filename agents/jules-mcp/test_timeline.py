import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import jules_client


class TestTimelinePagination(unittest.TestCase):
    @patch("jules_client.call")
    def test_timeline_single_page(self, mock_call):
        mock_call.return_value = {
            "activities": [
                {"id": "act1", "createTime": "2025-01-01T00:00:00Z"},
                {"id": "act2", "createTime": "2025-01-01T00:01:00Z"},
            ],
            "nextPageToken": "",
        }
        acts, complete = jules_client.timeline("sess_123", max_pages=5, key="test_key")
        self.assertEqual(len(acts), 2)
        self.assertTrue(complete)
        self.assertEqual(mock_call.call_count, 1)
        mock_call.assert_called_with(
            "GET",
            "sessions/sess_123/activities",
            query_params={"pageSize": 100},
            key="test_key",
            base_url=jules_client.BASE,
        )

    @patch("jules_client.call")
    def test_timeline_multiple_pages(self, mock_call):
        def side_effect(method, endpoint, query_params=None, key=None, base_url=None):
            token = (query_params or {}).get("pageToken", "")
            if not token:
                return {
                    "activities": [{"id": "act1", "createTime": "2025-01-01T00:00:00Z"}],
                    "nextPageToken": "tok1",
                }
            elif token == "tok1":
                return {
                    "activities": [{"id": "act2", "createTime": "2025-01-01T00:01:00Z"}],
                    "nextPageToken": "",
                }
            return {}

        mock_call.side_effect = side_effect
        acts, complete = jules_client.timeline("sess_123", max_pages=5, key="test_key")
        self.assertEqual(len(acts), 2)
        self.assertTrue(complete)
        self.assertEqual(mock_call.call_count, 2)

    @patch("jules_client.call")
    def test_timeline_truncated_max_pages(self, mock_call):
        def side_effect(method, endpoint, query_params=None, key=None, base_url=None):
            token = (query_params or {}).get("pageToken", "0")
            num = int(token) if token.isdigit() else 0
            return {
                "activities": [{"id": f"act_{num}", "createTime": f"2025-01-01T00:{num:02d}:00Z"}],
                "nextPageToken": str(num + 1),
            }

        mock_call.side_effect = side_effect
        acts, complete = jules_client.timeline("sess_123", max_pages=3, key="test_key")
        self.assertEqual(len(acts), 3)
        self.assertFalse(complete)
        self.assertEqual(mock_call.call_count, 3)

    @patch("jules_client.call")
    def test_timeline_sorts_by_create_time(self, mock_call):
        mock_call.return_value = {
            "activities": [
                {"id": "act2", "createTime": "2025-01-01T00:05:00Z"},
                {"id": "act1", "createTime": "2025-01-01T00:01:00Z"},
            ],
            "nextPageToken": "",
        }
        acts, complete = jules_client.timeline("sess_123", max_pages=5, key="test_key")
        self.assertEqual([a["id"] for a in acts], ["act1", "act2"])
        self.assertTrue(complete)


if __name__ == "__main__":
    unittest.main()
