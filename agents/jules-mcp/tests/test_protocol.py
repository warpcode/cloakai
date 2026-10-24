"""Unit tests for JSON-RPC 2.0 and MCP protocol schemas."""

from __future__ import annotations

import unittest
import sys
from pathlib import Path

# Add agents/jules-mcp to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    CallToolResult,
    InitializeParams,
    InitializeResult,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    JulesApprovePlanParams,
    JulesArchiveSessionParams,
    JulesAuditParams,
    JulesCallParams,
    JulesDeleteSessionParams,
    JulesGetActivityParams,
    JulesGetPatchParams,
    JulesGetSessionParams,
    JulesGetSourceParams,
    JulesListSessionsParams,
    JulesNudgeParams,
    JulesSendParams,
    JulesSourcesParams,
    JulesStartParams,
    JulesStatusParams,
    ServerCapabilities,
    ServerInfo,
    TextContent,
    Tool,
    ToolInputSchema,
    model_to_json_schema,
)


class TestProtocolModels(unittest.TestCase):
    def test_jsonrpc_request_serialization(self):
        req = JSONRPCRequest(id=1, method="tools/list", params={})
        d = req.model_dump(by_alias=True)
        self.assertEqual(d["jsonrpc"], "2.0")
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["method"], "tools/list")

    def test_jsonrpc_response_make_result(self):
        resp = JSONRPCResponse.make_result("req-123", {"status": "ok"})
        d = resp.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(d["jsonrpc"], "2.0")
        self.assertEqual(d["id"], "req-123")
        self.assertEqual(d["result"], {"status": "ok"})
        self.assertNotIn("error", d)

    def test_jsonrpc_response_make_error(self):
        resp = JSONRPCResponse.make_error(42, METHOD_NOT_FOUND, "Tool not found")
        d = resp.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(d["id"], 42)
        self.assertEqual(d["error"]["code"], METHOD_NOT_FOUND)
        self.assertEqual(d["error"]["message"], "Tool not found")

    def test_initialize_schemas(self):
        params = InitializeParams(
            protocolVersion="2024-11-05",
            capabilities={},
            clientInfo={"name": "test-client", "version": "1.0"},
        )
        self.assertEqual(params.protocolVersion, "2024-11-05")
        self.assertEqual(params.clientInfo.name, "test-client")

        res = InitializeResult(
            protocolVersion="2024-11-05",
            capabilities=ServerCapabilities(tools={"listChanged": False}),
            serverInfo=ServerInfo(name="cloakai-jules", version="2.0.0"),
        )
        d = res.model_dump(by_alias=True)
        self.assertEqual(d["serverInfo"]["name"], "cloakai-jules")

    def test_tool_schemas_and_conversion(self):
        schema = model_to_json_schema(JulesStartParams)
        self.assertEqual(schema.type, "object")
        self.assertIn("prompt", schema.properties)
        self.assertIn("prompt", schema.required)
        self.assertIn("branch", schema.properties)

    def test_jules_send_params_aliases(self):
        # Using prompt
        p1 = JulesSendParams(sessionId="123", prompt="hello")
        self.assertEqual(p1.session_id, "123")
        self.assertEqual(p1.prompt, "hello")

        # Using message alias
        p2 = JulesSendParams(session_id="456", message="world")
        self.assertEqual(p2.session_id, "456")
        self.assertEqual(p2.prompt, "world")

    def test_jules_nudge_params_validation(self):
        p = JulesNudgeParams(session_id="s1", template="pr_reminder")
        self.assertEqual(p.template, "pr_reminder")

        with self.assertRaises(Exception):
            JulesNudgeParams(session_id="s1", template="invalid_template")

    def test_jules_delete_params_confirm_gate(self):
        p1 = JulesDeleteSessionParams(session_id="s1")
        self.assertFalse(p1.confirm)

        p2 = JulesDeleteSessionParams(session_id="s1", confirm=True)
        self.assertTrue(p2.confirm)


if __name__ == "__main__":
    unittest.main()
