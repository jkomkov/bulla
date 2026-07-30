# Cyber-Evaluation ActionReceipt Profile v0.1 — DRAFT

> **Superseded.** The corrected
> [`eval-receipt-profile-v0.2-draft.md`](./eval-receipt-profile-v0.2-draft.md)
> replaces this draft. Version 0.1 incorrectly classified a gateway's own
> observation as `execution_verified`, assigned `received_at` outside witness
> intake, and bound a trajectory by count rather than exact ordered lineage.
> Retain this file only as a versioned historical record.

**Status:** draft, 2026-07-22. A workflow profile over ActionReceipt v0.2/v0.3 for
recording a dangerous-capability evaluation and any incident that arises from it. It
does not change the ActionReceipt wire shape. It is motivated by the July 2026 model-
evaluation security incident publicly described by Hugging Face (2026-07-16) and OpenAI
(2026-07-21); it is a **conceptual defensive profile**, not a reconstruction of either
organization's records, and it makes no claim that any receipt scheme would have
prevented that incident. It has an offline synthetic replay and a zero-Bulla checker;
it has no live gateway integration, no production monitor, and no external witness.

## 1. What this profile is and is not

Receipts record **authority, scope, decisions, evidence, and recourse**. They do not
contain a workload, patch a vulnerability, or provide isolation. In the incident's
terms: a receipt layer is a policy-bearing boarding pass plus a flight recorder outside
the aircraft — useful only if the pass is checked at each real door and the recorder
sits outside the control of the thing it records. Two consequences follow and are
normative here:

- **Infrastructure issues capability receipts, not the agent.** An adversarial or
  misaligned agent cannot be trusted to report its own behavior. A `capability.use`
  receipt MUST be signed by the enforcement point (gateway, broker, control plane),
  not by the evaluated agent. The agent MAY sign its *request*; only the enforcement
  point signs what it *permitted, refused, or observed*.
- **Coverage, not only validity.** An action that leaves no receipt is invisible to
  receipt verification. This profile therefore requires reconciliation against an
independent denominator (`bulla.coverage.event_coverage`); an observed consequential
action with no attestation-verified covering receipt is a high-severity finding.

## 2. Record classes

```text
eval.run.authorize   (evaluation authority)  the signed mandate
  └─ capability.use  (enforcement point)      one permitted/refused/observed action
       └─ trajectory.decide (monitor)         a cumulative reliance decision
incident.handoff     (responder / counterparty)  a portable cross-org bundle
```

### 2.1 `eval.run.authorize`
Issued once, before execution, by the evaluation authority. `action.type =
"eval.run.authorize"`. The subject binds: model and harness identities; the safeguards
state (e.g. reduced cyber refusals: true/false); the authorized target; the interval;
compute, network, credential, and privilege budgets; and explicit prohibitions
(public internet, third-party production systems, credential collection, benchmark
answers). The mandate's `event`/`attestation` hashes are the parent every downstream
receipt references.

### 2.2 `capability.use`
Issued by the enforcement point for each consequential action it mediates — package
fetch, egress, credential use, privilege change, sensitive read, cross-tenant
operation, external publication, durable write. `action.subject` MUST carry a stable
`event_id` (the id the independent denominator also records) and record: the requested
action, the observed action, the mandate reference, the policy decision
(`PERMIT | REFUSE`), and an infrastructure evidence reference. Grounding is
`execution_verified` for the gateway's own observation, never `self_asserted`.

### 2.3 `trajectory.decide`
Issued by a monitor over the accumulated receipt lineage — not per action. Individual
actions can each look acceptable while the sequence trends toward a prohibited outcome.
Returns one of `PROCEED | ESCALATE | REFUSE_AND_FREEZE`. A freeze SHOULD revoke
credentials, stop inference, preserve sandboxes, pin log roots, and page a responder.
This is a policy over receipts; it is **not** a production cyber monitor and makes no
completeness claim.

### 2.4 `incident.handoff`
Issued by a security responder and exchanged between affected organizations. It
separates findings by epistemic status — `observed | inferred | counterparty_confirmed
| unresolved` — binds each to exact evidence by hash, and conveys notification time,
containment request, and a challenge/correction channel. Sensitive artifacts
(credentials, payloads, customer data) are referenced by content hash and access
condition, never inline.

## 3. Temporal claims

Per `docs/EVIDENCE-CONTRACT.md`, the four temporal labels stay distinct:
`claimed_at` (actor), `received_at` (witness intake), `witnessed_at` (signed
checkpoint), `anchored_before` (external timestamp). A mandate interval is a
`claimed_at` bound; a gateway's `capability.use` observation carries `received_at`;
neither establishes that a described real-world act occurred at the claimed time.

## 4. The failure boundary (normative honesty)

A conforming implementation MUST NOT present this profile as prevention. Specifically:
if the enforcement point is itself bypassed (the incident's package-proxy escape), the
`capability.use` receipt for that path is never issued, and the bypass is detectable
ONLY through `event_coverage` against a denominator observed outside the bypassed
component. The replay demonstrates exactly this: a refused egress with a receipt, an
unreceipted lower-layer bypass, and the coverage reconciliation that surfaces it.

The profile requires `minimum_verification_depth="attestation"` and an explicit
`accepted_issuers` set containing the enforcement-point identity at this security
boundary. A valid receipt from an unrelated signer, or a malformed, tampered, unsigned,
or ambiguously bound receipt, MUST NOT erase an unreceipted-action finding. The
denominator itself MUST fail closed on missing or duplicate event ids. The standalone
checker recomputes digest integrity and coverage; ed25519 issuer authenticity remains
the separate identity rung.

## 5. Signer topology

```text
evaluation authority     signs eval.run.authorize
enforcement point        signs capability.use (observed decision)
trajectory monitor       signs trajectory.decide
independent log          retains and witnesses the history
incident commander       signs reliance on findings
counterparty             signs receipt, dispute, or correction
```

No single party — least of all the evaluated agent — controls all of these keys.
