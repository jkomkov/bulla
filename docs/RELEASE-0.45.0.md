# Bulla 0.45.0 publication contract

Status: release candidate. Merging a candidate branch does not authorize a
package publication, GitHub release, or Glyph deployment.

## Candidate invariant

The release adds the public `bulla receipt kit` package and CLI capability. It
ships `action-receipt-v0.2-verification-kit.zip` without changing the
normative ActionReceipt v0.2 format. Bulla's package version and the receipt
format version are separate clocks: `0.45.0` identifies the tooling release;
`v0.2` identifies the unchanged normative format. The expected kit SHA-256 is:

```text
4ea268d7e1d7b99a30a3db5a4acfb240fcdb0d906391dda1f1a2fb9e4a3bc51b
```

The wheel, source distribution, GitHub release asset, and Glyph download are
publisher-operated mirrors. Their byte equality does not make any mirror an
independent witness. The manifest checks archive contents; the detached digest
or signed release receipt authenticates the archive.

## Publication checklist

Do not request publication until every box below is backed by the exact
candidate commit and recorded in the release run.

- [ ] The monorepo release-candidate PR and public Bulla mirror PR have passed
      their required R3 reviews with no open material finding.
- [ ] The public mirror is byte-equivalent to the intended `bulla/` subtree,
      apart from declared standalone-repository differences.
- [ ] Pull-request checks pass after the public release-candidate PR targets
      `main`; checks on an earlier stacked base do not substitute.
- [ ] After the public release-candidate PR is integrated, Python 3.10 through
      3.13 and Linux/macOS package checks pass on the exact resulting `main`
      commit. A PR head, synthetic merge commit, pre-rebase commit, or
      pre-retarget commit is not the release source commit.
- [ ] The exact green `main` commit is recorded as the sole `source_commit`
      accepted by release preparation, tagging, publication, provenance
      verification, and finalization.
- [ ] Two clean kit builds produce the expected digest above, with identical
      member order, timestamps, modes, manifest, and payload bytes.
- [ ] Hostile archive tests reject missing, changed, extra, duplicated,
      traversing, symlinked, and case-colliding members.
- [ ] The zero-dependency checker runs with Bulla imports and network access
      unavailable, and agrees with the browser verifier on every shipped
      vector.
- [ ] The wheel and source distribution embed the exact kit bytes, and an
      isolated installed `bulla receipt kit` exports those bytes unchanged.
- [ ] The release preimage binds the exact wheel, source-distribution, and kit
      digests. The signer pin and release repository controls match the exact
      candidate commit.
- [ ] The Glyph candidate contains the same kit and detached digest, labels the
      payment fixture as constructed, and does not present a global verified
      status.
- [ ] The published-CLI evidence snapshot has been refreshed for Bulla 0.45.0
      before any Glyph deployment.

After every box is complete, obtain a separate written instruction containing
this exact authorization:

```text
APPROVE BULLA 0.45.0 PUBLICATION
```

Without that instruction, do not trigger trusted publishing, create or push a
release tag, finalize a GitHub release, or upload an asset.

## Publication order

1. Record the final green public-mirror `main` commit and run the trusted
   publication workflow for that exact commit.
2. Verify PyPI's accepted wheel and source-distribution hashes and publisher
   attestation.
3. Mint the signed post-publication ActionReceipt binding the accepted wheel,
   source distribution, and verification-kit digest.
4. Attach the receipt, kit, and detached digest to the immutable GitHub release
   and verify byte equality with PyPI and the candidate.
5. Update Glyph's published-package evidence to the accepted Bulla 0.45.0
   artifacts and rerun its copy, claims, browser, accessibility, crawler, and
   presentation gates.
6. Obtain a second written instruction containing this exact authorization:

   ```text
   APPROVE GLYPH VERIFICATION-KIT DEPLOYMENT
   ```

7. Only then may the matching Glyph release be deployed.

Any digest, signer, candidate-commit, or publisher-identity mismatch aborts the
sequence. Do not replace an asset in place or reuse a release tag. A local
candidate, tag, or unsigned receipt is not publication evidence.

PyPI publication consumes the version. A defect discovered after upload is
handled by a yank or correction record and a new package version; deletion,
replacement, or reuse of `0.45.0` is not an in-place repair path. The second
Glyph authorization must therefore bind the accepted PyPI bytes and
provenance that exist only after the first authorization has executed.
