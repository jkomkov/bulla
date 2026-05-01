"""Claude Code MCP host.

Claude Code (Anthropic's CLI agent) stores its full settings at
``~/.claude/settings.json``. MCP servers live under one of:

- top-level ``mcpServers`` (Cursor-compatible shape)
- nested ``mcp.servers``

Bulla's :func:`default_parse` looks at both shapes.

Workspace-level overrides may live at ``.claude/settings.json`` in the
current working directory.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


class ClaudeCodeHost:
    name = "claude-code"
    display_name = "Claude Code"

    def candidate_paths(self) -> Iterator[Path]:
        yield Path.cwd() / ".claude" / "settings.json"
        yield Path.home() / ".claude" / "settings.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)


register(ClaudeCodeHost())
