# Bulla Witness Contract

Normative reference for the witness kernel. Deviation between code and this spec is a bug in one or the other. For theoretical motivation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf).

## Canonical Objects

| Object | Identity | Contents |
|---|---|---|
| `Composition` | `canonical_hash()` — SHA-256 of sorted structural JSON | Tools (name, internal state, observable schema) + edges + dimensions |
| `Diagnostic` | `content_hash()` — SHA-256 of measurement content | Fee, blind spots, bridges, rank data. Excludes timestamps |
| `WitnessReceipt` | `receipt_hash` — SHA-256 of all fields except `anchor_ref` | Binds composition + diagnostic + policy + lexical constitution + provenance |

Three hashes, three concerns: what was proposed, what was measured, what was witnessed.

## Hash Coverage

`receipt_hash` includes: `receipt_version`, `kernel_version`, `composition_hash`, `diagnostic_hash`, `policy_profile`, `fee`, `blind_spots_count`, `bridges_required`, `unknown_dimensions`, `disposition`, `timestamp`, `patches`, `parent_receipt_hash`, `active_packs`, `witness_basis`.

`receipt_hash` excludes: `anchor_ref` (external publication proof, added after witness event).

Rationale: the hash must be computable at witness time. Anchor ref arrives later.

## Policy Semantics

`PolicyProfile` fields: `name`, `max_blind_spots`, `max_fee`, `max_unknown`, `require_bridge`.

Disposition priority (first match wins):
1. `blind_spots > 0 AND fee > max_fee` → `refuse_pending_disclosure`
2. `unknown_dimensions > max_unknown` (when `max_unknown >= 0`) → `refuse_pending_disclosure`
3. `require_bridge AND blind_spots > 0` → `proceed_with_bridge`
4. `blind_spots > max_blind_spots` → `proceed_with_bridge`
5. `fee > max_fee` → `proceed_with_receipt`
6. Otherwise → `proceed`

`max_unknown = -1` disables the unknown threshold (default).

## Anti-Reflexivity Laws

**Law 1**: The measurement layer (`diagnostic.py`) has zero imports from the witness layer (`witness.py`). Measurement does not know it is being witnessed.

**Law 2**: The witness kernel never mutates a `Composition`. It proposes patches; it never applies them silently. `Composition` is `frozen=True` with immutable `tuple` fields.

## Receipt Chains

`parent_receipt_hash` links a receipt to a prior witness event. Canonical chain: original → bridge → patched. The patched receipt's `parent_receipt_hash` equals the original receipt's `receipt_hash`.

Chains are advisory, not enforced by the kernel. Verification is the consumer's responsibility.

## Lexical Constitution

Convention packs define the vocabulary under which tools are classified. Packs are ordered; later packs override earlier ones on dimension collision. This order is semantics.

`active_packs` in the receipt is a tuple of `PackRef(name, version, hash)` in precedence order. The receipt binds the measurement to the lexical constitution under which it was taken.

Pack hash is SHA-256 of the parsed canonical JSON (not raw YAML bytes), ensuring format-independent identity.

## Epistemic Provenance

`WitnessBasis(declared, inferred, unknown)` is **caller-attested**. The kernel records it; it does not compute it. The caller (typically `BullaGuard` or an inference pipeline) is responsible for honest attestation.

**Derivation rule**: When `witness_basis` is provided, `unknown_dimensions` is derived from `witness_basis.unknown`. The explicit `unknown_dimensions` parameter is a fallback for non-attested cases. This prevents lying receipts.

Invariant: `witness_basis is not None` implies `receipt.unknown_dimensions == witness_basis.unknown`.

## Verification

**`verify_receipt_consistency(receipt, comp, diag)`**: Checks composition hash, diagnostic hash, fee, blind spots count, bridges required, and basis/unknown agreement. Requires kernel objects.

**`verify_receipt_integrity(receipt_dict)`**: Self-contained tamper detection. Reconstructs the hash input from a serialized dict and compares to the claimed `receipt_hash`. No kernel required. The `to_dict()` round-trip is the verification path.

## `max_unknown` Definition

A convention dimension is **unknown** when it is relevant to the composition but could not be assigned a `declared` or `inferred` value under the active packs. `max_unknown` bounds the number of such dimensions a policy will tolerate before refusing.
