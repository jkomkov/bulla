#!/usr/bin/env python3
"""Canonical proof artifact: full Bulla v0.25-0.28 pipeline on real MCP servers.

Two servers (filesystem + GitHub), one cross-server seam (path_convention),
one convention mismatch (absolute_local vs relative_repo). Measures the
composition, extracts obligations, runs guided discovery, issues a receipt
with discovered vocabulary, and verifies receipt integrity.

Usage:
    python run_canonical_demo.py           # mock adapter (deterministic)
    python run_canonical_demo.py --live    # real LLM probing (requires API key)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from bulla import (
    BullaGuard,
    boundary_obligations_from_decomposition,
    coordination_step,
    decompose_fee,
    detect_contradictions,
    diagnose,
    verify_receipt_integrity,
    witness,
)
from bulla.discover.adapter import DiscoverAdapter


_VALUE_MAP: dict[tuple[str, str], str] = {
    ("filesystem", "path_convention_match"): "absolute_local",
    ("github", "path_convention_match"): "relative_repo",
}
"""Values sourced from manual inspection of the MCP server implementations.
The filesystem server operates on absolute paths within its allowed_directories
sandbox. The GitHub server operates on repo-relative paths."""


class RealWorldMockAdapter:
    """Deterministic adapter returning known convention values for real MCP servers."""

    def complete(self, prompt: str) -> str:
        n_obligations = len(re.findall(r"OBLIGATION \d+:", prompt))
        blocks: list[str] = []
        for idx in range(1, n_obligations + 1):
            pattern = rf"OBLIGATION {idx}:.*?Server group:\s*(\S+).*?Dimension:\s*(\S+)"
            m = re.search(pattern, prompt, re.DOTALL)
            if m:
                server = m.group(1)
                dimension = m.group(2)
                value = _VALUE_MAP.get((server, dimension))
                if value:
                    blocks.append(
                        f"---BEGIN_VERDICT_{idx}---\n"
                        f"verdict: CONFIRMED\n"
                        f"evidence: {server} server uses {value} paths\n"
                        f"convention_value: {value}\n"
                        f"---END_VERDICT_{idx}---"
                    )
                else:
                    blocks.append(
                        f"---BEGIN_VERDICT_{idx}---\n"
                        f"verdict: UNCERTAIN\n"
                        f"evidence: no known convention for {server}/{dimension}\n"
                        f"convention_value:\n"
                        f"---END_VERDICT_{idx}---"
                    )
            else:
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: UNCERTAIN\n"
                    f"evidence: could not parse obligation\n"
                    f"convention_value:\n"
                    f"---END_VERDICT_{idx}---"
                )
        return "\n\n".join(blocks)


def _load_manifests(manifests_dir: Path) -> tuple[list[dict], list[str]]:
    server_names: list[str] = []
    all_tools: list[dict] = []
    for manifest_file in sorted(manifests_dir.glob("*.json")):
        with open(manifest_file) as f:
            data = json.load(f)
        tools_data = data.get("tools", data) if isinstance(data, dict) else data
        if not isinstance(tools_data, list):
            continue
        server = manifest_file.stem
        server_names.append(server)
        for t in tools_data:
            t["name"] = f"{server}__{t.get('name', 'unknown')}"
        all_tools.extend(tools_data)
    return all_tools, server_names


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulla canonical proof artifact")
    parser.add_argument(
        "--live", action="store_true",
        help="Use real LLM for guided discovery (requires API key)",
    )
    args = parser.parse_args()

    demo_dir = Path(__file__).resolve().parent
    manifests_dir = demo_dir / "manifests"
    receipts_dir = demo_dir / "receipts"
    receipts_dir.mkdir(exist_ok=True)

    all_tools, server_names = _load_manifests(manifests_dir)
    server_tool_counts = {}
    for sname in server_names:
        server_tool_counts[sname] = sum(
            1 for t in all_tools if t["name"].startswith(f"{sname}__")
        )

    guard = BullaGuard.from_tools_list(all_tools, name="canonical-demo")
    comp = guard.composition
    diag = diagnose(comp)

    partition: list[frozenset[str]] = []
    tool_to_server = {t.name: t.name.split("__")[0] for t in comp.tools}
    for sname in server_names:
        tools_in = frozenset(
            tname for tname, srv in tool_to_server.items() if srv == sname
        )
        if tools_in:
            partition.append(tools_in)

    decomposition = decompose_fee(comp, partition)
    obligations = boundary_obligations_from_decomposition(
        comp, list(decomposition.partition), diag,
    )

    if args.live:
        from bulla.discover.adapter import get_adapter
        adapter: DiscoverAdapter = get_adapter()
    else:
        adapter = RealWorldMockAdapter()

    conv_result = coordination_step(
        comp, partition, all_tools, adapter,
        max_rounds=5,
        parent_obligations=obligations,
    )

    discovered_pack = conv_result.discovered_pack
    inline_dims = discovered_pack if discovered_pack.get("dimensions") else None

    contradictions = detect_contradictions(discovered_pack) if inline_dims else ()

    receipt = witness(
        diagnose(conv_result.final_comp),
        conv_result.final_comp,
        witness_basis=guard.witness_basis,
        inline_dimensions=inline_dims,
        boundary_obligations=obligations,
        contradictions=contradictions if contradictions else None,
    )
    receipt_dict = receipt.to_dict()

    receipt_v030_path = receipts_dir / "audit_receipt_v030.json"
    receipt_v030_path.write_text(json.dumps(receipt_dict, indent=2), encoding="utf-8")
    valid = verify_receipt_integrity(receipt_dict)

    bar = "\u2550" * 60
    print()
    print(f"  {bar}")
    from bulla import __version__
    print(f"    The Seam Problem \u2014 Bulla v{__version__}")
    print(f"  {bar}")
    print()

    server_str = ", ".join(
        f"{s} ({server_tool_counts[s]} tools)" for s in server_names
    )
    print(f"    Servers: {server_str}")
    print()
    print(f"    Coherence fee: {diag.coherence_fee}")
    print(f"    Cross-server boundary fee: {decomposition.boundary_fee}")

    seen_obl: set[tuple[str, str]] = set()
    for obl in obligations:
        servers_in_edge = " \u2194 ".join(
            sorted({p.split("__")[0] for p in obl.source_edge.split(" -> ")})
        )
        key = (obl.dimension, servers_in_edge)
        if key not in seen_obl:
            seen_obl.add(key)
            print(f"    Obligation: {obl.dimension} at {servers_in_edge}")
    print()

    n_confirmed = conv_result.total_confirmed
    n_rounds = len(conv_result.rounds)
    print(f"    Guided discovery ({n_rounds} round(s), {n_confirmed} confirmed):")

    probe_tool_values: dict[str, dict[str, str]] = {}
    for rnd in conv_result.rounds:
        for p in rnd.probes:
            if p.verdict.value == "confirmed" and p.convention_value:
                server = p.obligation.placeholder_tool
                dim = p.obligation.dimension
                probe_tool_values.setdefault(dim, {})[server] = p.convention_value

    for dim_name, tv in probe_tool_values.items():
        for server_prefix, value in tv.items():
            print(f"      {server_prefix}: {value}")
    print()

    if contradictions:
        print(f"    Contradictions: {len(contradictions)}")
        for c in contradictions:
            vals_str = " vs ".join(c.values)
            print(f"      {c.dimension}: {vals_str} ({c.severity.value.upper()})")
        print()

    receipt_hash = receipt_dict.get("receipt_hash", "")[:8]
    valid_str = "VALID" if valid else "INVALID"
    print(f"    Receipt: {receipt_hash}... ({valid_str})")

    if inline_dims:
        dims = inline_dims.get("dimensions", {})
        for dname, ddef in dims.items():
            vals = ddef.get("known_values", [])
            print(f"    Discovered conventions: {dname} {vals}")

    print()
    print(f"  {bar}")
    print()


if __name__ == "__main__":
    main()
