# Compatibility and legacy verification

## Producers

Unsigned and content-only producers emit the normative ActionReceipt
`schema_version: "0.2"` and CANON_VERSION 2. Full content+envelope signing opts
into the released implementation of the non-normative v0.3 draft. Producers
must not emit `authorization`, even null,
under a v0.1/v0.2 tag. A semantic change is a new version, not an editorial
repair.

## ActionReceipt verifiers

Verifiers continue to accept v0.1 receipts using the receipt's stored
`schema_version` in the content-hash preimage. v0.2 adds grounding and
conventions; it does not reinterpret v0.1 bytes.

Draft v0.3 verifiers preserve v0.1/v0.2 preimages exactly. A v0.3 receipt adds
the required `authorization` member to the wire shape and attestation preimage,
and its content hash carries `schema_version: "0.3"`. Old v0.2-only verifiers are
not expected to accept v0.3 receipts.

## WitnessReceipt verifiers

For pre-CANON-2 WitnessReceipts with no `canon_version`, try compact canonical
JSON first, then the legacy spaced JSON form. Report which form matched. A
legacy match is valid legacy verification, not tampering. New receipts stamp
`canon_version: 2` and never mint the legacy form.

## Profiles

Profiles may reference ActionReceipt hashes and impose stronger workflow-level
requirements. They may not change the v0.2 hash preimages, canonicalization,
field meanings, or receipt shape. Profile vocabulary stays inside the open
`action` object.

`bulla.routed-inference/0.1-draft` uses an additive `ReceiptRef` object with the
exact observed receipt's `event` and `attestation` hashes. It binds an occurrence
plus the vouched content/envelope; it does not make the actor-supplied timestamp
independently truthful. The profile's executable terms use the portable
`jsonschema+quantum/1` subset: strings, booleans, safe integers, `enum`, `const`,
integer bounds, and integer quantum. Floats, regex, implicit coercion, and
nonportable legacy forms are unsupported rather than coerced.

The draft profile requires exact `term_root` equality at every edge. This is a
v0.1 conservation mechanism, not a general attenuation or intersection rule.
Older ActionReceipts remain valid receipts but are not automatically conforming
routed-inference profile events.
