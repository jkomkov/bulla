# Bulla 0.47.1 publication contract

Status: authorized coordinated product release.

Repository-owner instruction recorded 2026-08-04: implement the First Action
Sprint. The 0.47.0 candidate was not uploaded to PyPI after its Windows gate
found a console-encoding defect. The same instruction authorizes the planned
correction release: one gated Bulla 0.47.1 publication, GitHub
release finalization, and deployment of the matching Glyph surface after the
technical and review gates below pass. It does not authorize
Claim Closure 002, outreach, inference clearing, a hosted register, settlement,
or stronger evidence labels.

## Candidate invariant

The release adds the stable `bulla demo` command by composing `wrap_action`,
ActionReceipt verification, `event_coverage`, and `receipt drill`. It does not
change the receipt schema. Bulla's package version and the
receipt format version are separate clocks: `0.47.1` identifies the tooling
release; `v0.2` identifies the unchanged normative format. The immutable
0.47.0 slot and tag remain the failed-candidate record and are not reused.

The demonstration is constructed. Its receiver record is the declared coverage
denominator and does not establish that a payment network moved funds. The
original receipt remains unchanged while a separate altered copy fails and a
second action produces coverage `1/2` with `pay_demo_043` named as unreceipted.
No reliance or eligibility policy is computed.

The expected unchanged verification-kit SHA-256 is:

```text
8f2cdd16bcbd1a1121f49545b6a6512872b188221ca30ec054dfd6b2fb2142ab
```

The wheel, source distribution, GitHub release asset, and Glyph download are
publisher-operated mirrors. Byte equality demonstrates reproducibility, not
independent custody. The signed post-publication receipt binds the accepted
package artifacts and the retained kit digest.

## Publication checklist

- [ ] Product-semantics and release/surface R3 reviews have no open material finding.
- [ ] Two consecutive global clean passes complete on the exact release commit.
- [ ] Python 3.10 through 3.13 and Linux, macOS, and Windows release checks pass.
- [ ] Two `bulla demo` runs produce byte-identical retained artifacts.
- [ ] The generated receipt is normative v0.2 and both retained checkers accept it.
- [ ] The altered copy fails while the original receipt digest remains unchanged.
- [ ] Coverage is `1/1` before bypass and `1/2` afterward with `pay_demo_043` named.
- [ ] Existing and symlink output paths fail closed without replacement.
- [ ] The kit remains byte-identical across wheel, source distribution, GitHub, and Glyph.
- [ ] The public surface preserves the event-truth and denominator boundaries.
- [ ] The six frozen misconception checks pass on the production stranger journey.

External review is not a publication or deployment prerequisite. External
evidence changes evidence labels; it does not decide whether tested product
code may ship. The owner instruction above is the release authorization.

## Publication order

1. Merge only the public Bulla commit after its gates pass. Keep the monorepo
   integration PR unmerged while its published-package evidence still names
   0.46.0. The exact green public `main` commit is the sole `source_commit`; a
   PR head, synthetic merge commit, pre-rebase commit, or pre-retarget commit
   is not the release source commit.
2. Run trusted publication for that exact Bulla commit as 0.47.1. The 0.47.0
   GitHub slot is already recorded as failed and PyPI never accepted it.
3. Verify PyPI's accepted wheel, source distribution, and publisher attestations.
4. Mint the signed post-publication ActionReceipt binding the accepted wheel,
   source distribution, and verification-kit digest.
5. Generate and set the complete GitHub release body before changing the draft
   to published, then verify every immutable asset.
6. Refresh Glyph's published-package and artifact evidence from the accepted
   release, rerun its gates, then merge and deploy the matching monorepo
   surface. Rerun the production stranger journey after deployment.

PyPI publication consumes the version. If PyPI accepts a defective release,
do not delete or replace it. Record the defect and publish a correction version.
