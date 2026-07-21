# Semantic Finality Profile 0.1 (Experimental)

Profile identifier: `bulla.semantic-finality/0.1-experimental`

Status: research-only. This profile is not exported as a stable Bulla API and
does not modify ActionReceipt v0.2/v0.3, FRSL-1, settlement custody, or any
public safety claim.

## Claim boundary

Every ambiguity statement means **ambiguity relative to the named finite model
class and `ModelClosureWarrant`**. `FINITE_EXACT` and `BOUNDED_EXACT` describe
the declared class, not the world. `OPEN_WORLD` and `UNKNOWN_COVERAGE` never
silently authorize finality. A simulated escrow proves protocol mechanics only;
it does not prove custody or collectibility.

## Constitutional objects

`AuthorityRegime` has three non-interchangeable permissions:

1. operative authority governs the current term;
2. refinement authority may admit narrowing evidence or precedent;
3. supersession authority may create a new epoch.

A refinement authorization is an authority-authenticated ActionReceipt with
`action.type = bulla.semantic.refine.authorize`. Its subject is exactly:

```json
{
  "admission_hash": "sha256:...",
  "prior_snapshot_hash": "sha256:...",
  "scope": "...",
  "authority_epoch": "sha256:...",
  "semantic_epoch": "sha256:..."
}
```

The envelope must have `retention_class = authority-permanent`, the exact
configured refinement principal and policy, and the exact permitted scope.
Revision uses two separately signed ActionReceipts,
`bulla.semantic.revise.authorize` and `bulla.term.supersede`, with identical
subjects binding the prior snapshot, prior/new authority epochs, reason, new
closure-warrant hash, and scope. A revision is accepted only after the same
claim is included under authenticated checkpoints from exactly two distinct
configured witness operators.

`ModelClosureWarrant.status` is one of:

- `FINITE_EXACT`
- `BOUNDED_EXACT`
- `EXPERT_ATTESTED`
- `EMPIRICALLY_STRESSED`
- `OPEN_WORLD`
- `UNKNOWN_COVERAGE`

The warrant also binds the model class, generation method, exclusions, domain
authority, adversarial expansion evidence, and scope. `EnvelopeSnapshot`
commits to `closure_warrant_hash` and
`semantic_epoch = H(authority_epoch, closure_warrant_hash)`. A changed authority,
model class, or warrant therefore stales the old term.

`ObservationAuthorization.basis` is `CONSENT`, `CONTRACT`, `REGULATION`, `DUTY`,
or `ORDER`. It records an asserted authorization basis; `legal_validity` is
always `not_asserted`. `ObservationConstitution` filters signed authorizations
and offers before optimization by observable allow/deny lists, purpose, reuse,
componentwise burden ceiling, provider, warrant class, basis, retention, and
challenge policy. Burden coordinates are never converted to money.

When independently warranted claims have an empty joint declared model set and
neither authority may supersede the other, the admission outcome is `CONFLICT`.
The claims and their commitments enter a quarantine certificate, the operative
state hash remains unchanged, and the transition is `ROUTE` to the authored
forum. `CONFLICT` is not a fifth state mutation.

## Finality objects

`ConsequenceProfile` binds an action, integer currency microunits, target
arguments, mutually declared FRSL consequence classes and losses, maximum
credible loss, settlement target, and external verifier.

For represented outcomes `W`, the v0.1 reserve is:

```text
worst_case_loss = max(loss(outcome) for outcome in W)
required_reserve = worst_case_loss + explicit_model_risk_buffer
```

There are no probabilities, portfolio netting, expected shortfall, shared
collateral, or custody semantics. A reserve release requires the same action and
semantic epoch, set inclusion of represented outcomes, and a non-increasing
required reserve.

`FinalityAssessment` is non-Boolean. Its status is `FINALIZE`,
`EXECUTE_PROVISIONALLY`, `REQUEST_EVIDENCE`, `ROUTE`, `REFUSE`, or `TERM_STALE`.
Implementations must apply this order:

1. epoch or closure mismatch: `TERM_STALE`;
2. conflict certificate: `ROUTE/CONFLICT`;
3. certified refusal: `REFUSE`;
4. certified reliance plus policy-sufficient, non-open closure and threshold:
   `FINALIZE`;
5. residual ambiguity plus exact reserve, exact external lock, adequate closure,
   and permitted action: `EXECUTE_PROVISIONALLY`;
6. constitutionally permitted enrichment: `REQUEST_EVIDENCE`;
7. multiple incomparable routes without authored priority:
   `ROUTE/CHOICE_REQUIRED`;
8. otherwise: `ROUTE`.

An authored resolution order may select among routes. Without one the
controller presents alternatives and routes; it never scalarizes the burden
vector.

Open ActionReceipt types are `bulla.finality.assess`,
`bulla.finality.reserve`, `bulla.finality.release`, and
`bulla.finality.finalize`.

## Canonicalization and replay

All profile hashes use Bulla canonical JSON v2: UTF-8 bytes of JSON with keys
sorted, separators `,` and `:`, and Python-compatible ASCII escaping, prefixed
as `sha256:<64 lowercase hex>`. Unknown or missing fields fail closed on typed
library surfaces.

Internal zero-import replay is available in
`scripts/verify_semantic_finality.py`. External reproduction receives the
written profile and `.blind.json` inputs only—never Bulla's checker or the
internal answer key. Agreement is evaluated later by canonical assessment hash.

## Pilot analysis gate

The pilot may report enrollment, schema failures, turnaround, replay, and
participant friction before efficacy analysis. The analysis action remains
refused until all of these hold simultaneously:

- exactly 300 accepted seams;
- exactly 100 per domain and 50 in each of six domain/stratum cells;
- at least three authors and three adjudicators, disjoint from each other and
  from the implementation team;
- exactly two signed adjudications per seam.

The 12-seam operational slice does not open this gate.
