#!/usr/bin/env python3
"""Verify the pairwise endpoint-coupling bound on the 703 corpus.

Corollary B for the signed-incidence note states that for a fixed
(edge, dimension) block B in a pairwise composition, rank(K[B, B]) <= 2.

This script does two things:
1. checks the block-rank distribution on the 703 real-schema corpus, and
2. runs the specific projection sanity check from Sprint 1:
   whether any block has rank 2 in the corresponding delta block but
   rank < 2 in the projected witness-Gram block.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from calibration.compute import diagnose_pair
from calibration.corpus import ManifestStore
from bulla.coboundary import build_coboundary, matrix_rank
from bulla.witness_geometry import compute_all

MIN_SCHEMA_FIELDS = 3


def _real_schema_servers(store: ManifestStore) -> dict[str, list[dict]]:
    servers: dict[str, list[dict]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        total_fields = sum(
            len(tool.get("inputSchema", {}).get("properties", {}))
            for tool in tools
        )
        if total_fields >= MIN_SCHEMA_FIELDS:
            servers[name] = tools
    return servers


def run_verification() -> dict[str, object]:
    store = ManifestStore(data_dir=Path("calibration/data/registry"))
    servers = _real_schema_servers(store)

    summary: dict[str, object] = {
        "tested": 0,
        "nonzero_fee": 0,
        "multi_blocks": 0,
        "k_rank_histogram": {},
        "blocks_delta_rank2_k_lt2": 0,
        "compositions_with_delta_rank2_k_lt2": 0,
        "max_delta_block_rank": 0,
        "max_k_block_rank": 0,
        "examples": [],
    }
    k_rank_histogram: Counter[int] = Counter()

    for server_a, server_b in itertools.combinations(sorted(servers.keys()), 2):
        result = diagnose_pair(
            server_a,
            servers[server_a],
            server_b,
            servers[server_b],
        )
        comp = result.kernel_composition
        diag = result.kernel_diagnostic
        summary["tested"] += 1

        if diag.coherence_fee <= 0:
            continue
        summary["nonzero_fee"] += 1

        tools = list(comp.tools)
        edges = list(comp.edges)
        delta, full_basis, _ = build_coboundary(tools, edges, use_internal=True)
        profile = compute_all(tools, edges)
        K = profile["K"]
        hidden_basis = profile["hidden_basis"]
        hidden_basis_set = set(hidden_basis)

        full_index = {pair: idx for idx, pair in enumerate(full_basis)}
        hidden_index = {pair: idx for idx, pair in enumerate(hidden_basis)}

        blocks: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for edge in comp.edges:
            label = f"{edge.from_tool}\u2192{edge.to_tool}"
            for dim in edge.dimensions:
                if dim.from_field:
                    pair = (edge.from_tool, dim.from_field)
                    if pair in hidden_basis_set:
                        blocks[(label, dim.name)].append(pair)
                if dim.to_field:
                    pair = (edge.to_tool, dim.to_field)
                    if pair in hidden_basis_set:
                        blocks[(label, dim.name)].append(pair)

        composition_has_counterexample = False
        for block_key, block_pairs in blocks.items():
            block_pairs = sorted(set(block_pairs))
            if len(block_pairs) <= 1:
                continue

            summary["multi_blocks"] += 1
            delta_cols = [full_index[pair] for pair in block_pairs]
            hidden_cols = [hidden_index[pair] for pair in block_pairs]

            delta_block = [[row[col] for col in delta_cols] for row in delta]
            k_block = [[K[i][j] for j in hidden_cols] for i in hidden_cols]

            delta_rank = matrix_rank(delta_block)
            k_rank = matrix_rank(k_block)

            k_rank_histogram[k_rank] += 1
            summary["max_delta_block_rank"] = max(
                int(summary["max_delta_block_rank"]),
                delta_rank,
            )
            summary["max_k_block_rank"] = max(
                int(summary["max_k_block_rank"]),
                k_rank,
            )

            if delta_rank == 2 and k_rank < 2:
                summary["blocks_delta_rank2_k_lt2"] += 1
                composition_has_counterexample = True
                examples = summary["examples"]
                if isinstance(examples, list) and len(examples) < 10:
                    examples.append(
                        {
                            "composition": f"{server_a}+{server_b}",
                            "block": list(block_key),
                            "pairs": [list(pair) for pair in block_pairs],
                            "fee": diag.coherence_fee,
                            "delta_rank": delta_rank,
                            "k_rank": k_rank,
                        }
                    )

        if composition_has_counterexample:
            summary["compositions_with_delta_rank2_k_lt2"] += 1

    summary["k_rank_histogram"] = {
        str(rank): count for rank, count in sorted(k_rank_histogram.items())
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-json",
        type=Path,
        default=Path("calibration/results/endpoint_coupling_703.summary.json"),
        help="where to write the summary JSON",
    )
    args = parser.parse_args()

    summary = run_verification()
    args.write_json.parent.mkdir(parents=True, exist_ok=True)
    args.write_json.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote summary to {args.write_json}")


if __name__ == "__main__":
    main()
