#!/usr/bin/env python3
"""Two-agent chain demo: vocabulary growth + receipt chaining.

Demonstrates the full SCPI coordination loop:
  Agent A: audits servers {filesystem, github}, discovers dimensions, produces receipt A
  Agent B: audits servers {github, puppeteer}, chains receipt A, discovers more, produces receipt B

Usage:
  python scripts/run_chain_demo.py              # mock adapter (reproducible)
  python scripts/run_chain_demo.py --live       # real LLM via env API key
"""
from __future__ import annotations

import argparse
import json
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
  entity_namespace:
    description: "Whether numeric entity IDs share a global sequence or are scoped per type"
    known_values: ["global_sequence", "per_type_sequence", "uuid"]
    field_patterns: ["*_number", "*_id"]
    description_keywords: ["issue number", "pull request number"]
    refines: "id_offset"
  boolean_default:
    description: "Whether boolean parameters default to true or false when omitted"
    known_values: ["true_default", "false_default", "required"]
    field_patterns: ["*_enabled", "*_flag"]
    description_keywords: ["default", "enabled", "flag"]
    refines: "null_handling"
---END_PACK---"""

MOCK_AGENT_B_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_agent_b"
pack_version: "0.1.0"
dimensions:
  viewport_unit:
    description: "Whether pixel coordinates are physical pixels or CSS pixels"
    known_values: ["physical", "css", "percentage"]
    field_patterns: ["*_x", "*_y", "*_width", "*_height"]
    description_keywords: ["viewport", "pixel", "coordinate"]
    refines: null
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
    for t in tools:
        t["name"] = f"{server_name}__{t['name']}"
    return tools


def run_demo(live: bool = False) -> None:
    from bulla.discover.adapter import MockAdapter, get_adapter
    from bulla.discover.engine import discover_dimensions
    from bulla.diagnostic import decompose_fee, diagnose, prescriptive_disclosure
    from bulla.guard import BullaGuard
    from bulla.infer.classifier import configure_packs, get_active_pack_refs, _reset_taxonomy_cache
    from bulla.witness import witness

    _reset_taxonomy_cache()

    print("=" * 70)
    print("  Bulla v0.23.0 — Two-Agent Chain Demo")
    print("=" * 70)
    print()

    # ── Agent A: filesystem + github ──────────────────────────────────
    print("─" * 70)
    print("  AGENT A: filesystem + github")
    print("─" * 70)

    tools_a = (
        _prefix_tools([dict(t) for t in TOOLS_FILESYSTEM], "filesystem")
        + _prefix_tools([dict(t) for t in TOOLS_GITHUB], "github")
    )

    if live:
        adapter_a = get_adapter()
    else:
        adapter_a = MockAdapter(MOCK_AGENT_A_RESPONSE)

    _reset_taxonomy_cache()
    disc_a = discover_dimensions(tools_a, adapter=adapter_a)
    print(f"  Discovered {disc_a.n_dimensions} dimension(s):")
    for dim_name, dim_def in disc_a.pack.get("dimensions", {}).items():
        refines = dim_def.get("refines")
        ref_str = f" (refines {refines})" if refines else ""
        print(f"    - {dim_name}{ref_str}")
    print()

    tmpdir = Path(tempfile.mkdtemp(prefix="bulla_chain_demo_"))
    pack_a_path = tmpdir / "agent_a.yaml"
    pack_a_path.write_text(yaml.dump(disc_a.pack, default_flow_style=False, sort_keys=False))
    configure_packs(extra_paths=[pack_a_path])

    guard_a = BullaGuard.from_tools_list(tools_a, name="agent-a")
    diag_a = guard_a.diagnose()
    basis_a = guard_a.witness_basis
    comp_a = guard_a.composition

    server_names_a = ["filesystem", "github"]
    partition_a = []
    for sn in server_names_a:
        tools_in = frozenset(t.name for t in comp_a.tools if t.name.startswith(f"{sn}__"))
        if tools_in:
            partition_a.append(tools_in)
    decomp_a = decompose_fee(comp_a, partition_a) if len(partition_a) > 1 else None

    receipt_a = witness(
        diag_a, comp_a,
        witness_basis=basis_a,
        active_packs=get_active_pack_refs(),
        inline_dimensions=disc_a.pack if disc_a.valid and disc_a.n_dimensions > 0 else None,
    )
    receipt_a_dict = receipt_a.to_dict()

    print(f"  Fee: {diag_a.coherence_fee}")
    print(f"  Blind spots: {len(diag_a.blind_spots)}")
    if decomp_a:
        print(f"  Boundary fee: {decomp_a.boundary_fee}")
    disc_str_a = f", {basis_a.discovered} discovered" if basis_a and basis_a.discovered > 0 else ""
    if basis_a:
        print(f"  Basis: {basis_a.declared} declared, {basis_a.inferred} inferred, {basis_a.unknown} unknown{disc_str_a}")
    print(f"  Receipt hash: {receipt_a.receipt_hash[:16]}...")
    print()

    receipt_a_path = tmpdir / "receipt_a.json"
    receipt_a_path.write_text(json.dumps(receipt_a_dict, indent=2))

    # ── Agent B: github + puppeteer, chaining receipt A ───────────────
    print("─" * 70)
    print("  AGENT B: github + puppeteer (chaining Agent A)")
    print("─" * 70)

    tools_b = (
        _prefix_tools([dict(t) for t in TOOLS_GITHUB], "github")
        + _prefix_tools([dict(t) for t in TOOLS_PUPPETEER], "puppeteer")
    )

    inherited_dims = receipt_a_dict.get("inline_dimensions")
    inherited_pack_path = None
    if inherited_dims:
        inherited_pack_path = tmpdir / "inherited.yaml"
        inherited_pack_path.write_text(yaml.dump(inherited_dims, default_flow_style=False, sort_keys=False))
        print(f"  Inherited {len(inherited_dims.get('dimensions', {}))} dimension(s) from Agent A")

    if live:
        adapter_b = get_adapter()
    else:
        adapter_b = MockAdapter(MOCK_AGENT_B_RESPONSE)

    _reset_taxonomy_cache()
    existing_extra = []
    if inherited_pack_path:
        existing_extra.append(inherited_pack_path)
    disc_b = discover_dimensions(tools_b, adapter=adapter_b, existing_packs=existing_extra or None)
    print(f"  Discovered {disc_b.n_dimensions} new dimension(s):")
    for dim_name, dim_def in disc_b.pack.get("dimensions", {}).items():
        refines = dim_def.get("refines")
        ref_str = f" (refines {refines})" if refines else ""
        print(f"    - {dim_name}{ref_str}")
    print()

    all_extra = list(existing_extra)
    if disc_b.valid and disc_b.n_dimensions > 0:
        pack_b_path = tmpdir / "agent_b.yaml"
        pack_b_path.write_text(yaml.dump(disc_b.pack, default_flow_style=False, sort_keys=False))
        all_extra.append(pack_b_path)

    configure_packs(extra_paths=all_extra if all_extra else None)

    guard_b = BullaGuard.from_tools_list(tools_b, name="agent-b")
    diag_b = guard_b.diagnose()
    basis_b = guard_b.witness_basis
    comp_b = guard_b.composition

    server_names_b = ["github", "puppeteer"]
    partition_b = []
    for sn in server_names_b:
        tools_in = frozenset(t.name for t in comp_b.tools if t.name.startswith(f"{sn}__"))
        if tools_in:
            partition_b.append(tools_in)
    decomp_b = decompose_fee(comp_b, partition_b) if len(partition_b) > 1 else None

    import copy
    inline_b = None
    if inherited_dims and disc_b.valid and disc_b.n_dimensions > 0:
        merged_inline = copy.deepcopy(inherited_dims)
        merged_inline.setdefault("dimensions", {}).update(disc_b.pack.get("dimensions", {}))
        inline_b = merged_inline
    elif inherited_dims:
        inline_b = copy.deepcopy(inherited_dims)
    elif disc_b.valid and disc_b.n_dimensions > 0:
        inline_b = disc_b.pack

    receipt_b = witness(
        diag_b, comp_b,
        witness_basis=basis_b,
        active_packs=get_active_pack_refs(),
        parent_receipt_hash=receipt_a.receipt_hash,
        inline_dimensions=inline_b,
    )

    print(f"  Fee: {diag_b.coherence_fee}")
    print(f"  Blind spots: {len(diag_b.blind_spots)}")
    if decomp_b:
        print(f"  Boundary fee: {decomp_b.boundary_fee}")
    disc_str_b = f", {basis_b.discovered} discovered" if basis_b and basis_b.discovered > 0 else ""
    if basis_b:
        print(f"  Basis: {basis_b.declared} declared, {basis_b.inferred} inferred, {basis_b.unknown} unknown{disc_str_b}")
    print(f"  Receipt hash: {receipt_b.receipt_hash[:16]}...")
    print(f"  Parent hash:  {receipt_b.parent_receipt_hashes[0][:16]}...")
    print()

    # ── Summary ───────────────────────────────────────────────────────
    print("=" * 70)
    print("  COORDINATION LOOP SUMMARY")
    print("=" * 70)
    print()

    a_dims = len(disc_a.pack.get("dimensions", {})) if disc_a.valid else 0
    b_new_dims = len(disc_b.pack.get("dimensions", {})) if disc_b.valid else 0
    inherited_count = len(inherited_dims.get("dimensions", {})) if inherited_dims else 0
    total_dims = inherited_count + b_new_dims

    print(f"  Vocabulary growth: Agent A discovered {a_dims} dims"
          f" -> Agent B inherited {inherited_count}, discovered {b_new_dims} new"
          f" -> total {total_dims}")
    print(f"  Chain: receipt_A (fee={diag_a.coherence_fee})"
          f" -> receipt_B (fee={diag_b.coherence_fee},"
          f" parent={receipt_a.receipt_hash[:12]}...)")
    print()

    chain_valid = receipt_b.parent_receipt_hashes == (receipt_a.receipt_hash,)
    print(f"  Chain integrity: {'VALID' if chain_valid else 'BROKEN'}")

    from bulla.witness import verify_receipt_integrity
    a_valid = verify_receipt_integrity(receipt_a_dict)
    b_valid = verify_receipt_integrity(receipt_b.to_dict())
    print(f"  Receipt A integrity: {'VALID' if a_valid else 'BROKEN'}")
    print(f"  Receipt B integrity: {'VALID' if b_valid else 'BROKEN'}")
    print()

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    _reset_taxonomy_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulla two-agent chain demo")
    parser.add_argument("--live", action="store_true", help="Use real LLM (requires API key)")
    args = parser.parse_args()
    run_demo(live=args.live)


if __name__ == "__main__":
    main()
