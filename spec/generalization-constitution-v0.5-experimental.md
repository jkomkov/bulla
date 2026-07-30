# Bulla Generalization Constitution v0.5 — Experimental Specification

Profile: `bulla.claim-flow/0.5-experimental`

Status: internal experimental profile. It does not alter stable imports,
ActionReceipt, FRSL-1, or the stable result algebra. Claim Flow v0.4 remains
frozen and replayable; v0.5 is a stricter layer over it.

## No free generalization

A forum finding establishes an institutional fact for its bound case and
purpose. Four later acts remain distinct:

1. `PROPOSE_PRECEDENT` records a reason, scope, exclusions, protected
   consequences, extractor, source finding, and epoch. A candidate is inert.
2. `ADOPT_PRECEDENT` requires the existing proposition-specific precedential
   authority and replays reason, scope, conservativity, refusal, and epoch
   checks.
3. `FIND_APPLICABILITY` requires forum authority for the later case and binds a
   replayable reason evaluation. Adoption does not decide applicability.
4. `SETTLE` requires its existing settlement authority and the adopted rule
   plus applicability finding. Neither predecessor supplies that authority.

A non-application emits a `DistinctionCertificate` naming the fresh reason,
authority, epoch, evidence policy, closure, harm, scope, resource, or
case-only/persuasive boundary. The certificate is exact only when the declared
finite comparison is exhausted.

## Finite generalization frontier

The reference engine models candidate scopes as a finite Boolean lattice. A
bit added to a mask widens represented scope. Safety is relative to a fixed
model class, closure warrant, authority regime, harm constitution, and semantic
epoch.

- certified safety is downward-closed;
- represented yield is monotone under widening;
- an unsafe witness rejects every super-scope containing its required mask;
- maximal safe scopes form an antichain;
- multiple maximal scopes are returned as `CHOICE_REQUIRED` and are never
  selected by the engine;
- incomplete search returns a safe antichain with
  `completeness = UNRESOLVED` and a replayable frontier hash.

Exact output is checked against exhaustive enumeration through width 12. The
optimized path uses deterministic wide-first search, counterexample pruning,
bit masks, and memoized witness evaluation. A forum may later adopt one scope;
the engine has no forum authority.

## Effect constitution

An `EffectWarrant` binds an FRSL-1 effect predicate, protected effect hashes,
harm class, scope, epoch, authority regime, and receipt. The effect classifier
accepts no action-name input. Renaming, wrappers, multi-step decomposition,
proxy fields, repeated temporary acts, and affiliated executors therefore
cannot change a categorical or human-review barrier while the protected effect
is unchanged.

## Evidence language

Every `CompoundingObservation` binds the corpus, author origin, adjudication
origin, replay status, closure warrant, and external participant counts. A
naked `DEMONSTRATED_COMPOUNDING` string is not a v0.5 result. For the v0.4
team-authored, machine-planted, internally replayed corpus, the canonical
display is:

> Compounding observed — internal captive lineage benchmark.

Captive controls can find unsoundness, generator echo, and regressions. They do
not establish foreign semantic validity, independent interpretation, field
utility, or open-world safety.

## Formal and executable correspondence

`InterpolantEnvelope.Generalization` proves the state non-mutation,
premise-separation, downward-closure, unsafe-super-scope, monotone-yield,
budget-monotonicity, effect-barrier, and historical-preservation abstractions.
Executable tests separately bind those abstractions to canonical artifacts.
The Lean statements are finite/order-theoretic; they do not certify Python or
the adequacy of an authored model class by themselves.
