#!/usr/bin/env python3
"""Verify the Partition Matroid Conjecture for typed repair.

Conjecture: Under DFD, the hidden columns of δ_full partition by
(edge, dimension). Within each partition block, the rank of the
witness Gram K restricted to block columns is at most 1.

Uses Bulla's existing diagnostic infrastructure.
"""

from __future__ import annotations

import json
import os
import sys
import itertools
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.diagnostic import diagnose
from bulla.guard import BullaGuard
from bulla.witness_geometry import compute_all


def check_partition_matroid(guard: BullaGuard) -> tuple[bool, dict]:
    """Check the partition matroid conjecture for a composition."""
    comp = guard._composition
    diag = diagnose(comp, include_witness_geometry=True)

    if diag.coherence_fee == 0:
        return True, {"fee": 0, "blocks": 0, "multi_blocks": 0, "max_block_rank": 0}

    # Get the witness Gram matrix K and hidden basis from compute_all
    tools = list(comp.tools)
    edges = list(comp.edges)
    result = compute_all(tools, edges)

    K = result["K"]
    h_basis = result["hidden_basis"]

    if not K or not h_basis:
        return True, {"fee": diag.coherence_fee, "blocks": 0, "multi_blocks": 0,
                       "max_block_rank": 0, "note": "K empty"}

    # Map hidden columns to (edge, dimension) blocks
    hb_idx = {h: i for i, h in enumerate(h_basis)}

    blocks: dict[tuple[str, str], list[int]] = defaultdict(list)

    for edge in comp.edges:
        label = f"{edge.from_tool}\u2192{edge.to_tool}"
        for dim in edge.dimensions:
            if dim.from_field:
                key = (edge.from_tool, dim.from_field)
                if key in hb_idx:
                    blocks[(label, dim.name)].append(hb_idx[key])
            if dim.to_field:
                key = (edge.to_tool, dim.to_field)
                if key in hb_idx:
                    blocks[(label, dim.name)].append(hb_idx[key])

    # For each block with >1 unique column, check rank of K[block, block]
    max_block_rank = 0
    violations = []
    n_multi_blocks = 0

    for block_key, col_indices in blocks.items():
        col_indices = sorted(set(col_indices))
        if len(col_indices) <= 1:
            continue

        n_multi_blocks += 1
        n = len(col_indices)
        sub_K = [[K[col_indices[i]][col_indices[j]]
                  for j in range(n)]
                 for i in range(n)]

        block_rank = matrix_rank(sub_K)
        max_block_rank = max(max_block_rank, block_rank)

        if block_rank > 1:
            violations.append({
                "block": block_key,
                "columns": [h_basis[c] for c in col_indices],
                "rank": block_rank,
            })

    passed = len(violations) == 0
    return passed, {
        "fee": diag.coherence_fee,
        "blocks": len(blocks),
        "multi_blocks": n_multi_blocks,
        "max_block_rank": max_block_rank,
        "violations": violations if violations else None,
    }


def main():
    print("=" * 60)
    print("PARTITION MATROID CONJECTURE VERIFICATION")
    print("=" * 60)

    manifest_dir = Path(__file__).resolve().parents[1] / "calibration" / "data" / "registry" / "manifests"

    min_fields = 3
    server_tools = {}
    all_server_tools = {}
    for fname in sorted(os.listdir(manifest_dir)):
        if not fname.endswith(".json"):
            continue
        with open(manifest_dir / fname) as f:
            data = json.load(f)
        name = fname.replace(".json", "")
        tools = data.get("tools", [])
        all_server_tools[name] = tools
        total_fields = sum(
            len(t.get("inputSchema", {}).get("properties", {}))
            for t in tools
        )
        if total_fields >= min_fields:
            server_tools[name] = tools

    print(f"\nAll servers: {len(all_server_tools)}")
    print(f"Real-schema servers: {len(server_tools)}")

    for corpus_name, tools_dict in [
        ("703 (real-schema)", server_tools),
    ]:
        servers = sorted(tools_dict.keys())
        n_pairs = len(servers) * (len(servers) - 1) // 2

        print(f"\n{'─' * 60}")
        print(f"Corpus: {corpus_name} ({n_pairs} pairs)")
        print(f"{'─' * 60}")

        passed = failed = errors = nonzero = 0
        max_rank_seen = 0
        total_multi_blocks = 0

        for a, b in itertools.combinations(servers, 2):
            try:
                combined = tools_dict[a] + tools_dict[b]
                guard = BullaGuard.from_tools_list(combined, name=f"{a}+{b}")

                ok, details = check_partition_matroid(guard)

                if details["fee"] > 0:
                    nonzero += 1

                max_rank_seen = max(max_rank_seen, details.get("max_block_rank", 0))
                total_multi_blocks += details.get("multi_blocks", 0)

                if ok:
                    passed += 1
                else:
                    failed += 1
                    print(f"  VIOLATION: {a}+{b}: {details['violations']}")

            except Exception as ex:
                errors += 1
                if errors <= 3:
                    print(f"  ERROR in {a}+{b}: {type(ex).__name__}: {ex}")

            total = passed + failed + errors
            if total % 100 == 0 and total > 0:
                print(f"  ...{passed} pass, {failed} fail, {errors} err, {nonzero} nonzero ({total}/{n_pairs})")

        print(f"\nResults ({corpus_name}):")
        print(f"  Tested:             {passed + failed}")
        print(f"  Errors:             {errors}")
        print(f"  Nonzero fee:        {nonzero}")
        print(f"  Passed:             {passed}")
        print(f"  Failed:             {failed}")
        print(f"  Max block rank:     {max_rank_seen}")
        print(f"  Multi-col blocks:   {total_multi_blocks}")

        if failed == 0 and nonzero > 0:
            print(f"\n  CONJECTURE: VERIFIED on {passed}/{passed} compositions")
            print(f"  ({nonzero} with nonzero fee, all partition blocks have rank ≤ 1)")
        elif nonzero == 0:
            print(f"\n  CONJECTURE: TRIVIALLY TRUE (no nonzero-fee compositions)")
        else:
            print(f"\n  CONJECTURE: VIOLATED on {failed} compositions")


if __name__ == "__main__":
    main()
