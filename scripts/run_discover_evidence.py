#!/usr/bin/env python3
"""Run bulla discover on the 4-server manifests and produce before/after comparison.

Usage:
    # With a real LLM (requires OPENAI_API_KEY or ANTHROPIC_API_KEY):
    python scripts/run_discover_evidence.py --live

    # With mock adapter (demonstrates the full loop without API keys):
    python scripts/run_discover_evidence.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bulla.cli import _load_manifests_dir
from bulla.diagnostic import decompose_fee, diagnose, prescriptive_disclosure
from bulla.discover.adapter import MockAdapter
from bulla.discover.engine import discover_dimensions
from bulla.guard import BullaGuard
from bulla.infer.classifier import _reset_taxonomy_cache, configure_packs

MANIFESTS_DIR = Path(__file__).parent.parent / "examples" / "real_world_audit" / "manifests"
OUTPUT_DIR = Path(__file__).parent.parent / "examples" / "real_world_audit"

PLAUSIBLE_LLM_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_4server_v1"
pack_version: "0.1.0"
dimensions:
  entity_namespace:
    description: "Whether numeric entity identifiers share a global sequence or are scoped per type — GitHub issues and pull requests share a monotonic counter, so issue #7 and PR #7 cannot coexist"
    known_values: ["global_sequence", "per_type_sequence", "uuid", "scoped_auto_increment"]
    field_patterns: ["*_number", "issue_number", "pull_number"]
    description_keywords: ["issue number", "pull request number", "entity number", "PR number"]
    refines: "id_offset"
  content_transport:
    description: "How file or page content is encoded for transport between tools — filesystem tools return raw UTF-8 text while puppeteer screenshot returns base64-encoded data"
    known_values: ["raw_utf8", "base64", "binary_stream"]
    field_patterns: ["*_content", "content", "encoded"]
    description_keywords: ["base64", "encoded", "raw text", "file content", "screenshot"]
    refines: null
  graph_operation_scope:
    description: "Whether a knowledge graph mutation operates on single entities or batches — memory server accepts arrays of entities/relations while other tools operate on single items"
    known_values: ["single", "batch", "streaming"]
    field_patterns: ["entities", "relations", "observations"]
    description_keywords: ["batch", "multiple entities", "array of"]
    refines: null
---END_PACK---"""


def load_all_tools() -> tuple[list[dict], list[str]]:
    return _load_manifests_dir(MANIFESTS_DIR)


def run_baseline(all_tools: list[dict], server_names: list[str]) -> dict:
    _reset_taxonomy_cache()
    configure_packs()
    guard = BullaGuard.from_tools_list(all_tools, name="audit-baseline")
    comp = guard.composition
    diag = diagnose(comp)

    partition = []
    tool_to_server = {t.name: t.name.split("__")[0] for t in comp.tools}
    for sname in server_names:
        group = frozenset(n for n, s in tool_to_server.items() if s == sname)
        if group:
            partition.append(group)
    decomp = decompose_fee(comp, partition) if len(partition) > 1 else None

    dims_matched = set()
    for bs in diag.blind_spots:
        dims_matched.add(bs.dimension.replace("_match", ""))

    return {
        "fee": diag.coherence_fee,
        "blind_spots": len(diag.blind_spots),
        "boundary_fee": decomp.boundary_fee if decomp else 0,
        "dimensions_active": sorted(dims_matched),
        "n_tools": diag.n_tools,
        "n_edges": diag.n_edges,
    }


def run_with_discovery(
    all_tools: list[dict],
    server_names: list[str],
    pack_path: Path,
) -> dict:
    _reset_taxonomy_cache()
    configure_packs(extra_paths=[pack_path])
    guard = BullaGuard.from_tools_list(all_tools, name="audit-discovered")
    comp = guard.composition
    diag = diagnose(comp)

    partition = []
    tool_to_server = {t.name: t.name.split("__")[0] for t in comp.tools}
    for sname in server_names:
        group = frozenset(n for n, s in tool_to_server.items() if s == sname)
        if group:
            partition.append(group)
    decomp = decompose_fee(comp, partition) if len(partition) > 1 else None

    dims_matched = set()
    for bs in diag.blind_spots:
        dims_matched.add(bs.dimension.replace("_match", ""))

    return {
        "fee": diag.coherence_fee,
        "blind_spots": len(diag.blind_spots),
        "boundary_fee": decomp.boundary_fee if decomp else 0,
        "dimensions_active": sorted(dims_matched),
        "n_tools": diag.n_tools,
        "n_edges": diag.n_edges,
    }


def main():
    parser = argparse.ArgumentParser(description="Run discovery evidence comparison")
    parser.add_argument("--live", action="store_true", help="Use real LLM (requires API key)")
    args = parser.parse_args()

    all_tools, server_names = load_all_tools()
    print(f"Loaded {len(all_tools)} tools from {len(server_names)} servers: {server_names}")

    # Baseline
    baseline = run_baseline(list(all_tools), server_names)
    print(f"\n=== BASELINE (base pack only) ===")
    print(f"  Fee:           {baseline['fee']}")
    print(f"  Blind spots:   {baseline['blind_spots']}")
    print(f"  Boundary fee:  {baseline['boundary_fee']}")
    print(f"  Dimensions:    {baseline['dimensions_active']}")

    # Discovery
    if args.live:
        result = discover_dimensions(all_tools)
    else:
        adapter = MockAdapter(PLAUSIBLE_LLM_RESPONSE)
        result = discover_dimensions(all_tools, adapter=adapter)

    if not result.valid:
        print(f"\nDiscovery failed: {result.errors}")
        sys.exit(1)

    print(f"\n=== DISCOVERY RESULTS ===")
    print(f"  Dimensions found: {result.n_dimensions}")
    for dim_name, dim_def in result.pack.get("dimensions", {}).items():
        refines = dim_def.get("refines")
        ref_str = f" (refines {refines})" if refines else ""
        print(f"    - {dim_name}: {dim_def['description'][:70]}{ref_str}")

    # Write discovered pack
    pack_path = OUTPUT_DIR / "discovered_pack.yaml"
    pack_path.write_text(
        yaml.dump(result.pack, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"\n  Wrote discovered pack to {pack_path}")

    # Save raw response
    raw_path = OUTPUT_DIR / "discovered_pack.raw.txt"
    raw_path.write_text(result.raw_response, encoding="utf-8")

    # Run audit with discovery
    discovered = run_with_discovery(list(all_tools), server_names, pack_path)
    print(f"\n=== WITH DISCOVERED PACK ===")
    print(f"  Fee:           {discovered['fee']}")
    print(f"  Blind spots:   {discovered['blind_spots']}")
    print(f"  Boundary fee:  {discovered['boundary_fee']}")
    print(f"  Dimensions:    {discovered['dimensions_active']}")

    # Comparison
    print(f"\n=== COMPARISON ===")
    print(f"  {'Metric':<20s} {'Baseline':>10s} {'Discovered':>10s} {'Delta':>10s}")
    print(f"  {'─'*20} {'─'*10} {'─'*10} {'─'*10}")
    for metric in ["fee", "blind_spots", "boundary_fee"]:
        b = baseline[metric]
        d = discovered[metric]
        delta = d - b
        sign = "+" if delta > 0 else ""
        print(f"  {metric:<20s} {b:>10d} {d:>10d} {sign}{delta:>9d}")
    print(f"  {'dims_active':<20s} {len(baseline['dimensions_active']):>10d} "
          f"{len(discovered['dimensions_active']):>10d} "
          f"{'+' if len(discovered['dimensions_active']) > len(baseline['dimensions_active']) else ''}"
          f"{len(discovered['dimensions_active']) - len(baseline['dimensions_active']):>9d}")

    new_dims = set(discovered["dimensions_active"]) - set(baseline["dimensions_active"])
    if new_dims:
        print(f"\n  New dimensions from discovery: {sorted(new_dims)}")

    _reset_taxonomy_cache()


if __name__ == "__main__":
    main()
