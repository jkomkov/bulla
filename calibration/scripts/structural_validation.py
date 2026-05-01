"""Validate structural scan against the registry calibration corpus.

Runs structural scan pairwise on the same compositions the calibration
pipeline uses, and compares findings to pack-based blind spots:

  - True positives: structural contradictions on fields the pack system
    also flags as blind spots
  - New findings: structural contradictions the pack system does NOT flag
    (interesting or false positive — inspect manually)
  - False negatives: pack-based blind spots with no structural signal
    (expected — the coboundary catches hidden fields, not visible ones)

Usage:
    python calibration/scripts/structural_validation.py [--corpus PATH]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calibration.corpus import ManifestStore
from bulla.guard import BullaGuard
from bulla.model import BlindSpot, SchemaContradiction


def _field_count(tools: list[dict[str, Any]]) -> int:
    """Count total input schema fields across tools (calibration filter)."""
    total = 0
    for t in tools:
        schema = t.get("inputSchema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, TypeError):
                continue
        props = schema.get("properties") or {}
        total += len(props)
    return total


MIN_SCHEMA_FIELDS = 3


def run_validation(corpus_dir: Path) -> dict[str, Any]:
    store = ManifestStore(data_dir=corpus_dir)
    servers = store.list_servers()

    server_tools: dict[str, list[dict[str, Any]]] = {}
    for s in servers:
        tools = store.get_tools(s)
        if tools and _field_count(tools) >= MIN_SCHEMA_FIELDS:
            server_tools[s] = tools

    real_servers = sorted(server_tools.keys())
    n_pairs = len(real_servers) * (len(real_servers) - 1) // 2
    print(f"Servers with >= {MIN_SCHEMA_FIELDS} schema fields: {len(real_servers)}")
    print(f"Pairwise compositions to scan: {n_pairs}")
    print()

    total_contradictions = 0
    total_agreements = 0
    total_homonyms = 0
    total_synonyms = 0
    total_blind_spots = 0
    total_coherence_fee = 0

    contradiction_details: list[dict[str, Any]] = []
    dimension_counter: Counter[str] = Counter()
    mismatch_type_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()

    compositions_with_contradictions = 0
    compositions_with_blind_spots = 0
    compositions_scanned = 0

    for a, b in itertools.combinations(real_servers, 2):
        tools_a = server_tools[a]
        tools_b = server_tools[b]

        prefixed: list[dict[str, Any]] = []
        for t in tools_a:
            d = dict(t)
            d["name"] = f"{a}__{t['name']}"
            prefixed.append(d)
        for t in tools_b:
            d = dict(t)
            d["name"] = f"{b}__{t['name']}"
            prefixed.append(d)

        guard = BullaGuard.from_tools_list(prefixed, name=f"{a}+{b}")
        diag = guard.diagnose()
        struct = guard.structural_diagnostic
        compositions_scanned += 1

        if diag.blind_spots:
            compositions_with_blind_spots += 1
            total_blind_spots += len(diag.blind_spots)
            total_coherence_fee += diag.coherence_fee

        if struct is not None:
            for overlap in struct.overlaps:
                category_counter[overlap.category] += 1
            if struct.contradictions:
                compositions_with_contradictions += 1
            total_contradictions += len(struct.contradictions)
            total_agreements += sum(
                1 for o in struct.overlaps if o.category == "agreement"
            )
            total_homonyms += sum(
                1 for o in struct.overlaps if o.category == "homonym"
            )
            total_synonyms += sum(
                1 for o in struct.overlaps if o.category == "synonym"
            )

            for c in struct.contradictions:
                mismatch_type_counter[c.mismatch_type] += 1
                contradiction_details.append({
                    "composition": f"{a}+{b}",
                    "field_a": c.field_a,
                    "field_b": c.field_b,
                    "tool_a": c.tool_a,
                    "tool_b": c.tool_b,
                    "mismatch_type": c.mismatch_type,
                    "severity": c.severity,
                    "details": c.details,
                })

    print("=" * 70)
    print("STRUCTURAL VALIDATION RESULTS")
    print("=" * 70)
    print()
    print(f"Compositions scanned:              {compositions_scanned}")
    print(f"  with blind spots (pack-based):   {compositions_with_blind_spots}")
    print(f"  with contradictions (structural): {compositions_with_contradictions}")
    print()
    print("--- Pack-based (coboundary) ---")
    print(f"Total blind spots:        {total_blind_spots}")
    print(f"Total coherence fee:      {total_coherence_fee}")
    print()
    print("--- Structural (schema comparison) ---")
    print(f"Total overlaps found:")
    for cat, count in sorted(category_counter.items()):
        print(f"  {cat:20s}: {count}")
    print(f"Total contradictions:     {total_contradictions}")
    print(f"Total agreements:         {total_agreements}")
    print(f"Total homonyms:           {total_homonyms}")
    print(f"Total synonyms:           {total_synonyms}")
    print()
    print("--- Contradiction breakdown by mismatch type ---")
    for mtype, count in mismatch_type_counter.most_common():
        print(f"  {mtype:10s}: {count}")
    print()

    if contradiction_details:
        print("--- Sample contradictions (first 20) ---")
        for cd in contradiction_details[:20]:
            print(
                f"  [{cd['composition']}] "
                f"{cd['field_a']}({cd['tool_a']}) vs "
                f"{cd['field_b']}({cd['tool_b']}): "
                f"{cd['mismatch_type']} — {cd['details']}"
            )
        if len(contradiction_details) > 20:
            print(f"  ... ({len(contradiction_details) - 20} more)")

    result = {
        "n_servers": len(real_servers),
        "n_compositions": compositions_scanned,
        "n_with_blind_spots": compositions_with_blind_spots,
        "n_with_contradictions": compositions_with_contradictions,
        "total_blind_spots": total_blind_spots,
        "total_coherence_fee": total_coherence_fee,
        "total_contradictions": total_contradictions,
        "total_agreements": total_agreements,
        "total_homonyms": total_homonyms,
        "total_synonyms": total_synonyms,
        "category_counts": dict(category_counter),
        "mismatch_type_counts": dict(mismatch_type_counter),
        "contradiction_details": contradiction_details,
    }

    report_path = corpus_dir / "report" / "structural_validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2))
    print(f"\nFull results written to: {report_path}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Structural validation")
    parser.add_argument(
        "--corpus",
        default="calibration/data/registry",
        help="Path to corpus directory",
    )
    args = parser.parse_args()
    run_validation(Path(args.corpus))


if __name__ == "__main__":
    main()
