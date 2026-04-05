#!/usr/bin/env python3
"""Multi-round convergence demo: coordination_step() driving repair to fixpoint.

Demonstrates Sprint 27's iterative convergence loop:

- Agent B has fee=2 from two independent blind spots across two edges.
- coordination_step() with a dimension-aware StagedMockAdapter:
    Round 1 confirms 1 obligation on edge 1 -> fee drops from 2 to 1.
    Round 2 confirms 1 obligation on edge 2 -> fee drops from 1 to 0.
  Terminates with "fee_zero".
- Agent C has fee=0, demonstrating the trivial fixpoint case.

Usage:
  python scripts/run_convergence_demo.py
"""
from __future__ import annotations

import re
import sys

from bulla import __version__
from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
)
from bulla.discover.adapter import MockAdapter
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    SemanticDimension,
    ToolSpec,
)
from bulla.repair import ConvergenceResult, coordination_step
from bulla.witness import verify_receipt_integrity, witness


# ── Topology engineered for fee=2 on Agent B ─────────────────────────
#
# Two edges with independent hidden dimensions:
# alpha__read.encoding  ->  beta__fetch.timeout    (transport)
# alpha__write.mode     ->  beta__post.payload     (protocol)
#
# Each edge has hidden fields on BOTH sides, creating 2 independent
# coboundary rows with rank_obs=0, rank_full=2, hence fee=2.

ALPHA_TOOLS = (
    ToolSpec("alpha__read", ("path", "encoding"), ("path",)),
    ToolSpec("alpha__write", ("path", "mode"), ("path",)),
)

BETA_TOOLS = (
    ToolSpec("beta__fetch", ("url", "timeout"), ("url",)),
    ToolSpec("beta__post", ("url", "payload"), ("url",)),
)

EDGES_B = (
    Edge("alpha__read", "beta__fetch", (
        SemanticDimension("transport", "encoding", "timeout"),
    )),
    Edge("alpha__write", "beta__post", (
        SemanticDimension("protocol", "mode", "payload"),
    )),
)

GAMMA_TOOLS = (
    ToolSpec("gamma__render", ("template", "style"), ("template", "style")),
    ToolSpec("gamma__export", ("format", "quality"), ("format", "quality")),
)

EDGES_C = (
    Edge("gamma__render", "gamma__export", (
        SemanticDimension("output_config", "style", "quality"),
    )),
)


def _mock_tools_as_dicts(tools: tuple[ToolSpec, ...]) -> list[dict]:
    """Convert ToolSpecs to MCP-style tool dicts."""
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


class StagedMockAdapter:
    """MockAdapter that confirms one new dimension per round.

    Tracks which dimensions have already been confirmed. Each round
    confirms the first obligation whose dimension is novel. This
    ensures independent edges are resolved in separate rounds,
    demonstrating true multi-round convergence.
    """

    def __init__(self) -> None:
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
            )

            if should_confirm:
                self._confirmed_dims.add(dim)
                confirmed_this_round = True
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: CONFIRMED\n"
                    f"evidence: field is observable in tool output\n"
                    f"convention_value: standard\n"
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


def _print_obligations(label: str, obls: tuple[BoundaryObligation, ...]) -> None:
    if not obls:
        print(f"  {label}: (none)")
        return
    print(f"  {label} ({len(obls)}):")
    for obl in obls:
        edge_str = f" ({obl.source_edge})" if obl.source_edge else ""
        print(f"    - {obl.placeholder_tool}:{obl.dimension}/{obl.field}{edge_str}")


def run_demo() -> None:
    print("=" * 70)
    print(f"  Bulla v{__version__} — Convergence Demo (Sprint 27)")
    print("=" * 70)
    print()

    # ── Agent B: alpha + beta (fee=2) with convergence repair ─────────
    print("─" * 70)
    print("  AGENT B: alpha + beta (fee=2, multi-round convergence)")
    print("─" * 70)

    comp_b = Composition("agent-b", ALPHA_TOOLS + BETA_TOOLS, EDGES_B)
    partition_b = [
        frozenset(t.name for t in ALPHA_TOOLS),
        frozenset(t.name for t in BETA_TOOLS),
    ]
    original_diag_b = diagnose(comp_b)
    decomp_b = decompose_fee(comp_b, partition_b)
    print(f"  Fee: {original_diag_b.coherence_fee}  (boundary_fee={decomp_b.boundary_fee})")
    assert original_diag_b.coherence_fee == 2, (
        f"Expected fee=2, got {original_diag_b.coherence_fee}"
    )

    own_obls_b = boundary_obligations_from_decomposition(comp_b, partition_b, original_diag_b)
    _print_obligations("Obligations", own_obls_b)

    all_b_tools = _mock_tools_as_dicts(ALPHA_TOOLS + BETA_TOOLS)
    staged_adapter = StagedMockAdapter()

    conv_result = coordination_step(
        comp_b,
        partition_b,
        all_b_tools,
        staged_adapter,
        max_rounds=5,
    )

    assert isinstance(conv_result, ConvergenceResult)
    print(f"\n  Convergence result:")
    print(f"    Rounds: {len(conv_result.rounds)}")
    print(f"    Final fee: {conv_result.final_fee}")
    print(f"    Converged: {conv_result.converged}")
    print(f"    Termination: {conv_result.termination_reason}")
    print(f"    Total confirmed: {conv_result.total_confirmed}")
    print(f"    Total denied: {conv_result.total_denied}")
    print(f"    Total uncertain: {conv_result.total_uncertain}")

    for i, rnd in enumerate(conv_result.rounds, 1):
        print(f"\n    Round {i}: fee {rnd.original_fee} -> {rnd.repaired_fee} "
              f"(delta={rnd.fee_delta}, confirmed={rnd.confirmed_count})")
        for p in rnd.probes:
            print(f"      {p.obligation.placeholder_tool}:{p.obligation.dimension}/{p.obligation.field}: "
                  f"{p.verdict.value}")

    assert len(conv_result.rounds) >= 2, (
        f"Expected >= 2 rounds for multi-round convergence, got {len(conv_result.rounds)}"
    )
    assert conv_result.converged, "Expected convergence"
    assert conv_result.final_fee < original_diag_b.coherence_fee

    fee_trace = [original_diag_b.coherence_fee]
    for rnd in conv_result.rounds:
        fee_trace.append(rnd.repaired_fee)
    print(f"\n  Fee trace: {' -> '.join(str(f) for f in fee_trace)} (PASSED)")

    receipt_b = witness(diagnose(conv_result.final_comp), conv_result.final_comp)
    print(f"  Receipt: {receipt_b.receipt_hash[:16]}...")
    print()

    # ── Agent C: fee=0, trivial fixpoint ──────────────────────────────
    print("─" * 70)
    print("  AGENT C: gamma (fee=0, trivial fixpoint)")
    print("─" * 70)

    comp_c = Composition("agent-c", GAMMA_TOOLS, EDGES_C)
    partition_c = [frozenset(t.name for t in GAMMA_TOOLS)]
    diag_c = diagnose(comp_c)

    all_c_tools = _mock_tools_as_dicts(GAMMA_TOOLS)
    noop_adapter = MockAdapter("")

    conv_result_c = coordination_step(
        comp_c,
        partition_c,
        all_c_tools,
        noop_adapter,
    )

    print(f"  Fee: {diag_c.coherence_fee}")
    print(f"  Convergence rounds: {len(conv_result_c.rounds)}")
    print(f"  Termination: {conv_result_c.termination_reason}")

    receipt_c = witness(diag_c, comp_c, parent_receipt_hash=receipt_b.receipt_hash)
    print(f"  Receipt: {receipt_c.receipt_hash[:16]}...")
    print()

    # ── Summary ───────────────────────────────────────────────────────
    print("=" * 70)
    print("  CONVERGENCE DEMO SUMMARY")
    print("=" * 70)
    print()

    print(f"  Agent B: fee {original_diag_b.coherence_fee} -> {conv_result.final_fee} "
          f"in {len(conv_result.rounds)} round(s) [{conv_result.termination_reason}]")
    print(f"  Agent C: fee={diag_c.coherence_fee}, "
          f"rounds={len(conv_result_c.rounds)} [{conv_result_c.termination_reason}]")
    print()

    b_ok = verify_receipt_integrity(receipt_b.to_dict())
    c_ok = verify_receipt_integrity(receipt_c.to_dict())
    print(f"  Receipt B integrity: {'VALID' if b_ok else 'BROKEN'}")
    print(f"  Receipt C integrity: {'VALID' if c_ok else 'BROKEN'}")

    chain_bc = receipt_c.parent_receipt_hashes == (receipt_b.receipt_hash,)
    print(f"  Chain B->C: {'VALID' if chain_bc else 'BROKEN'}")
    print()

    if not (b_ok and c_ok and chain_bc):
        print("  INTEGRITY FAILURE")
        sys.exit(1)


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
