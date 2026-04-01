"""Tests for the MCP server scanner."""

import json
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest

from bulla.scan import (
    ScanError,
    _send_request,
    _send_notification,
    scan_mcp_server,
    scan_mcp_servers,
)


class TestSendRequest:
    def test_valid_response(self):
        proc = MagicMock()
        response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        proc.stdout.readline.return_value = json.dumps(response).encode() + b"\n"
        result = _send_request(proc, "tools/list", {}, msg_id=1)
        assert result == response

    def test_error_response(self):
        proc = MagicMock()
        response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "fail"}}
        proc.stdout.readline.return_value = json.dumps(response).encode() + b"\n"
        with pytest.raises(ScanError, match="fail"):
            _send_request(proc, "tools/list", {}, msg_id=1)

    def test_empty_response(self):
        proc = MagicMock()
        proc.stdout.readline.return_value = b""
        with pytest.raises(ScanError, match="closed stdout"):
            _send_request(proc, "tools/list", {}, msg_id=1)

    def test_invalid_json(self):
        proc = MagicMock()
        proc.stdout.readline.return_value = b"not json\n"
        with pytest.raises(ScanError, match="Invalid JSON"):
            _send_request(proc, "tools/list", {}, msg_id=1)


class TestSendNotification:
    def test_sends_without_id(self):
        proc = MagicMock()
        _send_notification(proc, "notifications/initialized")
        call_args = proc.stdin.write.call_args[0][0]
        data = json.loads(call_args.decode())
        assert "id" not in data
        assert data["method"] == "notifications/initialized"


class TestScanErrors:
    def test_nonexistent_command(self):
        with pytest.raises(ScanError, match="Cannot spawn"):
            scan_mcp_server("/nonexistent_binary_12345")


class TestScanMcpServers:
    @patch("bulla.scan.scan_mcp_server")
    def test_merges_tools(self, mock_scan):
        mock_scan.side_effect = [
            [{"name": "tool_a", "inputSchema": {"properties": {"x": {}}}}],
            [{"name": "tool_b", "inputSchema": {"properties": {"y": {}}}}],
        ]
        result = scan_mcp_servers(["cmd1", "cmd2"])
        assert len(result) == 2
        names = {t["name"] for t in result}
        assert names == {"tool_a", "tool_b"}


class TestScanCLI:
    def test_scan_missing_command(self):
        r = subprocess.run(
            [sys.executable, "-m", "bulla", "scan"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0
