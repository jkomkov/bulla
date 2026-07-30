# Cyber-Evaluation ActionReceipt Profile v0.2 — DRAFT

**Status:** supersedes v0.1, 2026-07-26. This source-only workflow profile uses
ActionReceipt v0.4 to record evaluation authority, boundary decisions,
observations, trajectory decisions, statements, and incident handoffs. It does
not change the ActionReceipt wire format and does not claim to prevent, detect,
or contain an attack.

The `glyph.agent-incident-packet/0.1-draft` profile defines the portable packet
that can carry these records.

## 1. Record classes

```text
eval.run.authorize
  ├─ capability.decide
  │    └─ capability.observe
  ├─ trajectory.decide
  └─ incident.statement
       └─ incident.handoff
```

`eval.run.authorize` records the evaluation authority, model, harness,
safeguards, authorized targets, budgets, prohibitions, claimed interval, policy
digest, and expected role issuers.

`capability.decide` records a pre-effect `PERMIT` or `REFUSE` decision. The
record binds one run, request, gateway-ingress observation, request hash,
mandate, policy, protocol, anchor, rationale, and retained evidence hash.
`PERMIT` states only that the boundary policy permitted dispatch.

`capability.observe` records a post-effect observation. The record binds the
permitted decision, target-side observation identifier, effect hash, retained
evidence, protocol, transport status, and one of `EFFECT_OBSERVED`,
`NO_EFFECT_OBSERVED`, or `OUTCOME_UNKNOWN`. A transport success does not
establish the claimed real-world outcome.

`trajectory.decide` records the exact ordered list of accepted attestation
hashes considered by the monitor for the same run. Cross-run capability
receipts are excluded. A receipt count is not a lineage commitment. Its
decision is `PROCEED`, `ESCALATE`, or `REFUSE_AND_FREEZE`.

`incident.statement` contains one issuer's statement and labels it `observed`,
`inferred`, `counterparty_confirmed`, or `unresolved`. Conflicting statements
remain separate. `counterparty_confirmed` is reserved for the closed
`affected_party` issuer role. Another role cannot relabel its own assertion as
counterparty confirmation.

`incident.handoff` binds the mandate, exact ordered timeline prefix available
before the handoff, coverage manifest, exact statement references, parent
references, recipient, requested actions, disclosure conditions, and
correction and challenge channels. The later packet-publish receipt binds the
complete timeline. A verifier recomputes every referenced commitment without
introducing a circular hash.

Decision and trajectory records match the referenced mandate's run and policy.
Observation records match the mandate run and the accepted permitted parent
decision's run, request, protocol, and mandate. Handoff statement and parent
references are exact ordered lists, not sets or subsets. Only a pre-effect
decision's authenticated actor `claimed_at` is checked against the mandate's
claimed validity interval; later evidentiary and response records may occur
after that interval.

## 2. Evidence grounding

Grounding describes the relationship between evidence and its issuer:

- A gateway's record of its own decision or observation is `self_asserted`.
- A target-signed observation available to the gateway is
  `counterparty_signed`.
- Team-controlled infrastructure is not `third_party_anchored`, even when a
  separate process or key operates it.
- `execution_verified` is limited to deterministic recomputation from pinned
  inputs. It does not describe an ordinary infrastructure observation.

The evaluated agent never issues a boundary decision or an authoritative
observation of its own effect.

## 3. Temporal labels

The four labels remain independent:

- ActionReceipt v0.4 authenticates the actor's `claimed_at`.
- Witness intake supplies `received_at`.
- A signed witness checkpoint supplies `witnessed_at`.
- A separately verified external time anchor supplies `anchored_before`.

No label is promoted into another. Actor time cannot select the authoritative
member of a correction fork.

Correction references are kind-specific. Statement corrections bind two prior
named statement attestations. Packet corrections bind a declared predecessor
packet-core hash and a different released replacement packet-core artifact;
the current wrapper core cannot commit to its own hash without circularity.
Resolving a packet-core hash does not verify that packet's separate publish
proof.

Redaction binding uses a released record artifact that names the exact source
and release byte hashes, tool, version, rules hash, and
`disclosure_safety=NOT_COMPUTED`. A reviewer statement binds only that record's
exact artifact hash with the closed claim `REDACTION_BINDING_REVIEWED` and
self-asserted grounding. The record omits the reviewer reference to avoid a
signature cycle.

## 4. Coverage

Coverage reconciles receipts with an event denominator controlled outside the
receipting boundary:

- Gateway-ingress observations reconcile with `capability.decide`.
- Upstream-effect observations reconcile with `capability.observe`.
- Coverage is computed separately for each anchor, phase, and protocol.
- Missing, malformed, empty, or duplicate observation identifiers make that
  coverage group `NOT_COMPUTED`.
- Invalid, unsigned, legacy, wrong-role, or insufficient-depth receipts never
  reduce the uncovered set.
- A valid uncovered observation is a high-severity coverage finding, not a
  receipt-integrity failure.

Each denominator is bound to a prior role-authenticated
`incident.statement`: HTTP decision denominators use the gateway role, MCP
decision denominators use the boundary role, and effect denominators use the
target role. The statement binds the exact denominator artifact digest. This
uses the closed claim `ORDERED_DENOMINATOR_SNAPSHOT` and self-asserted
grounding. It authenticates the observer's report, not the report's
completeness or organizational independence. A self-shortening observer
remains undetectable.

`PATH_SEPARATE_TEAM_CONTROLLED` states technical path separation only.
`SEPARATELY_CONTROLLED` requires an out-of-band control relationship not
established by the packet.

## 5. Strict ingestion

Receipts are parsed from exact bytes through `bulla.receipt_parser`.
Implementations reject duplicate JSON members, unknown closed fields,
non-finite numbers, malformed Unicode, excessive resource use, and invalid
hashes before applying profile logic. Directory readers reject absolute paths,
parent traversal, backslashes, symlinks, undeclared files, path escapes, and
unsafe archive members.

## 6. Verification and limits

A verifier reports packet integrity, receipt integrity, issuer and role
acceptance, authority and occurrence binding, lineage, coverage, witness
evidence, conflicts, and corrections separately. It does not compute reliance
or disclosure safety.

This profile supplies accountability records and reconciliation rules. It is
not a sandbox, proxy, firewall, vulnerability detector, EDR, SIEM, or incident
adjudicator. A bypass that reaches a path absent from the out-of-boundary
denominator remains undetermined.
