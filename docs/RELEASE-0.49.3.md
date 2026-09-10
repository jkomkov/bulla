# Bulla 0.49.3 — package entry

This release updates the README, package URLs, and release metadata. It changes
no functional capture, verification, receiver-policy, reconciliation, or
ActionReceipt semantics. Package and receipt-format versions remain separate.
The version declaration changes producer provenance; newly generated receipts
can therefore have different metadata and hashes. Historical receipt bytes,
schema identifiers, and the retained v0.2 kit are not rewritten.

The approved implementation source is the public Bulla repository, based on
`9e2e5a1da744bcb781a8a6b87c23a5774da9991f`. The allowed-difference manifest
compares with the 0.49.2 publication source
`848417f39eecfefd70b6ed8d06e2c2969fe8de55`. Older monorepo runtime and
research code are not transplanted into this release.

## Release sequence

Two role-separated R3 reviews and two consecutive clean release passes cover
the exact candidate. After exact-green PR integration, the exact public main
commit is supplied to the read-only release preflight. Compatibility tests,
two reproducible builds, archive inventory and parity, installed-wheel tests,
and two session-root lifecycles precede any release identity.

Only a successful exact preflight authorizes the existing preparation workflow
to mint the signed slot and immutable tag. Trusted publication consumes only
the manifest-bound wheel and sdist; it does not rebuild them. PyPI hashes and
provenance must match before finalization. The old release identities, including
failed 0.49.0 and 0.49.1 slots and drafts, remain intact.

The browser inspector is a subsequent website milestone, not an installed
feature of this patch. This record does not authorize a protocol promotion,
new inference, participant exposure, or financial arrangement.
