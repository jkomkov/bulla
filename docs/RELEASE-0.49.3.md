# Bulla 0.49.3 — package entry

This release updates the README, package URLs, and release metadata, and repairs
a Windows capture-root concurrency failure found by prepublication validation.
Verification, receiver-policy, reconciliation, and ActionReceipt semantics are
unchanged. Package and receipt-format versions remain separate.
The version declaration changes producer provenance; newly generated receipts
can therefore have different metadata and hashes. Historical receipt bytes,
schema identifiers, and the retained v0.2 kit are not rewritten.

The approved implementation source is the public Bulla repository, based on
`9e2e5a1da744bcb781a8a6b87c23a5774da9991f`. The allowed-difference manifest
compares with the 0.49.2 publication source
`848417f39eecfefd70b6ed8d06e2c2969fe8de55`. Older monorepo runtime and
research code are not transplanted into this release.

## Authorized Windows repair

The documentation-only candidate at `50515fa59dafdc379be603ee23b73875b9a43c9e`
failed Windows preflight during eight concurrent root initializations. Native
claim creation returned access denied; no release identity was minted. The user
then authorized a narrowly scoped runtime repair. A delete-pending owner is one
documented cause of this error, not proof of the precise cause of that CI event.

On native access denial the initializer now waits only for a valid root with no
remaining claim, within the original five-second deadline. It cannot acquire
ownership, remove any claim, or repair a root on this path. Genuine denied access
and stranded claims remain bounded failures. The source-difference manifest pins
the exact before/after capture module and preserves the original commitment for
every other protected source and specification file.

A Windows-only test deliberately holds the native claim delete-pending before
its owner closes the handle, observes actual error 5, and requires the losing
initializer to wait. Platform-neutral tests cover native-error classification,
valid publication, absent/malformed roots, unreadable claims, and stranded claims.
The original concurrency, privacy, filesystem, and CP1252 regressions remain.

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
