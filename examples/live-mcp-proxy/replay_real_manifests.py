"""Replay captured real-MCP-server manifests through the live proxy.

The fake-backend smoke demo verifies the proxy *wires up* correctly.
This script verifies the proxy *detects real obstructions* end-to-end
without manual composition construction — using the actual
``tools/list`` responses captured from
``@modelcontextprotocol/server-filesystem`` and ``server-github``.

The auto-discovery spike at ``bulla/spikes/auto_discovery/`` confirmed
``BullaGuard.from_tools_list`` recovers 88% of the canonical-demo's
documented fee on these inputs. This script proves the proxy's meta-
tools surface that result.

Run from the repo root::

    python bulla/examples/live-mcp-proxy/replay_real_manifests.py

Output:
  - A list of meta-tool dispatches (fee, should_proceed verdict, bridge advices)
  - The full proxy session telemetry written to events.jsonl
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bulla.live import LiveSession
from bulla.live_proxy import (
    BullaLiveProxy,
    TelemetrySink,
    _meta_tool_definitions,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MANIFEST_DIR = (
    REPO_ROOT / "bulla" / "examples" / "real_world_audit" / "manifests"
)


def _load_tools(server: str, manifest_name: str) -> list[dict]:
    raw = json.loads((MANIFEST_DIR / manifest_name).read_text())
    tools = raw["tools"] if isinstance(raw, dict) else raw
    return tools


async def main() -> None:
    proxy = BullaLiveProxy(backends=[], telemetry=TelemetrySink(
        path=Path(__file__).parent / "events.jsonl",
    ))
    proxy.telemetry.open()
    # Build the live session the same way `start_backends` would
    # — but without spawning subprocesses, since we already have
    # the captured tools/list responses on disk.
    proxy.session = LiveSession(name="real-manifest-demo")
    for server, manifest in [
        ("filesystem", "filesystem.json"),
        ("github", "github.json"),
    ]:
        tools = _load_tools(server, manifest)
        proxy.session.add_server(server, tools)
    proxy._namespaced_tools = _meta_tool_definitions()

    async def dispatch(name: str, args: dict) -> dict:
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })
        return json.loads(resp["result"]["content"][0]["text"])

    print("─── Bulla proxy: real captured MCP manifests ───")
    print(f"  filesystem: {len(proxy.session.servers)} servers registered")
    print(f"  starting fee: {proxy.session.fee}")
    print(f"  starting blind_spots: {len(proxy.session.hidden_basis)}")

    fee = await dispatch("bulla__fee", {})
    print(f"\n  bulla__fee() → {fee}")

    # Pick a representative cross-server-style call: write_file (filesystem)
    # — pretend the agent wants to write what it just read from a GitHub
    # PR comment. The composition has hidden conventions on the seam.
    verdict = await dispatch("bulla__should_proceed", {
        "server": "filesystem",
        "tool": "write_file",
        "arguments": {"path": "/tmp/out.txt", "content": "hello"},
    })
    print(f"\n  bulla__should_proceed(filesystem.write_file) → {verdict}")

    bridge = await dispatch("bulla__bridge", {
        "server": "filesystem",
        "tool": "write_file",
        "arguments": {"path": "/tmp/out.txt", "content": "hello"},
    })
    print(f"\n  bulla__bridge(filesystem.write_file) → "
          f"{bridge['n_value_level']} value-level + "
          f"{bridge['n_schema_level']} schema-level advices "
          f"(showing first 2):")
    for a in bridge["advices"][:2]:
        print(f"    - kind={a['kind']} edge={a['edge']} "
              f"dimension={a['dimension']}")

    why = await dispatch("bulla__why", {"about": "should_proceed"})
    print(f"\n  bulla__why(should_proceed) → "
          f"{len(why['theorems'])} theorems, kernel={why['kernel_version']}")
    for t in why["theorems"]:
        print(f"    - {t['theorem']} ({t['aristotle_run']})")

    proxy.telemetry.close()


if __name__ == "__main__":
    asyncio.run(main())
