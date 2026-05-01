"""Run proxy replay on known cyclic pairs from the schema structure profile.

This validates the Act 2 -> Act 5 bridge on controlled cases:
we already know the tool graph has a cycle, and we ask whether the
proxy's local traced subcomposition recovers nontrivial fee/Betti data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bulla.guard import BullaGuard
from bulla.proxy import BullaProxySession

CYCLIC_LIMIT = 5
CONTROL_LIMIT = 2


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _load_manifest_dir(manifests_dir: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = _load_json(path)
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            result[path.stem] = tools
    return result


def _build_guard_for_pair(
    server_tools: dict[str, list[dict[str, Any]]],
    left: str,
    right: str,
) -> BullaGuard:
    prefixed: list[dict[str, Any]] = []
    for server_name in (left, right):
        for tool in server_tools[server_name]:
            clone = dict(tool)
            clone["name"] = f"{server_name}__{tool['name']}"
            prefixed.append(clone)
    return BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")


def _find_cycle(comp) -> list[str]:
    adjacency: dict[str, list[str]] = {}
    for edge in comp.edges:
        adjacency.setdefault(edge.from_tool, []).append(edge.to_tool)
        adjacency.setdefault(edge.to_tool, []).append(edge.from_tool)

    visited: set[str] = set()
    stack: list[str] = []
    in_stack: set[str] = set()
    parent: dict[str, str | None] = {}

    def dfs(node: str, prev: str | None) -> list[str] | None:
        visited.add(node)
        stack.append(node)
        in_stack.add(node)
        parent[node] = prev
        for neighbor in adjacency.get(node, []):
            if neighbor == prev:
                continue
            if neighbor in in_stack:
                idx = stack.index(neighbor)
                return stack[idx:]
            if neighbor not in visited:
                cycle = dfs(neighbor, node)
                if cycle is not None:
                    return cycle
        stack.pop()
        in_stack.remove(node)
        return None

    for node in adjacency:
        if node not in visited:
            cycle = dfs(node, None)
            if cycle is not None:
                return cycle
    return []


def _edge_lookup(comp) -> dict[frozenset[str], Any]:
    lookup: dict[frozenset[str], Any] = {}
    for edge in comp.edges:
        lookup[frozenset((edge.from_tool, edge.to_tool))] = edge
    return lookup


def _trace_from_cycle(comp, cycle: list[str]) -> list[dict[str, Any]]:
    lookup = _edge_lookup(comp)
    trace: list[dict[str, Any]] = []
    for idx, tool_name in enumerate(cycle):
        server, tool = tool_name.split("__", 1)
        call: dict[str, Any] = {
            "server": server,
            "tool": tool,
            "arguments": {},
            "result": {},
        }
        if idx > 0:
            prev = cycle[idx - 1]
            edge = lookup[frozenset((prev, tool_name))]
            dim = edge.dimensions[0]
            if edge.from_tool == prev and edge.to_tool == tool_name:
                source_field = dim.from_field
                target_field = dim.to_field
            else:
                source_field = dim.to_field
                target_field = dim.from_field
            call["arguments"] = {target_field: f"synthetic-{idx}"}
            call["argument_sources"] = {
                target_field: {
                    "call_id": idx,
                    "field": source_field,
                }
            }
        trace.append(call)
    return trace


def _trace_for_edge_free_pair(
    server_tools: dict[str, list[dict[str, Any]]],
    left: str,
    right: str,
) -> list[dict[str, Any]]:
    left_tool = server_tools[left][0]["name"]
    right_tool = server_tools[right][0]["name"]
    return [
        {"server": left, "tool": left_tool, "arguments": {}, "result": {}},
        {"server": right, "tool": right_tool, "arguments": {}, "result": {}},
    ]


def run_harness(corpus_dir: Path) -> dict[str, Any]:
    report_dir = corpus_dir / "report"
    manifests_dir = corpus_dir / "manifests"
    cyclic_pairs = _load_json(report_dir / "cyclic_pairs.json")
    pair_rows = [
        json.loads(line)
        for line in (report_dir / "schema_structure_pairs.jsonl").read_text().splitlines()
        if line.strip()
    ]
    server_tools = _load_manifest_dir(manifests_dir)

    cyclic_results: list[dict[str, Any]] = []
    for row in cyclic_pairs[:CYCLIC_LIMIT]:
        guard = _build_guard_for_pair(
            server_tools,
            row["left_server"],
            row["right_server"],
        )
        cycle = _find_cycle(guard.composition)
        if not cycle:
            continue
        trace = _trace_from_cycle(guard.composition, cycle)
        session = BullaProxySession(
            {
                row["left_server"]: server_tools[row["left_server"]],
                row["right_server"]: server_tools[row["right_server"]],
            }
        )
        records = session.replay_trace(trace)
        final_local = records[-1].local_diagnostic
        cyclic_results.append(
            {
                "pair_name": row["pair_name"],
                "cycle_tools": cycle,
                "trace_length": len(trace),
                "local_diagnostic": final_local.to_dict(),
                "validated": (
                    final_local.betti_1 > 0
                    and final_local.coherence_fee > 0
                ),
            }
        )

    control_rows = [
        row
        for row in pair_rows
        if row["n_edges"] == 0
    ][:CONTROL_LIMIT]
    control_results: list[dict[str, Any]] = []
    for row in control_rows:
        trace = _trace_for_edge_free_pair(
            server_tools,
            row["left_server"],
            row["right_server"],
        )
        session = BullaProxySession(
            {
                row["left_server"]: server_tools[row["left_server"]],
                row["right_server"]: server_tools[row["right_server"]],
            }
        )
        records = session.replay_trace(trace)
        final_local = records[-1].local_diagnostic
        control_results.append(
            {
                "pair_name": row["pair_name"],
                "trace_length": len(trace),
                "local_diagnostic": final_local.to_dict(),
                "validated": final_local.coherence_fee == 0,
            }
        )

    result = {
        "cyclic_cases": cyclic_results,
        "control_cases": control_results,
        "validated_cyclic": sum(case["validated"] for case in cyclic_results),
        "validated_controls": sum(case["validated"] for case in control_results),
    }
    out_path = report_dir / "proxy_cyclic_harness.json"
    out_path.write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    corpus_dir = Path("calibration/data/registry")
    result = run_harness(corpus_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
