"""Cursor MCP host.

Cursor stores MCP server configs at:
- ``.cursor/mcp.json`` in the workspace root (project-level)
- ``~/.cursor/mcp.json`` (user-level)

Format: standard ``{"mcpServers": {...}}`` dict.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


class CursorHost:
    name = "cursor"
    display_name = "Cursor"

    def candidate_paths(self) -> Iterator[Path]:
        yield Path.cwd() / ".cursor" / "mcp.json"
        yield Path.home() / ".cursor" / "mcp.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)


register(CursorHost())
