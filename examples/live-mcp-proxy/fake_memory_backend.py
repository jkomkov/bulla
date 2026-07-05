"""Tiny MCP server impersonating a memory tool.

Companion to fake_fetch_backend.py — consumes the encoding dimension
visibly, so the composition (fetch → memory) has a one-sided seam:
fetch's encoding is hidden, memory's is visible. Bulla classifies
this as schema-level (manifest edit required) — exactly the kind of
obstruction the live proxy is meant to surface.
"""

from __future__ import annotations

import json
import sys


TOOLS = [
    {
        "name": "store",
        "description": "Store fetched content in memory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "required": ["content"],
        },
        "_internal_state": ["content", "encoding"],
        "_observable_schema": ["content", "encoding"],
        "_consumes_dimensions": [
            {"name": "encoding", "to_field": "encoding"},
        ],
    },
]


def respond(msg_id: int, result: dict) -> None:
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0", "id": msg_id, "result": result,
    }) + "\n")
    sys.stdout.flush()


def main() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            respond(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "fake-memory", "version": "0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            respond(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            respond(msg_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "stored": True,
                    "echoed_args": params.get("arguments", {}),
                })}],
            })
        elif msg_id is not None:
            respond(msg_id, {})


if __name__ == "__main__":
    main()
