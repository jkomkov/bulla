# ActionReceipt v0.1 — wire spec

**Status:** superseded for producers by `action-receipt-v0.2.md`
(2026-07-13); retained in full — a v0.1 receipt recomputes with its own
`schema_version` forever, and this document remains its verification
reference.

A receipt records **one consequential agent action**: what was done, under whose
authority, within what bounds, with what recomputable verdict, and how it is
contested. This document is sufficient to verify a receipt **without the bulla
source** — a second implementer (Go, TypeScript, an auditor, a competitor) reads
only this. The reference implementation is `bulla.action_receipt`; the golden
vectors in `vectors/` pin every hash and verdict below.

## Canonicalization (the one rule — deed layer only)

> **Scope note (2026-07-12):** this rule governs the DEED layer (ActionReceipt,
> certificates) covered by this spec. The measurement layer (`WitnessReceipt`)
> currently hashes with Python's default *spaced* separators — see
> `WITNESS-CONTRACT.md` § Canonicalization. Unification is deferred to v0.2
> (RFC 8785 decision, `ADR-001-standing-model.md`) so canon migrates once.

Every hash is over the **canonical JSON** of a value:

```
canonical(x) = json.dumps(x, sort_keys=True, separators=(",", ":"))   # UTF-8, no spaces
H(x)         = "sha256:" + hex(sha256(canonical(x)))
```

Object keys are sorted; arrays keep their given order; no insignificant
whitespace. Every hash carries the `sha256:` prefix so an algorithm bump is
detectable.

## Document shape

```jsonc
{
  "schema_version": "0.1",
  "kind": "action_receipt",              // the only kind in v0.1
  "action":   { "type": "<open vocab>", "subject": { ... } },   // e.g. "package.release", "github.create_file"
  "diagnostic_ref": { "status": "reference"|"not_applicable"|"deferred", "ref"?: "sha256:…" },
  "evidence_refs": [ { "name": "<str>", "hash": "sha256:…" }, ... ],
  "anchor_ref": { ... },                 // {"kind":"git"|"pypi"|…, "ref":"…", "root_of_trust"?:{…}}
  "mandate":   { "authority": {…}, "bounds": {…} },     // ex ante legitimacy
  "remedy":    { "challenge_window": "…", "forum": {…}, "remedies": [ … ] },  // ex post contestation
  "retention": { "record": "<class>", "disclosure": "<class>" },
  "stake": null,                         // RESERVED (the bond slot) — always null in v0.1
  "signature": { … } | null,             // detached proof over hashes.content
  "timestamp": "<ISO-8601>",
  "producer":  { "bulla_version": "…", ... },   // provenance, NOT identity
  "hashes": { "content": "sha256:…", "event": "sha256:…", "attestation": "sha256:…", "log_leaf": "sha256:…" }
}
```

`mandate` / `remedy` / `retention` are **named views** over one recourse envelope
(see below); they are what a reader sees, but the *envelope* is what the
attestation hash commits.

## The four hashes — each answers one question

1. **`content`** — *"recompute the verdict."* The recomputable claim, free of the
   envelope, the clock, and the signature. Preimage:
   ```
   { "schema_version", "kind", "action", "diagnostic_ref", "evidence_refs", "anchor_ref" }
   ```
   Identical on any machine and any producer version — this is the receipt's
   recomputable identity, and the field a bond's slash condition recomputes.

2. **`event`** — *"which occurrence."* `H({ "content_hash": <content>, "timestamp": <timestamp> })`.
   Two re-derivations of the same claim share `content` but differ here.

3. **`attestation`** — *"who vouched."* Commitment to {content, signature, the
   recourse envelope}:
   ```
   H({ "content_hash": <content>, "signature": <signature or null>, "recourse_envelope": <envelope> })
   ```
   where `<envelope>` is the canonical envelope object reconstructed from the
   views (next section). With `signature: null` this is still well-defined (an
   unsigned receipt has a stable attestation identity).

4. **`log_leaf`** — *"where logged."* RFC 6962 leaf of the attestation hash:
   `"sha256:" + hex(sha256(0x00 ‖ utf8(<attestation_hash string>)))`. Ready to
   append to an RFC 6962 log (`bulla` uses `DeedLog`).

## The recourse envelope (what `attestation` commits)

Reconstruct from the views, then canonicalize:

```
recourse_envelope = {
  "deed_schema": "0.2",
  "authority":  mandate.authority,          // omit if absent
  "bounds":     mandate.bounds,             // omit if absent
  "recourse":   remedy,                     // omit if empty
  "retention_class":  retention.record,     // omit if absent
  "disclosure_class": retention.disclosure  // omit if absent
}
authority = { "principal", "policy", "delegation": [ … ] }
bounds    = { "scope", "expires"?, "rollback_window"? }
remedy    = { "challenge_window", "forum": {"log_endpoint","trusted_root_ref"}, "remedies": [ {"rung","verifier","anchor"}, … ] }
```

**The modality law (a verifier MUST enforce it).** Recourse has no stateful
respondent — the actor is gone at contest time. Therefore:
- every remedy names a non-empty `verifier` **and** a non-empty `anchor` (a
  remedy with no stateful anchor is process theater — reject the receipt);
- an `escalate` remedy requires `authority` (its anchor is the delegation chain);
- `forum.trusted_root_ref` is required (a forum that verifies against the host's
  own served root is self-consistency, not recourse);
- remedy `rung` ∈ `{recompute, challenge, cure, revert, slash, escalate}`.

## Verification levels (`verified_to`) — honest about depth

A verifier reports the **highest** rung it reached, never a lying boolean:

- **`digest`** — the four hashes recompute and match; the envelope re-validates
  (modality law); every `evidence_ref` has a name and a hash; `diagnostic_ref`
  has a valid non-null status. Zero dependencies.
- **`attestation`** — additionally, the detached `signature` over `hashes.content`
  verifies (ed25519 / did:key or COSE). Skipped, not failed, when the receipt is
  unsigned.
- **`log_inclusion`** — additionally, an external inclusion proof (Rekor / an
  RFC 6962 registry) binds the receipt to a public log. v0.1 carries no inline
  proof; this rung is the `bulla[sigstore]` follow-up. **Named, never faked.**

A receipt whose content was altered and whose hashes were *recomputed* by an
adversary still fails at `attestation`: the signature is over `content`, so any
change invalidates it and it cannot be re-forged without the key.

## Genesis / root of trust

A `package.release` receipt for the release that *introduces* receipts cannot be
its own root of trust. Its `anchor_ref.root_of_trust`
(`{scheme:"sigstore-pep740", rekor_log_index, attestation_bundle_sha256}`) points
at the **external** PEP 740 / Sigstore attestation; `bulla` binds to that public
log, it does not replace it. Verifying that binding is the `log_inclusion` rung.
