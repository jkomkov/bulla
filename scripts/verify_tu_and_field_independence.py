#!/usr/bin/env python3
"""Verify the Correspondence Theorem's structural hypothesis.

Two claims to verify:
1. SIGNED INCIDENCE PROPERTY: Every row of every Bulla coboundary matrix
   has at most one +1 and at most one -1 entry. This makes δ a signed
   incidence matrix of a directed multigraph, which is totally unimodular
   (Schrijver, Theory of Linear and Integer Programming, Ch. 19).

2. FIELD INDEPENDENCE: For TU matrices, rank is the same over every field.
   We verify this by computing rank(δ) mod p for p ∈ {2, 3, 5, 7} and
   checking exact agreement with rank over Q.

Uses the 703-composition real-schema calibration corpus.
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

# Add bulla to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def matrix_rank_mod_p(matrix: list[list[Fraction]], p: int) -> int:
    """Gaussian elimination over F_p (integers mod p)."""
    if not matrix or not matrix[0]:
        return 0
    m = len(matrix)
    n = len(matrix[0])
    # Reduce entries mod p
    rows = [[int(x) % p for x in row] for row in matrix]
    rank = 0
    for col in range(n):
        pivot = None
        for row in range(rank, m):
            if rows[row][col] % p != 0:
                pivot = row
                break
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        # Find modular inverse of pivot
        scale = rows[rank][col] % p
        inv = pow(scale, p - 2, p)  # Fermat's little theorem
        rows[rank] = [(x * inv) % p for x in rows[rank]]
        for row in range(m):
            if row != rank and rows[row][col] % p != 0:
                factor = rows[row][col] % p
                rows[row] = [(rows[row][j] - factor * rows[rank][j]) % p for j in range(n)]
        rank += 1
    return rank


def check_signed_incidence(matrix: list[list[Fraction]]) -> tuple[bool, str]:
    """Check that each row has at most one +1 and at most one -1."""
    for i, row in enumerate(matrix):
        pos_count = sum(1 for x in row if x > 0)
        neg_count = sum(1 for x in row if x < 0)
        non_unit = [x for x in row if x != 0 and abs(x) != 1]
        if pos_count > 1:
            return False, f"Row {i}: {pos_count} positive entries"
        if neg_count > 1:
            return False, f"Row {i}: {neg_count} negative entries"
        if non_unit:
            return False, f"Row {i}: non-unit entries {non_unit}"
    return True, "OK"


def load_registry_compositions() -> list[Composition]:
    """Load the 703-composition real-schema calibration corpus."""
    index_path = Path(__file__).resolve().parents[1] / "calibration" / "data" / "registry" / "receipts" / "index.json"
    if not index_path.exists():
        # Try alternative path
        index_path = Path(__file__).resolve().parents[1] / "calibration" / "data" / "index" / "pairwise_index.json"

    if not index_path.exists():
        print(f"Cannot find calibration corpus at {index_path}")
        print("Falling back to built-in test compositions...")
        return create_test_compositions()

    with open(index_path) as f:
        data = json.load(f)

    compositions = []
    for entry in data:
        if isinstance(entry, dict) and "composition" in entry:
            comp_data = entry["composition"]
        elif isinstance(entry, dict):
            comp_data = entry
        else:
            continue
        try:
            comp = parse_composition(comp_data)
            compositions.append(comp)
        except (KeyError, TypeError):
            continue

    return compositions


def parse_composition(data: dict) -> Composition:
    """Parse a composition from calibration data."""
    tools = []
    for t in data.get("tools", []):
        tools.append(ToolSpec(
            name=t["name"],
            internal_state=tuple(t.get("internal_state", ())),
            observable_schema=tuple(t.get("observable_schema", ())),
        ))

    edges = []
    for e in data.get("edges", []):
        dims = []
        for d in e.get("dimensions", []):
            dims.append(SemanticDimension(
                name=d["name"],
                from_field=d.get("from_field"),
                to_field=d.get("to_field"),
            ))
        edges.append(Edge(
            from_tool=e["from_tool"],
            to_tool=e["to_tool"],
            dimensions=tuple(dims),
        ))

    return Composition(
        name=data.get("name", "unnamed"),
        tools=tuple(tools),
        edges=tuple(edges),
    )


def create_test_compositions() -> list[Composition]:
    """Create a small set of test compositions for verification."""
    # Simple 2-tool composition with hidden field
    comp1 = Composition(
        name="test_simple",
        tools=(
            ToolSpec("A", ("x", "y", "z"), ("x", "y")),
            ToolSpec("B", ("x", "y", "w"), ("x", "y")),
        ),
        edges=(
            Edge("A", "B", (
                SemanticDimension("dim1", "x", "x"),
                SemanticDimension("dim2", "y", "y"),
                SemanticDimension("dim3", "z", "w"),
            )),
        ),
    )

    # Triangle with cycle
    comp2 = Composition(
        name="test_triangle",
        tools=(
            ToolSpec("A", ("a1", "a2"), ("a1",)),
            ToolSpec("B", ("b1", "b2"), ("b1",)),
            ToolSpec("C", ("c1", "c2"), ("c1",)),
        ),
        edges=(
            Edge("A", "B", (SemanticDimension("d", "a1", "b1"),)),
            Edge("B", "C", (SemanticDimension("d", "b1", "c1"),)),
            Edge("C", "A", (SemanticDimension("d", "c1", "a1"),)),
        ),
    )

    # 4-tool with multiple dimensions
    comp3 = Composition(
        name="test_multi",
        tools=(
            ToolSpec("P", ("amount", "currency", "date"), ("amount",)),
            ToolSpec("Q", ("amount", "currency", "date"), ("amount", "currency")),
            ToolSpec("R", ("value", "curr", "timestamp"), ("value",)),
            ToolSpec("S", ("total", "curr", "time"), ("total", "curr")),
        ),
        edges=(
            Edge("P", "Q", (
                SemanticDimension("money", "amount", "amount"),
                SemanticDimension("curr", "currency", "currency"),
            )),
            Edge("Q", "R", (
                SemanticDimension("money", "amount", "value"),
                SemanticDimension("curr", "currency", "curr"),
            )),
            Edge("R", "S", (
                SemanticDimension("money", "value", "total"),
                SemanticDimension("curr", "curr", "curr"),
            )),
        ),
    )

    return [comp1, comp2, comp3]


def main():
    print("=" * 70)
    print("CORRESPONDENCE THEOREM VERIFICATION")
    print("Signed Incidence Property + Field Independence")
    print("=" * 70)

    comps = load_registry_compositions()
    print(f"\nLoaded {len(comps)} compositions")

    primes = [2, 3, 5, 7]

    si_pass = 0
    si_fail = 0
    fi_pass = 0
    fi_fail = 0
    nonzero_fee = 0

    for i, comp in enumerate(comps):
        # Build full coboundary
        delta_full, v_basis, e_basis = build_coboundary(
            list(comp.tools), list(comp.edges), use_internal=True
        )
        delta_obs, _, _ = build_coboundary(
            list(comp.tools), list(comp.edges), use_internal=False
        )

        # Check signed incidence property
        ok_full, msg_full = check_signed_incidence(delta_full)
        ok_obs, msg_obs = check_signed_incidence(delta_obs)

        if ok_full and ok_obs:
            si_pass += 1
        else:
            si_fail += 1
            print(f"\n  SIGNED INCIDENCE VIOLATION in {comp.name}:")
            if not ok_full:
                print(f"    δ_full: {msg_full}")
            if not ok_obs:
                print(f"    δ_obs: {msg_obs}")

        # Compute Q-valued ranks
        rank_full_q = matrix_rank(delta_full)
        rank_obs_q = matrix_rank(delta_obs)
        fee_q = rank_full_q - rank_obs_q

        if fee_q > 0:
            nonzero_fee += 1

        # Compute ranks over F_p for each prime
        field_independent = True
        for p in primes:
            rank_full_p = matrix_rank_mod_p(delta_full, p)
            rank_obs_p = matrix_rank_mod_p(delta_obs, p)
            fee_p = rank_full_p - rank_obs_p

            if fee_p != fee_q:
                field_independent = False
                print(f"\n  FIELD INDEPENDENCE VIOLATION in {comp.name}:")
                print(f"    fee_Q = {fee_q}, fee_F{p} = {fee_p}")
                print(f"    rank_full: Q={rank_full_q}, F{p}={rank_full_p}")
                print(f"    rank_obs:  Q={rank_obs_q}, F{p}={rank_obs_p}")

        if field_independent:
            fi_pass += 1
        else:
            fi_fail += 1

        # Progress
        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(comps)} compositions checked")

    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    print(f"\nCompositions tested:       {len(comps)}")
    print(f"Nonzero-fee compositions:  {nonzero_fee}")
    print(f"\nSigned Incidence Property:")
    print(f"  Pass: {si_pass}/{len(comps)}")
    print(f"  Fail: {si_fail}/{len(comps)}")
    print(f"\nField Independence (Q vs F_2, F_3, F_5, F_7):")
    print(f"  Pass: {fi_pass}/{len(comps)}")
    print(f"  Fail: {fi_fail}/{len(comps)}")

    if si_fail == 0 and fi_fail == 0:
        print(f"\n{'=' * 70}")
        print("CORRESPONDENCE THEOREM: VERIFIED")
        print(f"{'=' * 70}")
        print("""
The coboundary δ of every tested composition is a signed incidence matrix
(each row has ≤1 positive and ≤1 negative entry). Signed incidence matrices
are totally unimodular (Schrijver, Ch. 19). For TU matrices, rank is
field-independent. Therefore:

  fee_Q(G) = fee_{F_p}(G)  for every prime p

The Z/2 obstruction theory (impossibility paper) and the Q-valued coherence
fee (Bulla implementation) compute the same invariant — not by empirical
coincidence, but by the structural property of the coboundary construction.
""")
    else:
        print(f"\n{'=' * 70}")
        print("CORRESPONDENCE THEOREM: VIOLATIONS FOUND — investigate")
        print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
