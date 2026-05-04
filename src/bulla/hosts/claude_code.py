"""Claude Code MCP host.

Claude Code (Anthropic's CLI agent) writes user-scoped MCP servers
to ``~/.claude.json`` (a single JSON file at the home directory).
The file is structured as::

    {
      "mcpServers": { ... },                    # global servers
      "projects": {
        "<absolute project path>": {
          "mcpServers": { "memory": {...}, ... },
          ...
        },
        ...
      },
      ...
    }

Most users keep their MCP servers under a per-project section rather
than the top-level ``mcpServers``. Bulla auto-detects which project
section to read by matching the current working directory against
the keys of ``projects``; the longest-matching prefix wins so a scan
inside a project subdirectory still finds the correct servers.

Older builds wrote ``~/.claude/settings.json``; that path is still
checked as a fallback. Project-scoped overrides may also live at
``<cwd>/.mcp.json`` (the canonical project-MCP file).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from bulla.config import McpServerEntry
from bulla.hosts import HostError, default_parse, register


class ClaudeCodeHost:
    name = "claude-code"
    display_name = "Claude Code"

    def candidate_paths(self) -> Iterator[Path]:
        # Project-scoped first — current working directory wins over
        # user-scoped configs when both are present.
        yield Path.cwd() / ".mcp.json"
        yield Path.cwd() / ".claude" / "settings.json"
        # User-scoped: the canonical Claude Code config is the
        # single-file ~/.claude.json. The .claude/settings.json
        # fallback covers older builds.
        yield Path.home() / ".claude.json"
        yield Path.home() / ".claude" / "settings.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        # If we're parsing the canonical ~/.claude.json with its
        # ``projects`` block, prefer the entry matching the current
        # working directory before falling back to the top-level
        # ``mcpServers``.
        if path == Path.home() / ".claude.json":
            entries = _parse_user_scoped_with_project_match(path)
            if entries:
                return entries
            # Fall through to default_parse, which will probably raise
            # HostError on an empty top-level mcpServers.
        return default_parse(path)


def _parse_user_scoped_with_project_match(path: Path) -> list[McpServerEntry]:
    """Look for the longest project key under ``projects`` that is a
    prefix of cwd, return its mcpServers entries. Returns an empty
    list when nothing matches; the caller falls back to default_parse.
    """
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return []
    cwd = str(Path.cwd().resolve())
    matched_key: str | None = None
    matched_len = -1
    for proj_path in projects.keys():
        if not isinstance(proj_path, str):
            continue
        try:
            proj_resolved = str(Path(proj_path).resolve())
        except OSError:
            continue
        if cwd == proj_resolved or cwd.startswith(proj_resolved + "/"):
            if len(proj_resolved) > matched_len:
                matched_key = proj_path
                matched_len = len(proj_resolved)
    if matched_key is None:
        return []
    proj_cfg = projects[matched_key]
    if not isinstance(proj_cfg, dict):
        return []
    servers = proj_cfg.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return []
    # Build a synthetic top-level dict so default_parse handles the
    # rest (command, args, env normalization, HTTP-transport skip).
    synthetic = {"mcpServers": servers}
    # Write to a temp path? No — call the parsing logic directly.
    return _parse_servers_dict(servers, source_path=path)


def _parse_servers_dict(
    servers: dict, *, source_path: Path
) -> list[McpServerEntry]:
    """Mirror of default_parse's normalization logic, applied to an
    already-extracted ``mcpServers`` dict (rather than re-reading the
    file). Used by the project-scoped path so we don't double-load."""
    import shlex
    import sys

    entries: list[McpServerEntry] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
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


register(ClaudeCodeHost())
