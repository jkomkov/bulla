# Bulla Protocol Note

A concise technical summary of the Bulla witness protocol (Sprints 25-31).

## 1. The Fee Theorem

A composition `G = (V, E)` assigns each tool `T` a presheaf section `F(T) = internal_state(T)` and an observable sub-presheaf `O(T) = observable_schema(T)`. The coboundary operator `delta: C^0 -> C^1` maps tool sections to edge sections along semantic dimensions. The coherence fee is:

```
fee(G) = rank(delta_full) - rank(delta_obs) = dim H^1(G; F/O)
```

Each unit of fee corresponds to one convention dimension that is structurally required by an edge but hidden from the observable schema of at least one endpoint. `fee = 0` iff every convention is either globally observable or globally irrelevant.

For any partition `P = {G_1, ..., G_k}` of the tools into disjoint groups:

```
fee(G) = sum_i fee(G_i) + boundary_fee(P)
```

where `boundary_fee(P) = rho_full(P) - rho_obs(P) >= 0` counts convention dimensions hidden at partition boundaries. The boundary fee is monotone on the refinement lattice: refining a partition can only increase it. It is not submodular.

## 2. Convergence Guarantee

The iterative repair loop `coordination_step()` wraps `repair_step()`:

1. Diagnose the composition; extract boundary obligations.
2. Probe each obligation via guided discovery (single batched LLM call).
3. For each CONFIRMED probe, make the obligated field observable.
4. Re-diagnose. If `fee = 0`, terminate. If `fee_delta = 0`, fixpoint. Otherwise, carry forward UNCERTAIN obligations and repeat.

**Termination theorem.** The fee is a non-negative integer. Each round with at least one confirmation strictly decreases it. The loop terminates in at most `fee_0` rounds, where `fee_0` is the initial coherence fee.

**Fixpoint characterization.** Two cases:
- `fee = 0`: full resolution. All conventions are observably coherent.
- `fee > 0, confirmed = 0`: irreducible. Remaining obligations cannot be resolved by the current tool set. These are genuine coordination gaps.

## 3. Contradiction Detection

When guided discovery confirms different convention values for the same dimension across different tools, the protocol detects this as a `ContradictionReport`:

```
detect_contradictions(discovered_pack) -> tuple[ContradictionReport, ...]
```

A dimension with `len(known_values) > 1` produces a MISMATCH. The contradiction is sealed into the `WitnessReceipt` under the `contradictions` field, included in the receipt hash when not None (conditional-include pattern for backward compatibility).

Two detection surfaces:
- **Intra-run**: A single `coordination_step()` discovers conflicting values from different server groups on the same dimension (e.g., filesystem uses `absolute_local`, GitHub uses `relative_repo` for `path_convention_match`).
- **Intra-agent**: A probe confirms a `convention_value` that differs from its obligation's `expected_value` (inherited from a parent receipt). Detected by `detect_expected_value_contradictions()`.

Contradictions are first-class protocol objects: frozen dataclasses, hashable, serializable, receipt-embedded. They survive the full `to_dict() -> JSON -> verify_receipt_integrity()` round-trip.

## 4. Worked Example

Two MCP servers: filesystem (14 tools, absolute local paths) and GitHub (26 tools, repo-relative paths). Composed into a single agent.

```
Coherence fee:           30
Boundary fee:            1
Boundary obligations:    3 (all on path_convention_match at filesystem <-> github)
Guided discovery:        3 rounds, 5 confirmed
  filesystem:            absolute_local
  github:                relative_repo
Contradictions:          1 (path_convention_match: MISMATCH)
Receipt integrity:       VALID
```

No runtime error occurs. Schema validation passes. The filesystem server's `read_file` accepts `/Users/me/repo/src/main.py`; the GitHub server's `create_or_update_file` accepts `src/main.py`. An agent that copies a file between them gets the path wrong silently. Bulla detects the incompatible convention at the boundary without executing either tool.

## 5. Open Questions

**(a) Hierarchical composition.** The boundary fee is monotone but not submodular on the partition lattice. For three or more levels of delegation (agent -> sub-agent -> tool), does the fee decomposition satisfy a tower law for non-disjoint partitions?

**(b) Policy enforcement.** ~~Resolved in v0.31.0.~~ `PolicyProfile` now includes `max_unmet_obligations` and `max_contradictions`. Both default to `-1` (disabled); `0` means strict (any occurrence triggers refusal); `N > 0` means tolerance. The threshold semantics follow the established `max_unknown` pattern: `-1` disables, `>= 0` enforces. `BullaGuard.enforce_policy()` is the one-call entry point.

**(c) Spectral refinements.** The fee is a rank difference (integer). The sheaf Laplacian eigenvalue spectrum provides a continuous refinement: compositions with the same fee can differ in how "close" they are to coherence. This requires eigenvector computation, deferred to future work.

**(d) Continuous relaxation.** The discrete fee `dim H^1(G; F/O)` counts obstructions. A continuous analog `||delta_full - delta_obs||_F` (Frobenius norm of the difference) gives a gradient toward coherence. Can the iterative repair loop use this gradient to prioritize which obligation to resolve first?

**(e) Receipt DAG composition.** When agent B inherits agent A's receipt with contradictions, does B's contradiction set union with A's, or does B re-derive contradictions from the merged pack? Union preserves provenance but may double-count; re-derivation is consistent but loses the history of which agent detected which contradiction.
