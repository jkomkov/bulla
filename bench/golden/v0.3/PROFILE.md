# Golden Gate v0.3 — Boundary Qualification Profile

Profile: `bulla.golden-suite/0.3-experimental`

The normative semantic-boundary protocol is defined separately in
`bulla/spec/semantic-boundary-v0.3-experimental.md`. This profile only fixes
the captive cases, oracle rules, scoring, and qualification gates.

## F9: boundary causes

F9 contains twelve cases for each substantive boundary and twelve for the
separate meta-level derivation stratum:

| Stratum | Required safe exit |
|---|---|
| Semantic | `ROUTE/SEMANTIC_INDETERMINACY` |
| Grounding | `ROUTE/UNRESOLVED_GROUNDING` |
| Authority | `TERM_STALE/CLAIM_CHAIN_BINDING_MISMATCH` |
| Derivation | `ROUTE/RESOURCE_BOUNDED_DERIVATION` |

No result may infer qW from qE, or infer either qW or qE from qS. A derivation
limit is reported separately and never treated as a substantive fact.

## F10: complexity bombs

F10 contains four seeds at widths 5 through 12 for each family:

- `irrelevant_literal`;
- `sparse_interaction`;
- `parity_frontier`;
- `ground_hidden`.

The checker evaluates positive and negative formulas on every finite vector.
`COMPILED` requires complete labeled coverage inside the 512-node bound.
`PARTIAL` may cover less, but neither certified region may contain an opposing
label. Coverage is reported rather than converted into correctness.

## Gates

- F9 denominator: 48; F10 denominator: 128.
- Zero unsafe F9 or F10 result.
- All 70 prior v0.2 `PARTIAL` cases report p10, median, and p90 RELY, REFUSE,
  ESCALATE, and joint certified mass.
- The 24-case scout never treats a definition as evidence that its premise
  obtains in the world.
- Unknown fields, malformed formulas, hash changes, count changes, and source
  denominator changes fail the standalone verifier.
- External status remains blocked until real participants provide evidence.
