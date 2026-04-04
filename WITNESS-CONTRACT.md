# Bulla Witness Contract

Normative reference for the witness kernel. Deviation between code and this spec is a bug in one or the other. For theoretical motivation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf).

## Canonical Objects

| Object | Identity | Contents |
|---|---|---|
| `Composition` | `canonical_hash()` — SHA-256 of sorted structural JSON | Tools (name, internal state, observable schema) + edges + dimensions |
| `Diagnostic` | `content_hash()` — SHA-256 of measurement content | Fee, blind spots, bridges, rank data. Excludes timestamps. `BlindSpot.from_tool`/`to_tool` are ergonomic fields excluded from hash |
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

**Law 2**: The witness kernel never mutates a `Composition` or `Diagnostic`. It proposes patches; it never applies them silently. `Composition`, `Diagnostic`, and `WitnessReceipt` are all `frozen=True` with immutable `tuple` fields.

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

## Hierarchical Fee Decomposition

**Law**: For any partition of tools into disjoint groups, the coherence fee decomposes as:

```
fee(G) = sum(fee(G_i)) + boundary_fee
```

where `boundary_fee = rho_full - rho_obs >= 0` is the rank contribution of cross-partition edges modulo internal edges, computed independently for full and observable coboundary matrices.

**Non-negativity**: The column-projection from full to observable fields preserves linear independence of cross-partition rows modulo internal rows. Hence `rho_full >= rho_obs` and `boundary_fee >= 0`.

**Vanishing condition**: `boundary_fee = 0` when every cross-partition edge dimension has both endpoint fields in the respective tools' observable schemas.

**Tower Law**: For a partition refined by sub-partitioning each group G_i via P_i: `bf(refined) = bf(coarse) + sum(bf(P_i))`. The boundary fee is additive across levels of hierarchy. Proof: apply the decomposition theorem at both levels; local fees cancel.

**Monotonicity**: Since `bf(P_i) >= 0`, refining a partition can only increase the boundary fee. The boundary fee defines a monotone function on the refinement lattice: 0 at the trivial partition, `total_fee` at singletons. Operationally: every level of delegation adds non-negative hidden cost.

**Interpretation**: `boundary_fee` counts convention dimensions hidden at partition boundaries — blind spots invisible at every level of a hierarchy that appear only in the flat expansion. This is the coherence cost of delegation without disclosure.

**Non-valuation**: The boundary fee is monotone but NOT a valuation on the partition lattice. For the A->B->C chain with P={AB,C} and Q={A,BC}: `bf(P) + bf(Q) = 2` but `bf(P^Q) + bf(P v Q) = 1`. The same hidden convention at B causes boundary fee in both partitions; resolving it once (in the discrete partition) suffices.

**Minimum Disclosure Set**: `minimum_disclosure_set(comp)` returns the smallest set of `(tool, field)` pairs whose disclosure eliminates the coherence fee. The cardinality always equals the fee — it is a basis for the quotient space of the full coboundary column space modulo the observable column space. Disclosures subsume bridges: `len(bridges) >= 2 * len(disclosures)` across all compositions.

**Non-submodularity**: The boundary fee is NOT submodular on the partition lattice. An adversarial survey of 10,000 random compositions (635,095 partition pairs) found 4,061 violations of `bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`, with maximum violation magnitude 3. The individual `rho_full` and `rho_obs` functions are submodular (matroid rank on row sets), but their difference `bf = rho_full - rho_obs` is not. The 9 original bundled compositions happen to satisfy submodularity, but this is a topological accident of their pipeline-like structure, not a general property.

**Conditional Diagnosis**: For partial compositions with open ports, placeholder tools with empty observable schemas produce worst-case fee estimates. Boundary obligations — fields the placeholder must expose — are read off the blind spots on placeholder edges.

**Online Resolution Protocol**: The full conditional loop is:
1. `conditional_diagnose(partial_comp, open_ports)` → obligations
2. Candidate tool arrives → `satisfies_obligations(candidate, obligations)` → pass/fail
3. `resolve_conditional(cond, {placeholder: candidate})` → `Resolution` with `resolved_fee`, `fee_delta`, `met_obligations`, `remaining_obligations`
4. If `resolved_fee > 0`: `minimum_disclosure_set(resolved_comp)` → prescribe remaining fixes

`resolve_conditional` supports partial resolution: resolve some placeholders while leaving others. `fee_delta` is always non-negative (replacing a placeholder with a real tool can only improve the fee).

**Structural vs Epistemic Unknown**: Open ports in partial compositions create **structural unknowns** (distinct from **epistemic unknowns** from classifier uncertainty). Structural unknowns count against `structural_unknowns` in the conditional diagnostic, not against `max_unknown` in policy evaluation.

**Trace Gap (Closed)**: The Frobenius trace gap `trace(L_full) - trace(L_obs) = ||delta_full||_F^2 - ||delta_obs||_F^2` equals the total count of hidden-endpoint instances across blind spots: `sum(from_hidden + to_hidden for each blind spot)`. This is a weighted blind-spot count derivable from the existing diagnostic, not a genuine spectral refinement: it can be positive when the fee is zero (hidden columns in the span of observable columns). A continuous spectral refinement requires the eigenvalue spectrum of the sheaf Laplacian, deferred to future work.

## MCP Surface: Prescriptive Witness

`bulla.witness` always returns `disclosure_set` — a list of `[tool_name, field_name]` pairs representing the minimum disclosure set. This makes every witness call prescriptive: the agent knows not just the fee, but the exact fields to fix.

When the optional `partition` parameter is provided (array of arrays of tool name strings), the output includes a `decomposition` field with `total_fee`, `local_fees`, `boundary_fee`, `rho_obs`, `rho_full`, `boundary_edges`. The decomposition field is absent when partition is not provided, preserving backward compatibility.

## CLI Surface: `bulla gauge`

`bulla gauge` is the live-server/manifest analog of `bulla check`. Where `check` operates on hand-authored YAML compositions and enforces CI gates, `gauge` operates on live MCP servers or manifest JSON files (the `tools/list` response) and produces prescriptive output: coherence fee, minimum disclosure set, and witness basis. It combines inference (`scan` + `infer`) and diagnosis (`diagnose`) into a single command for the 30-second adoption experience. CI gating flags (`--max-fee`, `--max-blind-spots`) mirror `check`'s exit-code semantics.

## CLI Surface: `bulla audit`

`bulla audit` reads an MCP configuration file (Cursor or Claude Desktop format), scans all configured servers in parallel, and diagnoses the combined cross-server composition. The unique output is the **cross-server risk decomposition**: using `decompose_fee()` with a partition-by-server, it separates the coherence fee into:

- **Intra-server fee**: blind spots within individual servers (sum of per-server local fees)
- **Boundary fee**: blind spots that only appear between independently-developed servers

The boundary fee quantifies conventions hidden at the seam between servers -- the exact gap that no individual server can detect on its own. This is the direct empirical instantiation of the hierarchical fee non-additivity theorem.

Auto-detection searches `.cursor/mcp.json` (project), `~/.cursor/mcp.json` (user), and Claude Desktop config (macOS). Only stdio-transport servers are scanned; HTTP/SSE entries are skipped with a warning. Failed servers are reported but do not block diagnosis of successful ones (`--skip-failed` default).

## `max_unknown` Definition

A convention dimension is **unknown** when it is relevant to the composition but could not be assigned a `declared` or `inferred` value under the active packs. `max_unknown` bounds the number of such dimensions a policy will tolerate before refusing.
