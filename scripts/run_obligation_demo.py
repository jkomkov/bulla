#!/usr/bin/env python3
"""Three-agent chain demo: obligation lifecycle and convergence.

Demonstrates the obligation lifecycle across a chain:
  Agent A: storage + api  — boundary_fee > 0, obligations emitted
  Agent B: api + render   — resolves A's obligations, adds own from boundary
  Agent C: render + db    — resolves all of B's, no new obligations

Each agent builds a composition with deliberate cross-server asymmetry
(fields hidden on BOTH sides of boundary edges), producing boundary
obligations that downstream agents must resolve.

Usage:
  python scripts/run_obligation_demo.py
"""
from __future__ import annotations

import sys

from bulla import __version__
from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    check_obligations,
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
from bulla.witness import verify_receipt_integrity, witness


# ── Tool definitions ─────────────────────────────────────────────────
#
# Fee > 0 requires hidden fields on BOTH sides of a cross-partition
# edge.  Each composition below has at least one such edge, ensuring
# boundary_fee > 0 and non-empty obligations.

# Agent A tools: storage + api
# storage__read_file hides "offset"; api__list_items also hides "offset".
# The "pagination" dimension links them across the server boundary,
# producing a boundary blind spot on both sides.
STORAGE_TOOLS = (
    ToolSpec(
        name="storage__read_file",
        internal_state=("path", "offset"),
        observable_schema=("path",),
    ),
    ToolSpec(
        name="storage__write_file",
        internal_state=("path", "content"),
        observable_schema=("path", "content"),
    ),
)

API_TOOLS = (
    ToolSpec(
        name="api__list_items",
        internal_state=("endpoint", "offset"),
        observable_schema=("endpoint",),
    ),
    ToolSpec(
        name="api__get_item",
        internal_state=("endpoint", "item_id"),
        observable_schema=("endpoint", "item_id"),
    ),
)

# Agent B tools: api (same as above) + render
# api__list_items hides "offset"; render__display hides "token".
# The "auth_scope" dimension links them, producing a boundary blind spot.
# render__display exposes "offset" observably, which satisfies A's obligations.
RENDER_TOOLS = (
    ToolSpec(
        name="render__display",
        internal_state=("template", "offset", "token"),
        observable_schema=("template", "offset"),
    ),
    ToolSpec(
        name="render__export",
        internal_state=("format", "token"),
        observable_schema=("format",),
    ),
)

# Agent C tools: render (fully observable versions) + db
# All render fields are now observable, satisfying B's "token" obligations.
RENDER_TOOLS_C = (
    ToolSpec(
        name="render__display",
        internal_state=("template", "offset", "token"),
        observable_schema=("template", "offset", "token"),
    ),
    ToolSpec(
        name="render__export",
        internal_state=("format", "token"),
        observable_schema=("format", "token"),
    ),
)

DB_TOOLS = (
    ToolSpec(
        name="db__query",
        internal_state=("sql", "page"),
        observable_schema=("sql", "page"),
    ),
    ToolSpec(
        name="db__insert",
        internal_state=("table", "data"),
        observable_schema=("table", "data"),
    ),
)

# ── Edge definitions ─────────────────────────────────────────────────

EDGES_A = (
    Edge(
        from_tool="storage__read_file",
        to_tool="api__list_items",
        dimensions=(
            SemanticDimension(name="path_resolve", from_field="path", to_field="endpoint"),
            SemanticDimension(name="pagination", from_field="offset", to_field="offset"),
        ),
    ),
)

EDGES_B = (
    Edge(
        from_tool="api__list_items",
        to_tool="render__display",
        dimensions=(
            SemanticDimension(name="content_type", from_field="endpoint", to_field="template"),
            SemanticDimension(name="auth_scope", from_field="offset", to_field="token"),
        ),
    ),
    Edge(
        from_tool="api__get_item",
        to_tool="render__export",
        dimensions=(
            SemanticDimension(name="format_resolve", from_field="item_id", to_field="format"),
        ),
    ),
)

EDGES_C = (
    Edge(
        from_tool="render__display",
        to_tool="db__query",
        dimensions=(
            SemanticDimension(name="display_config", from_field="template", to_field="sql"),
        ),
    ),
    Edge(
        from_tool="render__export",
        to_tool="db__insert",
        dimensions=(
            SemanticDimension(name="format_type", from_field="format", to_field="table"),
        ),
    ),
)


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
    print(f"  Bulla v{__version__} — Obligation Lifecycle Demo")
    print("=" * 70)
    print()

    # ── Agent A: storage + api ────────────────────────────────────────
    print("─" * 70)
    print("  AGENT A: storage + api")
    print("─" * 70)

    comp_a = Composition(
        name="agent-a",
        tools=STORAGE_TOOLS + API_TOOLS,
        edges=EDGES_A,
    )
    diag_a = diagnose(comp_a)
    partition_a = [
        frozenset(t.name for t in STORAGE_TOOLS),
        frozenset(t.name for t in API_TOOLS),
    ]
    decomp_a = decompose_fee(comp_a, partition_a)

    print(f"  Fee: {diag_a.coherence_fee}  (boundary_fee={decomp_a.boundary_fee})")
    print(f"  Blind spots: {len(diag_a.blind_spots)}")
    assert decomp_a.boundary_fee > 0, "Demo requires boundary_fee > 0 for Agent A"

    own_obls_a = boundary_obligations_from_decomposition(comp_a, partition_a, diag_a)
    _print_obligations("Obligations emitted", own_obls_a)
    assert len(own_obls_a) > 0, "Demo requires at least one obligation"

    receipt_a = witness(diag_a, comp_a, boundary_obligations=own_obls_a)
    print(f"  Receipt: {receipt_a.receipt_hash[:16]}...")
    print()

    # ── Agent B: api + render (chains A) ──────────────────────────────
    print("─" * 70)
    print("  AGENT B: api + render (chaining Agent A)")
    print("─" * 70)

    comp_b = Composition(
        name="agent-b",
        tools=API_TOOLS + RENDER_TOOLS,
        edges=EDGES_B,
    )
    diag_b = diagnose(comp_b)
    partition_b = [
        frozenset(t.name for t in API_TOOLS),
        frozenset(t.name for t in RENDER_TOOLS),
    ]
    decomp_b = decompose_fee(comp_b, partition_b)

    print(f"  Fee: {diag_b.coherence_fee}  (boundary_fee={decomp_b.boundary_fee})")
    print(f"  Blind spots: {len(diag_b.blind_spots)}")

    met_b, unmet_b, irr_b = check_obligations(own_obls_a, comp_b)
    print()
    print("  Checking Agent A's obligations:")
    print(f"    Met: {len(met_b)}")
    for obl in met_b:
        print(f"      {obl.dimension}: \"{obl.field}\" now observable")
    print(f"    Unmet: {len(unmet_b)}")
    for obl in unmet_b:
        print(f"      {obl.dimension}: \"{obl.field}\" still hidden")
    print(f"    Irrelevant: {len(irr_b)}")
    for obl in irr_b:
        print(f"      {obl.dimension}: \"{obl.field}\" not in composition")

    own_obls_b = boundary_obligations_from_decomposition(comp_b, partition_b, diag_b)
    _print_obligations("Own new obligations", own_obls_b)

    # Propagation: unmet from parent + own new (deduplicated)
    seen: dict[tuple[str, str, str], BoundaryObligation] = {}
    for obl in (*unmet_b, *own_obls_b):
        key = (obl.placeholder_tool, obl.dimension, obl.field)
        if key not in seen:
            seen[key] = obl
    combined_b = tuple(seen.values()) if seen else None
    _print_obligations("Total on receipt (propagated + own)", combined_b or ())

    receipt_b = witness(
        diag_b, comp_b,
        parent_receipt_hash=receipt_a.receipt_hash,
        boundary_obligations=combined_b,
    )
    print(f"  Receipt: {receipt_b.receipt_hash[:16]}...")
    print()

    # ── Agent C: render + db (chains B) ───────────────────────────────
    print("─" * 70)
    print("  AGENT C: render + db (chaining Agent B)")
    print("─" * 70)

    comp_c = Composition(
        name="agent-c",
        tools=RENDER_TOOLS_C + DB_TOOLS,
        edges=EDGES_C,
    )
    diag_c = diagnose(comp_c)
    partition_c = [
        frozenset(t.name for t in RENDER_TOOLS_C),
        frozenset(t.name for t in DB_TOOLS),
    ]
    decomp_c = decompose_fee(comp_c, partition_c)

    print(f"  Fee: {diag_c.coherence_fee}  (boundary_fee={decomp_c.boundary_fee})")
    print(f"  Blind spots: {len(diag_c.blind_spots)}")

    parent_obls_for_c = combined_b or ()
    met_c, unmet_c, irr_c = check_obligations(parent_obls_for_c, comp_c)
    print()
    print("  Checking Agent B's obligations (including propagated):")
    print(f"    Met: {len(met_c)}")
    for obl in met_c:
        print(f"      {obl.dimension}: \"{obl.field}\" now observable")
    print(f"    Unmet: {len(unmet_c)}")
    print(f"    Irrelevant: {len(irr_c)}")

    own_obls_c = boundary_obligations_from_decomposition(comp_c, partition_c, diag_c)

    # Propagation for C
    seen_c: dict[tuple[str, str, str], BoundaryObligation] = {}
    for obl in (*unmet_c, *own_obls_c):
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
    print("  OBLIGATION LIFECYCLE SUMMARY")
    print("=" * 70)
    print()

    n_a = len(own_obls_a)
    n_b = len(combined_b) if combined_b else 0
    n_c = len(combined_c) if combined_c else 0
    print(f"  Obligation propagation: A[{n_a}] -> B[{n_b}] -> C[{n_c}]")
    print(f"    A emitted {n_a} obligation(s)")
    print(f"    B resolved {len(met_b)} of A's, added {len(own_obls_b)} own -> {n_b} on receipt")
    print(f"    C resolved {len(met_c)} of B's, added {len(own_obls_c)} own -> {n_c} on receipt")
    print()

    # Verify receipt integrity
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
