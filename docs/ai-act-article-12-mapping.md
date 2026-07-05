# ActionReceipt → EU AI Act Article 12 (record-keeping)

**Scope, stated plainly.** This maps `ActionReceipt` fields to the record-keeping
obligations of **Article 12** of the EU AI Act (Regulation 2024/1689). It is a
claim of **Article 12-style traceability** — a receipt gives a high-risk system a
per-action, tamper-evident, machine-verifiable event record — **not** a claim of
"AI Act compliance." Compliance is a system-and-process determination for a
provider and its auditors; a receipt is one technical building block toward the
traceability such a determination requires. Nothing here is legal advice.

Why this matters now: Article 12's obligations attach to high-risk systems, and
its risk classes — systems that *reject an applicant*, *modify a medical or
billing record*, *change an access permission* — are close to the acts an
`ActionReceipt` is built to record. Regulated demand is the one adopter class
that does not wait for network effects, so the schema is designed to *cover*
these obligations rather than to be retrofitted to them.

## Article 12 obligation → ActionReceipt field

| Article 12 obligation (paraphrased) | ActionReceipt field(s) | Notes |
|---|---|---|
| Automatic recording of **events** ("logs") over the system's lifetime | the receipt itself, one per consequential `action`; `hashes.log_leaf` appends it to an RFC 6962 log | append-only, tamper-evident; a *missing* log entry is detectable (see `bulla coverage`) |
| **Traceability** of the system's functioning appropriate to its purpose | `action.type` + `action.subject` + `diagnostic_ref` | the recomputable verdict makes the trace *checkable*, not merely stored |
| Recording the **period of each use** / time reference | `timestamp`; `hashes.event` binds the occurrence to that time | |
| The **reference data / inputs** against which the action was checked | `evidence_refs` (name+hash of each input/artifact); `diagnostic_ref.ref` (the composition/verdict) | inputs are pinned by hash, not copied — privacy-preserving by default |
| **Identification of the natural persons** involved in verification (where required) | `mandate.authority.principal` + `mandate.authority.delegation` | the accountable principal and the delegation chain |
| Situations that may present a **risk** or trigger substantial modification | `diagnostic_ref` (the semantic-risk verdict) + `remedy` (challenge window, forum, remedy ladder) | risk is recorded *with its recourse path*, not just flagged |
| Records usable for **post-market monitoring** and by the deployer | `hashes.content` (recomputable), `producer`, the RFC 6962 log | anyone re-derives the verdict from pinned inputs |

## Where a receipt does more than Article 12 asks

- **Recomputable verdict.** Article 12 asks that events be *logged*; a receipt
  additionally carries a verdict any party can *re-derive* from pinned inputs
  (`diagnostic_ref`). A signed log line records that something happened; a
  receipt records *what was concluded and lets you check it*.
- **Retention asymmetry.** `retention` distinguishes records of power
  (`authority-permanent`) from records about persons (which must be able to
  end). Article 12 sets a floor for keeping records; a receipt also encodes when
  person-facing fields should expire — a data-minimisation posture aligned with
  the GDPR, not only the AI Act.
- **Recourse.** `remedy` records *how the action is contested*, which Article 12
  does not require. Traceability that leads nowhere is decorative; a receipt
  binds the record to a challenge window, a forum, and a remedy ladder.

## Where it is silent (honest gaps)

A receipt is a per-action record. Article 12 conformity also depends on
system-level logging retention periods, the provider's quality-management
system, and human-oversight arrangements (Articles 9, 14, 17) — none of which a
single receipt determines. `retention` names a class; the *enforcement* of an
expiry (the forgetting mechanism) is a separately pre-registered instrument, not
shipped in v0.1.
