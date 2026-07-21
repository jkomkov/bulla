# Bulla Claim Flow v0.4 — Experimental Specification

Profile: `bulla.claim-flow/0.4-experimental`

Status: internal, captive research profile. This document changes neither the
stable Bulla imports nor ActionReceipt, FRSL-1, or the stable result algebra.

## Constitutional boundary

A forum finding creates an institutional fact for one case, purpose, scope,
epoch, and appeal state. It is neither a world observation nor a reusable rule.
The only reusable rule is an explicit FRSL-1 reason adopted by an authority
holding a proposition-specific `ADOPT_PRECEDENT` grant. A case-only finding
applies only to its bound or canonically equivalent record; a persuasive rule
is advisory; a binding rule may mutate a semantic surface only inside its
declared reason and structured applicability scope.

The four claim transitions are closed:

1. `APPRAISE(evidence bundle, appraisal authority) -> EvidenceClaim`;
2. `FORUM_FINDING(EvidenceClaim, forum authority) -> InstitutionalFact`;
3. `ADOPT_PRECEDENT(InstitutionalFact, reason, precedential authority) -> PrecedentRule`;
4. `SETTLE(EvidenceClaim | InstitutionalFact, settlement authority) -> SettlementClaim`.

Computation, entailment, signatures, reserves, and settlement do not construct
`WorldClaim`. Each output binds the exact token and authorization receipt for
its own transition. A `ClaimFlowTrace` is one action-scoped derivation DAG, not
a global semantic graph.

## Precedent admission

`build_precedent_admission` accepts only an `ADOPTED` rule whose effect is
`BINDING_WITHIN_SCOPE`. The admission rechecks:

- final institutional-fact binding;
- precedential authority token and receipt;
- exact reason and reason vocabulary;
- semantic epoch;
- structured applicability scope;
- finite conservativity;
- preservation of prior RELY and REFUSE cells.

A fresh reason, widened scope, new protected consequence, or prior-epoch rule
returns `LEGISLATION_REQUIRED` or staleness. A later review or remedy appends a
new artifact; it does not mutate the historical decision.

## Derivation accountability

An action-relevant `RESOURCE_BOUNDED` result is valid only when a
`DerivationBudgetPolicy` was authorized before execution, permits the exact
backend hash, and precommits logical limits for models, candidate atoms,
formula nodes, branch nodes, and opposing pairs. `DerivationRunReceipt` binds
logical counters, completed regions, a replayable search-frontier hash, and the
output. Wall time and peak memory are observations, never semantic proof.

A budget selected after observing the likely result produces
`ROUTE/UNAUTHORIZED_DERIVATION_BUDGET`.

## Finality obstruction

`bulla experimental explain-finality` exhaustively enumerates finite policy
alternatives within the precommitted branch budget. It emits independently
replayable blocker sets and sufficient routes. Exact minimality is claimed only
after every declared alternative is examined. Multiple incomparable minimal
routes produce `CHOICE_REQUIRED`. Reserve delta is `NOT_APPLICABLE` for
reversible-only, human-review, and categorical-harm routes.

## Trust and claim limits

The reference implementation, Lean abstraction, benchmark, and their oracles
are all team-authored. Passing them supports `INTERNAL_CAPTIVE` only. Neither a
signature nor GitHub infrastructure is an independent witness. The profile
does not establish worldly truth, legal validity, field safety, or economic
value.
