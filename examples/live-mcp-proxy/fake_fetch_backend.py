"""Tiny MCP server impersonating a fetch tool.

Mirrors the structure of @modelcontextprotocol/server-fetch but
embeds a hidden `encoding` field in its internal state — so when it
composes with a memory-style consumer that expects `encoding` to be
visible, Bulla flags a schema-level obstruction.

Used by run_demo.sh as a self-contained backend (no npx / network).
"""

from __future__ import annotations

import json
import sys


TOOLS = [
    {
        "name": "get",
        "description": "Fetch the contents of a URL",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        },
        "_internal_state": ["url", "body", "encoding"],
        "_observable_schema": ["url", "body"],
        "_emits_dimensions": [
            {"name": "encoding", "from_field": "encoding"},
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
                "serverInfo": {"name": "fake-fetch", "version": "0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            respond(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            respond(msg_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "body": "<simulated bytes>",
                    "echoed_args": params.get("arguments", {}),
                })}],
            })
        elif msg_id is not None:
            respond(msg_id, {})


if __name__ == "__main__":
    main()
