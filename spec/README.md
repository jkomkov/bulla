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
