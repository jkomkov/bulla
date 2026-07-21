# Security Policy

Bulla emits portable, recomputable records of consequential agent actions,
including cryptographic authority bindings, evidence references, bounds, and
recourse. Its own integrity is the whole product, so security reports are taken
seriously and credited.

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
  from the ActionReceipt specification (`bulla._canonical`, `spec/`).
- **Anchor-binding bypass** — a receipt accepted as anchored to a timestamp it
  is not actually committed to (`bulla.ots`, OpenTimestamps).
- **Determinism breaks** — inputs that make a content hash depend on wall-clock,
  environment, or iteration order (the recomputability property).
- **Supply-chain integrity** of the published artifact (the release pipeline,
  attestations, Trusted Publishing).

## Out of scope

- Failure to establish a worldly claim that the receipt explicitly reports as
  self-asserted, unresolved, unreachable, not applicable, or outside its
  declared verification depth.
- Completeness of experimental semantic results outside their pinned finite
  model class, closure warrant, vocabulary, and logical resource budget.
- Missing independent witness operators, forum reachability, production
  settlement custody, stake, or slashing. These are disclosed gaps, not shipped
  guarantees.
- Denial of service from pathologically large inputs.

## Supported versions

Pre-1.0: only the latest released minor receives security fixes. Once 1.0 ships,
this policy will name a support window.
