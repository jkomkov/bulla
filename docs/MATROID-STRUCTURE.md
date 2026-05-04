# Witness Matroid: the matroid Bulla computes on

> **Status.** Verification doc, not new theory. This document pins down which matroid Bulla's existing greedy-repair algorithm is computing on, and clarifies the relationship between two distinct disclosure notions in the codebase.

## The matroid in one paragraph

Given a Bulla composition `G` with hidden fields `H = {h_1, ..., h_n}` and witness Gram `K(G) ∈ Mat_{n×n}(ℚ)` (computed by `bulla.witness_geometry.witness_gram`), the **witness matroid** `M(G)` is the column matroid of `K(G)` on ground set `H`. By construction, `K = W^T W` for some rational matrix `W` (the projected coboundary on hidden columns), so `M(G)` is the linear matroid represented by `W` over `ℚ`.

- **Rank.** `rank(M(G)) = rank(K(G))` (matroid-invariant; always holds). In the **fee-well-formed regime** (Sprint 8: `is_well_formed_for_fee`) this further coincides with `coherence_fee(G)`. In other regimes (Sprint 7's random stress), `rank(K(G))` and `coherence_fee(G)` can diverge — see the Regime Lattice section below.
- **Independence.** A subset `S ⊆ H` is independent iff the corresponding columns of `K` are linearly independent.
- **Bases.** A basis is a maximal independent set; `|basis| = rank(K(G))`. (In the fee-well-formed regime this equals `fee(G)`; in other regimes it does not.)
- **Loops.** A field `h_i` is a loop iff `K[i, i] = 0` (the entire `i`-th column is zero in `W`); loops are not in any basis.
- **Coloops.** A field `h_i` is a coloop iff every basis contains `i`; equivalently, removing `i` strictly drops the rank.

Bulla's existing functions `bulla.witness_geometry.coloops()` and `bulla.witness_geometry.loops()` already implement these matroid notions.

## Greedy repair = matroid-greedy

The function `bulla.witness_geometry.weighted_greedy_repair(K, hidden_basis, costs)` returns a minimum-cost basis of `M(G)` under positive monotone costs, by Edmonds (1971): the greedy algorithm — sort by cost, accept each element iff it remains independent — is globally optimal on a matroid.

**Verification.** `bulla/tests/test_witness_matroid.py::TestGreedyIsMatroidGreedy::test_greedy_matches_exhaustive_min_cost_basis` enumerates all bases of `M(G)` for small examples (`n ≤ 4`), computes the minimum-cost basis exhaustively, and asserts `weighted_greedy_repair` returns a basis of equal cost across multiple cost vectors (including rationals). 19 tests, all passing.

This verification does NOT prove a new theorem. It pins down that Bulla's greedy implementation correctly realizes the matroid-greedy on `M(G)`, with the understanding that this correctness follows from Edmonds (1971) once we agree that `M(G)` is the right matroid.

## Two disclosure notions, intentionally different

The codebase has two operations that look like "compute a disclosure set" but operate at different levels:

### 1. `weighted_greedy_repair` — basis of `M(G)`

Returns a minimum-cost subset `S ⊆ H` such that `S` is a basis of the column matroid `M(G)`. The cardinality is `fee(G)`. Operationally: "which `fee(G)` hidden fields, if disclosed, would reduce the coherence fee to zero?"

This is a matroid-theoretic answer. It does not consult the observable coboundary; it operates on the hidden-column rank structure of `K`.

### 2. `minimum_disclosure_set` (in `bulla.diagnostic`) — observable-coboundary augmentation

Returns a minimum subset `S ⊆ H` such that promoting `S` to observable raises `rank(δ_obs)` to match `rank(δ_full)`. This consults the observable coboundary explicitly and may differ from `weighted_greedy_repair` when the matroid has structure that the observable layer doesn't see.

### When they coincide and when they don't

**Cardinality agreement is regime-dependent, not structural.** Sprint 7's
random-composition sweep (`bulla/tests/test_disclosure_semantics_random.py`)
shows the actual landscape:

| measurement | empirical rate over 500 random small compositions |
|---|---|
| `diag.coherence_fee == \|weighted_greedy_repair\| == \|minimum_disclosure_set\|` | **3.8%** |
| `diag.coherence_fee < 0` (formula `h1_obs − h1_full` goes negative) | **40.8%** |
| `\|weighted_greedy_repair\| ≠ \|minimum_disclosure_set\|` (cardinality divergence) | **91.6%** |
| `\|weighted_greedy_repair\| == rank(K)` (matroid invariant) | **100%** |

The only **structural invariant** is `|weighted_greedy_repair| == rank(K)`, which holds by construction (greedy returns a max-rank basis on the column matroid `M(K)`). Everything else is regime-dependent.

**The all-hidden cycle family is a special case.** When every tool's `observable_schema` is empty, `δ_obs` is structurally zero, `rank_obs = 0`, and the codebase's fee formula reduces to `fee = h1_obs − h1_full = rank_internal − 0 = rank_internal ≥ 0`. The Schur-complement construction `K = W^T W` collapses to `K = H^T H` (with `H = δ_internal`), so `rank(K) = rank_internal = fee`, and `minimum_disclosure_set` (greedy on `δ_obs` augmenting toward `δ_full`) terminates at the same cardinality. Sprint 6 Phase E verified this across 35 cycle-family cells; that result is correct **for the all-hidden regime** but does not generalize.

**The general regime is more complex.** In a composition with mixed observable/internal field structure, `δ_obs` and `δ_full` (which the codebase calls `delta_full` but actually constructs from `internal_state` only — the variable name is misleading) operate on **disjoint column sets**: observable fields vs internal fields. There is no direct nesting. The codebase's fee formula `fee = h1_obs − h1_full = rank_internal − rank_obs` can be negative when the observable side has more obstruction than the internal side, and the three values diverge:

- `diag.coherence_fee` reports the formula value (possibly negative).
- `weighted_greedy_repair` returns a `rank(K)`-element basis of `M(K)` regardless of sign.
- `minimum_disclosure_set` runs its own greedy and may return zero elements (its early-exit `if fee == 0` test treats negative fees similarly).

The functions are doing genuinely different things — they are not two ways to compute the same number.

**Operational rule (revised).**

- Use `weighted_greedy_repair` when you want a **minimum-cost basis of the column matroid `M(K)`** (a matroid-theoretic answer; cardinality always equals `rank(K)`).
- Use `minimum_disclosure_set` when you want a **subset of hidden fields whose disclosure raises the observable rank**, on the codebase's specific greedy ordering. This may have cardinality 0, less than `rank(K)`, equal to `rank(K)`, or undefined-relative-to-fee depending on the composition's regime.
- They coincide in cardinality (and often in choice) on the **all-hidden** regime (empty observable schemas) — the regime the cycle-family sprints worked in. They diverge in cardinality in 91.6% of random general compositions.

**Note on the prior overclaim (Sprint 6 → corrected in Sprint 7).** A previous version of this section claimed:

> "Cardinality always agrees in the current Bulla model. The Schur-complement construction `K = W^T W` ... gives `rank(K) = ... = fee`. So both functions return sets of cardinality exactly `fee`. This is the exact-regime agreement condition."

Sprint 7's random-composition test demonstrated this is **wrong outside the all-hidden regime**. The cardinality agreement on the cycle family was correct as a finding for that family; the generalization to "the current Bulla model" was unjustified. Specifically, the Schur-complement formula `K = W^T W` with `W = (I − P_O) H` *assumes* `δ_obs ⊆ δ_full` as a column-restriction relationship — but in the codebase, `δ_obs` and `δ_internal` (called `delta_full`) are matrices on **disjoint** column sets (observable vs internal field bases), so the nesting that would justify the Schur-complement identity doesn't hold in general.

The reliable structural identity is just `|weighted_greedy_repair| == rank(K)` (matroid invariant) and the special-case agreement in all-hidden compositions.

## Sprint 8 — regime table

Sprint 8's regime audit (`bulla/calibration/scripts/sprint8_regime_audit.py`) established the precise validity boundary for the `coherence_fee` formula across four corpora. The fee formula `fee = rank_internal − rank_obs` is interpretable as a non-negative coherence fee only when `rank_internal ≥ rank_obs` (the **well-formedness predicate**, available as `bulla.regime.is_well_formed_for_fee`).

| Corpus | n | well-formed (fee ≥ 0) | obs-dominance (fee < 0) | rank(K) = fee always? |
|---|---|---|---|---|
| All-hidden cycle family (Sprint 6) | 70 | 100% | 0 | **YES** |
| Curated `bulla/compositions` + `bulla/audit` | 14 | 100% | 0 | **YES** |
| Registry pair compositions (sampled from 57 servers) | 250 | 100% | 0 | **YES** |
| Pre-computed registry pairs (`schema_structure_pairs.jsonl`) | 703 | 100% | 0 | **YES** (per JSONL) |
| Random-stress generator (broad `bulla.model` API) | 1000 | 60.7% | 39.3% | NO (general regime) |

**Headline.** Across **967/967 real-MCP composition samples**, the well-formedness predicate holds and `rank(K) = fee` is a meaningful cardinality identity. The negative-fee phenomenon is a **stress-test artifact** of the random generator, not a real-MCP concern.

**Why real-MCP stays well-formed.** In a real MCP composition, every observable seam dimension is shadowed by a compatible internal-state declaration on at least one endpoint — observable schema is a *projection* of internal state, not an independent declaration. The random stress generator violates this by drawing observable/internal partitions independently per tool with no shadowing constraint.

**Operational rule (Sprint 8).**

- For any composition derived from real MCP manifests via `BullaGuard.from_tools_list` (the standard pipeline): **`coherence_fee` is always non-negative**; the `weighted_greedy_repair` and `minimum_disclosure_set` cardinalities agree in practice.
- For any composition constructed directly via `bulla.model` primitives (random generators, hand-crafted stress cases, hypothetical model extensions): **call `bulla.regime.classify(comp)` and check `is_well_formed_for_fee` before interpreting `diag.coherence_fee` as a fee**. If the predicate fails, the value is a signed obstruction imbalance from the formula `h1_obs − h1_internal`, not a coherence fee.

**Naming caveat (`delta_full` is misleading).** Throughout the codebase, callers of `build_coboundary(..., use_internal=True)` unpack the returned matrix as `delta_full`. This name suggests "δ on observable + internal" but the code actually constructs "δ on internal only" — the columns are *disjoint* from those of `delta_obs`, not nested. Sprint 8 added clarifying inline comments at each `delta_full` callsite (`bulla/src/bulla/diagnostic.py`); a code-wide rename was deferred to keep the audit non-invasive. When reading prose or code that says `δ_full`, mentally read `δ_internal`.

## Sprint 9 — schema-shape invariant: structural explanation

Sprint 8 established the **measurement** (real-MCP fees are non-negative; random-stress fees go negative 39.3% of the time). Sprint 9 establishes the **structural explanation**: a per-tool schema-shape invariant that *implies* non-negative fee.

**Theorem (Sprint 9 schema-shape invariant; full proof in `papers/composition-doctrine/sprint9_schema_shape_invariant.md`).** *If for every tool `t` in composition `G`, `observable_schema(t) ⊆ internal_state(t)` (the **projective observables** condition), then `coherence_fee(G) ≥ 0`.*

The proof is elementary: under projectivity the column basis of `δ_obs` injects into the column basis of `δ_internal`, the matrix entries match on the image, and rank-monotonicity gives `rank(δ_obs) ≤ rank(δ_internal)`.

**Three layers of regime characterization** — distinguishing *definition*, *measurement*, *explanation*:

| Layer | Predicate | When to use |
|---|---|---|
| **Explanation** (structural, schema-side) | `has_projective_observables(comp)` | Static check on tool definitions. Catches tooling bugs *before* any matrix is built. |
| **Measurement** (rank-based) | `is_well_formed_for_fee(comp)` ⇔ `rank(δ_internal) ≥ rank(δ_obs)` | Runtime check after building δ. Necessary-and-sufficient for `coherence_fee ≥ 0`. |
| **Diagnostic** (signed) | `RegimeReport` dominance flags | Reporting / classification across mixed corpora. |

**Implication structure.** The explanation implies the measurement: `has_projective_observables ⇒ is_well_formed_for_fee`. The converse fails — Sprint 7's disjoint-partition random sweep produced 60.7% well-formed-fee compositions but only 0.2% projective. So projectivity is *sufficient* but not *necessary* for `fee ≥ 0`; this is fine because we only need a sufficient condition that catches all real-MCP cases (which it does, 100%).

**Empirical confirmation of the implication (Sprint 9).** A repaired random generator that enforces `internal_state` = full field surface and `observable_schema ⊆ internal_state` produces non-negative fee on **1000/1000** trials (`bulla/tests/test_schema_shape_invariant.py::test_well_formed_random_implies_nonneg_fee`).

**Operational consequence.** For UX, the right gate at tool-definition time is `has_projective_observables(comp)`. A tool definition where `observable_schema ⊄ internal_state` is a tooling bug — the tool is exposing fields it doesn't actually own. `BullaGuard.from_tools_list` enforces this by construction, which is why all real-MCP-derived compositions satisfy the schema-shape invariant.

## Sprint 10 — regime lattice (mathematical hygiene)

To prevent future overclaims, every theorem in the program now carries a regime tag. The "lattice" below is an **implication graph**, not a literal subset chain — each arrow `R₁ → R₂` means "every composition satisfying `R₁` also satisfies `R₂`" (where the implication is proven), and unproven relationships are marked with `?`:

```
  arbitrary bulla.model composition  (every Bulla composition)
       │
       │  has_projective_observables (Sprint 9 schema-shape)
       ▼
  projective-observable composition  (Sprint 9 theorem applies)
       │
       │  ⇒ is_well_formed_for_fee  (Sprint 9 theorem; rank monotonicity)
       ▼
  fee-well-formed composition  (rank_internal ≥ rank_obs; Sprint 8)

  fee-well-formed  ?⇒  exact-regime
    (NOT a proven implication; exact-regime asserts DFD + CHP independently)

  exact-regime composition  (paper §3.5 DFD + CHP)
       │
       │  ⇒ matroid disclosure semantics fully meaningful
       │  ⇒ |weighted_greedy_repair| = |minimum_disclosure_set| = fee
       ▼
  all-hidden exact composition  (cycle family — narrow special case
                                 where every observable_schema = ())
```

The proven implications above are: `projective ⇒ well-formed-for-fee` (Sprint 9 / Sprint 10 Lean rank-monotonicity), and `exact-regime ⇒ matroid disclosure agreement` (paper §3.5). The relationship between `fee-well-formed` and `exact-regime` is intentionally NOT shown as an arrow — exact-regime is a separate strengthening that adds DFD/CHP independently of the fee-well-formedness condition.

Every theorem in the program implicitly applies at one of these levels. The table below makes the per-regime guarantees explicit:

| Regime | Detected by | Fee non-negative? | Matroid `\|greedy\| = rank(K)` | `\|greedy\| = \|min_disclosure\|` | Disclosure semantics meaningful? | Locality cycle family applies? |
|---|---|---|---|---|---|---|
| Arbitrary `bulla.model` composition | (any) | NO (39.3% violate; Sprint 7) | YES (matroid invariant) | NO (91.6% diverge; Sprint 7) | NO without regime check | NO |
| Projective-observable composition | `has_projective_observables` | **YES** (Sprint 9 theorem) | YES | empirically common | partial (depends on rank coincidences) | NO (cycle family is special case) |
| Well-formed-for-fee composition | `is_well_formed_for_fee` | YES (by definition) | YES | empirically common | YES | NO |
| Exact-regime composition | DFD + CHP (paper §3.5; predicate TODO) | YES | YES | YES (matroid bases ↔ disclosure sets) | YES (matroid greedy is provably optimal) | YES (subset) |
| All-hidden exact composition | `is_all_hidden ∧ exact-regime` | YES | YES | YES | YES | YES (Sprint 6 + Sprint 7 grids verified) |

**How to use the lattice.**

- Before stating "fee X holds in Bulla", name a regime row.
- Before claiming `weighted_greedy_repair == minimum_disclosure_set`, the right regime is `exact-regime` (or stronger), not just "current Bulla model".
- The Sprint 5 / 6 / 7 cycle-family theorems live in the bottom row (all-hidden exact). Do not extrapolate without checking the regime predicate.
- The Sprint 9 schema-shape theorem lives at the second row (projective-observable). It guarantees fee ≥ 0 but says nothing about disclosure-set agreement — that requires the exact-regime row.

**Detector status (updated through Sprint 11).** Of the four lattice predicates plus `is_all_hidden`, all are now exposed in `bulla.regime`:

- `has_projective_observables` (Sprint 9), `is_well_formed_for_fee` (Sprint 8), `is_all_hidden` (Sprint 8).
- `has_dfd_conservative`, `has_chp_conservative`, `is_exact_regime_conservative` (Sprint 11).

The Sprint-11 predicates are explicitly named **conservative** because they are sufficient — but not necessary — conditions for the abstract paper §3.5 conditions. A composition can satisfy abstract DFD/CHP without satisfying the conservative detector (e.g., via a richer field-type bijection structure not currently expressible in `bulla.model`). Always preserve the "conservative" qualifier in user-facing prose; never silently shorten to "DFD detector" or "CHP detector".

**Why this matters.** The Sprint 5 → Sprint 9 sequence repeatedly discovered overclaims that arose from missing regime tags: "the cycle family fee gap matches `b_1`" (Sprint 5, wrong identity), "cardinality always agrees in current Bulla model" (Sprint 6, wrong scope), "FMT threshold is `⌈m/2⌉`" (Sprint 6, wrong for odd m). Each correction sharpened the regime tag on the relevant theorem. The lattice above codifies the discipline: state the regime first, claim the theorem second.

## What this matroid is NOT

- **Not a graphic matroid** in general. The witness Gram comes from a sheaf-theoretic coboundary, not from edge-incidence on a single graph. Some compositions with simple seam structure may produce graphic matroids; most don't.
- **Not necessarily a regular matroid.** Bulla works over `ℚ` (exact rational arithmetic); the matroid is `ℚ`-representable but may not be representable over every field.
- **Not a connected matroid in general.** The witness Gram can be block-diagonal when the composition decomposes into disjoint sub-compositions; `M(G)` is then a direct sum of the per-block matroids. Bulla's `bulla.witness_geometry._connected_components_of_gram` already detects these blocks.

## Module API

```python
from bulla.witness_matroid import (
    rank_of_columns,        # rank of K-submatrix on given column indices
    is_independent,         # are these columns linearly independent?
    is_basis,               # are these columns a maximal independent set?
    all_bases,              # enumerate all bases (small n only; raises ValueError above 10000)
    min_cost_basis_exhaustive,  # exhaustive min-cost basis (for verification)
)
```

These are thin wrappers over the existing `bulla.coboundary.matrix_rank`. They exist to give matroid-theoretic vocabulary to code that previously had to compute submatrix ranks ad-hoc, and to enable the verification tests in `bulla/tests/test_witness_matroid.py`.

## What this verification establishes

- **Bulla's greedy repair is provably matroid-optimal** (matched against exhaustive enumeration on `n ≤ 4` examples; Edmonds 1971 covers the general case once the matroid identification is granted).
- **The two disclosure notions in the codebase are now distinguished in writing**, with the operational rule for which to use under what regime.
- **The matroid framework is a thin verification layer**, not a new mathematical structure. The witness Gram already represented this matroid; this doc and module name it.

## What this verification does NOT establish

- New matroid-theoretic theorems specific to MCP composition.
- A general framework for matroid minors under composition operations (server deletion, disclosure, bridge insertion). Such operations exist informally but require a written specification before becoming an API. Loops can be deleted (already handled by `weighted_greedy_repair`); coloops can be contracted (forced into every basis); a general minor calculus is future work.
- A Tutte polynomial computation on real corpora. This is the natural next step if the matroid framework proves useful as a research direction; deferred until there's a question that the Tutte polynomial would answer.
