#!/usr/bin/env python3
"""Live reflexive demo: agent witnesses its own composition.

Shows the full bulla pipeline without any MCP transport:
  1. Parse a composition from YAML text
  2. Diagnose blind spots (Layer A: measurement)
  3. Witness the diagnostic (Layer B: binding + Layer C: judgment)
  4. Auto-bridge and re-witness (dual-receipt flow)
  5. Verify hash integrity across all three boundaries

Run:
  python examples/demo_reflexive.py
"""

from __future__ import annotations

from bulla import (
    DEFAULT_POLICY_PROFILE,
    diagnose,
    load_composition,
    witness,
)

# ── 1. Define a composition with a blind spot ────────────────────────

COMPOSITION_YAML = """\
name: invoice-pipeline
tools:
  parser:
    internal_state: [raw_text, amount, currency_code]
    observable_schema: [amount]
  settlement:
    internal_state: [amount, currency_unit, ledger_id]
    observable_schema: [amount]
edges:
  - from: parser
    to: settlement
    dimensions:
      - name: currency
        from_field: currency_code
        to_field: currency_unit
"""

print("=" * 60)
print("bulla reflexive demo")
print("=" * 60)

# ── 2. Parse and diagnose ────────────────────────────────────────────

comp = load_composition(text=COMPOSITION_YAML)
diag = diagnose(comp)

print(f"\nComposition: {comp.name}")
print(f"  Tools: {len(comp.tools)}")
print(f"  Edges: {len(comp.edges)}")
print(f"  Composition hash: {comp.canonical_hash()[:16]}...")
print(f"\nDiagnostic:")
print(f"  Coherence fee: {diag.coherence_fee}")
print(f"  Blind spots: {len(diag.blind_spots)}")
for bs in diag.blind_spots:
    print(f"    - {bs.dimension} on {bs.edge}")
    print(f"      from_field={bs.from_field} (hidden={bs.from_hidden})")
    print(f"      to_field={bs.to_field} (hidden={bs.to_hidden})")

# ── 3. Witness the original composition ──────────────────────────────

receipt = witness(diag, comp)

print(f"\nWitness Receipt (original):")
print(f"  Disposition: {receipt.disposition.value}")
print(f"  Receipt hash: {receipt.receipt_hash[:16]}...")
print(f"  Diagnostic hash: {receipt.diagnostic_hash[:16]}...")
print(f"  Policy: {receipt.policy_profile.name}")
print(f"  Patches proposed: {len(receipt.patches)}")
for p in receipt.patches:
    patch = p.to_bulla_patch()
    print(f"    - {patch['action']} {patch['field']} in {patch['target_tool']}")
    print(f"      eliminates: {patch['eliminates']}")

# ── 4. Auto-bridge: apply patches, re-diagnose, re-witness ──────────

import yaml

raw = yaml.safe_load(COMPOSITION_YAML)
tools_section = raw.get("tools", {})
for br in diag.bridges:
    for tool_name in br.add_to:
        if tool_name in tools_section:
            tool = tools_section[tool_name]
            internal = tool.get("internal_state", [])
            obs = tool.get("observable_schema", [])
            if br.field not in internal:
                internal.append(br.field)
            if br.field not in obs:
                obs.append(br.field)

patched_yaml = yaml.dump(raw, default_flow_style=False, sort_keys=False)
patched_comp = load_composition(text=patched_yaml)
patched_diag = diagnose(patched_comp)
patched_receipt = witness(patched_diag, patched_comp)

print(f"\nWitness Receipt (after bridge):")
print(f"  Disposition: {patched_receipt.disposition.value}")
print(f"  Receipt hash: {patched_receipt.receipt_hash[:16]}...")
print(f"  Blind spots: {patched_receipt.blind_spots_count}")
print(f"  Fee: {patched_receipt.fee}")

# ── 5. Verify hash integrity ────────────────────────────────────────

print(f"\nHash integrity:")
print(f"  Original composition hash:  {receipt.composition_hash[:16]}...")
print(f"  Patched composition hash:   {patched_receipt.composition_hash[:16]}...")
assert receipt.composition_hash != patched_receipt.composition_hash
print(f"  Composition hashes differ:  YES (bridge changed structure)")

print(f"  Diagnostic hash (original): {receipt.diagnostic_hash[:16]}...")
print(f"  Diagnostic hash (patched):  {patched_receipt.diagnostic_hash[:16]}...")
assert receipt.diagnostic_hash != patched_receipt.diagnostic_hash
print(f"  Diagnostic hashes differ:   YES (measurement changed)")

print(f"  Receipt hash (original):    {receipt.receipt_hash[:16]}...")
print(f"  Receipt hash (patched):     {patched_receipt.receipt_hash[:16]}...")
assert receipt.receipt_hash != patched_receipt.receipt_hash
print(f"  Receipt hashes differ:      YES (different events)")

# Three-hash boundary: all six hashes are distinct
all_hashes = {
    receipt.composition_hash,
    receipt.diagnostic_hash,
    receipt.receipt_hash,
    patched_receipt.composition_hash,
    patched_receipt.diagnostic_hash,
    patched_receipt.receipt_hash,
}
assert len(all_hashes) == 6
print(f"  All 6 hashes unique:        YES")

print(f"\n{'=' * 60}")
print(f"Demo complete. The agent observed a blind spot,")
print(f"applied a bridge, and verified the repair — all")
print(f"with content-addressable, tamper-evident receipts.")
print(f"{'=' * 60}")
