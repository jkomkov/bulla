# Evidence Contract

Canonical machine-readable source: `glyph/data/evidence-contract.json`. If this prose and
that file disagree, the file governs. The status registry's external counters may change
only together with evidence satisfying these definitions.

## Why this document exists

The status registry reports external authors, adjudicators, independent implementations, and
witnesses (A/J/I/W). Every one of those counters is currently zero. The purpose of this
contract is to keep them honest as they change: each counter has one definition, one set of
disqualifiers, and one auxiliary category for the evidence that most often gets misclassified.

## The ladder

| Evidence | Counts as |
|---|---|
| Outsider runs a supplied checker on supplied artifacts | External replay (auxiliary) |
| Outsider writes a second checker from the specification | Independent implementation |
| Outsider authors previously unseen cases | External author |
| Outsider decides contested semantic cases | External adjudicator |
| Separately controlled service retains receipts | Independent witness |

An external replay establishes that an external person reproduced the team's supplied
procedure on the team's supplied artifacts. It does not establish an independent
implementation, and it never increments the implementations counter.

## Counter definitions

**External author.** A person or organization outside the team authors previously unseen
cases, seams, or interpretations that the system is then evaluated against. The material must
not have been visible to the team before the evaluation froze; authorship is attributable and
disclosed; team assistance is limited to format documentation and intake mechanics.

**External adjudicator.** A person outside the team decides contested cases whose outcomes
were not known to the team in advance, blind where the protocol requires blindness. Decisions
are retained verbatim, including disagreements. Machine oracles authored by the team are not
adjudication.

**Independent implementation.** An outside party implements the normative format from the
specification — no Bulla imports, no adaptation of team-authored checker source — publishes
the implementation and build instructions, passes the public vectors plus previously unseen
adversarial vectors with byte-identical hashes and equivalent rejection behavior, and
documents what contact occurred with the team.

**Independent witness.** A service under a separate control domain — distinct legal control,
key custody, and operational authority — verifies ActionReceipts on intake, retains exact
bytes, returns inclusion and consistency proofs, and publishes signed checkpoints the team
does not control, under a declared retention and privacy policy. Team-controlled instances on
separate infrastructure are fault-domain diversity, not independence, and do not count.

## Disclosure and assistance

Paid external participation may count, with the engagement and payment disclosed alongside
the evidence. Specification clarification and intake mechanics are permitted assistance.
Shared code, pairing, debugging of the external artifact, or outcome discussion before freeze
disqualify the increment.

## Temporal claims

Four labels, kept distinct on every surface:

- `claimed_at` — supplied by the actor. Under wire v0.2/v0.3 this value is not bound into the
  signed occurrence identity; see the action-receipt v0.4 draft.
- `received_at` — observed by a witness at intake.
- `witnessed_at` — included in a signed witness checkpoint.
- `anchored_before` — an externally timestamped upper bound.

None of these establishes that the described real-world act occurred at the claimed time.

## Release coverage

`release_receipt_required_since: 0.44.0` is the immutable enforcement epoch. A receipt is
contemporaneous only when minted post-publication by the release workflow; reconstructed
receipts are labeled as such and are never reclassified. Historical unreceipted releases
remain missing. Coverage is reported as three separate figures: forward contemporaneous since
the epoch, all-time contemporaneous, and all-time availability including reconstruction.
