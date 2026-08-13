# Bulla Acceptance Contract 0.1 (experimental)

An Acceptance Contract fixes the evidence a receiving system requires before a
named consequence becomes eligible. It composes ActionReceipts, separately
accepted trust context, receiver observations, coverage, corrections, and one
explicit consequence. It does not change the ActionReceipt wire format or the
stable Bulla API.

The profile's central rule is:

> The receipt preserves the claim. The receiver's policy determines what the
> claim permits.

## Fixed transaction

The alpha covers one synthetic release handoff. A deploy agent reports that an
exact build is ready in staging. A release policy fixed before acceptance also
requires an accepted rollback-test record for that build and contract.

- With no rollback record, the decision is `HOLD_FOR_EVIDENCE`.
- With a verified `PASS`, the decision is `PROCEED` and promotion is `ELIGIBLE`.
- With a verified `FAIL`, the decision is `REFUSE`.

Eligibility neither issues authorization nor attempts promotion.

## Conditional evidence requests

The missing-evidence report names a record that would be evaluated if supplied.
It does not say the test exists, will pass, or guarantees a release. Closure sets
are inclusion-minimal only inside the finite request catalog declared by this
contract. Changing the policy requires a new contract revision and hash.

## Trust boundary

The verification context is outside the bundle. It selects the accepted
contract hash, issuer keys, policy authority, adapters, and epochs. A key or
policy carried in the transaction cannot authenticate itself.

## What this result does not establish

The retained records do not establish that deployment occurred, that every
effect appears in the receiver record, or that a reported test result is worldly
truth. The implementation is project-authored, synthetic, and source-only.

## Reproduce

From the public Bulla source tree with the identity extra installed:

```bash
PYTHONPATH=bulla/src python3 bulla/examples/acceptance-contract/run_demo.py --story --out /tmp/bulla-acceptance
python3 -I bulla/spec/acceptance-contract/check.py /tmp/bulla-acceptance/missing --context /tmp/bulla-acceptance/context.json --format story
python3 -I bulla/spec/acceptance-contract/check.py /tmp/bulla-acceptance/passing --context /tmp/bulla-acceptance/context.json --format story
python3 -I bulla/spec/acceptance-contract/check.py /tmp/bulla-acceptance/failing --context /tmp/bulla-acceptance/context.json --format story
```
