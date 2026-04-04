#!/usr/bin/env python3
"""Diamond demo: multi-agent vocabulary convergence via receipt DAG.

Demonstrates the convergence protocol:
  Agent A: audits {filesystem, github}, discovers dims_a, produces receipt_a
  Agent C: audits {github, puppeteer}, discovers dims_c, produces receipt_c (independent)
  Agent D: merges receipt_a + receipt_c, re-audits with merged vocabulary

Mock responses are designed with adversarial overlap:
  Agent A discovers pagination_base (refines id_offset, field_patterns: *_page, *_offset)
  Agent C discovers page_index_origin (refines id_offset, field_patterns: *_page, *_index)
  Overlap on *_page is detected; precedence order resolves which survives.

Usage:
  python scripts/run_diamond_demo.py              # mock adapter (reproducible)
  python scripts/run_diamond_demo.py --live       # real LLM via env API key
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml


MOCK_AGENT_A_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_agent_a"
pack_version: "0.1.0"
dimensions:
  pagination_base:
    description: "Whether page numbering starts at 0 or 1"
    known_values: ["zero_based", "one_based"]
    field_patterns: ["*_page", "*_offset"]
    description_keywords: ["page number", "pagination"]
    refines: "id_offset"
  path_context:
    description: "Whether file paths are absolute or relative to a working directory"
    known_values: ["absolute", "relative", "uri"]
    field_patterns: ["*_path", "*_file"]
    description_keywords: ["file path", "directory"]
    refines: "path_convention"
---END_PACK---"""

MOCK_AGENT_C_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_agent_c"
pack_version: "0.1.0"
dimensions:
  page_index_origin:
    description: "Whether page/index values start at 0 or 1"
    known_values: ["zero_based", "one_based"]
    field_patterns: ["*_page", "*_index"]
    description_keywords: ["page", "index", "offset"]
    refines: "id_offset"
  selector_syntax:
    description: "Whether element selectors use CSS, XPath, or accessibility labels"
    known_values: ["css", "xpath", "aria_label", "test_id"]
    field_patterns: ["*_selector", "*_element"]
    description_keywords: ["selector", "element", "CSS selector"]
    refines: null
---END_PACK---"""


TOOLS_FILESYSTEM = [
    {"name": "read_file", "description": "Read the contents of a file",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute file path to read"}}}},
    {"name": "write_file", "description": "Write content to a file",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute file path"},
         "content": {"type": "string", "description": "File content to write"}}}},
    {"name": "list_directory", "description": "List directory contents",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Directory path to list"}}}},
]

TOOLS_GITHUB = [
    {"name": "list_issues", "description": "List issues in a repository",
     "inputSchema": {"type": "object", "properties": {
         "owner": {"type": "string"}, "repo": {"type": "string"},
         "page": {"type": "integer", "description": "Page number (default: 1)"},
         "per_page": {"type": "integer", "description": "Results per page (max 100)"}}}},
    {"name": "get_pull_request", "description": "Get a specific pull request",
     "inputSchema": {"type": "object", "properties": {
         "owner": {"type": "string"}, "repo": {"type": "string"},
         "pull_number": {"type": "integer", "description": "Pull request number"}}}},
    {"name": "search_repositories", "description": "Search for repositories",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "page": {"type": "integer"},
         "sort": {"type": "string", "description": "Sort field: stars, forks, updated"}}}},
]

TOOLS_PUPPETEER = [
    {"name": "navigate", "description": "Navigate to a URL",
     "inputSchema": {"type": "object", "properties": {
         "url": {"type": "string", "description": "URL to navigate to"}}}},
    {"name": "click", "description": "Click on an element",
     "inputSchema": {"type": "object", "properties": {
         "selector": {"type": "string", "description": "CSS selector of element to click"},
         "x": {"type": "integer", "description": "X coordinate to click"},
         "y": {"type": "integer", "description": "Y coordinate to click"}}}},
    {"name": "screenshot", "description": "Take a screenshot of the page",
     "inputSchema": {"type": "object", "properties": {
         "width": {"type": "integer", "description": "Viewport width in pixels"},
         "height": {"type": "integer", "description": "Viewport height in pixels"},
         "full_page": {"type": "boolean", "description": "Capture full page (default: false)"}}}},
]


def _prefix_tools(tools: list[dict], server_name: str) -> list[dict]:
    out = []
    for t in tools:
        t2 = dict(t)
        t2["name"] = f"{server_name}__{t2['name']}"
        out.append(t2)
    return out


def run_demo(live: bool = False) -> None:
    from bulla.discover.adapter import MockAdapter, get_adapter
    from bulla.discover.engine import discover_dimensions
    from bulla.diagnostic import decompose_fee, diagnose, prescriptive_disclosure
    from bulla.guard import BullaGuard
    from bulla.infer.classifier import configure_packs, get_active_pack_refs, _reset_taxonomy_cache
    from bulla.merge import merge_receipt_vocabularies
    from bulla.witness import verify_receipt_integrity, witness

    _reset_taxonomy_cache()
    tmpdir = Path(tempfile.mkdtemp(prefix="bulla_diamond_demo_"))

    print("=" * 70)
    print("  Bulla v0.24.0 — Diamond DAG Demo (Vocabulary Convergence)")
    print("=" * 70)
    print()

    # ── Agent A: filesystem + github ──────────────────────────────────
    print("─" * 70)
    print("  AGENT A: filesystem + github")
    print("─" * 70)

    tools_a = (
        _prefix_tools(TOOLS_FILESYSTEM, "filesystem")
        + _prefix_tools(TOOLS_GITHUB, "github")
    )

    adapter_a = get_adapter() if live else MockAdapter(MOCK_AGENT_A_RESPONSE)

    _reset_taxonomy_cache()
    disc_a = discover_dimensions(tools_a, adapter=adapter_a)
    print(f"  Discovered {disc_a.n_dimensions} dimension(s):")
    for dim_name, dim_def in disc_a.pack.get("dimensions", {}).items():
        refines = dim_def.get("refines")
        pats = dim_def.get("field_patterns", [])
        ref_str = f" (refines {refines})" if refines else ""
        print(f"    - {dim_name}{ref_str}  patterns={pats}")
    print()

    pack_a_path = tmpdir / "agent_a.yaml"
    pack_a_path.write_text(yaml.dump(disc_a.pack, default_flow_style=False, sort_keys=False))
    configure_packs(extra_paths=[pack_a_path])

    guard_a = BullaGuard.from_tools_list(tools_a, name="agent-a")
    diag_a = guard_a.diagnose()
    basis_a = guard_a.witness_basis

    receipt_a = witness(
        diag_a, guard_a.composition,
        witness_basis=basis_a,
        active_packs=get_active_pack_refs(),
        inline_dimensions=disc_a.pack if disc_a.valid and disc_a.n_dimensions > 0 else None,
    )
    receipt_a_dict = receipt_a.to_dict()
    receipt_a_path = tmpdir / "receipt_a.json"
    receipt_a_path.write_text(json.dumps(receipt_a_dict, indent=2))

    print(f"  Fee: {diag_a.coherence_fee}")
    print(f"  Blind spots: {len(diag_a.blind_spots)}")
    print(f"  Receipt hash: {receipt_a.receipt_hash[:16]}...")
    print()

    # ── Agent C: github + puppeteer (independent) ─────────────────────
    print("─" * 70)
    print("  AGENT C: github + puppeteer (independent)")
    print("─" * 70)

    tools_c = (
        _prefix_tools(TOOLS_GITHUB, "github")
        + _prefix_tools(TOOLS_PUPPETEER, "puppeteer")
    )

    adapter_c = get_adapter() if live else MockAdapter(MOCK_AGENT_C_RESPONSE)

    _reset_taxonomy_cache()
    disc_c = discover_dimensions(tools_c, adapter=adapter_c)
    print(f"  Discovered {disc_c.n_dimensions} dimension(s):")
    for dim_name, dim_def in disc_c.pack.get("dimensions", {}).items():
        refines = dim_def.get("refines")
        pats = dim_def.get("field_patterns", [])
        ref_str = f" (refines {refines})" if refines else ""
        print(f"    - {dim_name}{ref_str}  patterns={pats}")
    print()

    pack_c_path = tmpdir / "agent_c.yaml"
    pack_c_path.write_text(yaml.dump(disc_c.pack, default_flow_style=False, sort_keys=False))
    configure_packs(extra_paths=[pack_c_path])

    guard_c = BullaGuard.from_tools_list(tools_c, name="agent-c")
    diag_c = guard_c.diagnose()
    basis_c = guard_c.witness_basis

    receipt_c = witness(
        diag_c, guard_c.composition,
        witness_basis=basis_c,
        active_packs=get_active_pack_refs(),
        inline_dimensions=disc_c.pack if disc_c.valid and disc_c.n_dimensions > 0 else None,
    )
    receipt_c_dict = receipt_c.to_dict()
    receipt_c_path = tmpdir / "receipt_c.json"
    receipt_c_path.write_text(json.dumps(receipt_c_dict, indent=2))

    print(f"  Fee: {diag_c.coherence_fee}")
    print(f"  Blind spots: {len(diag_c.blind_spots)}")
    print(f"  Receipt hash: {receipt_c.receipt_hash[:16]}...")
    print()

    # ── Agent D: merge A+C, re-audit with merged vocabulary ───────────
    print("─" * 70)
    print("  AGENT D: merge A + C, re-audit {filesystem, github, puppeteer}")
    print("─" * 70)

    merged_vocab, overlaps = merge_receipt_vocabularies([receipt_a_dict, receipt_c_dict])
    merged_dims = merged_vocab.get("dimensions", {}) if merged_vocab else {}

    print(f"  Merge: {len(merged_dims)} dims from 2 receipts, {len(overlaps)} overlap(s) detected")
    for o in overlaps:
        pats = ", ".join(o.shared_patterns)
        print(f"    {o.dim_a} <-> {o.dim_b}: field_patterns intersect on {pats}")
    print()

    merged_receipt_path = tmpdir / "merged.json"
    from bulla.model import Disposition, DEFAULT_POLICY_PROFILE, WitnessReceipt
    from bulla import __version__ as kver
    from datetime import datetime, timezone

    merge_receipt = WitnessReceipt(
        receipt_version="0.1.0",
        kernel_version=kver,
        composition_hash="no_composition",
        diagnostic_hash="no_diagnostic",
        policy_profile=DEFAULT_POLICY_PROFILE,
        fee=0,
        blind_spots_count=0,
        bridges_required=0,
        unknown_dimensions=0,
        disposition=Disposition.PROCEED,
        timestamp=datetime.now(timezone.utc).isoformat(),
        parent_receipt_hashes=(receipt_a.receipt_hash, receipt_c.receipt_hash),
        inline_dimensions=merged_vocab,
    )
    merged_receipt_dict = merge_receipt.to_dict()
    merged_receipt_path.write_text(json.dumps(merged_receipt_dict, indent=2))

    # Re-audit with merged vocabulary (all three server sets)
    _reset_taxonomy_cache()
    merged_pack_path = tmpdir / "merged_vocab.yaml"
    merged_pack_path.write_text(yaml.dump(merged_vocab, default_flow_style=False, sort_keys=False))
    configure_packs(extra_paths=[merged_pack_path])

    tools_d = (
        _prefix_tools(TOOLS_FILESYSTEM, "filesystem")
        + _prefix_tools(TOOLS_GITHUB, "github")
        + _prefix_tools(TOOLS_PUPPETEER, "puppeteer")
    )

    guard_d = BullaGuard.from_tools_list(tools_d, name="agent-d")
    diag_d = guard_d.diagnose()
    basis_d = guard_d.witness_basis

    receipt_d = witness(
        diag_d, guard_d.composition,
        witness_basis=basis_d,
        active_packs=get_active_pack_refs(),
        parent_receipt_hashes=(receipt_a.receipt_hash, receipt_c.receipt_hash),
        inline_dimensions=merged_vocab,
    )

    print(f"  Agent D (re-audit with merged vocabulary):")
    print(f"    Fee: {diag_d.coherence_fee}")
    print(f"    Blind spots: {len(diag_d.blind_spots)}")
    print(f"    Receipt hash: {receipt_d.receipt_hash[:16]}...")
    print(f"    Parents: {receipt_d.parent_receipt_hashes[0][:12]}..., {receipt_d.parent_receipt_hashes[1][:12]}...")
    print()

    # ── Summary ───────────────────────────────────────────────────────
    print("=" * 70)
    print("  CONVERGENCE SUMMARY")
    print("=" * 70)
    print()
    print(f"  Agent A: {disc_a.n_dimensions} discovered, fee={diag_a.coherence_fee}")
    print(f"  Agent C: {disc_c.n_dimensions} discovered, fee={diag_c.coherence_fee}")
    print(f"  Agent D (merged): {len(merged_dims)} unique dims ({len(overlaps)} overlap), fee={diag_d.coherence_fee}")
    print()
    print(f"  Convergence delta: fee_A={diag_a.coherence_fee}, fee_C={diag_c.coherence_fee}, fee_merged={diag_d.coherence_fee}")
    print(f"  DAG: receipt_A + receipt_C -> receipt_D")
    print()

    a_valid = verify_receipt_integrity(receipt_a_dict)
    c_valid = verify_receipt_integrity(receipt_c_dict)
    d_valid = verify_receipt_integrity(receipt_d.to_dict())
    merge_valid = verify_receipt_integrity(merged_receipt_dict)
    print(f"  Receipt A integrity: {'VALID' if a_valid else 'BROKEN'}")
    print(f"  Receipt C integrity: {'VALID' if c_valid else 'BROKEN'}")
    print(f"  Receipt D integrity: {'VALID' if d_valid else 'BROKEN'}")
    print(f"  Merge receipt integrity: {'VALID' if merge_valid else 'BROKEN'}")
    print()

    shutil.rmtree(tmpdir, ignore_errors=True)
    _reset_taxonomy_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulla diamond DAG demo")
    parser.add_argument("--live", action="store_true", help="Use real LLM (requires API key)")
    args = parser.parse_args()
    run_demo(live=args.live)


if __name__ == "__main__":
    main()
