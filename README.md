# Bulla

**Create, verify, and reconcile portable receipts for consequential agent actions.**

Glyph defines a portable receipt for consequential agent actions. Bulla is the
Python reference implementation. It creates and verifies ActionReceipts locally
and computes coverage against a separately supplied action record.

Receipt verification detects changes in the records supplied to the verifier.
Coverage reports actions in a supplied action record that have no matching
receipt. These checks answer different questions and remain separate.

## Install

Bulla supports Python 3.10 and later. Core receipt creation and digest
verification require no hosted service.

```bash
python -m pip install "bulla==0.47.1"
bulla demo
```

## Run one action

`bulla demo` runs one fixed local action through Bulla, emits its receipt
automatically, and retains every artifact in a new directory. It then changes a
copy of the receipt, sends a second action around the receipting boundary, and
compares the receipt set with the constructed receiver record.

```text
FIRST ACTION DEMO · CONSTRUCTED LOCAL SCENARIO

ACTION
recorded action       payments.charge
amount                USD 125.00
declared limit        USD 200.00

ALTERATION CONTROL
record integrity      FAILED
original receipt      UNCHANGED

OMISSION CONTROL
coverage before       1/1
coverage after        1/2
unreceipted action    pay_demo_043
original integrity    VERIFIED
```

The receipt caught alteration. The receiver's action record caught omission.
The receiver is constructed, and neither record establishes that funds moved.
The final stage runs Bulla and the retained standalone checker with network
access denied.

Use `bulla demo --out DIR` to choose a new output directory, or
`bulla demo --format json` for the versioned CLI report. Bulla refuses to
replace an existing path.

In application code, `wrap_action` emits the JSON record. Applications do not
hand-author the wire format.

## Why retain the receipt

A *bulla* was the clay envelope sealed around a record so it could survive the
absence of the parties who made it. Bulla applies that discipline to agent
actions: the action may finish in milliseconds, but a retained receipt keeps
its declared authority, evidence, limits, and challenge path available to the
next system or institution.

The format is intended for the customer, auditor, dispute forum, or underwriter
who arrives after the agent and its runtime are gone and applies its own checks.

## Verify one receipt

Download the constructed canonical payment receipt and verify it locally:

```bash
curl -fsSLo constructed-payment-authorization-v0.2.json \
  https://glyphstandard.com/examples/payment-authorization-v0.2.json
bulla receipt verify constructed-payment-authorization-v0.2.json --format json
```

The constructed receipt records a USD 125.00 charge, declares a USD 200.00
limit, and carries an executable convention that recomputes conformance.
The checked result is:

```text
integrity            VERIFIED
authenticity         UNVERIFIED
authority            UNAUTHENTICATED
declared_bounds       CONFORMS
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

## Retain the verification kit

The package carries the v0.2 specification, constructed vectors, expected
dimensional verdicts, and a zero-dependency checker as one immutable archive:

```bash
bulla receipt kit --out action-receipt-v0.2-verification-kit.zip
```

Expected archive digest:

```text
sha256:8f2cdd16bcbd1a1121f49545b6a6512872b188221ca30ec054dfd6b2fb2142ab
```

After extracting the archive, run `python3 verify.py` to check the kit itself,
or run its zero-dependency checker against a retained v0.2 receipt:

```bash
python3 verify.py receipt RECEIPT.json --format text
```

The checker imports no Bulla code and makes no network request. The manifest
checks the retained contents; authenticate the archive itself with the detached
digest or the signed release receipt.

## Rehearse verification without dependencies

`bulla receipt drill` checks one normative v0.2 receipt with Bulla and with the
retained standalone checker while network access is denied:

```bash
bulla receipt drill RECEIPT.json --format text
```

The report separates facts recomputed from retained bytes from claims that need
another record and dimensions that cannot be decided from the supplied
material. It does not establish event occurrence, live authority, recourse
reachability, receipt coverage, or a reliance decision.

When `--kit` is used, the detached digest checks the supplied bytes and Bulla
also requires those bytes to match the kit retained inside the installed
distribution before it executes the standalone checker. The sidecar alone does
not establish publisher identity or authorize unfamiliar code.

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
the receipt set. For an exact retained-record match, add `record_sha256` using
`observed_record_sha256`; the receipt must carry the same digest in its result
or evidence references. Without that field, coverage is action-id correlation:

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
- [Release lineage](https://github.com/jkomkov/bulla/blob/main/docs/RELEASE-LINEAGE.md)
- [Security policy](https://github.com/jkomkov/bulla/security/policy)

## License and security

Bulla is licensed under the
[Apache License 2.0](https://github.com/jkomkov/bulla/blob/main/LICENSE).
Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jkomkov/bulla/security/advisories/new)
or the [security policy](https://github.com/jkomkov/bulla/blob/main/SECURITY.md).
