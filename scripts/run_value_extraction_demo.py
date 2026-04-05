#!/usr/bin/env python3
"""Convention value extraction demo: probe results -> micro-pack -> receipt.

Demonstrates Sprint 28's value extraction pipeline:

- Agent A has fee=2 from two independent blind spots (pagination, path_convention).
- coordination_step() confirms convention values:
    Round 1: confirms pagination = zero_based -> fee 2 -> 1.
    Round 2: confirms path_convention = absolute -> fee 1 -> 0.
- extract_pack_from_probes() generates a micro-pack dict.
- The pack is embedded as inline_dimensions on A's receipt.
- Agent B receives A's receipt via chain, inheriting the enriched vocabulary.
- Agent B's diagnosis operates on a richer pack stack.

Usage:
  python scripts/run_value_extraction_demo.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

from bulla import __version__
from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
)
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    SemanticDimension,
    ToolSpec,
)
from bulla.repair import ConvergenceResult, coordination_step, extract_pack_from_probes
from bulla.witness import verify_receipt_integrity, witness


# ── Topology: fee=2 with two named dimensions ────────────────────────

API_TOOLS = (
    ToolSpec("api__list_items", ("cursor", "offset", "limit"), ("cursor", "limit")),
    ToolSpec("api__get_item", ("item_id", "format", "abs_flag"), ("item_id",)),
)

STORAGE_TOOLS = (
    ToolSpec("storage__read_file", ("path", "encoding", "abs_path"), ("encoding",)),
    ToolSpec("storage__write_file", ("dest", "mode", "rel_path"), ("dest", "mode")),
)

EDGES_A = (
    Edge("storage__read_file", "api__list_items", (
        SemanticDimension("pagination", "abs_path", "offset"),
    )),
    Edge("api__get_item", "storage__write_file", (
        SemanticDimension("path_convention", "abs_flag", "rel_path"),
    )),
)


class ValueAwareMockAdapter:
    """Mock adapter that confirms obligations with specific convention values.

    Maps dimension names to (convention_value, round_to_confirm) pairs.
    Confirms one new dimension per round to demonstrate multi-round
    convergence with distinct convention values.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {
            "pagination": "zero_based",
            "path_convention": "absolute",
        }
        self._confirmed_dims: set[str] = set()

    def complete(self, prompt: str) -> str:
        n_obls = len(re.findall(r"OBLIGATION \d+:", prompt))
        if n_obls == 0:
            return ""

        dims: list[str] = []
        for idx in range(1, n_obls + 1):
            pattern = rf"OBLIGATION {idx}:.*?Dimension:\s*(\S+)"
            match = re.search(pattern, prompt, re.DOTALL)
            dims.append(match.group(1) if match else "")

        confirmed_this_round = False
        blocks = []
        for idx in range(1, n_obls + 1):
            dim = dims[idx - 1]
            should_confirm = (
                not confirmed_this_round
                and dim not in self._confirmed_dims
                and dim in self._values
            )

            if should_confirm:
                self._confirmed_dims.add(dim)
                confirmed_this_round = True
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: CONFIRMED\n"
                    f"evidence: field is present in tool output schema\n"
                    f"convention_value: {self._values[dim]}\n"
                    f"---END_VERDICT_{idx}---"
                )
            else:
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: UNCERTAIN\n"
                    f"evidence: cannot determine from schema alone\n"
                    f"convention_value:\n"
                    f"---END_VERDICT_{idx}---"
                )
        return "\n\n".join(blocks)


def _mock_tools_as_dicts(tools: tuple[ToolSpec, ...]) -> list[dict]:
    result = []
    for t in tools:
        props = {}
        for f in t.internal_state:
            props[f] = {"type": "string"}
            if f in t.observable_schema:
                props[f]["description"] = f"Observable field: {f}"
        result.append({
            "name": t.name,
            "description": f"Tool {t.name}",
            "inputSchema": {"type": "object", "properties": props},
        })
    return result


def run_demo() -> None:
    print("=" * 70)
    print(f"  Bulla v{__version__} -- Value Extraction Demo (Sprint 28)")
    print("=" * 70)
    print()

    # ── Agent A: convergence + value extraction ──────────────────────
    print("-" * 70)
    print("  AGENT A: api + storage (fee=2, value extraction)")
    print("-" * 70)

    comp_a = Composition("agent-a", API_TOOLS + STORAGE_TOOLS, EDGES_A)
    partition_a = [
        frozenset(t.name for t in API_TOOLS),
        frozenset(t.name for t in STORAGE_TOOLS),
    ]
    original_diag = diagnose(comp_a)
    decomp_a = decompose_fee(comp_a, partition_a)
    print(f"  Initial fee: {original_diag.coherence_fee}  (boundary_fee={decomp_a.boundary_fee})")
    assert original_diag.coherence_fee == 2, (
        f"Expected fee=2, got {original_diag.coherence_fee}"
    )

    all_a_tools = _mock_tools_as_dicts(API_TOOLS + STORAGE_TOOLS)
    adapter = ValueAwareMockAdapter()

    conv_result = coordination_step(
        comp_a, partition_a, all_a_tools, adapter, max_rounds=5,
    )
    assert isinstance(conv_result, ConvergenceResult)

    print(f"\n  Convergence: fee {original_diag.coherence_fee} -> {conv_result.final_fee} "
          f"in {len(conv_result.rounds)} round(s) [{conv_result.termination_reason}]")

    for i, rnd in enumerate(conv_result.rounds, 1):
        print(f"    Round {i}: fee {rnd.original_fee} -> {rnd.repaired_fee} "
              f"(confirmed={rnd.confirmed_count})")
        for p in rnd.probes:
            cv = f" = {p.convention_value}" if p.convention_value else ""
            print(f"      {p.obligation.placeholder_tool}:{p.obligation.dimension}"
                  f"/{p.obligation.field}: {p.verdict.value}{cv}")

    # Extract convention values into micro-pack
    pack = conv_result.discovered_pack
    pack_dims = pack.get("dimensions", {})
    print(f"\n  Discovered conventions:")
    for dname, ddef in pack_dims.items():
        vals = ddef.get("known_values", [])
        tools = ddef.get("provenance", {}).get("source_tools", [])
        tool_str = f" (from {', '.join(tools)})" if tools else ""
        print(f"    {dname}: {', '.join(vals)}{tool_str}")

    assert len(pack_dims) == 2, f"Expected 2 dimensions, got {len(pack_dims)}"
    assert "pagination" in pack_dims
    assert "path_convention" in pack_dims
    assert "zero_based" in pack_dims["pagination"]["known_values"]
    assert "absolute" in pack_dims["path_convention"]["known_values"]

    # Witness Agent A with inline_dimensions
    final_diag_a = diagnose(conv_result.final_comp)
    receipt_a = witness(
        final_diag_a,
        conv_result.final_comp,
        inline_dimensions=pack,
    )
    print(f"\n  Receipt A: {receipt_a.receipt_hash[:16]}...")
    print(f"  Receipt A inline_dimensions: {len(pack_dims)} dimension(s)")
    assert receipt_a.inline_dimensions is not None
    assert verify_receipt_integrity(receipt_a.to_dict()), "Receipt A integrity failed"

    # ── Agent B: receives A's receipt via chain ──────────────────────
    print()
    print("-" * 70)
    print("  AGENT B: receives Agent A's receipt (chain inheritance)")
    print("-" * 70)

    receipt_a_dict = receipt_a.to_dict()

    inherited_dims = receipt_a_dict.get("inline_dimensions", {}).get("dimensions", {})
    print(f"  Inherited from A: {len(inherited_dims)} dimension(s)")
    for dname, ddef in inherited_dims.items():
        vals = ddef.get("known_values", [])
        print(f"    {dname}: {', '.join(vals)}")

    # Agent B has its own composition (trivially bridged)
    b_tools = (
        ToolSpec("cache__get", ("key", "ttl"), ("key", "ttl")),
        ToolSpec("cache__set", ("key", "value", "ttl"), ("key", "value", "ttl")),
    )
    b_edges = (
        Edge("cache__get", "cache__set", (
            SemanticDimension("cache_key", "key", "key"),
        )),
    )
    comp_b = Composition("agent-b", b_tools, b_edges)
    diag_b = diagnose(comp_b)
    print(f"\n  Agent B own fee: {diag_b.coherence_fee}")

    receipt_b = witness(
        diag_b,
        comp_b,
        parent_receipt_hash=receipt_a.receipt_hash,
        inline_dimensions=receipt_a_dict.get("inline_dimensions"),
    )
    print(f"  Receipt B: {receipt_b.receipt_hash[:16]}...")
    print(f"  Receipt B inline_dimensions: "
          f"{len(receipt_b.inline_dimensions.get('dimensions', {}))} dimension(s)")
    assert receipt_b.inline_dimensions is not None
    assert len(receipt_b.inline_dimensions.get("dimensions", {})) == 2
    assert verify_receipt_integrity(receipt_b.to_dict()), "Receipt B integrity failed"

    # Verify chain linkage
    assert receipt_b.parent_receipt_hashes == (receipt_a.receipt_hash,)

    # ── Summary ─────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  VALUE EXTRACTION DEMO SUMMARY")
    print("=" * 70)
    print()
    print(f"  Agent A: fee {original_diag.coherence_fee} -> {conv_result.final_fee} "
          f"in {len(conv_result.rounds)} round(s)")
    print(f"  Discovered: {len(pack_dims)} convention(s)")
    for dname, ddef in pack_dims.items():
        vals = ddef.get("known_values", [])
        print(f"    {dname} = {', '.join(vals)}")
    print(f"  Agent B: inherited {len(inherited_dims)} dimension(s) from chain")
    print()

    a_ok = verify_receipt_integrity(receipt_a.to_dict())
    b_ok = verify_receipt_integrity(receipt_b.to_dict())
    chain_ok = receipt_b.parent_receipt_hashes == (receipt_a.receipt_hash,)
    print(f"  Receipt A integrity: {'VALID' if a_ok else 'BROKEN'}")
    print(f"  Receipt B integrity: {'VALID' if b_ok else 'BROKEN'}")
    print(f"  Chain A->B: {'VALID' if chain_ok else 'BROKEN'}")
    print()

    if not (a_ok and b_ok and chain_ok):
        print("  INTEGRITY FAILURE")
        sys.exit(1)

    print("  ALL CHECKS PASSED")
    print()


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
