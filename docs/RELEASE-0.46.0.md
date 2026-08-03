# Bulla 0.46.0 publication contract

Status: authorized coordinated product release.

Repository-owner instruction recorded 2026-08-03: implement the Terminal
Verifier Sprint. That instruction authorizes one gated Bulla 0.46.0
publication, GitHub release finalization, and deployment of the matching Glyph
surface after the technical and review gates below pass. It does not authorize
Claim Closure 002, outreach, a hosted register, or stronger evidence labels.

## Candidate invariant

The release adds the stable `bulla receipt drill` command and extends the
zero-dependency kit checker to inspect a caller-supplied normative ActionReceipt
v0.2. It does not change the receipt schema. Bulla's package version and the
receipt format version are separate clocks: `0.46.0` identifies the tooling
release; `v0.2` identifies the unchanged normative format. The expected kit
SHA-256 is:

```text
8f2cdd16bcbd1a1121f49545b6a6512872b188221ca30ec054dfd6b2fb2142ab
```

The wheel, source distribution, GitHub release asset, and Glyph download are
publisher-operated mirrors. Byte equality demonstrates reproducibility, not
independent custody. The caller-provided detached digest is trust input; the
signed release receipt binds publisher identity after PyPI publication.

The public release lineage records that 0.45.0 was not published and that the
immutable 0.45.1 GitHub body retained provisional wording. Neither historical
record is rewritten by this release.

## Publication checklist

Do not request publication until every box below is backed by the exact green
public `main` commit and recorded in the release run.

- [ ] The terminal-verifier candidate has passed role-separated R3 artifact and
      verifier-semantics review with no open material finding.
- [ ] Two consecutive global clean passes complete on the exact release commit.
- [ ] Python 3.10 through 3.13 and Linux, macOS, and Windows release checks pass.
- [ ] The standalone checker, Bulla verifier, and browser agree on supported
      v0.2 vectors and hostile raw-input cases at their declared capability
      rungs.
- [ ] Duplicate members and resource-limit violations fail before semantic
      verification; both checkers reject the deterministic tamper control.
- [ ] The drill completes with DNS, sockets, HTTP, and child process network
      utilities denied by an audit hook installed before checker import.
- [ ] Two clean kit builds are byte-identical and match the digest above.
- [ ] Wheel and source distribution embed the exact kit; an isolated installed
      `bulla receipt kit` exports those bytes unchanged.
- [ ] The release preimage binds the accepted wheel, source distribution, and
      kit digests and the final GitHub body contains no provisional wording.
- [ ] Glyph serves immutable 0.45.1 and 0.46.0 kit paths with headers computed
      from the actual bytes and keeps adjacent artifact-like paths as 404s.
- [ ] The frozen misconception test and production stranger journey reject all
      four registered misunderstandings.

External review is not a publication or deployment prerequisite. External
evidence changes evidence labels; it does not decide whether tested product
code may ship. The owner instruction above is the release authorization.

## Publication order

1. Merge the public Bulla and monorepo integration commits after their gates
   pass. The exact green public `main` commit is the sole `source_commit`; a PR
   head, synthetic merge commit, pre-rebase commit, or pre-retarget commit is
   not the release source commit.
2. Run trusted publication for that exact Bulla commit. If PyPI has already
   consumed 0.46.0, record the failed slot and continue with 0.46.1.
3. Verify PyPI's accepted wheel, source distribution, and publisher
   attestations.
4. Mint the signed post-publication ActionReceipt binding the accepted wheel,
   source distribution, and verification-kit digest.
5. Generate and set the complete GitHub release body before changing the draft
   to published, then verify every immutable asset.
6. Refresh Glyph's published-artifact evidence, deploy the matching surface,
   and rerun the production stranger journey.

PyPI publication consumes the version. If PyPI accepts a defective release,
do not delete or replace it. Record the defect and publish a correction version.
