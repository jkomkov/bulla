"""Cline MCP host.

Cline is a VS Code extension (``saoudrizwan.claude-dev``) that ships
its MCP config inside the VS Code globalStorage directory. The same
extension installs cleanly into every VS Code fork; Bulla scans each
known fork's data directory.

Per-OS data directory templates:
- macOS:   ``~/Library/Application Support/<editor>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json``
- Linux:   ``~/.config/<editor>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json``
- Windows: ``%APPDATA%/<editor>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json``

Where ``<editor>`` is one of:

- ``Code`` (Visual Studio Code)
- ``Code - Insiders`` (VS Code Insiders)
- ``Cursor`` (Cursor IDE — VS Code fork)
- ``Windsurf`` (Codeium IDE — VS Code fork)
- ``VSCodium`` (open-source VS Code build)

Config format: standard ``{"mcpServers": {...}}`` JSON dict (same as
Claude Desktop).

References:
- https://github.com/cline/cline (extension ID ``saoudrizwan.claude-dev``)
- https://code.visualstudio.com/docs/copilot/customization/mcp-servers
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


_CLINE_EXT_RELATIVE = (
    "User",
    "globalStorage",
    "saoudrizwan.claude-dev",
    "settings",
    "cline_mcp_settings.json",
)

# All known VS Code-fork editor data-directory names. Order is roughly
# ranked by adoption: regular VS Code first, then forks. detect_all()
# returns every match so multi-install setups surface them all.
_EDITOR_DIRS = (
    "Code",
    "Code - Insiders",
    "Cursor",
    "Windsurf",
    "VSCodium",
)


def _editor_data_root(editor: str) -> Path | None:
    """Return the OS-specific parent directory for ``<editor>/User/...``."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / editor
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / editor
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / editor
    return None


class ClineHost:
    name = "cline"
    display_name = "Cline"

    def candidate_paths(self) -> Iterator[Path]:
        for editor in _EDITOR_DIRS:
            root = _editor_data_root(editor)
            if root is not None:
                yield root.joinpath(*_CLINE_EXT_RELATIVE)

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)


register(ClineHost())
