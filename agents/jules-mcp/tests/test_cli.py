"""Unit tests for the Jules CLI interface."""

from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli import build_parser, main
from jules_client import JulesClient, JulesError


class TestJulesCLI(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_parser_sources(self):
        args = self.parser.parse_args(["sources", "--page-size", "10"])
        self.assertEqual(args.subcommand, "sources")
        self.assertEqual(args.page_size, 10)

    def test_parser_start(self):
        args = self.parser.parse_args([
            "start", "fix issue",
            "--source", "github/owner/repo",
            "--branch", "develop",
            "--require-approval",
        ])
        self.assertEqual(args.subcommand, "start")
        self.assertEqual(args.prompt, "fix issue")
        self.assertEqual(args.source, "github/owner/repo")
        self.assertEqual(args.branch, "develop")
        self.assertTrue(args.require_approval)

    def test_parser_nudge(self):
        args = self.parser.parse_args(["nudge", "s123", "pr_reminder"])
        self.assertEqual(args.subcommand, "nudge")
        self.assertEqual(args.session_id, "s123")
        self.assertEqual(args.template, "pr_reminder")

    def test_parser_delete_confirm_gate(self):
        args1 = self.parser.parse_args(["delete", "s123"])
        self.assertFalse(args1.confirm)

        args2 = self.parser.parse_args(["delete", "s123", "--confirm"])
        self.assertTrue(args2.confirm)

    def test_parser_mcp_default_and_http(self):
        args1 = self.parser.parse_args(["mcp"])
        self.assertEqual(args1.subcommand, "mcp")
        self.assertFalse(args1.http)

        args2 = self.parser.parse_args(["mcp", "--http", "--port", "9000"])
        self.assertEqual(args2.subcommand, "mcp")
        self.assertTrue(args2.http)
        self.assertEqual(args2.port, 9000)

    @patch("cli._get_client")
    def test_cmd_sources_execution(self, mock_get_client):
        mock_client = MagicMock(spec=JulesClient)
        mock_client.list_sources.return_value = [
            {
                "id": "github/owner/repo",
                "githubRepo": {"defaultBranch": {"displayName": "main"}, "branches": [{"displayName": "main"}]},
            }
        ]
        mock_get_client.return_value = mock_client

        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["sources"])
        self.assertEqual(ret, 0)
        self.assertIn("github/owner/repo", stdout.getvalue())

    @patch("cli._get_client")
    def test_cmd_delete_refuses_without_confirm(self, mock_get_client):
        mock_client = MagicMock(spec=JulesClient)
        mock_get_client.return_value = mock_client

        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            ret = main(["delete", "s123"])
        self.assertEqual(ret, 1)
        self.assertIn("Refusing to permanently delete", stderr.getvalue())
        mock_client.delete_session.assert_not_called()

    @patch("cli._get_client")
    def test_cmd_delete_succeeds_with_confirm(self, mock_get_client):
        mock_client = MagicMock(spec=JulesClient)
        mock_client.delete_session.return_value = {}
        mock_get_client.return_value = mock_client

        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["delete", "s123", "--confirm"])
        self.assertEqual(ret, 0)
        self.assertIn("permanently deleted", stdout.getvalue())

    @patch("cli.cmd_mcp")
    def test_mcp_dispatch(self, mock_cmd_mcp):
        mock_cmd_mcp.return_value = 0
        ret = main(["mcp", "--http"])
        self.assertEqual(ret, 0)
        mock_cmd_mcp.assert_called_once()
        args = mock_cmd_mcp.call_args[0][0]
        self.assertTrue(args.http)


if __name__ == "__main__":
    unittest.main()
