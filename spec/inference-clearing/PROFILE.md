# Bulla Recheckable Inference 0.1 Experimental

Technical profile: `bulla.inference-clearing/0.1-experimental`

Maturity: `experimental · SOURCE_ONLY · synthetic-public · team-operated · A0/J0/I0/W0 · r0`

## Definition

Bulla packages an inference order, returned bytes, retained computation,
receiver record, and buyer policy into a clearing record another machine can
inspect after the provider disappears.

The canonical result is intentionally narrow:

> Two providers returned the same answer. After both disappeared, one result
> could be recomputed from retained evidence. The other could only be taken on
> the provider's word.

Reproduction establishes a computational relation. It does not establish which
model the provider historically ran, whether the answer is true, whether the
receiver record is complete, or whether funds moved.

## Evidence dimensions

The relation report keeps these fields independent:

- `output_binding`: `VERIFIED | FAILED | NOT_COMPUTED`
- `model_binding`: `TERM_BOUND | UNBOUND | UNAVAILABLE | NOT_COMPUTED`
- `relation_reproduction`: `REPRODUCED | FAILED | UNAVAILABLE | NOT_COMPUTED`
- `provider_process_claim`: `SELF_ASSERTED | ABSENT`
- `provider_execution_occurrence`: `NOT_ESTABLISHED`

The deterministic adapter can establish only that the retained, term-bound
model maps the retained input to the returned bytes. Witness inclusion,
signatures, guarantees, collateral, and successful recomputation cannot upgrade
historical execution occurrence.

## Terms and buyer policy

Before provider acceptance, the term document binds the input hash, exact
expected model hash, output schema, receiver anchor, buyer policy hash, price,
consequence, relation-evidence requirements, and challenge path. Provider
acceptance binds the complete term root, expected model hash, execution-report
hash, and closed provider-process claim before receiver delivery.

The external buyer context supplies accepted issuers, roots, adapters, policy,
and exact evidence requirements. No policy field has an implicit default, and
the bundle cannot bootstrap trust.

## Canonical bundles

- `opaque`: output bound; model and relation unavailable; `REFUSE`;
  payment `INELIGIBLE`.
- `recheckable`: exact model term-bound; relation reproduced; `RELY`; payment
  `ELIGIBLE`; authorization `NOT_ISSUED`; settlement `NOT_ATTEMPTED`.
- `bypass`: the same retained computation plus `effect-bypass-001` in the
  receiver record without a matching receipt; coverage `1/2`; `REFUSE`;
  payment `INELIGIBLE`.

Only these three bundles are tracked. Hostile variants are generated in
temporary directories from the closed mutation registry.

## Consequence stages

The verifier reports these dimensions without collapsing them:

- reliance decision;
- payment eligibility;
- settlement authorization; and
- settlement execution.

Eligibility does not imply authorization or execution. A downstream invalid
authorization or execution attempt does not retroactively rewrite a correctly
computed eligibility result.

## Sequence and trust boundary

```text
inference.order
→ inference.route
→ inference.accept
→ inference.delivery
→ assurance.collateral.bind
[→ assurance.guarantee.issue]
→ inference.coverage.checkpoint
→ bulla.rely
[→ assurance.settlement.authorize → rail.settlement.report]
```

Every receipt binds the exact term root, transaction, issuer role, and prior
event/attestation pair. The receiver signs the exact coverage-record hash,
anchor, and counts before reliance. This authenticates the supplied receiver
record; it does not establish that the record is complete.

## Exit classes

- `2`: malformed, unsafe, unsupported, or resource-limited input.
- `1`: integrity, signature, role, lineage, or required trust-policy failure.
- `0`: multidimensional verification completed, including legitimate
  `REFUSE` and `INELIGIBLE` outcomes.

No global `valid`, `safe`, or `trusted` verdict exists.

## Reproduce

```bash
PYTHONPATH=bulla/src python3 bulla/examples/inference-clearing/run_demo.py --story --out "$DIR"
python3 -I bulla/spec/inference-clearing/check.py "$DIR/recheckable" \
  --context "$DIR/contexts/recheckable.json"
node bulla/spec/inference-clearing/check.mjs "$DIR/recheckable" \
  --context "$DIR/contexts/recheckable.json"
```

The runtime accepts only the fixed task. Its localhost provider processes
terminate before Python and Node verify the retained bundles offline. Failure
injections stop after the provider response, after receiver delivery, or before
coverage reconciliation without issuing settlement authorization.
