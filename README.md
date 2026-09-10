# Bulla

Bulla creates portable ActionReceipts for software purchases, tool calls, and
other consequential actions. Applications retain the records, verify them
locally, apply receiver-supplied policies, and reconcile them with their own
event records.

Use Bulla when an application needs to give another party a transaction record
it can retain and inspect without continued access to the issuing service.
The application creates the receipt where it accepts or completes the action;
the model does not need to generate receipt JSON.

## Install and try the local demo

Python 3.10 or later is required. Receipt creation and local file checks need
no hosted Bulla service or account.

```bash
python -m pip install "bulla==0.49.3"
bulla demo
```

Each run creates a fresh directory and prints its location after `artifacts`.
The constructed demo records a USD 125 payment request with a declared USD 200
limit. It keeps the original receipt, changes a copy, and compares the receipts
with a separately supplied customer record. No funds move.

| Demo condition | Recorded result |
| --- | --- |
| Original receipt | Integrity check passes |
| Changed copy | Integrity check fails |
| Additional customer-record entry | One action has no matching receipt |

The altered file fails its integrity check because its amount no longer matches
its stored commitments. This unsigned example is not an authentication test:
someone could change a record and recompute its hashes. Coverage is relative
to the supplied customer record; it cannot reveal an action absent from both
that record and the receipt set.

For a chosen output location, use `bulla demo --out DIR`; `DIR` must not already
exist. Use `bulla demo --format json` for the machine-readable report. The
[quickstart](https://bullalabs.com/bulla/quickstart) walks through the saved files
and the retained standalone checker.

## Capture an existing MCP call

Bulla's `capture` command wraps an existing stdio MCP server. Keep your current
server command after `--`. The following is a configuration template: replace
the absolute paths and `SERVER COMMAND...` with your own values, then configure
your MCP client to launch the wrapped server.

```text
bulla capture mcp --session-root /absolute/path/to/bulla-calls -- SERVER COMMAND...
bulla capture check /absolute/path/to/bulla-calls --show-receipts
bulla receipt verify /absolute/path/to/receipt.json
```

The wrapper forwards newline-delimited traffic byte-for-byte without changing
the client handshake. A completed client-originated `tools/call` and matching
response produce a local receipt. The session root supports successive server
lifecycles without changing the wrapper configuration.

**Privacy default: commitments only.** Request and response payloads are not
retained unless you choose `--retain-payloads`. That option saves exact frames
and can retain prompts, credentials, tool arguments, and results. Review the
directory's access controls and retention needs before enabling it.

An optional `--key FILE` signature authenticates only the local observer's
statement, not the MCP server, tool execution, or result correctness. The
capture directory is implementation-local, not a portable protocol object.
See the [MCP capture guide](https://bullalabs.com/bulla/capture) for client
configuration, signing requirements, and the full claim boundary.

## Create a receipt in application code

This runnable example records a constructed local response, checks the receipt,
and compares it with two supplied application events. In an integration,
replace the constructed response with the bytes your application actually
receives; Bulla does not perform or judge that work for you.

```python
import hashlib
from bulla import event_coverage, verify_receipt, wrap_action

with wrap_action("demo.echo", {"event_id": "action-001"}) as action:
    response = b"hello"
    action.set_result("sha256:" + hashlib.sha256(response).hexdigest())

receipt = action.receipt
assert verify_receipt(receipt).ok

coverage = event_coverage(
    [{"id": "action-001"}, {"id": "action-002"}], [receipt]
)
assert coverage["coverage"] == 0.5
assert coverage["unreceipted_delta"] == ["action-002"]
```

The receipt is available to serialize and retain; the application decides
where it is delivered. This comparison correlates action identifiers. For an
exact saved-record binding, supply `record_sha256` using
`observed_record_sha256` as described in the
[Python SDK](https://bullalabs.com/bulla/sdk).

## Understand the checks

Receipt verification reports separate dimensions. Integrity checks the supplied
record's structure and commitments. With the required cryptographic support,
signature verification checks a signature under a key; it does not establish
that the key is an authority your application should accept. Referenced
evidence is not automatically fetched or treated as true.

A receiver supplies its own `ReliancePolicy` to compute `RELY`, `REFUSE`, or
`ESCALATE`. Those results do not execute a payment, grant access, or create legal
authority. The evidence-strict policy requires separately supplied acceptance
of the exact evidence digest and grounding class, not merely a label carried
inside the receipt. See [receiver policies](https://bullalabs.com/bulla/concepts#reliance).

Reconciliation finds supplied customer events without matching receipts. It
does not establish that the customer's record is complete. None of these
checks alone proves that the underlying action occurred or that its result
was correct. Verification and the decision to rely remain separate.

## Why not signed JSON?

Signed JSON can carry the same facts. In our scheduling experiment, matched
signed records produced the same decisions as ActionReceipts. Bulla supplies
a versioned record format, local verification tools, receiver-policy
evaluation, and receipt reconciliation. The intended advantage is a shared
interface rather than a custom convention for each integration. Reduced
integration effort and interoperability between independently operated systems
remain unmeasured. Read the [comparison methods](https://bullalabs.com/research/routed-buyer-continuity#methods)
and [ecosystem guide](https://bullalabs.com/bulla/ecosystem).

## Formats, documentation, and maintenance

MCP capture is included in Bulla 0.49.3. It emits ActionReceipt v0.4, an
experimental draft format; v0.2 remains the normative default for the general
receipt tools. v0.3 remains a released, non-normative draft. Package versions
and receipt-format versions are separate.

- [ActionReceipt specification](https://bullalabs.com/spec)
- [CLI reference](https://bullalabs.com/bulla/cli) and [capability reference](https://github.com/jkomkov/bulla/blob/main/docs/CAPABILITIES.md)
- [Buyer requirements](https://bullalabs.com/buyers) and [status](https://bullalabs.com/status)
- [Changelog](https://github.com/jkomkov/bulla/blob/main/CHANGELOG.md)

John Komkov is the maintainer of record. Use
[GitHub issues](https://github.com/jkomkov/bulla/issues) for product questions
and implementation feedback. Report security vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jkomkov/bulla/security/advisories/new),
following the [security policy](https://github.com/jkomkov/bulla/blob/main/SECURITY.md).
Bulla is [Apache-2.0 licensed](https://github.com/jkomkov/bulla/blob/main/LICENSE).
Bulla Labs is operated by Glyph Standard, Inc.

## The broader program

Bulla Labs develops software and standards for
[Answerable Computing](https://bullalabs.com/answerable-computing).
The [machine-buyer experiment](https://bullalabs.com/research/routed-buyer-continuity)
studies retained evidence and process continuity, including signed-JSON parity.
Its scheduler, durable controller, and simulator are research code, not package
features. Research on witnessing, adjudication, and financial backing remains
separate from the released tools; the [research index](https://bullalabs.com/research)
links to the source profiles and their implementation status.
