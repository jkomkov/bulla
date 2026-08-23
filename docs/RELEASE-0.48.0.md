# Bulla 0.48.0 publication contract

## Scope

Bulla 0.48.0 adds an optional grounding floor to `ReliancePolicy` and publishes
`reliance.evidence-strict.v1`. It does not change any ActionReceipt wire format,
source-only research profile, settlement behavior, or existing policy definition.
The package version and the
receipt format version are separate clocks.

Repository-owner instruction recorded 2026-08-23: implement the reviewed Sprint
0.10 evidence-strict policy, publish it only after the public Bulla default branch is
green, and preserve the existing release ceremony. External review is not a publication or deployment prerequisite.

## Frozen compatibility conditions

- `reliance.strict.v1` remains
  `sha256:a05fc64115edc0676b4bd0092c0cadf94400abf6cbb7d32520944bdefdc5ee0b`.
- `reliance.pragmatic.v1` remains
  `sha256:2549faa0f297c8e43d9462a28b464cd2e0813e986b58555d9b29572351fc0b88`.
- The new optional field is omitted when unset.
- Source-only profiles remain outside the wheel and sdist.

Grounding is a supplied evidence classification. It does not establish occurrence,
worldly truth, organizational independence, custody, settlement, or downstream
effect.

## Publication sequence

1. Merge the reviewed public Bulla pull request.
2. Run the signed default-branch preparation workflow for the exact green `main`
   commit. The exact green public `main` commit is the sole `source_commit`; a PR
   head, synthetic merge commit, pre-rebase commit, or local build is insufficient.
3. Publish through the trusted PyPI workflow. PyPI publication consumes the version.
4. Verify PyPI's accepted wheel, sdist, provenance, installed policy definitions, and
   archive membership against the reviewed candidate.
5. Refresh Glyph's published-package and artifact evidence from those accepted bytes.

This record does not authorize unrelated releases, operational witness claims,
external-counter increments, or settlement activity.
