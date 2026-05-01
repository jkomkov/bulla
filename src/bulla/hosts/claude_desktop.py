"""Claude Desktop MCP host.

Cross-platform config locations:
- macOS: ``~/Library/Application Support/Claude/claude_desktop_config.json``
- Linux: ``~/.config/Claude/claude_desktop_config.json``
- Windows: ``%APPDATA%/Claude/claude_desktop_config.json``

Format: standard ``{"mcpServers": {...}}`` dict.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


class ClaudeDesktopHost:
    name = "claude-desktop"
    display_name = "Claude Desktop"

    def candidate_paths(self) -> Iterator[Path]:
        if sys.platform == "darwin":
            yield (
                Path.home()
                / "Library"
                / "Application Support"
                / "Claude"
                / "claude_desktop_config.json"
            )
        elif sys.platform.startswith("linux"):
            yield Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
        elif sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                yield Path(appdata) / "Claude" / "claude_desktop_config.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)


register(ClaudeDesktopHost())
