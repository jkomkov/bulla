"""Windsurf (Codeium) MCP host.

Windsurf stores MCP server configs at ``~/.codeium/windsurf/mcp_config.json``
on macOS/Linux and ``%USERPROFILE%/.codeium/windsurf/mcp_config.json`` on
Windows. The file follows the standard ``{"mcpServers": {...}}`` shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


class WindsurfHost:
    name = "windsurf"
    display_name = "Windsurf"

    def candidate_paths(self) -> Iterator[Path]:
        yield Path.home() / ".codeium" / "windsurf" / "mcp_config.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)


register(WindsurfHost())
