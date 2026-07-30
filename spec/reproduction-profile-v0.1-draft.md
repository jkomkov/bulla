# Third-Party Reproduction ActionReceipt Profile v0.1 — DRAFT

**Status:** draft, 2026-07-24. A workflow profile over ActionReceipt v0.2 for recording
that an outside party attempted to reproduce a published claim, including attempts that
never ran. It does not change the ActionReceipt wire shape, and it requires no library
change: `action.type` is open vocabulary and every field this profile adds lives inside
the `action` object. It has example receipts and a reconciliation test; it has no
external submission, no independent implementation, and no adjudicator. Every external
counter on the status registry remains zero.

This profile does not make reproduction possible. It records what a reproduction
attempt found, or why it could not proceed.

## 1. What this profile is and is not

A published claim about a system's behavior is checkable only by parties who can obtain
the system, the inputs, and the method. When they cannot, the claim rests on the word of
the party making it, and the questions that matter run through the party being
questioned.

The obvious remedy is an outside laboratory that reproduces the claim and publishes a
finding. That remedy has a gap this profile addresses and does not close:

- **A finding published as a report is itself a first-party claim.** Trust moves from
  the original claimant to the laboratory; it does not terminate. It terminates only if
  the finding recomputes for a reader who trusts neither party.
- **A laboratory that attempts four reproductions and publishes three is the original
  problem in a new position.** Selective publication is invisible to anyone reading only
  what was published.

Two consequences follow and are normative here:

- **The reproducer's own record is subject to the same discipline as the claim under
  test.** A `reproduction.report` MUST be recomputable from its own content, and MUST
  bind the full set of attempts it covers.
- **An attempt that never ran is still a record.** A reproduction blocked for want of
  access MUST emit a receipt naming what was requested, from whom, when, and the
  response. A refusal recorded this way is a dated, checkable fact rather than silence.

What this profile does not do: it does not obtain access, does not establish that a
reproduced claim is true, does not adjudicate a divergence, and does not bind a party
that declines to emit receipts at all. Reproduction of a claim about a system nobody
outside can run remains impossible, and this profile records that impossibility rather
than concealing it.

## 2. Record classes

```text
reproduction.attempt   (reproducing party)  one attempt at one claim, including blocked
  └─ reproduction.report (reproducing party)  the published finding over a set of attempts
```

### 2.1 `reproduction.attempt`

Issued by the reproducing party, once per claim under test, **whether or not the attempt
ran**. `action.type = "reproduction.attempt"`.

`action.subject` MUST carry a stable `event_id` — the identifier the attempt register
also records, and the key the coverage reconciliation in §3 matches on. It MUST bind:

- `claim_under_test` — the claim as published, pinned by `digest`, `retrieved_at`, and a
  retrieval `uri`; when the claim is itself an ActionReceipt, the two-hash
  `{event, attestation}` reference instead.
- `method` — the harness or checker actually used: `repository`, `commit`,
  `checker_sha256`, and `independently_written` (a boolean the reproducer asserts).
- `environment` — `language`, `runtime`, `operating_system`, `cryptographic_library`.
- `result` — one value from the closed set below, with `result.status` required. The
  field is `action.subject.result`, deliberately not `action.outcome`, which `wrap_action`
  already binds to the execution status of the wrapped call.

| `result.status` | Meaning |
|---|---|
| `reproduced` | The method ran and its result agrees with the published claim under the declared comparison. |
| `diverged` | The method ran and its result disagrees. `result.divergences` MUST be non-empty. |
| `inconclusive` | The method ran and the comparison does not decide. `result.reason` MUST be present. |
| `blocked` | The method did not run. `result.block` is REQUIRED, per §2.2. |

`fixture_results`, `divergences`, and `ambiguities` carry the per-case detail; the
existing `spec/routed-inference-conformance-report-template.json` is the field-level
model for these and SHOULD be reused rather than re-derived.

Comparison tolerance is not invented here. When agreement is not bit-exact, the attempt
carries the v0.2 `verification_semantics` object (`comparison_fn`, `threshold`,
`seed_regime`, `environment_class`) inside `action.subject`, which is the wire spec's
existing carrier for exactly this.

### 2.2 `blocked` — the required shape

An attempt that could not proceed MUST record the refusal rather than omitting the
attempt:

```jsonc
"result": {
  "status": "blocked",
  "block": {
    "requested": "<what access, artifact, or capability was asked for>",
    "requested_from": "<the party asked>",
    "requested_at": "<ISO-8601>",
    "response": "denied" | "unanswered" | "conditioned" | "withdrawn",
    "response_at": "<ISO-8601, omitted when response is unanswered>",
    "conditions": "<present iff response is conditioned>"
  }
}
```

`unanswered` requires a `requested_at` old enough to be meaningful under the reporting
protocol, and states nothing about intent. This profile records that a request was made
and what came back; it does not characterize motive, and a conforming implementation
MUST NOT present a `blocked` attempt as evidence of concealment.

### 2.3 `reproduction.report`

Issued by the reproducing party when a finding is published. `action.type =
"reproduction.report"`. **One report per attempt**: `action.subject.event_id` MUST equal
the `event_id` of the attempt it publishes, which is the key the reconciliation in §3
matches on. `action.subject` MUST bind:

- `event_id` — the attempt this report publishes.
- `finding` — the published statement, and the `result.status` it reports.
- `denominator_digest` — the canonical digest of the attempt register this report is
  drawn from (CANON_VERSION 2, per the wire spec's one rule). A reader recomputes it
  against the register to confirm the register was not shortened after the fact.
- `implementer` — `name`, `organization`, `contact`, matching the existing conformance
  template.
- `declaration` — the reproducer's own statement of what the work was, including whether
  the supplied checker was executed rather than an independent implementation written.

A `reproduction.report` MUST NOT claim a grounding class stronger than the attempt it
publishes. A summary across several attempts is a convenience document, not a substitute
for the per-attempt reports, and MUST NOT be reconciled in their place.

## 3. Selective disclosure is a reconciliation, not a promise

The report's completeness is checked, not asserted. Reconciliation uses the existing
`bulla.coverage.event_coverage` with no profile-specific machinery:

- **denominator** — the attempt register: one record per `event_id`, fixed before the
  reporting deadline.
- **numerator** — the `reproduction.report` receipts actually published.
- **finding** — `unreceipted_delta` names every attempt that left no published report.

Four attempts and three published reports yield exactly one entry in
`unreceipted_delta`, and `coverage` reads 0.75. That is the whole mechanism, and it is
the reason the denominator must be fixed independently of the reporter's later choices
about what to publish. A register the reporter can silently shorten reconciles against
nothing.

A report naming an attempt the register never held is surfaced separately as a
`phantom_receipt_id` rather than counted as coverage. `event_coverage` fails closed on a
missing or duplicate `event_id`, so a malformed register is an error rather than a
coverage figure that reads high.

`bulla/tests/test_reproduction_profile.py` exercises each of these, including the
four-attempts-three-reports case and the register that fails closed on a duplicate.

## 4. The failure boundary (normative honesty)

A conforming implementation MUST NOT present this profile as verification of the claim
under test, and MUST NOT present it as a substitute for access.

- **Grounding is capped by re-derivability.** An attempt is `execution_verified` only
  when its result is re-derivable by a third party from pinned inputs with the pinned
  method. Otherwise it is `self_asserted` — testimony by the reproducer, carrying a
  timestamp and a signature and nothing more. The v0.2 minimum-over-necessary-evidence
  display rule then caps the whole receipt at that class, which is the correct outcome
  and MUST NOT be worked around.
- **A reproducing party can misreport.** These receipts bind what was claimed, by whom,
  under what method, and when. They do not establish that the reported run occurred as
  described. The remedy for a suspect report is an independent reproduction of the
  reproduction, which this profile records identically and does not privilege.
- **The register is the trust root of §3.** If the reporter controls the denominator
  without constraint, coverage measures nothing. An anchored or independently retained
  register is what makes the reconciliation meaningful; this profile specifies the
  reconciliation and does not supply the anchor.
- **A blocked attempt is not an accusation.** See §2.2.

## 5. Signer topology

```text
reproducing party        signs reproduction.attempt and reproduction.report
claim publisher          signs nothing here; the claim under test is referenced, not endorsed
register keeper          fixes and retains the attempt register used as the denominator
adjudicator              decides a contested divergence, in a named forum, never the log
```

The reproducing party and the register keeper SHOULD NOT be the same party. When they
are, §3 establishes internal consistency only, and the report SHOULD say so.

## 6. Relation to existing surfaces

This profile receipts a workflow that is already specified in prose and already has an
intake:

- `bulla/agents/REPLICATION-INVITATION.md` — the reporting protocol: the issue-title
  convention, the acknowledgement window, and the commitment to publish a replication
  whether it confirms or contradicts.
- `bulla/spec/routed-inference-IMPLEMENTER.md` — the rule this profile inherits without
  restating: running a supplied checker on supplied artifacts is fixture reproduction,
  not an independent implementation.
- `bulla/spec/routed-inference-conformance-report-template.json` — the field-level model
  for `method`, `environment`, `fixture_results`, `divergences`, and `ambiguities`.
- `bulla/bench/invention/external/` — `RECRUITMENT.md`, `intake.schema.json`,
  `adjudication.schema.json`, and the role-disjointness and blinding requirements for
  external adjudication.
- `glyph/data/evidence-contract.json` — the counter definitions. A reproduction receipt
  is evidence for a counter; it is not itself a counter increment, and this profile does
  not change what any counter means.
