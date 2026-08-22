# Bulla

**Receipts for Agents.**

Bulla creates portable ActionReceipts for consequential agent transactions. A
receiving system can verify the record locally, apply its own `ReliancePolicy`,
and reconcile the receipt set against its own event records.

Answerable-computing profiles for inference procurement, witnessed history,
consequence rules, and correction networks live in repository source. They are
not part of the installed package.

Glyph Standard publishes the ActionReceipt format, Bulla, and the public test
suite.

The application creates the receipt where an action is accepted or completed:
an API gateway, tool router, payment handler, or agent runtime. The model does
not need to know about Bulla or write JSON.

## Install and run one transaction

Bulla supports Python 3.10 and later. Receipt creation and file-integrity checks
run locally and require no hosted Bulla service.

```bash
python -m pip install "bulla==0.47.1"
bulla demo
```

The fixed demo creates a receipt for one constructed USD 125 payment, checks the
saved file, rejects an altered copy, and compares the receipt set with a
separately supplied receiver record containing one additional action.

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

The altered file fails its integrity check. The separate receiver record exposes
an action with no matching receipt. Neither result establishes that funds moved
or that the receiver record contains every action.

Use `bulla demo --out DIR` to choose a fresh output directory or `bulla demo
--format json` for the versioned machine report. Bulla refuses to replace an
existing path.

## Provider logs and receiver records

Provider logs are useful, and Bulla does not replace them. They usually describe
an activity stream inside the provider's system, use a provider-specific schema,
and remain under the provider's custody.

An ActionReceipt has a different job: hand the receiving party one portable
record for one transaction. If a provider exports the relevant event, binds it to the
buyer's request and accepted terms, authenticates it, and lets the buyer retain
it, that export can become evidence for an ActionReceipt. The standard format
means a buyer does not need a different log integration for every provider.

| | Provider log | ActionReceipt |
|---|---|---|
| Primary use | Operate and debug the provider | Hand one transaction to the receiving party |
| Custody | Usually controlled by the provider | Retained by each receiving party |
| Format | Provider-specific | Open and versioned |
| Scope | System activity stream | One action or transaction |
| Verification | Whatever the provider exposes | Local checks defined by the format |
| Completeness | Not assumed | Not assumed |

Buyers, gateways, and marketplaces can require receipt support before routing
work or accepting a delivery. A provider that supports the format can qualify
for those workflows and use the same agreed transaction file for acceptance,
audit, and disputes. This repository does not claim that receipts improve
payment speed, insurance pricing, or reputation.

## The receipt is not the decision

An ActionReceipt preserves the request, accepted permissions and limits,
reported result, and supplied evidence. It does not make the next decision.
The customer, auditor, marketplace, or downstream agent applies its own policy
to the retained record.

An eligible consequence is still separate from authority to execute it. A
receipt does not establish that the reported event occurred or that the
receiver record contains every relevant event.

## Add Bulla where the application acts

`wrap_action` creates the JSON file around the application call:

```python
from bulla import wrap_action

with wrap_action(
    "payments.charge",
    {"event_id": "pay-1", "amount_minor": 12500},
) as action:
    action.set_result("sha256:" + "0" * 64)

receipt = action.receipt
```

The receipt can record the action claim, declared authority and limits, supplied
evidence references, and challenge path. The exact fields are defined by the
[ActionReceipt standard](https://glyphstandard.com/spec).

## Verify one receipt

Download the constructed payment receipt and check it locally:

```bash
curl -fsSLo constructed-payment-authorization-v0.2.json \
  https://glyphstandard.com/examples/payment-authorization-v0.2.json
bulla receipt verify constructed-payment-authorization-v0.2.json --format json
```

The receipt records a USD 125.00 charge, declares a USD 200.00 limit, and carries
an executable rule for checking the limit. The dimensional report includes:

```text
integrity             VERIFIED
authenticity          UNVERIFIED
authority             UNAUTHENTICATED
declared_bounds       CONFORMS
grounding             SELF_ASSERTED
recourse              NAMED
reachability          UNVERIFIED
reliance_decision     NOT_COMPUTED
```

These are separate results, not one global safety or truth verdict. The same
receipt is available at `spec/vectors/payment-authorization.json`; its expected
result is pinned in `spec/vectors/expected.json` and recomputed in CI.

## Check receipt coverage

`event_coverage` compares valid receipts with a receiver record supplied outside
the receipt set. For an exact saved-record match, add `record_sha256` using
`observed_record_sha256`; the receipt must carry the same digest in its result or
evidence references. Without that field, coverage is action-ID correlation.

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

Receipt integrity is unchanged in the second comparison. The supplied receiver
record contains one action with no matching receipt. Bulla does not establish
that the record itself is complete.

## Keep the verification kit

The package carries the v0.2 specification, constructed examples, expected
dimensional reports, and a zero-dependency checker as one immutable archive:

```bash
bulla receipt kit --out action-receipt-v0.2-verification-kit.zip
```

Expected archive digest:

```text
sha256:8f2cdd16bcbd1a1121f49545b6a6512872b188221ca30ec054dfd6b2fb2142ab
```

After extracting the archive, run `python3 verify.py` to check the kit or run
its standalone checker against a saved v0.2 receipt:

```bash
python3 verify.py receipt RECEIPT.json --format text
```

The checker imports no Bulla code and makes no network request. The manifest
checks the archive contents. Authenticate the archive itself with the detached
digest or signed release receipt.

`bulla receipt drill` runs both the installed Bulla checker and the retained
standalone checker while network access is denied:

```bash
bulla receipt drill RECEIPT.json --format text
```

## Published package and source profiles

Bulla 0.47.1 ships ActionReceipt creation and verification, explicit reliance
policy, and coverage reconciliation. The following examples are experimental
repository-source profiles. They do not add installed commands or stable Python
exports.

## Experimental: evaluate one receiving policy

The source-only [Acceptance Contract
alpha](spec/acceptance-contract/PROFILE.md) demonstrates an agent-to-agent
deployment handoff. A deploy agent reports that staging is ready; the receiving
release policy also requires a rollback-test record for the exact build and
contract. Missing evidence produces a conditional request, `PASS` permits
eligibility, and `FAIL` refuses it. Even the eligible result leaves
authorization unissued and execution unattempted.

```bash
PYTHONPATH=src python3 examples/acceptance-contract/run_demo.py \
  --story --out /tmp/bulla-acceptance
python3 -I spec/acceptance-contract/check.py \
  /tmp/bulla-acceptance/missing \
  --context /tmp/bulla-acceptance/context.json \
  --format story
```

This profile is inspectable in source and excluded from the installed package.

## Experimental: trace a correction through declared reliance

The source-only [Reliance Map profile](spec/reliance-map/PROFILE.md) starts
from an accepted declared graph. When an authenticated correction notice
targets one source digest, the verifier identifies the exact descendants that
require rechecking. Complete branches with no declared path are reported
separately; incomplete lineage remains unresolved.

The constructed corpus contains 10,000 declared decisions: 2,500 require
rechecking, 5,000 have no declared path under the accepted graph, and 2,500
remain unresolved because their lineage is incomplete. Python and standalone
Node produce the same report.

```bash
PYTHONPATH=src python3 spec/reliance-map/check.py \
  spec/reliance-map/generated/graph.json \
  --ledger spec/reliance-map/generated/correction-ledger.json \
  --context spec/reliance-map/generated/context.json

node spec/reliance-map/check.mjs \
  spec/reliance-map/generated/graph.json \
  --ledger spec/reliance-map/generated/correction-ledger.json \
  --context spec/reliance-map/generated/context.json
```

The map does not capture dependencies automatically, establish that a
correction is true, reverse an action, or authorize a consequence. The profile
and its Handoff Admission parsing dependency are excluded from the installed
package.

## Experimental: bind recourse to witness equivocation

The source-only [Witness Covenant profile](spec/witness-covenant/PROFILE.md)
binds a dedicated test-ledger allocation to one objective fault: two authentic,
same-size checkpoints for the same log and epoch carry different roots. The
verifier keeps checkpoint authenticity, equivocation, challenge state, capital,
eligibility, authorization, and an attempted ledger event separate.

A bond changes recourse only. It does not make a receipt true, make the witness
independent, establish custody or collection, or upgrade a provider claim.

```bash
PYTHONPATH=src python3 spec/witness-covenant/check.py \
  spec/witness-covenant/vectors/fork-closed \
  --context spec/witness-covenant/contexts/fork-closed.json

node spec/witness-covenant/check.mjs \
  spec/witness-covenant/vectors/fork-closed \
  --context spec/witness-covenant/contexts/fork-closed.json
```

## Experimental: verify an answerability network

The source-only [Answerability Network
profile](spec/answerability-network/PROFILE.md) composes a constructed inference
procurement, ActionReceipt, witnessed checkpoint, covenant, and 10,000-decision
declared reliance graph. When the verifier receives two authentic incompatible
checkpoint views, it identifies 2,500 declared descendants for recheck, leaves
5,000 complete unrelated decisions unaffected under the accepted graph, and
keeps 2,500 incomplete branches unresolved.

Only the witness covenant's bounded remedy can become eligible. Provider claims
and unrelated branches do not inherit the witness fault.

```bash
PYTHONPATH=src python3 spec/answerability-network/check.py \
  spec/answerability-network/vectors/fork-closed \
  --context spec/answerability-network/contexts/fork-closed.json

node spec/answerability-network/check.mjs \
  spec/answerability-network/vectors/fork-closed \
  --context spec/answerability-network/contexts/fork-closed.json
```

## Where Bulla fits

- **Payments:** record the request, authorization, amount limits, supplied
  evidence, and dispute path.
- **Permissions and writes:** bind an operation to its stated principal and
  policy.
- **Gateways and provider handoffs:** retain the request and terms that crossed
  an organizational boundary.

ActionReceipt v0.2 remains the normative default. ActionReceipt v0.4 is an
opt-in experimental draft. Source-only research profiles remain inspectable on
GitHub but are excluded from the installed package unless the distribution
policy explicitly releases them.

## Limits

- File integrity does not establish that the reported action occurred or that
  every recorded field is true.
- A signature authenticates an accepted key; it does not create authority.
- Coverage is relative to the supplied receiver record.
- Bulla does not establish that the supplied receiver record is complete.
- Unsigned receipts remain unauthenticated.
- Reliance remains `NOT_COMPUTED` unless a reliance policy is supplied.

Multidimensional reports reject Boolean coercion. Callers inspect the named
dimensions or apply an explicit reliance policy.

## Documentation

- [Quickstart](https://glyphstandard.com/bulla/quickstart)
- [Bulla documentation](https://glyphstandard.com/bulla)
- [ActionReceipt standard](https://glyphstandard.com/spec)
- [Buyer requirements](https://glyphstandard.com/buyers)
- [Ecosystem map](https://glyphstandard.com/bulla/ecosystem)
- [Answerable Computing](https://glyphstandard.com/bulla/answerable-computing)
- [Status and evidence](https://glyphstandard.com/evidence)
- [Complete capability reference](https://github.com/jkomkov/bulla/blob/main/docs/CAPABILITIES.md)
- [Source-only experimental research](https://glyphstandard.com/bulla/experimental)
- [Changelog](https://github.com/jkomkov/bulla/blob/main/CHANGELOG.md)
- [Release lineage](https://github.com/jkomkov/bulla/blob/main/docs/RELEASE-LINEAGE.md)
- [Security policy](https://github.com/jkomkov/bulla/security/policy)

## License and security

Bulla is the developer toolkit. ActionReceipt is the open format implemented by
Bulla. Glyph Standard, Inc. maintains the specification; Res Agentica contains
the broader research program.

Bulla is licensed under the
[Apache License 2.0](https://github.com/jkomkov/bulla/blob/main/LICENSE).
Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jkomkov/bulla/security/advisories/new)
or the [security policy](https://github.com/jkomkov/bulla/blob/main/SECURITY.md).
