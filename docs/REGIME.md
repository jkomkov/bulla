# When is `coherence_fee` a fee? — the Bulla regime guide

> **Audience.** Anyone using Bulla via the CLI, Python API, or MCP server who wants to know when `coherence_fee` is a meaningful number, what the regime warnings mean, and how to fix tool definitions that trigger them.

## TL;DR

- For any composition derived from real MCP manifests via `BullaGuard.from_tools_list` (the standard pipeline), `coherence_fee` is **always** a non-negative coherence fee. You can read it directly. **No further action needed.**
- For any YAML composition Bulla loads via `bulla diagnose <file.yaml>`, the YAML parser pre-validates the schema-shape invariant; malformed YAMLs are rejected at load time with a precise error.
- For Python-constructed compositions that bypass both pipelines (random generators, programmatic builds, custom tooling), call `bulla.regime.classify(comp)` and check `is_well_formed_for_fee` before interpreting `coherence_fee` as a fee.

The CLI emits a regime warning to stderr when a loaded composition fails the schema-shape predicate. If you see one of these warnings, this document explains what it means and what to do.

## What can `coherence_fee` actually be?

Bulla computes `coherence_fee = h1_obs − h1_internal`. This formula is well-defined for every composition, but its **interpretation as a fee** (a non-negative obstruction count) only holds when `rank_internal ≥ rank_obs`.

Three things can happen:

| Sign of `coherence_fee` | Meaning |
|---|---|
| `> 0` | Genuine obstruction: hidden semantic structure exceeds what the seam exposes. The fee counts how many fields would need to be disclosed to bring the seam into coherence. |
| `= 0` | No obstruction: the observable schema fully captures the relevant hidden structure. |
| `< 0` | **Not a fee**: the formula went negative. This means the composition violates the schema-shape invariant — observable schema declares fields the tool's internal_state doesn't carry. The value is a *signed obstruction imbalance*, not a coherence fee. The composition needs structural repair, not disclosure. |

In **967/967** real-MCP composition pairs sampled by Sprint 8's regime audit, only the first two outcomes were observed. Negative fees only arise from compositions that violate the schema-shape invariant — these are tooling bugs, not corner cases.

## The regime warning

If you see something like:

```
[my_composition.yaml] WARNING: composition `my_composition.yaml` has 1 schema-shape violation(s) (projective_observables predicate fails).
  - tool `t1`: observable fields ['secret'] not in internal_state.
  Reading `SIGNED OBSTRUCTION IMBALANCE (NOT a fee — see schema-shape warning)` below at your own risk; ...
```

what it means:

> Tool `t1` exposes `secret` in its `observable_schema`, but `secret` is **not** declared in its `internal_state`. The tool is announcing a field it doesn't actually own. By the schema-shape theorem (Sprint 9), this can produce negative `coherence_fee` and the diagnostic value below is not interpretable as a fee.

The fix is on the tool-definition side, not in Bulla:

- **If the tool actually carries the field**, add it to `internal_state`:
  ```yaml
  tools:
    t1:
      internal_state: [secret, other_fields]   # add 'secret' here
      observable_schema: [secret]
  ```
- **If the tool does NOT actually carry the field**, remove it from `observable_schema`:
  ```yaml
  tools:
    t1:
      internal_state: [hidden_a]
      observable_schema: []                    # remove 'secret'
  ```

## The regime lattice

Bulla compositions live in one of several regimes, ordered by the strength of the structural guarantees they admit:

| Regime | What's true | How to detect |
|---|---|---|
| **Arbitrary `bulla.model` composition** | Anything goes. `coherence_fee` may be negative. | Default — no predicate check. |
| **Projective-observable** | `coherence_fee ≥ 0` is guaranteed (Sprint 9 theorem). | `bulla.regime.has_projective_observables(comp)` |
| **Fee-well-formed** | `rank_internal ≥ rank_obs`; `coherence_fee` is a true non-negative fee. Implied by projective-observable. | `bulla.regime.is_well_formed_for_fee(comp)` |
| **Exact-regime (conservative)** | Strongest disclosure guarantees: `weighted_greedy_repair` and `minimum_disclosure_set` agree on cardinality and on bases (paper §3.5). | `bulla.regime.is_exact_regime_conservative(comp)` |
| **All-hidden** | Cycle-family special case; every observable_schema is empty. | `bulla.regime.is_all_hidden(comp)` |

The `bulla regime` CLI command (Sprint 11) prints the per-regime classification of a composition:

```
$ bulla regime my_composition.yaml
my_composition.yaml:
  rank_obs:                       6
  rank_internal:                  6
  fee_formula:                    0
  is_well_formed_for_fee:         True   ← coherence_fee is a fee
  has_projective_observables:     True   ← schema-shape invariant holds
  has_dfd_conservative:           True
  has_chp_conservative:           True
  is_exact_regime_conservative:   True   ← strongest disclosure guarantees
  is_all_hidden:                  False
```

For programmatic access, `bulla diagnose --format json` includes a `regime` block with the same fields (Sprint 11 Phase 5).

## When you want everything in one artifact (Sprint 13/14)

`bulla certify` bundles regime classification + fee + interpretation labels + cross-server decomposition + witness geometry into a **single per-composition certificate**:

```
$ bulla certify my_composition.yaml
$ bulla certify my_composition.yaml --format json
$ bulla certify --seed-set --format json --output certs.json
```

Use `bulla regime` for a quick predicate-only check; use `bulla certify` when you want the full bundle in one structured artifact (e.g., for CI gating, audit reports, or programmatic consumers).

**Sprint 14 (v1.0 schema) machine-readable claims.** The certificate's `claims` block is the source of truth for programmatic consumers. Each claim is `{value, status, licensed_by}` with `status ∈ {"certified", "candidate", "not_certified", "not_applicable"}`. Six v1.0 claims:

- `schema_shape_valid` — `certified` iff `has_projective_observables`.
- `fee_is_nonnegative`, `fee_is_interpretable` — `certified` iff `is_well_formed_for_fee` (kept separate for forward compatibility).
- `exact_disclosure_equivalence` — `certified` iff well-formed AND exact-conservative; this is what licenses the Sprint 11/12 disclosure-set agreement claim.
- `repair_basis_status` — `certified` / `candidate` / `not_applicable` / `not_certified` depending on regime + fee. The Sprint 14 bridge to Sprint 15+ repair logic.
- `global_composition_certified` — internal-consistency claim.

Free-text labels like `display.fee_interpretation` and `display.repair_semantics` are kept for UI back-compat but should NOT be parsed by programmatic consumers — read the `claims` block instead.

See `papers/composition-doctrine/sprint13_certification_suite.md` for the v1.0 schema layout, claim derivation tables, and `certificate_hash` discipline.

## When in doubt

- Reach for `bulla regime <file>` for a quick classification.
- Read `bulla/docs/MATROID-STRUCTURE.md` for the per-regime guarantees on disclosure semantics.
- File an issue if you see a regime warning on a composition that came from `BullaGuard.from_tools_list` — that pipeline is supposed to maintain the schema-shape invariant by construction.

## The completeness verdict — when bulla can prove you're fully covered

`bulla certify` prints a **COMPLETENESS** verdict. It answers a question most tools cannot: *is the fee missing anything, or is it provably the whole story here?* The verdict is a plain-language reading of the `exact_disclosure_equivalence` claim (which is the machine-readable source of truth — parse that, not the text); it never claims more than that claim licenses.

| Verdict | When | What it means |
|---|---|---|
| **✓ PROVEN** | exact regime (DFD ∧ CHP) ∧ well-formed | The fee is exact and the prescribed disclosures are provably minimal (composition-doctrine Lemma 3.9 / the exact-conservative matroid equivalence): no convention mismatch is missed, and no smaller fix suffices. |
| **~ LOWER BOUND** | well-formed but not exact-conservative (surrogate regime) | The fee is a floor. The exact-regime guarantee does not hold, so additional obstruction may exist and the disclosure set may not be minimal. |
| **– N/A** | not well-formed for the fee | `observable_schema` is not a subset of `internal_state` per tool; classify with `bulla regime` and fix the schema shape first. |

**Two scope riders are printed with every verdict, and they are not boilerplate:**

1. **Coherence completeness only — not delivery.** A PROVEN verdict certifies that the loaded *conventions* compose (the type/convention layer). It does **not** certify that the composition delivers the right *result*. Whether the provider actually did what it should — the value/delivery layer — is a separate problem and out of scope for the fee.
2. **Relative to the loaded vocabulary.** Completeness is with respect to the loaded convention packs. An obstruction in a dimension you did not load is not seen; `fee = 0` means "no conflict detectable under the loaded packs," not "no conflict possible."

Because the verdict lives in the certificate's `display` block, it is **excluded from the content hash** — rewording it never changes the deed. The guarantee it reports comes entirely from the regime predicates in the signed `claims`.

## Empirical baseline (Sprint 8 + 9 + 11 audits)

| Corpus | n | well-formed-for-fee | projective | exact-conservative |
|---|---|---|---|---|
| Curated `bulla/compositions` + `bulla/audit` | 14 | 100% | 100% | ~71% |
| Cycle family (Sprint 6 grid) | 70 | 100% | 100% (vacuous) | 100% |
| Registry pair compositions (sampled) | 250 | 100% | 100% | (varies) |
| Pre-computed registry pairs | 703 | 100% | 100% (assumed) | (varies) |
| Sprint 7 random-stress (disjoint partition) | 1000 | 60.7% | 0.2% | ~0% |
| Sprint 9 well-formed random (projective by construction) | 1000 | 100% | 100% | (varies) |

Real-MCP compositions reliably satisfy the well-formedness and schema-shape predicates. The exact-regime-conservative predicate is the strongest guarantee and may not always hold even on real compositions (e.g., `mcp_official_composition.yaml` fails DFD-conservative because some seam dimensions cross field-types; the composition is still well-formed for fee, just not in the strictest regime).
