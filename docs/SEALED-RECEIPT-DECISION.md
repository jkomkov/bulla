# Sealed-Receipt Decision: Epistemic Receipt

**Decision**: Keep `EpistemicReceipt` outside the sealed `WitnessReceipt` hash contract.

**Date**: 2026-04-24

**Status**: Final for this cycle.

## Context

The epistemic receipt is a narrow product-facing view derived from `RepairGeometry`. It answers: what does Bulla promise here, and with what confidence? Three fields matter: `fee`, `geometry_dividend`, `regime` (exact vs surrogate).

The question: should any of this enter the sealed `WitnessReceipt._hash_input()`?

## Decision rationale

**No.** Four reasons:

1. **The local/session distinction is load-bearing.** `WitnessReceipt` is session-wide and composition-scoped. `EpistemicReceipt` is local to a call cluster within a proxy session. Sealing a local view into a session-level hash would conflate two scopes that are architecturally separated for good reason.

2. **The hash contract stays stable.** Adding fields to `_hash_input()` is a one-way door. Every new conditional field adds a backward-compatibility clause ("pre-vX.Y receipts verify correctly because..."). The current conditional set (`parent_receipt_hashes`, `inline_dimensions`, `boundary_obligations`, `contradictions`, `structural_contradictions`, `unmet_obligations`, `contradiction_score`) is already seven entries. Adding more without a concrete downstream need is complexity debt.

3. **No downstream consumer requires tamper-evident epistemic status.** The epistemic receipt is consumed by the product layer (CLI JSON, SDK callers) to make repair decisions. No current or planned consumer needs to verify "this receipt claimed exact regime" after the fact. If that need arises, the decision can be revisited.

4. **The product can use it immediately without sealing.** The `epistemic_receipt` key appears as a sibling in CLI JSON output. SDK callers access it via `record.local_diagnostic.repair_geometry.epistemic_view()`. Neither path requires hash coverage.

## What would change this decision

- A concrete use case where a downstream agent needs to verify that a prior receipt's epistemic regime was exact (not just that the fee was N).
- A product decision boundary that depends on `geometry_dividend` or `residual_regime` being tamper-evident across a receipt chain.

Neither exists today.

## Architecture invariant

`EpistemicReceipt` is a **derived view**, not a stored field. It is computed on demand from `RepairGeometry` via `epistemic_view()`. It has no independent state. This is the correct architecture: the analytical object (`RepairGeometry`) lives in the proxy layer; the product view (`EpistemicReceipt`) is a projection of it.
