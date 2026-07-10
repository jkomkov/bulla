# Governance

Bulla is the Apache-2.0 reference implementation of the **Glyph** coherence-receipt
standard. This document says who decides what, and how that is intended to open up
as the project grows. It is deliberately small; it will grow with the community, not
ahead of it.

## Current model: single maintainer (BDFL), by necessity not preference

Today the project has one maintainer of record — **John Komkov** (`@jkomkov`) — who
has final say on merges, releases, and the roadmap. This is honest about the current
bus factor (one), not an ideal end state.

Decisions are made in the open: design happens in issues and pull requests, and the
rationale for a non-obvious call is written down (see the CHANGELOG and
`WITNESS-CONTRACT.md`, which record *why*, not just *what*).

## What is deliberately hard to change

Two surfaces are load-bearing for a system whose whole pitch is "don't trust it,
recompute it," and change to them requires extra care (a written rationale, a
migration/verify story, and — once co-maintainers exist — a second reviewer):

- **The wire format and canonicalization** (`spec/`, `WITNESS-CONTRACT.md`,
  `bulla._canonical`). A hash a third party cannot reproduce from the published spec
  is a broken promise. Format changes are versioned (`CANON_VERSION`,
  `ALGORITHM_VERSION`) and keep old artifacts verifiable.
- **The trust semantics of the registry and identity layers** (`registry.py`,
  `identity.py`): host-asserted roots refused, borrowed inclusion refused, signing
  under an issuer that did not sign refused. `CONTRIBUTING.md` records the merge rule
  that protects these.

## The path to open governance

The intended progression, tied to real signals rather than dates:

1. **Now** — single maintainer; everything in the open; `CODEOWNERS` names the owner
   of each integrity-critical surface.
2. **On the first sustained external contributors** — invite co-maintainers with
   merge rights, adopt a two-reviewer rule for the load-bearing surfaces above, and
   split `CODEOWNERS` accordingly.
3. **On real adoption / a second independent implementation of the spec** — move the
   *standard* (Glyph: `spec/` + the independent verifier) to neutral, multi-party
   governance (a working group or a foundation), so no single party is the gatekeeper
   of the substrate. The Apache-2.0 license is chosen precisely to make that possible.

## Security

Vulnerability reports follow `SECURITY.md` (private disclosure, coordinated fix).

## Changing this document

Amendments are proposed by pull request and, until co-maintainers exist, decided by
the maintainer of record with the rationale recorded in the PR.
