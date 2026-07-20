# Semantic Settlement v0.1 — Frozen Internal Handoff

Date: 2026-07-18

Frozen engine commit: `30619618ed74c134aa94cbf7c6f5f8ef440df460`

## Classification

| Result | Status | Boundary |
|---|---|---|
| Differential refinement authorization | verified | exact signed receipt, role, scope, epoch, snapshot |
| Witnessed supersession | verified | two distinct configured signed checkpoints and claim-bound inclusions |
| Closure-bound semantic epoch | verified | declared warrant only; no open-world completeness claim |
| Conflict non-mutation | verified | finite declared semantics and Lean abstraction |
| Worst-case reserve and release | verified | integer declared outcomes; no probabilities or custody |
| Procurement shadow | demonstrated | simulated adapter only |
| Zero-import finality replay | demonstrated | one frozen internal vector; more blind vectors may be added |
| 10x exhaustion conversion | demonstrated | 69 bounded reruns: 45 synthesis and 24 planning |
| Exit algebra | verified | 60/60 expected exits; 12 each across five statuses |
| Adaptive observability scout | demonstrated | 60 bounded cases; no unsafe leaves; threshold met, not action-gating |
| Hostile challenge receipts | demonstrated | five signed internal challenges and signed hash-bound dispositions |
| 12-seam live operational slice | blocked | external authors/adjudicators unavailable in this execution context |
| Independent external implementation | blocked | packet prepared; no implementer result received |
| Production settlement | out of scope | no custody, collectibility, collateral, stake, or slashing |

No stable Bulla API, FRSL-1 expansion, public safety claim, generic standards
proposal, or efficacy statistic is promoted by this handoff.

The 10x conversion produced 6 `COMPILED`, 18 `ESCALATE`, and 21 time-ceiling
`INDETERMINATE` synthesis exits; planning produced 10 `PLANNED` and 14 exact
opposing-pair-bound `INDETERMINATE` exits. The frozen 240-case file hash was
identical before and after. The adaptive scout Pareto-improved all 60 declared
eligible fixtures with zero unsafe terminal leaves and 333,333 ppm median
disclosure reduction. This clears the preregistered scout threshold only: it
does not integrate adaptive planning into the finality controller.

## Reproduction

```sh
cd bulla
PYTHONPATH=src pytest -q tests/test_semantic_settlement.py tests/test_adaptive_observability.py
PYTHONPATH=src python examples/semantic-settlement/demo.py
python -I scripts/verify_semantic_finality.py \
  bench/invention/semantic-settlement/reproduction-vectors/procurement-provisional.internal.json
cd ../papers/interpolant-envelope/lean
/Users/jkomkov/.elan/bin/lake build
/Users/jkomkov/.elan/bin/lake env lean InterpolantEnvelope/Axioms.lean
```

The external implementer receives the profile and `.blind.json` vector, not the
checker, internal vector, or answer key.
