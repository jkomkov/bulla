# Bulla 0.45.1 publication contract

Status: authorized coordinated product release.

Repository-owner instruction recorded 2026-08-03: `ship the product stack`.
That instruction authorizes integration, Bulla 0.45.1 publication, GitHub
release finalization, and deployment of the matching Glyph product surface
after their technical gates pass. It does not authorize Claim Closure 002,
outreach, or stronger evidence labels.

## Candidate invariant

The release adds the public `bulla receipt kit` package and CLI capability. It
ships `action-receipt-v0.2-verification-kit.zip` without changing the
normative ActionReceipt v0.2 format. Bulla's package version and the receipt
format version are separate clocks: `0.45.1` identifies the tooling release;
`v0.2` identifies the unchanged normative format. The expected kit SHA-256 is:

```text
2ec524f78885c122fd9e6301c85d757e94cf0c468278d11f1414b19a3a3c313c
```

The 0.45.0 prepublication workflow stopped at its Windows smoke gate before
build or upload. Its immutable slot and tag remain as the correction record.
Version 0.45.1 normalizes every kit text input to UTF-8 with LF line endings
and uses ASCII output in the standalone checker.

The wheel, source distribution, GitHub release asset, and Glyph download are
publisher-operated mirrors. Their byte equality does not make any mirror an
independent witness. The manifest checks archive contents; the detached digest
or signed release receipt authenticates the archive.

## Publication checklist

Do not request publication until every box below is backed by the exact
candidate commit and recorded in the release run.

- [ ] The portability correction has passed R3 artifact and verifier-semantics
      review with no open material finding.
- [ ] Pull-request checks pass on Python 3.10 through 3.13.
- [ ] After integration, Linux, macOS, and Windows release smoke checks pass on
      the exact resulting public `main` commit. A PR head, synthetic merge commit, pre-rebase commit, or
      pre-retarget commit is not the release source commit.
- [ ] The exact green `main` commit is recorded as the sole `source_commit`
      accepted by release preparation, tagging, publication, provenance
      verification, and finalization.
- [ ] Two clean kit builds produce the expected digest above, with identical
      member order, timestamps, modes, manifest, and payload bytes.
- [ ] Hostile archive tests reject missing, changed, extra, duplicated,
      traversing, symlinked, and case-colliding members.
- [ ] The zero-dependency checker runs with Bulla imports and network access
      unavailable under UTF-8 and Windows default console encodings.
- [ ] The wheel and source distribution embed the exact kit bytes, and an
      isolated installed `bulla receipt kit` exports those bytes unchanged.
- [ ] The release preimage binds the exact wheel, source-distribution, and kit
      digests. The signer pin and release repository controls match the exact
      candidate commit.
- [ ] The Glyph candidate contains the same kit and detached digest, labels the
      payment fixture as constructed, and does not present a global verified
      status.

External review is not a publication or deployment prerequisite. External
evidence changes evidence labels; it does not decide whether tested product
code may ship. The owner instruction above is the release authorization.

## Publication order

1. Record the final green public-mirror `main` commit and run the trusted
   publication workflow for that exact commit.
2. Verify PyPI's accepted wheel and source-distribution hashes and publisher
   attestation.
3. Mint the signed post-publication ActionReceipt binding the accepted wheel,
   source distribution, and verification-kit digest.
4. Attach the receipt, kit, and detached digest to the immutable GitHub release
   and verify byte equality with PyPI and the candidate.
5. Refresh Glyph's published-CLI evidence to the accepted Bulla 0.45.1
   artifacts and rerun its copy, claims, browser, accessibility, crawler, and
   presentation gates.
6. Record the accepted wheel, source-distribution, kit, signed release-receipt,
   GitHub release-asset, and Glyph asset digests in the release run.
7. Deploy the matching Glyph surface and verify the production downloads and
   retained-receipt interaction.

Any digest, signer, candidate-commit, or publisher-identity mismatch aborts the
sequence. Do not replace an asset in place or reuse a release tag. A local
candidate, tag, or unsigned receipt is not publication evidence.

PyPI publication consumes the version. A defect discovered after upload is
handled by a yank or correction record and a new package version; deletion,
replacement, or reuse of `0.45.1` is not an in-place repair path.
