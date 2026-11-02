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
        self.assertIn("prompt_count", data)
        self.assertEqual(data["prompt_count"], 10)

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

    def test_prompts_catalogue_endpoint(self):
        resp = self.client.get("/prompts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("prompts", data)
        self.assertEqual(len(data["prompts"]), 10)
        names = {p["name"] for p in data["prompts"]}
        self.assertIn("jules_task_scaffold", names)
        self.assertIn("jules_agent_bolt", names)

    def test_prompts_render_endpoint(self):
        resp = self.client.post("/prompts/jules_agent_bolt", json={"focus_slice": "core/hotpath.py"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("messages", data)
        self.assertIn("core/hotpath.py", data["messages"][0]["content"]["text"])

    def test_prompts_render_endpoint_404(self):
        resp = self.client.post("/prompts/unknown_template", json={})
        self.assertEqual(resp.status_code, 404)

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

    def test_mcp_post_prompts_list(self):
        body = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "prompts/list",
            "params": {},
        }
        resp = self.client.post("/mcp", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], 20)
        prompts = data["result"]["prompts"]
        self.assertEqual(len(prompts), 10)

    def test_mcp_post_prompts_get(self):
        body = {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "prompts/get",
            "params": {
                "name": "jules_agent_sentinel",
                "arguments": {"risk_class": "XSS"},
            },
        }
        resp = self.client.post("/mcp", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], 21)
        self.assertIn("XSS", data["result"]["messages"][0]["content"]["text"])

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

    def test_cors_disallows_untrusted_origin_by_default(self):
        """Verify arbitrary origins do not receive permissive CORS headers with credentials."""
        resp = self.client.options(
            "/health",
            headers={
                "Origin": "https://malicious.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertNotIn("access-control-allow-origin", resp.headers)
        self.assertNotIn("access-control-allow-credentials", resp.headers)


if __name__ == "__main__":
    unittest.main()
