# ActionReceipt v0.4 — occurrence-bound draft

**Status:** opt-in experimental draft included in Bulla 0.44.3, 2026-07-29. It
does not supersede the normative v0.2 wire or the opt-in v0.3
authority-binding draft. Default Bulla minting remains v0.2. Historical
v0.1/v0.2/v0.3 bytes and preimages remain unchanged.

## Purpose

v0.2 and v0.3 compute an `event` hash from the content hash and producer-supplied
timestamp, but neither the content signature nor the anchored attestation commits
to that event. A party can therefore change the timestamp and recompute the
unsigned downstream hashes. v0.4 binds the actor's claimed occurrence separately
from both reusable content and authority.

The evidence-integrity vector pair `signed-authorized.json` and
`tampered-timestamp.json` pins the historical boundary: the receipts differ in
`timestamp` and `hashes.event` yet retain the same v0.2/v0.3 attestation and log
leaf. That pair is a correction record, not a v0.4 conformance vector.

The distinction is intentional:

- `content` identifies the recomputable claim;
- `event` identifies one claimed occurrence of that content;
- `occurrence` proves that the content signer authenticated the event identifier
  and claimed time;
- `authorization` proves that the same signer authenticated the authority and
  recourse envelope for that occurrence;
- `attestation` commits to all three proofs and is the logged identity.

This still does not prove when the worldly act occurred. Temporal evidence is
reported as four separate labels: `claimed_at` (actor-authenticated),
`received_at` (witness-observed), `witnessed_at` (signed checkpoint), and
`anchored_before` (external upper bound).

## Canonical data model

Every v0.4 hash uses `canonicalization = "bulla-jcs-int/1"`: RFC 8785 member
ordering and JSON string serialization, restricted to null, booleans, strings,
arrays, objects, and integers in `[-9007199254740991, 9007199254740991]`.
Floats, non-finite values, duplicate keys, and lone surrogates are invalid. Use
integer quantum units or decimal strings for quantities.

The conformance parser limits a receipt to 1 MiB, depth 32, 50,000 aggregate
JSON nodes, and 256 KiB per string before cryptographic work begins.

## Hashes and proofs

Let `CJ` be `bulla-jcs-int/1`, `H(x) = "sha256:" || hex(SHA-256(UTF8(CJ(x))))`,
and `LEAF(x) = "sha256:" || hex(SHA-256(0x00 || UTF8(x)))`.

```text
content_hash = H({
  schema_version: "0.4", canonicalization: "bulla-jcs-int/1", kind,
  action, diagnostic_ref, evidence_refs, anchor_ref, [conventions]
})

event_hash = H({content_hash, event_id, claimed_at})
envelope_hash = H(recourse_envelope)
authorization_hash = H({event_hash, envelope_hash})

signature     = SIGN_DOMAIN(schema="0.4", purpose="content", digest=content_hash)
occurrence    = SIGN_DOMAIN(schema="0.4", purpose="occurrence", digest=event_hash)
authorization = SIGN_DOMAIN(schema="0.4", purpose="authorization", digest=authorization_hash)

attestation_hash = H({
  content_hash, signature, event_hash, occurrence,
  recourse_envelope, authorization
})
log_leaf = LEAF(attestation_hash)
```

The three proofs must name the same proof type, issuer, and verification method.
They are either all present or all null. A complete proof set that fails any
member or same-signer check fails at the attestation rung.

`event_id` is a canonical lowercase UUIDv4 generated before dispatch.
`claimed_at` is actor-authenticated text; profiles SHOULD use canonical UTC
RFC 3339, but verifiers do not treat its clock value as witnessed time.

The temporal evidence labels remain separate, as required by
`docs/EVIDENCE-CONTRACT.md`:

| Label | Established by |
|---|---|
| `claimed_at` | The actor's occurrence proof; a commitment, not a clock fact |
| `received_at` | A witness observing intake |
| `witnessed_at` | Inclusion under a signed witness checkpoint |
| `anchored_before` | An external timestamp anchor |

No verifier or UI may collapse these into one generic time verdict.

## Compatibility and boundaries

- Historical receipts always recompute under their own schema and canonicalizer.
- A v0.4 verifier must not rewrite `timestamp` into `claimed_at` inside old bytes.
- `producer` remains unauthenticated provenance metadata and is excluded from
  stable content identity.
- Log inclusion proves that the occurrence-bound attestation was retained under
  the referenced root; it does not prove worldly performance.
- Authority, occurrence, witnessing, and chronology remain distinct verdicts.
- The v0.4 reference implementation and vectors ship as an opt-in experimental
  draft in Bulla 0.44.3 after Python and browser parity checks. Publication
  does not make v0.4 normative or the default minting format. Normative
  promotion requires a separate protocol decision and evidence beyond this
  release.
