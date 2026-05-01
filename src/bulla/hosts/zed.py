"""Zed editor MCP host.

Zed embeds MCP servers under ``context_servers`` (NOT ``mcpServers``)
inside its main settings file:

- macOS / Linux: ``~/.config/zed/settings.json``
- Windows: ``%APPDATA%/Zed/settings.json``

The ``context_servers`` value is a dict keyed by server name; each entry
follows the same ``{command, args, env}`` shape as standard MCP configs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import default_parse, register


class ZedHost:
    name = "zed"
    display_name = "Zed"

    def candidate_paths(self) -> Iterator[Path]:
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                yield Path(appdata) / "Zed" / "settings.json"
        else:
            yield Path.home() / ".config" / "zed" / "settings.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path, servers_key="context_servers")


register(ZedHost())
