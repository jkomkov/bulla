"""MCP configuration file parser for Cursor and Claude Desktop."""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpServerEntry:
    """A single MCP server parsed from a config file."""

    name: str
    command: str
    env: dict[str, str] = field(default_factory=dict)


class ConfigError(Exception):
    """Raised when a config file cannot be parsed."""


def parse_mcp_config(path: Path) -> list[McpServerEntry]:
    """Parse a Cursor or Claude Desktop MCP configuration JSON.

    Expects the standard format::

        {"mcpServers": {"name": {"command": "...", "args": [...], "env": {...}}}}

    Entries that use HTTP/SSE transport (``"type": "http"`` or ``"url"``
    present) are skipped with a warning to stderr -- only stdio servers
    are supported.

    Raises :class:`ConfigError` if the file is missing or malformed.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(f"Expected JSON object in {path}")

    servers_dict: dict[str, Any] = data.get("mcpServers", {})
    if not servers_dict:
        raise ConfigError(
            f"No 'mcpServers' key found in {path}. "
            "Expected Cursor or Claude Desktop MCP config format."
        )

    entries: list[McpServerEntry] = []
    for name, cfg in servers_dict.items():
        if not isinstance(cfg, dict):
            continue

        if cfg.get("type") in ("http", "sse", "streamableHttp") or "url" in cfg:
            print(
                f"Skipping '{name}': HTTP/SSE transport not supported "
                f"(stdio only)",
                file=sys.stderr,
            )
            continue

        cmd_str = cfg.get("command", "")
        if not cmd_str:
            print(f"Skipping '{name}': no 'command' field", file=sys.stderr)
            continue

        args = cfg.get("args", [])
        full_command = shlex.join([cmd_str] + [str(a) for a in args])
        env = cfg.get("env", {})
        if not isinstance(env, dict):
            env = {}

        entries.append(McpServerEntry(name=name, command=full_command, env=env))

    return entries


def find_mcp_config() -> Path | None:
    """Auto-detect MCP config in standard locations.

    Walks every registered host in :mod:`bulla.hosts` (Cursor, Claude Code,
    Cline, Claude Desktop, Zed, Windsurf) and returns the first config
    file found on disk. Returns ``None`` if no host config is detected.

    For programmatic access to all matches (including the host that owns
    each match), use :func:`bulla.hosts.detect_all` directly.
    """
    from bulla.hosts import detect_all

    matches = detect_all()
    if not matches:
        return None
    return matches[0].path
