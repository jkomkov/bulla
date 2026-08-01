# ActionReceipt — independent specification bundle

This directory is the implementation-independent entry point for ActionReceipt
v0.2. Bulla is the Apache-2.0 reference implementation; it is not required to
read, produce, or verify the wire format.

**Normative version:** `0.2` (2026-07-13). **Opt-in released draft:** `0.3`
(authority binding, implemented in Bulla 0.44.0 but non-normative). The v0.2
wire format is frozen for this research program. A workflow-accountability profile may reference its hashes
but may not change the receipt shape or canonicalization rules.

## Five-minute verification

From this directory, with Python 3 and no installed packages:

```sh
python3 vectors/independent_check.py
```

The digest rung imports only the Python standard library. It verifies the golden
receipts, rejects each structural tamper for its intended reason, and exercises
CANON_VERSION 2 plus legacy WitnessReceipt verification. When PyNaCl is present,
an optional identity rung verifies the v0.3 signatures; otherwise it reports the
skipped depth. The checker never imports `bulla`.

For a clean-directory test, build the release bundle and unpack it anywhere:

```sh
python3 build_release_bundle.py
mkdir /tmp/action-receipt-v0.2-check
cd /tmp/action-receipt-v0.2-check
unzip /path/to/bulla/spec/dist/action-receipt-v0.2.zip
python3 action-receipt-v0.2/vectors/independent_check.py
```

## Bundle contents

- [`action-receipt-v0.2.md`](action-receipt-v0.2.md) — normative wire,
  canonicalization, hashing, modality, and compatibility rules.
- [`action-receipt-v0.2.schema.json`](action-receipt-v0.2.schema.json) — document
  shape and closed top-level vocabulary. The normative prose and checker carry
  the cross-field laws JSON Schema cannot express.
- [`action-receipt-v0.3-draft.md`](action-receipt-v0.3-draft.md) and
  [`action-receipt-v0.3.schema.json`](action-receipt-v0.3.schema.json) — the
  versioned, opt-in released-draft content-signer envelope-binding extension.
- [`IMPLEMENTATION-CHECKLIST.md`](IMPLEMENTATION-CHECKLIST.md) — fail-closed
  implementation checklist.
- [`vectors/`](vectors/) — golden, tampered, CANON-2, and legacy vectors.
- [`vectors/independent_check.py`](vectors/independent_check.py) — zero-dependency
  digest-rung verifier.
- [`COMPATIBILITY.md`](COMPATIBILITY.md) — producer and legacy-verifier rules.
- [`routed-inference-profile-v0.1-draft.md`](routed-inference-profile-v0.1-draft.md)
  — provider-neutral, single-router/single-provider answerability profile with
  fourteen standalone adversarial traces and a finite violation taxonomy in
  [`routed-inference-vectors/`](routed-inference-vectors/). Full disclosure,
  retained bindings, and draft/local evidence only.
- [`routed-inference-requirement-evidence.md`](routed-inference-requirement-evidence.md)
  — publication gate mapping routed-inference claim classes to executable evidence
  and their mandatory proof boundaries.
- [`build_routed_profile_bundle.py`](build_routed_profile_bundle.py) — builds the
  deterministic routed-profile reproduction zip separately from the frozen v0.2
  ActionReceipt bundle. Running its supplied checker is fixture reproduction, not
  an independent implementation.

## Reading the version numbers

A bare `v0.3` or `v0.4` in this directory is ambiguous, because four independent
things are versioned here and all of them start at `0.1`. Read the axis, not the
number:

| Axis | Files | What the number tracks |
|---|---|---|
| **Wire format** | `action-receipt-v0.N.md` | The receipt shape and canonicalization. `0.1` superseded, **`0.2` normative**, `0.3` opt-in released draft, `0.4` opt-in experimental draft included in Bulla 0.44.4 that does *not* supersede `0.2`. |
| **Workflow profiles** | `routed-inference-`, `reproduction-`, `eval-receipt-profile-` | A workflow recorded *over* the wire format. Each is independently at `0.1`. A profile may reference wire hashes; it may not change the receipt shape. |
| **Experimental research profiles** | `*-experimental.md` | Captive research. Each declares a namespaced identifier on its third line — `bulla.semantic-finality/0.1-experimental`, `bulla.semantic-boundary/0.3-experimental`, `bulla.claim-flow/0.4-experimental`, and `bulla.claim-flow/0.5-experimental` (the Generalization Constitution continues the claim-flow family rather than opening a new one). Cite that identifier, never the bare version. |
| **Commitment slot** | `commitment-slot-` | A separate draft mechanism and its recourse algebra, both at `0.1`. |

So `v0.3` names both the opt-in authority-binding wire extension shipped in PyPI
and `bulla.semantic-boundary/0.3-experimental`, which is research-only. `v0.4`
names both an opt-in released experimental wire draft and `bulla.claim-flow/0.4-experimental`,
which ships in PyPI 0.44.1 under `bulla.experimental`. They are unrelated.

**If you are implementing, build against `0.2` on the wire axis.** Nothing else
in this directory is normative.

## Everything else in this directory

The bundle above is the frozen v0.2 distribution. The directory also carries
design notes, drafts, and research profiles that are *not* part of it. Every
top-level document states its own status in its first five lines; that line
governs. Grouped:

- **Wire history and adjacent design** — `action-receipt-v0.1.md` (superseded for
  producers), `action-receipt-v0.2-draft.md` (folded into the normative text),
  `action-receipt-v0.4-draft.md` (opt-in released experimental draft),
  `receipt-primitive-v0.1.md`
  (definitional), `delegation-design-note.md` (implemented as a released draft in
  0.44.0), `threat-model-v0.1.md`, `ADR-001-standing-model.md` (accepted).
- **Workflow profiles** — `reproduction-profile-v0.1-draft.md`,
  `eval-receipt-profile-v0.1-draft.md`, the routed-inference set listed above, and
  `routed-inference-IMPLEMENTER.md`, which carries the rule the reproduction
  profile inherits: running a supplied checker on supplied artifacts is fixture
  reproduction, not an independent implementation.
- **Experimental research** — `claim-flow-v0.4-experimental.md`,
  `generalization-constitution-v0.5-experimental.md`,
  `semantic-boundary-v0.3-experimental.md`,
  `semantic-finality-v0.1-experimental.md`.
- **Drafts not on any release path** — `commitment-slot-v0.1-draft.md`,
  `commitment-slot-recourse-algebra-v0.1-draft.md`.
- **Process** — `EXTERNAL-REVIEW-PACKAGE.md`, `IMPLEMENTATION-CHECKLIST.md`,
  `COMPATIBILITY.md`.

For what is actually released versus experimental, and for the external
participation counts, the authority is the generated status table:
[`../docs/WHAT-EXISTS-TODAY.md`](../docs/WHAT-EXISTS-TODAY.md), rendered at
[glyphstandard.com/status](https://glyphstandard.com/status). A document's status
line says what *it* claims; the status table says what the project has evidence
for. Where they disagree, the table governs.

## Conformance boundary

Passing the independent checker establishes wire-level digest conformance. It
does not by itself establish signature validity, honest evidence grounding,
complete workflow coverage, non-equivocation, semantic policy obedience, current
non-revocation, or remedy reachability. The optional identity rung establishes
that the same content signer signed the exact v0.3 envelope and, for a structured
did:key chain, reproduces six separate delegation dimensions. A valid chain binds
the exact *declared* policy and scope; it does not prove that the act obeyed them,
and unresolved time/revocation must not be read as authority in force. In
particular, a valid local receipt is not proof that an omitted action never
occurred.

## License and governance

The specification, schema, checker, and vectors are distributed under the
repository's Apache-2.0 Bulla license. Normative v0.2 changes require an
explicit future version; compatibility repairs must not silently rewrite v0.2.
