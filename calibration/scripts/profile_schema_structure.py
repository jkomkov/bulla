"""Profile the real-schema corpus for Bulla structure decisions.

This script is the empirical grounding step for the post-scalar Bulla
roadmap. It answers:

1. What structural scan categories actually occur in the current corpus?
2. How sparse / cyclic are the inferred composition graphs?
3. Which semantic dimensions dominate the pairwise server landscape?
4. Which small parameters look genuinely plausible for theorem work?

The output is a JSON report written under the corpus report directory.
In addition to the aggregate profile, the script emits per-pair records and
the subset of cyclic pairs used by the proxy bridge harness.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calibration.corpus import ManifestStore
from calibration.index import MIN_SCHEMA_FIELDS
from bulla.guard import BullaGuard


def _field_count(tools: list[dict[str, Any]]) -> int:
    total = 0
    for tool in tools:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, TypeError, ValueError):
                schema = {}
        total += len((schema or {}).get("properties", {}))
    return total


def _summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "median": 0, "max": 0, "mean": 0.0}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 3),
    }


def run_profile(corpus_dir: Path) -> dict[str, Any]:
    store = ManifestStore(data_dir=corpus_dir)

    server_tools: dict[str, list[dict[str, Any]]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if tools and _field_count(tools) >= MIN_SCHEMA_FIELDS:
            server_tools[name] = tools

    pair_rows: list[dict[str, Any]] = []
    overlap_categories: Counter[str] = Counter()
    mismatch_types: Counter[str] = Counter()
    dimension_counts: Counter[str] = Counter()

    for left, right in itertools.combinations(sorted(server_tools.keys()), 2):
        prefixed: list[dict[str, Any]] = []
        for tool in server_tools[left]:
            clone = dict(tool)
            clone["name"] = f"{left}__{tool['name']}"
            prefixed.append(clone)
        for tool in server_tools[right]:
            clone = dict(tool)
            clone["name"] = f"{right}__{tool['name']}"
            prefixed.append(clone)

        guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
        comp = guard.composition
        diag = guard.diagnose()
        struct = guard.structural_diagnostic

        for edge in comp.edges:
            for dim in edge.dimensions:
                dimension_counts[dim.name] += 1

        if struct is not None:
            for overlap in struct.overlaps:
                overlap_categories[overlap.category] += 1
            for contradiction in struct.contradictions:
                mismatch_types[contradiction.mismatch_type] += 1

        pair_rows.append(
            {
                "pair_name": f"{left}+{right}",
                "left_server": left,
                "right_server": right,
                "n_tools": len(comp.tools),
                "n_edges": len(comp.edges),
                "betti_1": diag.betti_1,
                "fee": diag.coherence_fee,
                "blind_spots": len(diag.blind_spots),
                "contradictions": 0 if struct is None else len(struct.contradictions),
                "overlaps": 0 if struct is None else len(struct.overlaps),
                "max_dims_per_edge": max(
                    (len(edge.dimensions) for edge in comp.edges),
                    default=0,
                ),
            }
        )

    with_edges = [row for row in pair_rows if row["n_edges"] > 0]
    cyclic = [row for row in with_edges if row["betti_1"] > 0]

    result = {
        "corpus": "registry_real_schema_pairwise",
        "real_schema_servers": len(server_tools),
        "pairwise_compositions": len(pair_rows),
        "all_pairs": {
            "tool_count": _summary([row["n_tools"] for row in pair_rows]),
            "edge_count": _summary([row["n_edges"] for row in pair_rows]),
            "betti_1": _summary([row["betti_1"] for row in pair_rows]),
            "fee": _summary([row["fee"] for row in pair_rows]),
            "contradictions": _summary([row["contradictions"] for row in pair_rows]),
            "max_dims_per_edge": _summary([row["max_dims_per_edge"] for row in pair_rows]),
        },
        "structure_gates": {
            "with_edges": len(with_edges),
            "edge_free_pairs": len(pair_rows) - len(with_edges),
            "acyclic_with_edges": sum(row["betti_1"] == 0 for row in with_edges),
            "cyclic_with_edges": len(cyclic),
            "positive_fee_pairs": sum(row["fee"] > 0 for row in pair_rows),
            "pairs_with_structural_contradictions": sum(row["contradictions"] > 0 for row in pair_rows),
        },
        "nonzero_edge_pairs": {
            "edge_count": _summary([row["n_edges"] for row in with_edges]),
            "betti_1": _summary([row["betti_1"] for row in with_edges]),
            "fee": _summary([row["fee"] for row in with_edges]),
            "contradictions": _summary([row["contradictions"] for row in with_edges]),
        },
        "overlap_categories": dict(overlap_categories),
        "mismatch_types": dict(mismatch_types),
        "top_dimensions": dimension_counts.most_common(20),
    }

    report_dir = corpus_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    out_path = report_dir / "schema_structure_profile.json"
    out_path.write_text(json.dumps(result, indent=2))

    pair_rows_path = report_dir / "schema_structure_pairs.jsonl"
    pair_rows_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in pair_rows) + "\n"
    )

    cyclic_pairs = sorted(
        (
            row
            for row in pair_rows
            if row["n_edges"] > 0 and row["betti_1"] > 0
        ),
        key=lambda row: (
            -row["betti_1"],
            -row["n_edges"],
            row["pair_name"],
        ),
    )
    cyclic_path = report_dir / "cyclic_pairs.json"
    cyclic_path.write_text(json.dumps(cyclic_pairs, indent=2))

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile Bulla schema structure")
    parser.add_argument(
        "--corpus",
        default="calibration/data/registry",
        help="Path to corpus directory",
    )
    args = parser.parse_args()
    result = run_profile(Path(args.corpus))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
