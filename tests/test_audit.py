"""Tests for bulla audit: config parser, parallel scan, CLI output."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bulla.config import ConfigError, McpServerEntry, find_mcp_config, parse_mcp_config
from bulla.scan import ServerScanResult, scan_mcp_servers_parallel

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CONFIG = FIXTURES / "sample_mcp_config.json"


# ── Config parser tests ──────────────────────────────────────────────


class TestParseConfig:
    def test_parse_valid_stdio_servers(self):
        entries = parse_mcp_config(SAMPLE_CONFIG)
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert "filesystem" in names
        assert "fetch" in names

        fs = next(e for e in entries if e.name == "filesystem")
        assert "npx" in fs.command
        assert "@modelcontextprotocol/server-filesystem" in fs.command

    def test_skips_http_entries(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "stdio_server": {
                    "command": "node",
                    "args": ["server.js"],
                },
                "http_server": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                },
                "sse_server": {
                    "url": "https://example.com/sse",
                },
            }
        }))
        entries = parse_mcp_config(cfg)
        assert len(entries) == 1
        assert entries[0].name == "stdio_server"

    def test_raises_missing_mcp_servers(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(json.dumps({"other_key": {}}))
        with pytest.raises(ConfigError, match="No 'mcpServers' key"):
            parse_mcp_config(cfg)

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            parse_mcp_config(tmp_path / "nonexistent.json")

    def test_env_passthrough(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "ghp_test123"},
                }
            }
        }))
        entries = parse_mcp_config(cfg)
        assert len(entries) == 1
        assert entries[0].env == {"GITHUB_TOKEN": "ghp_test123"}


class TestFindConfig:
    def test_returns_none_when_nothing_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        assert find_mcp_config() is None


# ── Parallel scan tests ──────────────────────────────────────────────


CANNED_TOOLS_A = [
    {"name": "create_payment", "description": "Create a payment", "inputSchema": {
        "type": "object", "properties": {
            "amount": {"type": "number"},
            "currency": {"type": "string"},
            "recipient": {"type": "string"},
            "date": {"type": "string"},
        }
    }},
    {"name": "validate_payment", "description": "Validate a payment transaction", "inputSchema": {
        "type": "object", "properties": {
            "amount": {"type": "number"},
            "currency": {"type": "string"},
            "sender": {"type": "string"},
            "timestamp": {"type": "string"},
        }
    }},
]

CANNED_TOOLS_B = [
    {"name": "check_balance", "description": "Check account balance", "inputSchema": {
        "type": "object", "properties": {
            "account_id": {"type": "string"},
            "currency": {"type": "string"},
            "date": {"type": "string"},
        }
    }},
]


class TestParallelScan:
    def test_mixed_success_failure(self):
        def mock_scan(command, *, timeout=10.0, env=None):
            if "fail" in command:
                from bulla.scan import ScanError
                raise ScanError("Cannot spawn server: file not found")
            return CANNED_TOOLS_A

        with patch("bulla.scan.scan_mcp_server", side_effect=mock_scan):
            results = scan_mcp_servers_parallel({
                "good_server": {"command": "echo good"},
                "bad_server": {"command": "fail"},
            })

        assert len(results) == 2
        good = next(r for r in results if r.name == "good_server")
        bad = next(r for r in results if r.name == "bad_server")
        assert good.ok
        assert len(good.tools) == 2
        assert not bad.ok
        assert "Cannot spawn" in bad.error

    def test_preserves_order(self):
        def mock_scan(command, *, timeout=10.0, env=None):
            return [{"name": "t"}]

        with patch("bulla.scan.scan_mcp_server", side_effect=mock_scan):
            results = scan_mcp_servers_parallel({
                "alpha": {"command": "cmd1"},
                "beta": {"command": "cmd2"},
                "gamma": {"command": "cmd3"},
            })

        assert [r.name for r in results] == ["alpha", "beta", "gamma"]


# ── Audit CLI tests (mocking scan layer) ─────────────────────────────


def _make_scan_results(tools_a=CANNED_TOOLS_A, tools_b=CANNED_TOOLS_B):
    return [
        ServerScanResult(name="filesystem", tools=tools_a),
        ServerScanResult(name="fetch", tools=tools_b),
    ]


def _run_audit(*args):
    """Run the audit CLI handler via subprocess."""
    import subprocess
    return subprocess.run(
        [sys.executable, "-m", "bulla", "audit", *args],
        capture_output=True,
        text=True,
    )


class TestAuditCli:
    def _run_audit(self, extra_args, scan_results, config_entries):
        """Run bulla audit in-process with mocked scan + config."""
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from bulla.cli import main

        out = io.StringIO()
        err = io.StringIO()
        exit_code = 0
        with patch(
            "bulla.scan.scan_mcp_servers_parallel",
            return_value=scan_results,
        ), patch(
            "bulla.config.parse_mcp_config",
            return_value=config_entries,
        ):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    sys.argv = ["bulla", "audit", str(SAMPLE_CONFIG)] + list(extra_args)
                    main()
                except SystemExit as e:
                    exit_code = e.code or 0
        return out.getvalue(), err.getvalue(), exit_code

    def test_audit_text_output(self):
        entries = [
            McpServerEntry("filesystem", "npx server-fs"),
            McpServerEntry("fetch", "uvx mcp-server-fetch"),
        ]
        text, _, _ = self._run_audit([], _make_scan_results(), entries)

        assert "server(s) scanned" in text
        assert "Coherence fee:" in text
        assert "Cross-server risk:" in text
        assert "Boundary fee:" in text

    def test_audit_json_output(self):
        entries = [
            McpServerEntry("filesystem", "npx server-fs"),
            McpServerEntry("fetch", "uvx mcp-server-fetch"),
        ]
        text, _, _ = self._run_audit(["--format", "json"], _make_scan_results(), entries)
        data = json.loads(text)

        assert "servers" in data
        assert "cross_server_decomposition" in data
        assert "coherence_fee" in data
        assert isinstance(data["servers"], list)
        assert len(data["servers"]) == 2
        assert any(s["name"] == "filesystem" for s in data["servers"])

    def test_audit_threshold_fail(self):
        entries = [
            McpServerEntry("filesystem", "npx server-fs"),
            McpServerEntry("fetch", "uvx mcp-server-fetch"),
        ]
        _, stderr_text, exit_code = self._run_audit(
            ["--max-fee", "0"], _make_scan_results(), entries
        )
        assert exit_code == 1 or "FAIL" in stderr_text

    def test_audit_with_failed_server(self):
        results = [
            ServerScanResult(name="filesystem", tools=CANNED_TOOLS_A),
            ServerScanResult(name="broken", tools=[], error="Cannot spawn server"),
        ]
        entries = [
            McpServerEntry("filesystem", "npx server-fs"),
            McpServerEntry("broken", "bad-cmd"),
        ]
        text, _, _ = self._run_audit([], results, entries)

        assert "1 skipped" in text
        assert "FAILED" in text
        assert "broken" in text
