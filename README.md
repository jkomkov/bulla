# Bulla

**Create, verify, and reconcile portable receipts for consequential agent actions.**

Glyph defines a portable receipt for consequential agent actions. Bulla is the
Python reference implementation. It creates and verifies ActionReceipts locally
and computes coverage against a separately supplied action record.

Receipt verification detects changes in the records supplied to the verifier.
Coverage reports actions in a supplied action record that have no matching
receipt. These checks answer different questions and remain separate.

A *bulla* was the clay envelope sealed around a record so it could survive the
absence of the parties who made it. Bulla applies that discipline to agent
actions: the action may finish in milliseconds, but its authority, evidence,
limits, and challenge path remain available to the next system or institution.

The receipt is addressed to the customer, auditor, dispute forum, or underwriter
who arrives after the agent and its runtime are gone.

## Install

Bulla supports Python 3.10 and later. Core receipt creation and digest
verification require no hosted service.

```bash
python -m pip install bulla
```

## Verify one receipt

Download the canonical payment receipt and verify it locally:

```bash
curl -fsSLo payment-authorization-v0.2.json \
  https://glyphstandard.com/examples/payment-authorization-v0.2.json
bulla receipt verify payment-authorization-v0.2.json --format json
```

The receipt records a USD 125.00 charge under a structured USD 200.00 maximum.
The checked result is:

```text
integrity            VERIFIED
authenticity         UNVERIFIED
authority            UNAUTHENTICATED
scope                 CONFORMS
grounding             SELF_ASSERTED
recourse              NAMED
reachability          UNVERIFIED
reliance_decision     NOT_COMPUTED
```

The same receipt is available offline at
`spec/vectors/payment-authorization.json`. Its expected result is pinned in
`spec/vectors/expected.json` and recomputed in CI.

Change `amount_minor` from `12500` to `12501` without recomputing the hashes.
The verifier returns nonzero, reports a content-hash mismatch, and suppresses
content-dependent conclusions.

## Create one receipt

```bash
bulla receipt create \
  --type demo.write \
  --subject path=/tmp/example.txt \
  --forum-endpoint https://example.invalid/challenge \
  --forum-root fixture:independently-pinned-root \
  --out receipt.json
bulla receipt verify receipt.json --format json
```

The unsigned result reaches the digest verification rung. It does not
authenticate the authority or compute a reliance decision.

## Check coverage

`event_coverage` compares valid receipts with an action record supplied outside
the receipt set:

```python
from bulla.action_receipt import verify_receipt
from bulla.coverage import event_coverage
from bulla.wrap import receipt_for

receipt = receipt_for("network.egress", {"event_id": "action-001"})
assert verify_receipt(receipt).ok

complete = event_coverage([{"id": "action-001"}], [receipt])
assert complete["coverage"] == 1.0
assert complete["unreceipted_delta"] == []

with_gap = event_coverage(
    [{"id": "action-001"}, {"id": "action-002"}],
    [receipt],
)
assert with_gap["coverage"] == 0.5
assert with_gap["unreceipted_delta"] == ["action-002"]
```

Receipt integrity remains unchanged in the second comparison. The supplied
action record contains one action with no matching receipt.

## Where Bulla fits

- **Payments:** record authorization, amount bounds, evidence, and recourse.
- **Permissions and writes:** bind a consequential operation to its principal
  and policy.
- **Gateways and provider handoffs:** retain the action and authority references
  that crossed the boundary.

ActionReceipt v0.2 remains the normative and default format. ActionReceipt v0.4
is available as an opt-in experimental draft. Source-only research profiles
remain inspectable on GitHub but are excluded from the installed package unless
the distribution policy explicitly lists them as released.

## Limits

- Receipt integrity does not establish the truth of every recorded field.
- Coverage is relative to the supplied action record.
- Bulla does not establish that the supplied action record is complete.
- Unsigned receipts remain unauthenticated.
- Reliance remains `NOT_COMPUTED` unless a reliance policy is supplied.

Multidimensional verification results reject Boolean coercion. Callers must
inspect the named dimensions or apply an explicit reliance policy.

## Documentation

- [Verification-first quickstart](https://glyphstandard.com/bulla/quickstart)
- [Bulla documentation](https://glyphstandard.com/bulla)
- [ActionReceipt standard](https://glyphstandard.com/spec)
- [Status and evidence](https://glyphstandard.com/status)
- [Complete capability reference](https://github.com/jkomkov/bulla/blob/main/docs/CAPABILITIES.md)
- [Source-only experimental research](https://glyphstandard.com/bulla/experimental)
- [Changelog](https://github.com/jkomkov/bulla/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/jkomkov/bulla/security/policy)

## License and security

Bulla is licensed under the
[Apache License 2.0](https://github.com/jkomkov/bulla/blob/main/LICENSE).
Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jkomkov/bulla/security/advisories/new)
or the [security policy](https://github.com/jkomkov/bulla/blob/main/SECURITY.md).
