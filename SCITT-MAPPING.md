# Bulla deeds as SCITT Signed Statements — the one-page mapping

SCITT (Supply Chain Integrity, Transparency and Trust; the IETF architecture
around COSE Signed Statements + transparency services) and bulla's deed
registry share a substrate: signed statements in an append-only, RFC-6962-style
verifiable log. This note states the correspondence exactly, so the concession
is explicit and graceful — and so the difference is, too.

| SCITT concept | Bulla object |
|---|---|
| Statement | The deed's attestation preimage: `{certificate_content_hash, signature, recourse_envelope?}` — canonical JSON, content-type `application/bulla-deed+json` |
| Signed Statement (COSE_Sign1) | `bulla.cose.sign_statement_cose(signer, cert_dict)` — a genuine RFC 9052 COSE_Sign1 by the deed's own ed25519 key (`[scitt]` extra) |
| Issuer | `issuer.id` (`did:key` by default; bulla signs, never mints) |
| Transparency Service / Registry | `bulla.registry.DeedLog` — RFC 6962 Merkle log, inclusion + consistency proofs |
| Receipt (SCITT sense: proof of registration) | `DeedLog.inclusion(index)` — verified against a root the consumer pins (`verify_inclusion_record(rec, trusted_root=…)`) |
| Registration policy | `DeedLog.append_certificate` — the verified submission boundary (integrity + issuer authenticity before recording) |

**What is the same.** Append-only log; signed statements; inclusion proofs as
registration receipts; issuer-keyed enumeration. Anyone who says "you
reinvented SCITT" is right about this layer, and this note is the concession.

**What is different — the two layers SCITT does not carry.**

1. **The statement is recomputable, not merely attested.** A SCITT statement
   is whatever the issuer signed. A bulla deed's core is a *recomputable
   certificate*: `deed = f(composition@h, algorithm@v)` — any party re-derives
   the verdict from pinned inputs before trusting any signature. Rung one of
   the ladder (recomputable → attested → logged → anchored → bonded) has no
   SCITT counterpart.
2. **The statement carries an appeal path under the modality law.** The v0.2
   recourse envelope binds `authority` (delegation chain → a surviving
   principal), `bounds`, and `recourse` — remedies each naming a verifier and
   a stateful anchor, because there is no respondent left to serve. SCITT
   proves *what was said and when*; the envelope states *what can be done
   about it, checked how, against what*.

**Wire note.** The COSE signature is computed over the RFC 9052
`Sig_structure` (a parallel, standards-true attestation by the same key), not
a re-wrapping of the deed's detached proof — the detached proof signs the bare
content hash and would not be a conformant COSE_Sign1 signature. Emit both
when interoperating; the registry leaf format is unchanged either way.
