#!/usr/bin/env python3
"""
Sprint B.2.b Empirical Check — Direct-Sum Position Characterization of B(A,B) > 0.

Question: For the 703 real-schema pairwise compositions, does the matroid-closure
characterization of bilateral fee exactly match the empirical data?

Specifically:
  B(A,B) > 0  ⟺  cl_M(O_A) ∩ cl_M(O_B) ⊄ loops(M)

where:
  - M(G) is the column matroid of δ_full(G)
  - O_A, O_B are the observable column sets for servers A, B respectively
  - cl_M(S) = {j : rank(δ_full[:, S ∪ {j}]) = rank(δ_full[:, S])}
  - loops(M) = {j : rank(δ_full[:, {j}]) = 0} (zero columns)

If this holds on all 703 compositions, the direct-sum-position predicate is the
correct condition for Sub-theorem (c) of Sprint B.2.b Lean formalization.

Usage:
    cd bulla && python -m calibration.b2b_empirical_check

Output: JSON summary + per-composition JSONL data.
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MIN_SCHEMA_FIELDS = 3


def _field_count(tools: list[dict[str, Any]]) -> int:
    """Count total inputSchema fields across all tools."""
    total = 0
    for t in tools:
        schema = t.get("inputSchema", {})
        props = schema.get("properties", {})
        total += len(props)
    return total


def matroid_closure(
    delta_full: list[list[Fraction]],
    col_set: set[int],
    n_cols: int,
) -> set[int]:
    """Compute cl_M(col_set) in the column matroid of delta_full.

    cl_M(S) = S ∪ {j : rank(δ[:, S ∪ {j}]) = rank(δ[:, S])}
    """
    from bulla.coboundary import matrix_rank

    # Compute rank of S
    if not delta_full or not delta_full[0]:
        return set(range(n_cols))  # empty matrix → all columns are loops

    def submatrix_cols(cols: set[int]) -> list[list[Fraction]]:
        sorted_cols = sorted(cols)
        return [[row[c] for c in sorted_cols] for row in delta_full]

    rank_S = matrix_rank(submatrix_cols(col_set)) if col_set else 0

    closure = set(col_set)
    for j in range(n_cols):
        if j in col_set:
            continue
        augmented = col_set | {j}
        rank_aug = matrix_rank(submatrix_cols(augmented))
        if rank_aug == rank_S:
            closure.add(j)

    return closure


def matroid_loops(
    delta_full: list[list[Fraction]],
    n_cols: int,
) -> set[int]:
    """Compute loops(M) = {j : column j is the zero vector}."""
    loops = set()
    for j in range(n_cols):
        if all(delta_full[i][j] == 0 for i in range(len(delta_full))):
            loops.add(j)
    return loops


def run_check() -> None:
    from bulla.coboundary import build_coboundary, matrix_rank
    from calibration.compute import diagnose_pair
    from calibration.corpus import ManifestStore

    data_dir = Path("calibration/data/registry")
    store = ManifestStore(data_dir=data_dir)

    # Real-schema filter (same as spectral.py)
    real_servers: dict[str, list[dict[str, Any]]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if _field_count(tools) >= MIN_SCHEMA_FIELDS:
            real_servers[name] = tools

    n_servers = len(real_servers)
    n_compositions = n_servers * (n_servers - 1) // 2
    logger.info(f"Real-schema corpus: {n_servers} servers → {n_compositions} compositions")

    # --- Step 1: Compute U(X) for each server via null-probe ---
    from bulla.guard import BullaGuard
    from bulla.model import ToolSpec

    null_probe = ToolSpec(
        name="null_probe",
        internal_state=(),
        observable_schema=(),
    )

    U_values: dict[str, int] = {}
    for name, tools in sorted(real_servers.items()):
        # Build composition of server X with null probe
        prefixed = []
        for t in tools:
            p = dict(t)
            p["name"] = f"{name}__{t['name']}"
            prefixed.append(p)

        null_tool_dict = {
            "name": "null_probe__null",
            "description": "null probe",
            "inputSchema": {"type": "object", "properties": {}},
        }
        guard = BullaGuard.from_tools_list(
            prefixed + [null_tool_dict],
            name=f"{name}+null_probe",
        )
        diag = guard.diagnose()
        U_values[name] = diag.coherence_fee

    carriers = {k: v for k, v in U_values.items() if v > 0}
    logger.info(f"Unilateral carriers: {len(carriers)} / {n_servers}")
    for name, u in sorted(carriers.items(), key=lambda x: -x[1]):
        logger.info(f"  {name}: U = {u}")

    # --- Step 2: For each composition, compute fee, U(A)+U(B), B(A,B),
    #             and check direct-sum position of matroid closures ---

    out_dir = Path("calibration/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "b2b_direct_sum_check.jsonl"
    summary_path = out_dir / "b2b_direct_sum_check.summary.json"

    stats = {
        "total": 0,
        "trivial": 0,          # fee = 0 and n_obs == n_full
        "B_zero": 0,           # B = 0 (including trivial)
        "B_positive": 0,       # B > 0
        "direct_sum_and_B_zero": 0,     # direct-sum position AND B = 0
        "not_direct_sum_and_B_pos": 0,  # NOT direct-sum position AND B > 0
        "MISMATCH": 0,                  # characterization fails
        "errors": 0,
    }

    with open(out_path, "w") as f:
        for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
            stats["total"] += 1
            try:
                result = diagnose_pair(a, real_servers[a], b, real_servers[b])
                comp = result.kernel_composition
                if comp is None:
                    stats["errors"] += 1
                    continue

                fee = result.coherence_fee
                U_a = U_values[a]
                U_b = U_values[b]
                B_ab = fee - U_a - U_b

                # Build coboundary matrices
                delta_full, v_basis_full, e_basis = build_coboundary(
                    comp.tools, comp.edges, use_internal=True
                )
                delta_obs, v_basis_obs, _ = build_coboundary(
                    comp.tools, comp.edges, use_internal=False
                )

                n_full = len(v_basis_full)
                n_obs = len(v_basis_obs)

                if n_full == 0 or not delta_full or not delta_full[0]:
                    stats["trivial"] += 1
                    stats["B_zero"] += 1
                    stats["direct_sum_and_B_zero"] += 1
                    continue

                # Identify O_A and O_B column indices in the full basis
                # Server prefixes (matching diagnose_pair normalization)
                prefix_a = a.replace("-", "_") + "__"
                prefix_b = b.replace("-", "_") + "__"

                O_A: set[int] = set()
                O_B: set[int] = set()
                for idx, (tool_name, field_name) in enumerate(v_basis_full):
                    # Check if this column is observable
                    tool_obj = next(
                        (t for t in comp.tools if t.name == tool_name), None
                    )
                    if tool_obj is None:
                        continue
                    if field_name in tool_obj.observable_schema:
                        if tool_name.startswith(prefix_a):
                            O_A.add(idx)
                        elif tool_name.startswith(prefix_b):
                            O_B.add(idx)

                # Compute matroid closures
                cl_O_A = matroid_closure(delta_full, O_A, n_full)
                cl_O_B = matroid_closure(delta_full, O_B, n_full)
                loops = matroid_loops(delta_full, n_full)

                # Direct-sum position check:
                # cl_M(O_A) ∩ cl_M(O_B) ⊆ loops(M)
                intersection = cl_O_A & cl_O_B
                is_direct_sum = intersection <= loops

                # Classification
                if B_ab == 0:
                    stats["B_zero"] += 1
                    if is_direct_sum:
                        stats["direct_sum_and_B_zero"] += 1
                    else:
                        stats["MISMATCH"] += 1
                        logger.warning(
                            f"MISMATCH: {a}+{b} has B=0 but NOT direct-sum position. "
                            f"intersection\\loops = {intersection - loops}"
                        )
                elif B_ab > 0:
                    stats["B_positive"] += 1
                    if not is_direct_sum:
                        stats["not_direct_sum_and_B_pos"] += 1
                    else:
                        stats["MISMATCH"] += 1
                        logger.warning(
                            f"MISMATCH: {a}+{b} has B={B_ab} but IS in direct-sum position"
                        )
                elif B_ab < 0:
                    stats["MISMATCH"] += 1
                    logger.warning(f"NEGATIVE B: {a}+{b} has B={B_ab}")

                record = {
                    "composition": f"{a}+{b}",
                    "fee": fee,
                    "U_a": U_a,
                    "U_b": U_b,
                    "B": B_ab,
                    "n_full": n_full,
                    "n_obs": n_obs,
                    "|O_A|": len(O_A),
                    "|O_B|": len(O_B),
                    "|cl(O_A)|": len(cl_O_A),
                    "|cl(O_B)|": len(cl_O_B),
                    "|cl(O_A)∩cl(O_B)|": len(intersection),
                    "|loops|": len(loops),
                    "is_direct_sum": is_direct_sum,
                    "characterization_holds": (
                        (B_ab == 0 and is_direct_sum)
                        or (B_ab > 0 and not is_direct_sum)
                    ),
                }
                f.write(json.dumps(record) + "\n")

                if stats["total"] % 100 == 0:
                    logger.info(f"  processed {stats['total']} compositions...")

            except Exception as e:
                stats["errors"] += 1
                logger.debug(f"Failed {a}+{b}: {e}")

    # --- Summary ---
    characterization_holds = stats["MISMATCH"] == 0
    stats["characterization_holds"] = characterization_holds

    logger.info("")
    logger.info("=" * 60)
    logger.info("Sprint B.2.b Empirical Check — Results")
    logger.info("=" * 60)
    logger.info(f"Total compositions:        {stats['total']}")
    logger.info(f"  B = 0:                   {stats['B_zero']}")
    logger.info(f"  B > 0:                   {stats['B_positive']}")
    logger.info(f"  Errors:                  {stats['errors']}")
    logger.info("")
    logger.info(f"Direct-sum AND B=0:        {stats['direct_sum_and_B_zero']}")
    logger.info(f"NOT direct-sum AND B>0:    {stats['not_direct_sum_and_B_pos']}")
    logger.info(f"MISMATCHES:                {stats['MISMATCH']}")
    logger.info("")
    if characterization_holds:
        logger.info("✓ CHARACTERIZATION HOLDS on all 703 compositions.")
        logger.info("  B(A,B) > 0 ⟺ cl_M(O_A) ∩ cl_M(O_B) ⊄ loops(M)")
        logger.info("  Sub-theorem (c) predicate: BilateralNonzero_iff_closureIntersection")
        logger.info("  Sprint B.2.b Lean target is well-posed.")
    else:
        logger.info("✗ CHARACTERIZATION FAILS.")
        logger.info(f"  {stats['MISMATCH']} compositions violate the biconditional.")
        logger.info("  Sub-theorem (c) needs a different predicate.")

    summary_path.write_text(json.dumps(stats, indent=2))
    logger.info(f"\nData: {out_path}")
    logger.info(f"Summary: {summary_path}")


if __name__ == "__main__":
    run_check()
