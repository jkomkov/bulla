"""MCP host registry.

A "host" is an application that consumes MCP servers and stores its
configuration somewhere on disk. Each host module registers itself at
import time. Adding a new host is a single new file under this package
plus an import line in :func:`_load_builtin_hosts`.

The registry decouples host-specific concerns (config paths, file
formats) from the rest of Bulla. Detection auto-discovers configs from
all registered hosts; explicit `--host <name>` selection is also
supported.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from bulla.config import McpServerEntry

__all__ = [
    "MCPHost",
    "DetectedConfig",
    "HostError",
    "PathProbe",
    "register",
    "all_hosts",
    "get",
    "detect_all",
    "default_parse",
    "diagnose_path",
]


class HostError(Exception):
    """Raised for host-registry operations (lookup miss, parse failure)."""


@runtime_checkable
class MCPHost(Protocol):
    """One MCP-consuming application (Cursor, Claude Code, Cline, ...).

    Host modules implement this protocol and call :func:`register` at
    import time.
    """

    name: str
    """Stable lookup key, lowercase-hyphenated. e.g. ``"claude-code"``."""

    display_name: str
    """Human-readable name. e.g. ``"Claude Code"``."""

    def candidate_paths(self) -> Iterator[Path]:
        """Yield candidate config paths in precedence order.

        Workspace-scoped paths first, then user-scoped. Hosts may yield
        OS-specific paths (using ``sys.platform``) and skip paths that
        don't apply to the current OS.
        """
        ...

    def parse(self, path: Path) -> list[McpServerEntry]:
        """Parse this host's config file into MCP server entries.

        Most hosts can delegate to :func:`default_parse` (top-level
        ``mcpServers`` dict). Hosts whose config nests MCP servers under
        a different key (e.g. Zed's ``context_servers``) override.
        """
        ...


@dataclass(frozen=True)
class DetectedConfig:
    """A host config found on disk."""

    host: MCPHost
    path: Path


@dataclass(frozen=True)
class PathProbe:
    """Diagnostic for one candidate path during scan.

    Surfaces *why* a path did or did not match. Used by ``bulla hosts list -v``
    to make multi-editor / multi-install setups explicable to users.
    """

    path: Path
    exists: bool
    """Whether the file is present on disk."""

    matched: bool
    """Whether the file passed the auto-detect signal-check (parses + has
    a recognized servers key). Implies ``exists``."""

    reason: str
    """One-line human-readable explanation. Always populated."""


_HOSTS: dict[str, MCPHost] = {}


def register(host: MCPHost) -> None:
    """Register a host. Idempotent — re-registration replaces."""
    if not isinstance(host.name, str) or not host.name:
        raise HostError(f"Host has invalid name: {host!r}")
    _HOSTS[host.name] = host


def all_hosts() -> Iterable[MCPHost]:
    """Return registered hosts in insertion order."""
    _ensure_loaded()
    return list(_HOSTS.values())


def get(name: str) -> MCPHost:
    """Look up a host by stable name. Raises :class:`HostError` on miss."""
    _ensure_loaded()
    try:
        return _HOSTS[name]
    except KeyError as e:
        known = ", ".join(sorted(_HOSTS))
        raise HostError(
            f"Unknown host {name!r}. Registered: {known}."
        ) from e


def detect_all() -> list[DetectedConfig]:
    """Walk every registered host's candidate paths; return existing matches.

    Order: hosts in registration order, then each host's own precedence
    (workspace → user). Multiple matches per host are possible and
    returned in that order.
    """
    _ensure_loaded()
    found: list[DetectedConfig] = []
    for host in _HOSTS.values():
        for path in host.candidate_paths():
            if path.exists() and _looks_like_mcp_config(host, path):
                found.append(DetectedConfig(host=host, path=path))
    return found


def diagnose_path(host: MCPHost, path: Path) -> PathProbe:
    """Classify why a candidate path did or did not match.

    Returns a :class:`PathProbe` with one of these reasons:

    - ``"not present"`` — file does not exist
    - ``"exists, parses, <signal> found"`` — full match
    - ``"exists but failed to parse"`` — file present, JSON/TOML decode error
    - ``"exists but no recognized servers key"`` — file parses but lacks
      mcpServers / mcp_servers / context_servers / mcp.servers
    """
    if not path.exists():
        return PathProbe(path=path, exists=False, matched=False, reason="not present")

    suffix = path.suffix.lower()
    try:
        if suffix == ".toml":
            try:
                import tomllib  # type: ignore[import-not-found]
            except ImportError:  # pragma: no cover - 3.10 path
                import tomli as tomllib  # type: ignore[no-redef]
            with path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, Exception) as e:  # noqa: BLE001
        return PathProbe(
            path=path, exists=True, matched=False,
            reason=f"exists but failed to parse: {type(e).__name__}",
        )

    if not isinstance(data, dict):
        return PathProbe(
            path=path, exists=True, matched=False,
            reason="exists but root is not a JSON/TOML object",
        )

    for key in ("mcpServers", "mcp_servers", "context_servers"):
        if key in data:
            return PathProbe(
                path=path, exists=True, matched=True,
                reason=f"exists, parses, {key!r} found",
            )

    nested = data.get("mcp")
    if isinstance(nested, dict):
        for sub in ("servers", "mcpServers"):
            if sub in nested:
                return PathProbe(
                    path=path, exists=True, matched=True,
                    reason=f"exists, parses, 'mcp.{sub}' found",
                )

    return PathProbe(
        path=path, exists=True, matched=False,
        reason="exists but no recognized servers key",
    )


def default_parse(
    path: Path,
    *,
    servers_key: str = "mcpServers",
) -> list[McpServerEntry]:
    """Standard parser for ``{servers_key: {name: {command, args, env}}}``.

    Used by hosts whose format matches Cursor / Claude Desktop. Hosts
    with nested or differently-keyed configs implement their own
    ``parse()`` and may delegate to this with a different key (e.g. Zed
    passes ``servers_key="context_servers"``).
    """
    import shlex

    if not path.exists():
        raise HostError(f"Config file not found: {path}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise HostError(f"Invalid JSON in {path}: {e}") from e

    if not isinstance(data, dict):
        raise HostError(f"Expected JSON object in {path}")

    servers = _extract_servers(data, servers_key)
    if not servers:
        raise HostError(
            f"No '{servers_key}' entries found in {path}."
        )

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


def _extract_servers(data: dict, key: str) -> dict:
    """Locate the servers dict, supporting both top-level and nested ``mcp.<key>``."""
    if key in data and isinstance(data[key], dict):
        return data[key]
    nested = data.get("mcp")
    if isinstance(nested, dict) and isinstance(nested.get(key), dict):
        return nested[key]
    if isinstance(nested, dict) and isinstance(nested.get("servers"), dict):
        return nested["servers"]
    return {}


def _looks_like_mcp_config(host: MCPHost, path: Path) -> bool:
    """Lightweight signal-check before claiming a path is a real MCP config.

    JSON is the dominant format; TOML is supported for hosts like Codex that
    keep MCP config in ``config.toml`` (snake_case ``mcp_servers``).
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".toml":
            try:
                import tomllib  # type: ignore[import-not-found]
            except ImportError:  # pragma: no cover - 3.10 path
                import tomli as tomllib  # type: ignore[no-redef]
            with path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, Exception):
        return False
    if not isinstance(data, dict):
        return False
    # Common signals across hosts. Specific hosts can be more strict in their
    # parse() but we want auto-detection to be permissive.
    if "mcpServers" in data or "mcp_servers" in data:
        return True
    if "context_servers" in data:
        return True
    nested = data.get("mcp")
    if isinstance(nested, dict) and ("servers" in nested or "mcpServers" in nested):
        return True
    return False


_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    _load_builtin_hosts()


def _load_builtin_hosts() -> None:
    """Import built-in host modules. Each registers itself at import time."""
    # Import order is also auto-detection precedence order.
    from bulla.hosts import cursor as _cursor  # noqa: F401
    from bulla.hosts import claude_code as _claude_code  # noqa: F401
    from bulla.hosts import cline as _cline  # noqa: F401
    from bulla.hosts import claude_desktop as _claude_desktop  # noqa: F401
    from bulla.hosts import codex as _codex  # noqa: F401
    from bulla.hosts import zed as _zed  # noqa: F401
    from bulla.hosts import windsurf as _windsurf  # noqa: F401
