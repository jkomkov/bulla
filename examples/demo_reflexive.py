#!/usr/bin/env python3
"""Live reflexive demo: agent witnesses its own composition.

Full v0.9 witness flow:
  1. Parse a composition with a blind spot
  2. Diagnose (measurement layer)
  3. Witness with WitnessBasis and active_packs
  4. Auto-bridge, re-witness with parent_receipt_hash (receipt chain)
  5. Verify chain with verify_receipt_consistency + verify_receipt_integrity
  6. Demonstrate max_unknown policy changing disposition

Run from repo root:
  python examples/demo_reflexive.py

Or after install:
  pip install -e . && python examples/demo_reflexive.py
"""

from __future__ import annotations

import yaml

from bulla import (
    PackRef,
    PolicyProfile,
    WitnessBasis,
    diagnose,
    load_composition,
    verify_receipt_consistency,
    verify_receipt_integrity,
    witness,
)

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


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


print("=" * 60)
print("  bulla v0.9 — reflexive witness demo")
print("=" * 60)

# ── 1. Parse and diagnose ────────────────────────────────────────────

comp = load_composition(text=COMPOSITION_YAML)
diag = diagnose(comp)

section("1. Composition & Diagnostic")
print(f"  Composition: {comp.name}")
print(f"  Tools: {len(comp.tools)}, Edges: {len(comp.edges)}")
print(f"  Hash: {comp.canonical_hash()[:16]}...")
print(f"  Fee: {diag.coherence_fee}, Blind spots: {len(diag.blind_spots)}")
for bs in diag.blind_spots:
    print(f"    ▸ {bs.dimension} on {bs.edge}")

# ── 2. Witness with provenance ───────────────────────────────────────

basis = WitnessBasis(declared=1, inferred=0, unknown=1)
packs = (
    PackRef(name="base", version="0.1.0", hash="demo"),
    PackRef(name="financial", version="0.1.0", hash="demo"),
)

receipt = witness(
    diag, comp,
    witness_basis=basis,
    active_packs=packs,
)

section("2. Original Witness Receipt")
print(f"  Disposition: {receipt.disposition.value}")
print(f"  Receipt hash: {receipt.receipt_hash[:16]}...")
print(f"  Basis: declared={basis.declared} inferred={basis.inferred} unknown={basis.unknown}")
print(f"  Active packs: {[p.name for p in receipt.active_packs]}")
print(f"  Patches proposed: {len(receipt.patches)}")

# ── 3. Auto-bridge and re-witness with receipt chain ─────────────────

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
patched_receipt = witness(
    patched_diag, patched_comp,
    parent_receipt_hash=receipt.receipt_hash,
    witness_basis=WitnessBasis(declared=2, inferred=0, unknown=0),
    active_packs=packs,
)

section("3. Patched Witness Receipt (chained)")
print(f"  Disposition: {patched_receipt.disposition.value}")
print(f"  Fee: {patched_receipt.fee} (was {receipt.fee})")
print(f"  Blind spots: {patched_receipt.blind_spots_count} (was {receipt.blind_spots_count})")
print(f"  Parent hash: {patched_receipt.parent_receipt_hashes[0][:16]}...")
print(f"  Chain valid: {patched_receipt.parent_receipt_hashes == (receipt.receipt_hash,)}")

# ── 4. Verification ──────────────────────────────────────────────────

section("4. Receipt Verification")

ok, violations = verify_receipt_consistency(receipt, comp, diag)
print(f"  Original consistency: {'PASS' if ok else 'FAIL ' + str(violations)}")

ok2, violations2 = verify_receipt_consistency(patched_receipt, patched_comp, patched_diag)
print(f"  Patched consistency:  {'PASS' if ok2 else 'FAIL ' + str(violations2)}")

int_ok = verify_receipt_integrity(receipt.to_dict())
print(f"  Original integrity:   {'PASS' if int_ok else 'FAIL'}")

int_ok2 = verify_receipt_integrity(patched_receipt.to_dict())
print(f"  Patched integrity:    {'PASS' if int_ok2 else 'FAIL'}")

tampered = receipt.to_dict()
tampered["fee"] = 999
int_fail = verify_receipt_integrity(tampered)
print(f"  Tampered integrity:   {'REJECTED' if not int_fail else 'BUG'}")

# ── 5. Policy: max_unknown changes disposition ───────────────────────

section("5. Policy Sensitivity (max_unknown)")

strict = PolicyProfile(name="strict", max_unknown=0)
r_strict = witness(diag, comp, witness_basis=basis, policy_profile=strict)
print(f"  max_unknown=0, basis.unknown={basis.unknown}")
print(f"  Disposition: {r_strict.disposition.value}")
assert r_strict.disposition.value == "refuse_pending_disclosure"

relaxed = PolicyProfile(name="relaxed", max_unknown=5)
r_relaxed = witness(diag, comp, witness_basis=basis, policy_profile=relaxed)
print(f"  max_unknown=5, basis.unknown={basis.unknown}")
print(f"  Disposition: {r_relaxed.disposition.value}")

# ── Done ──────────────────────────────────────────────────────────────

assert ok and ok2 and int_ok and int_ok2 and not int_fail

print(f"\n{'=' * 60}")
print("  Demo complete. Full witness flow exercised:")
print("  diagnose → witness → bridge → chain → verify → policy")
print(f"{'=' * 60}")
