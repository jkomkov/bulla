# Cyber-Evaluation Incident Replay

A **conceptual defensive reconstruction** of the July 2026 model-evaluation security
incident shape, built to show what an answerability layer adds — and, just as clearly,
where it does not help.

This is synthetic. There is no real exploit, credential, payload, or organization's
record here; the event model is abstract (`network.egress` to a prohibited
destination), never a technique. It does not claim any receipt scheme would have
prevented the incident.

Motivating disclosures (external, cited, not reproduced):

- Hugging Face, 2026-07-16 — https://huggingface.co/blog/security-incident-july-2026
- OpenAI, 2026-07-21 — https://openai.com/index/hugging-face-model-evaluation-security-incident/

## What it demonstrates

Four deliberate paths:

1. A prohibited egress request through a receipted **gateway** → `REFUSE`, with a
   receipt the **gateway** signs (infrastructure-issued, not agent-issued).
2. A lower-layer **bypass** around the gateway → no receipt emitted. Bulla misses it.
3. `event_coverage` reconciles a separately retained network-flow denominator against the
   attestation-verified receipts from the designated gateway and surfaces the bypass
   as an **unreceipted action** — the honest point that coverage needs a denominator,
   verification depth, and an issuer policy.
4. A portable **incident bundle** handed to the affected service and checked by a
   standalone no-Bulla checker: receipt digests, exact lineage, handoff commitments,
   and coverage recompute with only the Python standard library. Findings remain
   separated by epistemic status, and sensitive artifacts are referenced by hash
   only. Issuer authenticity remains a separate Ed25519 rung.

A minimal **trajectory monitor** watches the accumulated receipt lineage and returns
`REFUSE_AND_FREEZE` when it sees the out-of-mandate egress attempt. It is a policy over
receipts, not a production cyber monitor.

## The failure boundary (the point of the exercise)

The gateway bypass (path 2) leaves no receipt. It is detectable **only** through
coverage against a denominator observed outside the bypassed component — never through
verifying the receipts that exist. Bulla records, reconciles, and makes the account
portable; it does not contain a bypass, patch a vulnerability, or provide isolation.

## Run it

```sh
cd bulla
PYTHONPATH=src python examples/eval-incident-replay/run_demo.py
python -I examples/eval-incident-replay/verify_bundle.py examples/eval-incident-replay/demo-output.json
```

The demo writes `demo-output.json`; the standalone no-Bulla verifier confirms receipt
digest integrity, the declared issuer topology, exact lineage, handoff commitments,
coverage reconciliation against the carried observed-event denominator, hash-only
artifacts, and epistemic separation with zero Bulla imports. It explicitly does not
verify Ed25519 issuer authenticity. The
record classes are specified in
`bulla/spec/eval-receipt-profile-v0.2-draft.md`.
