"""Integration tests for FastAPI endpoints in Jules MCP server."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import app
import jules_client as jules


class TestFastAPIServerIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["server"], "cloakai-jules")
        self.assertIn("tool_count", data)

    def test_tools_catalogue_endpoint(self):
        resp = self.client.get("/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tools", data)
        names = {t["name"] for t in data["tools"]}
        self.assertIn("jules_start", names)
        self.assertIn("jules_status", names)
        self.assertIn("jules_sources", names)
        self.assertIn("jules_nudge", names)

    def test_mcp_post_initialize(self):
        """Verify POST /mcp handles the exact payload from cloakai-healthcheck.sh."""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cloakai-healthcheck", "version": "1"},
            },
        }
        resp = self.client.post("/mcp", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["jsonrpc"], "2.0")
        self.assertEqual(data["id"], 1)
        self.assertIn("serverInfo", data["result"])
        self.assertEqual(data["result"]["serverInfo"]["name"], "cloakai-jules")

    def test_mcp_post_tools_list(self):
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        resp = self.client.post("/mcp", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], 2)
        tools = data["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("jules_start", names)
        self.assertIn("jules_sources", names)

    @patch("handlers.HANDLERS")
    def test_mcp_post_tools_call(self, mock_handlers):
        from protocol import CallToolResult
        mock_handler = AsyncMock(return_value=CallToolResult.text("mock response"))
        mock_handlers.get.return_value = mock_handler
        mock_handlers.__contains__.return_value = True
        mock_handlers.__getitem__.return_value = mock_handler

        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "jules_send",
                "arguments": {"session_id": "123", "prompt": "test"},
            },
        }
        resp = self.client.post("/mcp", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], 3)
        self.assertEqual(data["result"]["content"][0]["text"], "mock response")

    def test_mcp_post_invalid_json(self):
        resp = self.client.post("/mcp", content=b"invalid json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["error"]["code"], -32700)

    def test_mcp_post_empty_body(self):
        resp = self.client.post("/mcp", content=b"")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["error"]["code"], -32700)

    def test_auth_headers_forwarding(self):
        with patch("server.AsyncJulesClient") as mock_client_cls:
            resp = self.client.post(
                "/mcp",
                headers={"X-Goog-Api-Key": "test-key-123"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(resp.status_code, 200)
            mock_client_cls.assert_called_with(api_key="test-key-123")


if __name__ == "__main__":
    unittest.main()
