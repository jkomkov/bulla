#!/usr/bin/env python3
"""Cross-server coherence audit on genuine MCP server manifests.

Loads real ``tools/list`` responses captured from live MCP servers,
composes them with server-name prefixes, and runs Bulla's full
diagnostic pipeline including fee decomposition by server partition.

Run:
    python examples/real_world_audit/run_audit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bulla.diagnostic import decompose_fee, prescriptive_disclosure
from bulla.guard import BullaGuard

MANIFESTS_DIR = Path(__file__).parent / "manifests"


def load_manifest(path: Path) -> tuple[str, list[dict]]:
    """Return (server_name, tools) from a provenance-tagged manifest."""
    data = json.loads(path.read_text())
    server_name = path.stem
    tools = data["tools"]
    prov = data.get("_bulla_provenance", {})
    print(f"  {server_name}: {len(tools)} tools  "
          f"(captured {prov.get('capture_date', '?')} "
          f"from {prov.get('server_package', '?')})")
    return server_name, tools


def main() -> None:
    manifest_files = sorted(MANIFESTS_DIR.glob("*.json"))
    if not manifest_files:
        print("No manifests found. Run capture first.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(manifest_files)} server manifest(s):\n")
    servers: dict[str, list[dict]] = {}
    for mf in manifest_files:
        name, tools = load_manifest(mf)
        servers[name] = tools

    all_tools: list[dict] = []
    for server_name, tools in servers.items():
        for tool in tools:
            tool["name"] = f"{server_name}__{tool.get('name', 'unknown')}"
        all_tools.extend(tools)

    print(f"\nTotal tools: {len(all_tools)} across {len(servers)} servers\n")

    guard = BullaGuard.from_tools_list(all_tools, name="real-world-audit")
    comp = guard.composition
    diag = guard.diagnose()

    print("=" * 60)
    print("COHERENCE DIAGNOSTIC")
    print("=" * 60)
    print(f"  Composition:    {comp.name}")
    print(f"  Tools:          {len(comp.tools)}")
    print(f"  Edges:          {len(comp.edges)}")
    print(f"  Coherence fee:  {diag.coherence_fee}")
    print(f"  Blind spots:    {len(diag.blind_spots)}")
    print(f"  Unbridged:      {diag.n_unbridged}")

    if diag.blind_spots:
        print(f"\n  Blind spot details (showing first 20 of {len(diag.blind_spots)}):")
        for bs in diag.blind_spots[:20]:
            hidden = []
            if bs.from_hidden:
                hidden.append(f"{bs.from_field} hidden in {bs.from_tool}")
            if bs.to_hidden:
                hidden.append(f"{bs.to_field} hidden in {bs.to_tool}")
            print(f"    - {bs.from_tool} <-> {bs.to_tool}: "
                  f"{bs.dimension} [{', '.join(hidden)}]")

    if diag.bridges:
        print(f"\n  Bridge recommendations: {len(diag.bridges)} "
              f"(showing first 10)")
        for br in diag.bridges[:10]:
            print(f"    - {br}")

    disclosure = prescriptive_disclosure(comp, diag.coherence_fee)
    if disclosure:
        print(f"\n  Minimum disclosure set ({len(disclosure)} fields):")
        for d in disclosure:
            print(f"    - {d}")

    if len(servers) > 1:
        tool_to_server = {
            t.name: t.name.split("__")[0] for t in comp.tools
        }
        partition = []
        for srv in servers:
            group = frozenset(
                t for t, s in tool_to_server.items() if s == srv
            )
            if group:
                partition.append(group)

        if len(partition) > 1:
            decomp = decompose_fee(comp, partition)
            srv_names = list(servers.keys())
            print("\n" + "=" * 60)
            print("CROSS-SERVER RISK DECOMPOSITION")
            print("=" * 60)
            print(f"  Total fee:      {decomp.total_fee}")
            for i, lf in enumerate(decomp.local_fees):
                label = srv_names[i] if i < len(srv_names) else f"group-{i}"
                print(f"  {label:20s} intra-fee: {lf}")
            print(f"  {'Boundary fee':20s}: {decomp.boundary_fee}")
            print(f"  Boundary edges:     {decomp.boundary_edges}")
            if decomp.boundary_fee > 0:
                print("\n  ** Cross-server boundary fee is non-zero. **")
                print("  This means the composition of these servers creates")
                print("  blind spots that no individual server can detect.")
            else:
                print("\n  Boundary fee is zero: these servers are semantically")
                print("  orthogonal -- they don't share convention-laden fields.")

    print()


if __name__ == "__main__":
    main()
