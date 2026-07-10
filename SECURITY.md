# Security Policy

Bulla emits cryptographically signed, timestamp-anchored, recomputable
attestations about software composition coherence. Its own integrity is the
whole product, so security reports are taken seriously and credited.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Instead, use either:

- **GitHub Security Advisories** — the "Report a vulnerability" button under the
  repository's **Security** tab (preferred; keeps the report private and
  coordinated), or
- **email** — `jk@gvt.ai` with subject `bulla security`.

Please include a description, affected version(s), and — ideally — a minimal
reproduction (a receipt/log/composition that demonstrates the issue).

You will get an acknowledgement within **3 business days** and a substantive
response (triage + planned fix window) within **10 business days**. We practice
coordinated disclosure: we will agree on a disclosure date with you, credit you
in the advisory and CHANGELOG unless you prefer otherwise, and publish a fix
before public details.

## In scope — what counts as a vulnerability

Anything that lets a party subvert the guarantees the receipts claim:

- **Signature forgery / bypass** — verifying a deed under an issuer that did not
  sign it (`bulla.identity`, ed25519/did:key).
- **Registry root equivocation or borrowed inclusion** — an inclusion proof
  accepted against a root not obtained independently of the host, or a valid
  proof for a leaf other than the deed's (`bulla.registry`, RFC 6962).
- **Canonicalization ambiguity or collision** — two distinct objects that share
  a canonical hash, or a hash a conformant reimplementation cannot reproduce
  from the spec (`bulla._canonical`, `WITNESS-CONTRACT.md`).
- **Anchor-binding bypass** — a receipt accepted as anchored to a timestamp it
  is not actually committed to (`bulla.ots`, OpenTimestamps).
- **Determinism breaks** — inputs that make a content hash depend on wall-clock,
  environment, or iteration order (the recomputability property).
- **Supply-chain integrity** of the published artifact (the release pipeline,
  attestations, Trusted Publishing).

## Out of scope

- The *detection* model's recall — that bulla does not flag a given seam is a
  capability limit, honestly disclosed (see the README on annotation-derived
  vs. execution-derived labels), not a security vulnerability.
- Missing performance/delivery enforcement — the bond/oracle rung is documented
  roadmap, not a shipped guarantee.
- Denial of service from pathologically large inputs.

## Supported versions

Pre-1.0: only the latest released minor receives security fixes. Once 1.0 ships,
this policy will name a support window.
