# Bulla 0.44.0 release contract

Status: release candidate until the public mirror tag completes the trusted
publishing workflow.

## Surfaces

- ActionReceipt v0.2 remains the normative stable wire format.
- ActionReceipt v0.3 authority binding is an opt-in released implementation of
  a non-normative draft.
- Modules under `bulla.experimental` remain experimental and receive no stable
  API promise from this release.
- Stable imports, v0.1/v0.2 preimages, canonicalization, and the result algebra
  are unchanged.

## Ceremony

1. Merge the monorepo release commit after the full local and three-platform
   gates pass.
2. Sync the exact `bulla/` subtree to a clean branch of the public
   `jkomkov/bulla` mirror and review its diff.
3. Build one wheel and one sdist from the clean mirror candidate. Verify an
   isolated install, zero-import vectors, version/tag equality, and byte parity
   between source, wheel, and sdist runtime files.
4. Merge the mirror release PR. Tag that final commit `v0.44.0`.
5. The tag workflow publishes through PyPI Trusted Publishing, verifies PyPI's
   accepted hashes and PEP 740 publisher identity, then mints the signed
   post-publication ActionReceipt and attaches it to the durable GitHub release.
6. Only after those steps succeed may status change from release candidate to
   published and receipted.

An unsigned, pre-publication, locally built, or unattached receipt is not a
contemporaneous release receipt. Git and PyPI hashes must agree; a tag alone is
not publication evidence.

## Candidate QA observation

The local candidate passed 12,773 tests (34 skipped and 69 intentionally
deselected), source/wheel/sdist runtime parity over 177 files, isolated wheel
installation, CLI create/verify smoke tests, and the 15-case zero-import
reference checker. This observation does not stand in for the required
three-platform workflow or the post-publication receipt.
