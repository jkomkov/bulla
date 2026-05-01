"""OpenAI Codex CLI MCP host.

Codex stores MCP configuration in TOML (not JSON, unlike every other
host) at:

- workspace: ``.codex/config.toml`` (trusted projects only)
- user: ``~/.codex/config.toml`` (cross-platform)

MCP servers live under ``[mcp_servers.<name>]`` tables (snake_case key,
not ``mcpServers``):

    [mcp_servers.filesystem]
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

The ``codex mcp`` subcommand also manages this file; Bulla just reads it.

Reference: https://developers.openai.com/codex/mcp
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import HostError, register

# Python 3.11+ ships tomllib in stdlib; 3.10 needs the tomli backport.
try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - 3.10 path
    import tomli as tomllib  # type: ignore[no-redef]


class CodexHost:
    name = "codex"
    display_name = "OpenAI Codex"

    def candidate_paths(self) -> Iterator[Path]:
        # Workspace first (trusted projects), then user.
        yield Path.cwd() / ".codex" / "config.toml"
        yield Path.home() / ".codex" / "config.toml"

    def parse(self, path: Path) -> list[McpServerEntry]:
        if not path.exists():
            raise HostError(f"Config file not found: {path}")

        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise HostError(f"Invalid TOML in {path}: {e}") from e

        servers = data.get("mcp_servers")
        if not isinstance(servers, dict) or not servers:
            raise HostError(
                f"No [mcp_servers.*] tables found in {path}. "
                f"Expected the Codex CLI MCP config format."
            )

        entries: list[McpServerEntry] = []
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue

            # Codex supports a transport hint via the optional "type" field; HTTP
            # variants are skipped to match the rest of Bulla's stdio-only stance.
            if cfg.get("type") in ("http", "sse", "streamableHttp") or "url" in cfg:
                print(
                    f"Skipping '{name}': HTTP/SSE transport not supported (stdio only)",
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


register(CodexHost())
