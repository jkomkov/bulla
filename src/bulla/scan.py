"""Live MCP server scanner via JSON-RPC 2.0 over stdio.

Spawns a server subprocess, performs the MCP initialize handshake,
calls ``tools/list``, and returns the raw tools array.  No MCP SDK
dependency -- pure subprocess + json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from bulla import __version__


class ScanError(Exception):
    """Raised when the scanner cannot communicate with the MCP server."""


def _send_request(
    proc: subprocess.Popen[bytes],
    method: str,
    params: dict[str, Any] | None = None,
    msg_id: int = 1,
) -> dict[str, Any]:
    """Send a JSON-RPC 2.0 request (with id) and read one response."""
    request: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "id": msg_id,
    }
    if params is not None:
        request["params"] = params

    payload = json.dumps(request)
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(payload.encode() + b"\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    if not line:
        raise ScanError(f"Server closed stdout before responding to '{method}'")

    try:
        response = json.loads(line)
    except json.JSONDecodeError as e:
        snippet = line[:200].decode(errors="replace")
        raise ScanError(
            f"Invalid JSON from server for '{method}': {snippet}"
        ) from e

    if "error" in response:
        err = response["error"]
        raise ScanError(
            f"Server error for '{method}': "
            f"[{err.get('code', '?')}] {err.get('message', str(err))}"
        )

    return response


def _send_notification(
    proc: subprocess.Popen[bytes],
    method: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Send a JSON-RPC 2.0 notification (no id, no response expected)."""
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        request["params"] = params
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(request).encode() + b"\n")
    proc.stdin.flush()


def scan_mcp_server(
    command: str,
    *,
    timeout: float = 10.0,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Spawn *command* as an MCP server, initialize, and return its tools.

    Returns the raw ``tools`` array from the ``tools/list`` response,
    suitable for passing to ``_composition_from_mcp_tools``.

    If *env* is provided, those variables are merged on top of the
    current process environment before spawning the server.
    """
    import os
    import shlex

    args = shlex.split(command)
    spawn_env = None
    if env:
        spawn_env = {**os.environ, **env}
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=spawn_env,
        )
    except FileNotFoundError as e:
        raise ScanError(f"Cannot spawn server: {e}") from e

    try:
        _send_request(proc, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bulla", "version": __version__},
        }, msg_id=1)

        _send_notification(proc, "notifications/initialized")

        resp = _send_request(proc, "tools/list", {}, msg_id=2)
        result = resp.get("result", {})
        tools: list[dict[str, Any]] = result.get("tools", [])
        if not tools and isinstance(result, list):
            tools = result
        return tools

    except ScanError:
        raise
    except Exception as e:
        stderr_out = ""
        if proc.stderr:
            try:
                stderr_out = proc.stderr.read(2048).decode(errors="replace")
            except Exception:
                pass
        raise ScanError(f"Scan failed: {e}\nServer stderr: {stderr_out}") from e
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def scan_mcp_servers(
    commands: list[str], *, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """Scan multiple MCP servers and return the merged tools list."""
    all_tools: list[dict[str, Any]] = []
    for cmd in commands:
        tools = scan_mcp_server(cmd, timeout=timeout)
        all_tools.extend(tools)
    return all_tools


@dataclass
class ServerScanResult:
    """Result of scanning a single MCP server."""

    name: str
    tools: list[dict[str, Any]]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def scan_mcp_servers_parallel(
    servers: dict[str, dict[str, Any]],
    *,
    timeout: float = 15.0,
    max_workers: int = 8,
) -> list[ServerScanResult]:
    """Scan multiple MCP servers in parallel with graceful failure.

    *servers* maps server name to ``{"command": "...", "env": {...}}``.
    Each server is scanned in its own thread.  Returns a list of
    :class:`ServerScanResult` (one per server, preserving input order).
    Failed servers have ``error`` set instead of raising.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _scan_one(name: str, cfg: dict[str, Any]) -> ServerScanResult:
        cmd = cfg.get("command", "")
        env = cfg.get("env")
        if not cmd:
            return ServerScanResult(name=name, tools=[], error="No command specified")
        try:
            tools = scan_mcp_server(cmd, timeout=timeout, env=env)
            return ServerScanResult(name=name, tools=tools)
        except ScanError as e:
            return ServerScanResult(name=name, tools=[], error=str(e))

    order = list(servers.keys())
    results_map: dict[str, ServerScanResult] = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(servers) or 1)) as pool:
        futures = {
            pool.submit(_scan_one, name, cfg): name
            for name, cfg in servers.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results_map[result.name] = result

    return [results_map[name] for name in order]
