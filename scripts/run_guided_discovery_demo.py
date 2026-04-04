#!/usr/bin/env python3
"""Three-agent chain with guided repair: obligation-directed discovery.

Extends the Sprint 25 obligation lifecycle with Sprint 26 guided
discovery.  Each agent runs guided_discover() to confirm whether
obligated fields are observable, then repairs the composition and
verifies the collective invariant: fee strictly decreases.

Uses BullaGuard.from_tools_list() with mock MCP tool dicts to prove
the full pipeline (addresses Sprint 25 review issue #1).

Usage:
  python scripts/run_guided_discovery_demo.py
"""
from __future__ import annotations

import sys

from bulla import __version__
from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    check_obligations,
    decompose_fee,
    diagnose,
    repair_composition,
)
from bulla.discover.adapter import MockAdapter
from bulla.discover.engine import guided_discover
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    ObligationVerdict,
    SemanticDimension,
    ToolSpec,
)
from bulla.witness import verify_receipt_integrity, witness


# ── Tool definitions ─────────────────────────────────────────────────
#
# Same as Sprint 25 obligation demo: cross-server edges with hidden
# fields on both sides to ensure boundary_fee > 0.

STORAGE_TOOLS = (
    ToolSpec("storage__read_file", ("path", "offset"), ("path",)),
    ToolSpec("storage__write_file", ("path", "content"), ("path", "content")),
)

API_TOOLS = (
    ToolSpec("api__list_items", ("endpoint", "offset"), ("endpoint",)),
    ToolSpec("api__get_item", ("endpoint", "item_id"), ("endpoint", "item_id")),
)

RENDER_TOOLS = (
    ToolSpec("render__display", ("template", "offset", "token"), ("template", "offset")),
    ToolSpec("render__export", ("format", "token"), ("format",)),
)

RENDER_TOOLS_C = (
    ToolSpec("render__display", ("template", "offset", "token"), ("template", "offset", "token")),
    ToolSpec("render__export", ("format", "token"), ("format", "token")),
)

DB_TOOLS = (
    ToolSpec("db__query", ("sql", "page"), ("sql", "page")),
    ToolSpec("db__insert", ("table", "data"), ("table", "data")),
)

EDGES_A = (
    Edge("storage__read_file", "api__list_items", (
        SemanticDimension("path_resolve", "path", "endpoint"),
        SemanticDimension("pagination", "offset", "offset"),
    )),
)

EDGES_B = (
    Edge("api__list_items", "render__display", (
        SemanticDimension("content_type", "endpoint", "template"),
        SemanticDimension("auth_scope", "offset", "token"),
    )),
    Edge("api__get_item", "render__export", (
        SemanticDimension("format_resolve", "item_id", "format"),
    )),
)

EDGES_C = (
    Edge("render__display", "db__query", (
        SemanticDimension("display_config", "template", "sql"),
    )),
    Edge("render__export", "db__insert", (
        SemanticDimension("format_type", "format", "table"),
    )),
)


def _mock_tools_as_dicts(tools: tuple[ToolSpec, ...]) -> list[dict]:
    """Convert ToolSpecs to MCP-style tool dicts for guided discovery."""
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


def _build_mock_response(
    obligations: tuple[BoundaryObligation, ...],
    comp: Composition,
) -> str:
    """Build a MockAdapter response that confirms observable fields."""
    all_observable: set[str] = set()
    for t in comp.tools:
        all_observable.update(t.observable_schema)

    blocks = []
    for idx, obl in enumerate(obligations, 1):
        if obl.field in all_observable:
            blocks.append(
                f"---BEGIN_VERDICT_{idx}---\n"
                f"verdict: CONFIRMED\n"
                f"evidence: field {obl.field} is present in the tool output\n"
                f"convention_value: standard\n"
                f"---END_VERDICT_{idx}---"
            )
        else:
            blocks.append(
                f"---BEGIN_VERDICT_{idx}---\n"
                f"verdict: DENIED\n"
                f"evidence: field {obl.field} is not exposed\n"
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
        print(f"    - {obl.dimension}: \"{obl.field}\" hidden in {obl.placeholder_tool}{edge_str}")


def run_demo() -> None:
    print("=" * 70)
    print(f"  Bulla v{__version__} — Guided Discovery Demo")
    print("=" * 70)
    print()

    # ── Agent A: storage + api ────────────────────────────────────────
    print("─" * 70)
    print("  AGENT A: storage + api")
    print("─" * 70)

    comp_a = Composition("agent-a", STORAGE_TOOLS + API_TOOLS, EDGES_A)
    diag_a = diagnose(comp_a)
    partition_a = [
        frozenset(t.name for t in STORAGE_TOOLS),
        frozenset(t.name for t in API_TOOLS),
    ]
    decomp_a = decompose_fee(comp_a, partition_a)

    print(f"  Fee: {diag_a.coherence_fee}  (boundary_fee={decomp_a.boundary_fee})")
    assert decomp_a.boundary_fee > 0, "Demo requires boundary_fee > 0"

    own_obls_a = boundary_obligations_from_decomposition(comp_a, partition_a, diag_a)
    _print_obligations("Obligations emitted", own_obls_a)
    assert len(own_obls_a) > 0

    receipt_a = witness(diag_a, comp_a, boundary_obligations=own_obls_a)
    print(f"  Receipt: {receipt_a.receipt_hash[:16]}...")
    print()

    # ── Agent B: api + render + guided repair ─────────────────────────
    print("─" * 70)
    print("  AGENT B: api + render (guided repair of A's obligations)")
    print("─" * 70)

    comp_b = Composition("agent-b", API_TOOLS + RENDER_TOOLS, EDGES_B)
    diag_b = diagnose(comp_b)
    partition_b = [
        frozenset(t.name for t in API_TOOLS),
        frozenset(t.name for t in RENDER_TOOLS),
    ]
    decomp_b = decompose_fee(comp_b, partition_b)
    original_fee_b = diag_b.coherence_fee

    print(f"  Fee (before repair): {original_fee_b}  (boundary_fee={decomp_b.boundary_fee})")

    all_b_tools = _mock_tools_as_dicts(API_TOOLS + RENDER_TOOLS)
    mock_response_b = _build_mock_response(own_obls_a, comp_b)
    adapter_b = MockAdapter(mock_response_b)

    guided_result_b = guided_discover(own_obls_a, all_b_tools, adapter_b)

    print()
    print(f"  Guided discovery: {guided_result_b.n_confirmed} confirmed, "
          f"{guided_result_b.n_denied} denied, {guided_result_b.n_uncertain} uncertain")
    for p in guided_result_b.probes:
        print(f"    {p.obligation.dimension}/{p.obligation.field}: {p.verdict.value}"
              + (f" (value: {p.convention_value})" if p.convention_value else ""))

    if guided_result_b.confirmed:
        repaired_comp_b = repair_composition(comp_b, guided_result_b.confirmed)
        repaired_diag_b = diagnose(repaired_comp_b)
        repaired_fee_b = repaired_diag_b.coherence_fee

        print()
        print(f"  Fee (after repair): {repaired_fee_b}")
        assert repaired_fee_b < original_fee_b, (
            f"Collective invariant violated: {repaired_fee_b} >= {original_fee_b}"
        )
        print(f"  Collective invariant: fee {original_fee_b} -> {repaired_fee_b} (PASSED)")

        comp_b = repaired_comp_b
        diag_b = repaired_diag_b
        decomp_b = decompose_fee(comp_b, partition_b)

    own_obls_b = ()
    if decomp_b.boundary_fee > 0:
        own_obls_b = boundary_obligations_from_decomposition(comp_b, partition_b, diag_b)
    _print_obligations("Own new obligations (post-repair)", own_obls_b)

    seen: dict[tuple[str, str, str], BoundaryObligation] = {}
    remaining_from_a = tuple(
        p.obligation for p in guided_result_b.probes
        if p.verdict != ObligationVerdict.CONFIRMED
    )
    for obl in (*remaining_from_a, *own_obls_b):
        key = (obl.placeholder_tool, obl.dimension, obl.field)
        if key not in seen:
            seen[key] = obl
    combined_b = tuple(seen.values()) if seen else None

    receipt_b = witness(
        diag_b, comp_b,
        parent_receipt_hash=receipt_a.receipt_hash,
        boundary_obligations=combined_b,
    )
    print(f"  Receipt: {receipt_b.receipt_hash[:16]}...")
    print()

    # ── Agent C: render + db + guided repair ──────────────────────────
    print("─" * 70)
    print("  AGENT C: render + db (guided repair of B's obligations)")
    print("─" * 70)

    comp_c = Composition("agent-c", RENDER_TOOLS_C + DB_TOOLS, EDGES_C)
    diag_c = diagnose(comp_c)
    partition_c = [
        frozenset(t.name for t in RENDER_TOOLS_C),
        frozenset(t.name for t in DB_TOOLS),
    ]
    decomp_c = decompose_fee(comp_c, partition_c)
    original_fee_c = diag_c.coherence_fee

    print(f"  Fee (before repair): {original_fee_c}  (boundary_fee={decomp_c.boundary_fee})")

    parent_obls_for_c = combined_b or ()
    if parent_obls_for_c:
        all_c_tools = _mock_tools_as_dicts(RENDER_TOOLS_C + DB_TOOLS)
        mock_response_c = _build_mock_response(parent_obls_for_c, comp_c)
        adapter_c = MockAdapter(mock_response_c)

        guided_result_c = guided_discover(parent_obls_for_c, all_c_tools, adapter_c)

        print()
        print(f"  Guided discovery: {guided_result_c.n_confirmed} confirmed, "
              f"{guided_result_c.n_denied} denied, {guided_result_c.n_uncertain} uncertain")
        for p in guided_result_c.probes:
            print(f"    {p.obligation.dimension}/{p.obligation.field}: {p.verdict.value}")

        remaining_c = tuple(
            p.obligation for p in guided_result_c.probes
            if p.verdict != ObligationVerdict.CONFIRMED
        )
    else:
        print("  No parent obligations to check.")
        remaining_c = ()

    own_obls_c = ()
    if decomp_c.boundary_fee > 0:
        own_obls_c = boundary_obligations_from_decomposition(comp_c, partition_c, diag_c)

    seen_c: dict[tuple[str, str, str], BoundaryObligation] = {}
    for obl in (*remaining_c, *own_obls_c):
        key = (obl.placeholder_tool, obl.dimension, obl.field)
        if key not in seen_c:
            seen_c[key] = obl
    combined_c = tuple(seen_c.values()) if seen_c else None

    receipt_c = witness(
        diag_c, comp_c,
        parent_receipt_hash=receipt_b.receipt_hash,
        boundary_obligations=combined_c,
    )
    print(f"  Receipt: {receipt_c.receipt_hash[:16]}...")
    print()

    # ── Summary ───────────────────────────────────────────────────────
    print("=" * 70)
    print("  GUIDED DISCOVERY SUMMARY")
    print("=" * 70)
    print()

    n_a = len(own_obls_a)
    n_b = len(combined_b) if combined_b else 0
    n_c = len(combined_c) if combined_c else 0
    print(f"  Obligation propagation: A[{n_a}] -> B[{n_b}] -> C[{n_c}]")
    print(f"  Fee trajectory: A[{diag_a.coherence_fee}] -> B_repair[{diag_b.coherence_fee}] -> C[{diag_c.coherence_fee}]")
    print()

    a_ok = verify_receipt_integrity(receipt_a.to_dict())
    b_ok = verify_receipt_integrity(receipt_b.to_dict())
    c_ok = verify_receipt_integrity(receipt_c.to_dict())
    print(f"  Receipt A integrity: {'VALID' if a_ok else 'BROKEN'}")
    print(f"  Receipt B integrity: {'VALID' if b_ok else 'BROKEN'}")
    print(f"  Receipt C integrity: {'VALID' if c_ok else 'BROKEN'}")

    chain_ab = receipt_b.parent_receipt_hashes == (receipt_a.receipt_hash,)
    chain_bc = receipt_c.parent_receipt_hashes == (receipt_b.receipt_hash,)
    print(f"  Chain A->B: {'VALID' if chain_ab else 'BROKEN'}")
    print(f"  Chain B->C: {'VALID' if chain_bc else 'BROKEN'}")
    print()

    all_ok = a_ok and b_ok and c_ok and chain_ab and chain_bc
    if not all_ok:
        print("  INTEGRITY FAILURE")
        sys.exit(1)


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
