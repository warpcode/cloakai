"""Unit tests for MCP request handlers and tool execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import HANDLERS, TOOLS, handle_jsonrpc
from jules_client import AsyncJulesClient, JulesError
from protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JSONRPCRequest,
)


class TestHandlers(unittest.TestCase):
    def setUp(self):
        self.mock_client = AsyncMock(spec=AsyncJulesClient)

    def test_initialize(self):
        req = JSONRPCRequest(
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 1)
        self.assertIsNone(res.error)
        self.assertEqual(res.result["serverInfo"]["name"], "cloakai-jules")
        self.assertEqual(res.result["protocolVersion"], "2024-11-05")

    def test_ping(self):
        req = JSONRPCRequest(id=2, method="ping")
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 2)
        self.assertEqual(res.result, {})

    def test_tools_list(self):
        req = JSONRPCRequest(id=3, method="tools/list")
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 3)
        tools = res.result["tools"]
        tool_names = {t["name"] for t in tools}
        expected = {
            "jules_start",
            "jules_send",
            "jules_status",
            "jules_approve_plan",
            "jules_sources",
            "jules_nudge",
            "jules_audit",
            "jules_archive_session",
            "jules_delete_session",
            "jules_get_patch",
        }
        self.assertTrue(expected.issubset(tool_names))

    def test_call_tool_jules_send(self):
        self.mock_client.send_message.return_value = {}
        req = JSONRPCRequest(
            id=4,
            method="tools/call",
            params={
                "name": "jules_send",
                "arguments": {"session_id": "s123", "prompt": "continue please"},
            },
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 4)
        self.mock_client.send_message.assert_awaited_once_with("s123", "continue please")
        self.assertFalse(res.result["isError"])
        self.assertIn("sent to s123", res.result["content"][0]["text"])

    def test_call_tool_jules_nudge(self):
        self.mock_client.nudge.return_value = {}
        req = JSONRPCRequest(
            id=5,
            method="tools/call",
            params={
                "name": "jules_nudge",
                "arguments": {"session_id": "s456", "template": "pr_reminder"},
            },
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 5)
        self.mock_client.nudge.assert_awaited_once_with("s456", template="pr_reminder")
        self.assertFalse(res.result["isError"])
        self.assertIn("pr_reminder", res.result["content"][0]["text"])

    def test_call_tool_jules_delete_session_confirm_false(self):
        self.mock_client.delete_session.side_effect = JulesError("Refusing to delete without confirm=True")
        req = JSONRPCRequest(
            id=6,
            method="tools/call",
            params={
                "name": "jules_delete_session",
                "arguments": {"session_id": "s789", "confirm": False},
            },
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 6)
        self.assertTrue(res.result["isError"])
        self.assertIn("Refusing to delete", res.result["content"][0]["text"])

    def test_call_tool_jules_get_patch(self):
        self.mock_client.extract_patch.return_value = {
            "patch": "--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-bar\n+baz\n",
            "source": "github/owner/repo",
            "commit_message": "fix: update foo",
            "session_id": "s999",
        }
        req = JSONRPCRequest(
            id=7,
            method="tools/call",
            params={
                "name": "jules_get_patch",
                "arguments": {"session_id": "s999"},
            },
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 7)
        self.assertFalse(res.result["isError"])
        self.assertIn("Undelivered Git Patch", res.result["content"][0]["text"])
        self.assertIn("+baz", res.result["content"][0]["text"])

    def test_unknown_method(self):
        req = JSONRPCRequest(id=8, method="unknown_method")
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 8)
        self.assertEqual(res.error.code, METHOD_NOT_FOUND)

    def test_unknown_tool(self):
        req = JSONRPCRequest(
            id=9,
            method="tools/call",
            params={"name": "non_existent_tool", "arguments": {}},
        )
        res = asyncio.run(handle_jsonrpc(req, self.mock_client))
        self.assertEqual(res.id, 9)
        self.assertEqual(res.error.code, METHOD_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
