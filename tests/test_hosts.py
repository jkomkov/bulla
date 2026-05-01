"""Tests for the MCP host registry and built-in host adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bulla import hosts
from bulla.config import McpServerEntry
from bulla.hosts import HostError


FIXTURES = Path(__file__).parent / "fixtures" / "hosts"


@pytest.fixture(autouse=True)
def _reset_loader_state(monkeypatch):
    """Each test sees a freshly-loaded registry. Idempotent in practice."""
    # The registry only loads once; tests should see the same set across runs.
    yield


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


def test_all_hosts_includes_seven_builtins():
    names = {h.name for h in hosts.all_hosts()}
    assert {"cursor", "claude-desktop", "claude-code", "cline", "windsurf", "zed", "codex"} <= names


def test_get_returns_host():
    h = hosts.get("cursor")
    assert h.name == "cursor"
    assert h.display_name == "Cursor"


def test_get_unknown_raises_with_known_list():
    with pytest.raises(HostError) as exc:
        hosts.get("nonexistent")
    msg = str(exc.value)
    assert "nonexistent" in msg
    assert "claude-code" in msg  # known list mentioned


def test_register_idempotent():
    h = hosts.get("cursor")
    hosts.register(h)
    assert hosts.get("cursor") is h


# ------------------------------------------------------------------
# Per-host fixture parsing
# ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["cursor", "claude-desktop", "claude-code", "cline", "windsurf"])
def test_default_format_hosts_parse(name):
    """Hosts that use top-level mcpServers parse into McpServerEntry list."""
    host = hosts.get(name)
    fixture_path = FIXTURES / f"{name.replace('-', '_')}.json"
    entries = host.parse(fixture_path)
    assert len(entries) >= 2
    assert all(isinstance(e, McpServerEntry) for e in entries)
    # First entry should have a real name + non-empty command
    assert entries[0].name
    assert entries[0].command


def test_zed_parses_context_servers_key():
    """Zed embeds servers under context_servers, NOT mcpServers."""
    host = hosts.get("zed")
    entries = host.parse(FIXTURES / "zed.json")
    assert len(entries) == 2
    names = {e.name for e in entries}
    assert names == {"filesystem", "github"}


def test_codex_parses_toml_mcp_servers():
    """Codex uses TOML with snake_case [mcp_servers.<name>] tables."""
    host = hosts.get("codex")
    entries = host.parse(FIXTURES / "codex.toml")
    assert len(entries) == 2
    by_name = {e.name: e for e in entries}
    assert "filesystem" in by_name
    assert "github" in by_name
    # Args were shell-joined onto the command
    assert "@modelcontextprotocol/server-filesystem" in by_name["filesystem"].command
    # env table preserved
    assert by_name["github"].env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "redacted"


def test_codex_invalid_toml(tmp_path):
    host = hosts.get("codex")
    bad = tmp_path / "config.toml"
    bad.write_text("not = valid = toml")
    with pytest.raises(HostError):
        host.parse(bad)


def test_codex_no_mcp_servers_table(tmp_path):
    host = hosts.get("codex")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[other]\nkey = "value"\n')
    with pytest.raises(HostError):
        host.parse(cfg)


def test_codex_skips_http_transport(tmp_path):
    host = hosts.get("codex")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mcp_servers.local]\n'
        'command = "npx"\n'
        'args = ["server"]\n'
        '\n'
        '[mcp_servers.remote]\n'
        'type = "http"\n'
        'url = "https://example.com/mcp"\n'
    )
    entries = host.parse(cfg)
    assert {e.name for e in entries} == {"local"}


def test_detect_all_finds_codex_workspace(tmp_path, monkeypatch):
    """Workspace .codex/config.toml is detected via the TOML signal-check."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[mcp_servers.fs]\ncommand = "npx"\nargs = ["server"]\n'
    )
    matches = hosts.detect_all()
    assert any(m.host.name == "codex" for m in matches)


# ------------------------------------------------------------------
# Cline multi-editor path coverage
# ------------------------------------------------------------------


def test_cline_enumerates_all_known_editor_forks():
    """Cline candidate_paths must cover every VS Code fork it ships into."""
    from bulla.hosts.cline import _EDITOR_DIRS

    host = hosts.get("cline")
    paths = list(host.candidate_paths())
    # On any single OS we still get one path per editor in _EDITOR_DIRS
    # (Windows path may be absent if APPDATA isn't set, but the test
    # platform always has a usable parent).
    assert len(paths) >= len(_EDITOR_DIRS) - 1

    path_strs = [str(p) for p in paths]
    for editor in _EDITOR_DIRS:
        assert any(editor in p for p in path_strs), f"missing {editor}"


def test_cline_paths_include_extension_id_and_settings_filename():
    """All candidate paths point at the saoudrizwan.claude-dev settings file."""
    host = hosts.get("cline")
    for path in host.candidate_paths():
        s = str(path)
        assert "saoudrizwan.claude-dev" in s
        assert s.endswith("cline_mcp_settings.json")


def test_cline_no_workspace_path_anymore():
    """The earlier guessed .cline/mcp.json workspace path was unsupported by docs and is gone."""
    host = hosts.get("cline")
    for path in host.candidate_paths():
        assert ".cline/mcp.json" not in str(path)


def test_detect_all_finds_cline_in_cursor_fork(tmp_path, monkeypatch):
    """Cline installed inside Cursor (a VS Code fork) is detected."""
    import json

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()

    # Build the Cursor-as-host-for-Cline path on the current OS
    if sys.platform == "darwin":
        editor_root = (
            tmp_path / "fake-home" / "Library" / "Application Support" / "Cursor"
        )
    elif sys.platform.startswith("linux"):
        editor_root = tmp_path / "fake-home" / ".config" / "Cursor"
    else:  # win32 — APPDATA also patched via monkeypatch below
        editor_root = tmp_path / "fake-home" / "Cursor"
        monkeypatch.setenv("APPDATA", str(tmp_path / "fake-home"))

    settings_dir = (
        editor_root
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
        / "settings"
    )
    settings_dir.mkdir(parents=True)
    (settings_dir / "cline_mcp_settings.json").write_text(json.dumps({
        "mcpServers": {"fs": {"command": "npx", "args": ["server"]}}
    }))

    matches = hosts.detect_all()
    cline_matches = [m for m in matches if m.host.name == "cline"]
    assert len(cline_matches) == 1
    assert "Cursor" in str(cline_matches[0].path)


def test_diagnose_path_not_present(tmp_path):
    host = hosts.get("cursor")
    probe = hosts.diagnose_path(host, tmp_path / "absent.json")
    assert probe.exists is False
    assert probe.matched is False
    assert "not present" in probe.reason


def test_diagnose_path_parse_failure(tmp_path):
    host = hosts.get("cursor")
    bad = tmp_path / "bad.json"
    bad.write_text("not valid {")
    probe = hosts.diagnose_path(host, bad)
    assert probe.exists is True
    assert probe.matched is False
    assert "failed to parse" in probe.reason


def test_diagnose_path_no_servers_key(tmp_path):
    host = hosts.get("cursor")
    cfg = tmp_path / "empty.json"
    cfg.write_text(json.dumps({"otherKey": "value"}))
    probe = hosts.diagnose_path(host, cfg)
    assert probe.exists is True
    assert probe.matched is False
    assert "no recognized servers key" in probe.reason


def test_diagnose_path_match(tmp_path):
    host = hosts.get("cursor")
    cfg = tmp_path / "ok.json"
    cfg.write_text(json.dumps({"mcpServers": {"x": {"command": "echo"}}}))
    probe = hosts.diagnose_path(host, cfg)
    assert probe.exists is True
    assert probe.matched is True
    assert "mcpServers" in probe.reason


def test_diagnose_path_toml_match(tmp_path):
    host = hosts.get("codex")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.fs]\ncommand = "echo"\n')
    probe = hosts.diagnose_path(host, cfg)
    assert probe.matched is True
    assert "mcp_servers" in probe.reason


def test_diagnose_path_zed_context_servers(tmp_path):
    host = hosts.get("zed")
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"context_servers": {"fs": {"command": "echo"}}}))
    probe = hosts.diagnose_path(host, cfg)
    assert probe.matched is True
    assert "context_servers" in probe.reason


# ------------------------------------------------------------------
# CLI output contract — light snapshot / token-presence tests.
# Locks down the format string drift that unit tests on diagnose_path
# would not catch. NOT byte-for-byte snapshots; just the load-bearing
# tokens automation might depend on.
# ------------------------------------------------------------------


def _run_hosts_list_cli(monkeypatch, tmp_path, *cli_args: str) -> str:
    """Invoke `bulla hosts list ...` and capture stdout in an empty home."""
    import argparse
    from bulla.cli import _cmd_hosts_list

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)

    args = argparse.Namespace(
        verbose="-v" in cli_args or "--verbose" in cli_args,
        host=None,
        format=("json" if "--format=json" in cli_args or
                ("--format" in cli_args and "json" in cli_args) else "text"),
    )
    # Allow --host=<name> syntax in tests
    for a in cli_args:
        if a.startswith("--host="):
            args.host = a.split("=", 1)[1]

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _cmd_hosts_list(args)
    return buf.getvalue()


def test_hosts_list_text_contains_load_bearing_tokens(monkeypatch, tmp_path):
    """Non-verbose text output must include header, separator, summary, and -v hint."""
    out = _run_hosts_list_cli(monkeypatch, tmp_path)
    assert "HOST" in out
    assert "STATUS" in out
    assert "PATH" in out
    # Each registered host appears
    for name in ("cursor", "claude-code", "cline", "claude-desktop", "codex", "zed", "windsurf"):
        assert name in out
    # Summary line includes "config(s) detected" and "registered host(s)"
    assert "config(s) detected" in out
    assert "registered host(s)" in out
    # Hints to richer output
    assert "--verbose" in out
    assert "--format json" in out


def test_hosts_list_verbose_text_contains_per_path_reason(monkeypatch, tmp_path):
    """Verbose mode must show per-path mark + reason lines."""
    out = _run_hosts_list_cli(monkeypatch, tmp_path, "-v")
    # Reason lines from PathProbe
    assert "not present" in out
    # Section headers: display name + parenthesized name
    assert "Cline  (cline)" in out
    # Mark glyph for non-present paths is "-"
    assert "    - " in out
    assert "  status:" in out
    assert "  paths checked:" in out


def test_hosts_list_json_is_parseable_with_stable_schema(monkeypatch, tmp_path):
    """JSON output must parse cleanly and carry the documented top-level fields."""
    out = _run_hosts_list_cli(monkeypatch, tmp_path, "--format", "json")
    doc = json.loads(out)
    assert doc["schema_version"] == "1"
    assert "hosts" in doc
    assert isinstance(doc["hosts"], list)
    assert doc["total_detected"] == 0  # nothing on the fake home
    assert doc["total_hosts"] == 7
    # Each host has the documented fields
    for entry in doc["hosts"]:
        assert set(entry.keys()) >= {"name", "display_name", "status", "matched_count", "paths"}
        assert entry["status"] in {"detected", "not_detected"}
        for probe in entry["paths"]:
            assert set(probe.keys()) == {"path", "exists", "matched", "reason"}


def test_hosts_list_json_records_a_match(monkeypatch, tmp_path):
    """When a config is present, the JSON document marks it detected with matched_count."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {"x": {"command": "echo"}}
    }))

    out = _run_hosts_list_cli(monkeypatch, tmp_path, "--format", "json")
    doc = json.loads(out)
    cursor = next(h for h in doc["hosts"] if h["name"] == "cursor")
    assert cursor["status"] == "detected"
    assert cursor["matched_count"] == 1
    matched_paths = [p for p in cursor["paths"] if p["matched"]]
    assert len(matched_paths) == 1
    assert "mcpServers" in matched_paths[0]["reason"]
    assert doc["total_detected"] >= 1


def test_detect_all_finds_multiple_cline_installs(tmp_path, monkeypatch):
    """Cline in both VS Code and Cursor surfaces as two detected configs."""
    import json

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()

    if sys.platform == "darwin":
        base = tmp_path / "fake-home" / "Library" / "Application Support"
        editor_roots = [base / "Code", base / "Cursor"]
    elif sys.platform.startswith("linux"):
        base = tmp_path / "fake-home" / ".config"
        editor_roots = [base / "Code", base / "Cursor"]
    else:
        monkeypatch.setenv("APPDATA", str(tmp_path / "fake-home"))
        editor_roots = [
            tmp_path / "fake-home" / "Code",
            tmp_path / "fake-home" / "Cursor",
        ]

    for root in editor_roots:
        settings_dir = (
            root / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings"
        )
        settings_dir.mkdir(parents=True)
        (settings_dir / "cline_mcp_settings.json").write_text(json.dumps({
            "mcpServers": {"fs": {"command": "npx", "args": ["server"]}}
        }))

    matches = hosts.detect_all()
    cline_matches = [m for m in matches if m.host.name == "cline"]
    assert len(cline_matches) == 2
    found_dirs = {p for m in cline_matches for p in str(m.path).split("/")}
    assert "Code" in found_dirs
    assert "Cursor" in found_dirs


def test_claude_code_command_args_joined():
    """Verify command + args are shell-joined, not just command alone."""
    host = hosts.get("claude-code")
    entries = host.parse(FIXTURES / "claude_code.json")
    fs = next(e for e in entries if e.name == "filesystem")
    assert "npx" in fs.command
    assert "@modelcontextprotocol/server-filesystem" in fs.command


def test_cursor_env_preserved():
    host = hosts.get("cursor")
    entries = host.parse(FIXTURES / "cursor.json")
    gh = next(e for e in entries if e.name == "github")
    assert gh.env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "redacted"


# ------------------------------------------------------------------
# default_parse signal-checks
# ------------------------------------------------------------------


def test_default_parse_skips_http_transport(tmp_path):
    """HTTP/SSE servers should be skipped with a warning, not raised."""
    cfg = tmp_path / "mixed.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "stdio_one": {"command": "echo", "args": ["hi"]},
            "remote": {"type": "http", "url": "https://example.com/mcp"},
            "url_only": {"url": "https://example.com/mcp"},
        }
    }))
    entries = hosts.default_parse(cfg)
    names = {e.name for e in entries}
    assert names == {"stdio_one"}


def test_default_parse_missing_file(tmp_path):
    with pytest.raises(HostError):
        hosts.default_parse(tmp_path / "absent.json")


def test_default_parse_invalid_json(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("not json {")
    with pytest.raises(HostError):
        hosts.default_parse(cfg)


def test_default_parse_no_servers_key(tmp_path):
    cfg = tmp_path / "empty.json"
    cfg.write_text(json.dumps({"otherKey": "value"}))
    with pytest.raises(HostError):
        hosts.default_parse(cfg)


def test_default_parse_alt_servers_key(tmp_path):
    """servers_key parameter lets us point at e.g. context_servers."""
    cfg = tmp_path / "zed-style.json"
    cfg.write_text(json.dumps({
        "context_servers": {
            "fs": {"command": "echo", "args": ["x"]}
        }
    }))
    entries = hosts.default_parse(cfg, servers_key="context_servers")
    assert len(entries) == 1
    assert entries[0].name == "fs"


def test_default_parse_nested_mcp_servers(tmp_path):
    """Some hosts nest under top-level mcp.servers — extractor handles it."""
    cfg = tmp_path / "nested.json"
    cfg.write_text(json.dumps({
        "mcp": {"servers": {"x": {"command": "echo"}}}
    }))
    entries = hosts.default_parse(cfg)
    assert len(entries) == 1
    assert entries[0].name == "x"


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------


def test_detect_all_returns_empty_when_nothing_present(tmp_path, monkeypatch):
    """With no hosts on disk, detect_all returns empty list."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    matches = hosts.detect_all()
    assert matches == []


def test_detect_all_finds_cursor_workspace(tmp_path, monkeypatch):
    """Workspace .cursor/mcp.json is detected."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {"x": {"command": "echo"}}
    }))
    matches = hosts.detect_all()
    assert len(matches) == 1
    assert matches[0].host.name == "cursor"


def test_find_mcp_config_returns_first_match(tmp_path, monkeypatch):
    """The legacy find_mcp_config() shim still returns the first detected path."""
    from bulla.config import find_mcp_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    target = cursor_dir / "mcp.json"
    target.write_text(json.dumps({
        "mcpServers": {"x": {"command": "echo"}}
    }))
    assert find_mcp_config() == target
