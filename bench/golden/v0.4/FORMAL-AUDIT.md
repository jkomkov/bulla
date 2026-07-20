# Claim Flow v0.4 formal audit

Toolchain: repository-pinned Lean toolchain in
`papers/interpolant-envelope/lean/lean-toolchain`.

Commands:

```text
lake build InterpolantEnvelope
lake env lean InterpolantEnvelope/Axioms.lean
```

Result: clean build; no `sorry` in `ClaimFlow.lean`.

Theorems audited:

- `no_borrowed_authority`;
- `no_free_precedent`;
- `case_only_finding_cannot_derive_general_precedent`;
- `binding_precedent_only_inside_declared_reason_and_scope`;
- `same_epoch_precedent_refinement_preserves_surfaces`;
- `supersession_preserves_historical_decision`;
- `increasing_budget_does_not_change_semantic_epoch`;
- `verified_trace_append`.

Axiom report: the scope, refinement, supersession, budget, and trace theorems
are axiom-free. The finite extension and constructor-inversion proofs report
Lean's standard `propext` and, for Boolean case splitting, `Quot.sound`; they
do not depend on an added project axiom. The broader inherited
`determines_iff_separates_opposing_pairs` theorem continues to report
`Classical.choice`, `propext`, and `Quot.sound`. No theorem treats the Python
parser, cryptography, or receipt verifier as proved by this abstraction.

Abstraction-to-code tests are in `bulla/tests/test_claim_flow.py` and
`bulla/tests/test_precedent_and_cuts.py`. They exercise explicit constructors,
borrowed-authority rejection, case-only and persuasive non-application,
legislation detection, finality frontier replay, strategic low-budget
rejection, and the precedent-admission binding.
