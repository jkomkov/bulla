# Bulla Golden Suite v0.1

This directory is the content-addressed, dual-use validation packet for the
experimental Semantic Settlement profile.  Runs performed by the Bulla team
are **captive validation**, never independent attestation.

The suite has one case corpus and three projections:

- `packets/golden-open.zip` includes design-case reference outputs.
- `packets/golden-blind-candidate.zip` withholds all reference outputs.  It is
  not labeled a completed blind packet until the private oracle key material
  is encrypted to reviewer-controlled custody.
- `packets/golden-cleanroom.zip` contains the written format, cases,
  commitments, and zero-import verifier, but no Bulla checker.

Found-data cases use captured public schemas only.  No harvested MCP package is
installed or executed.  The existing 57-manifest corpus is an indirect seed;
the suite does not describe it as a continuous public crawler.

Generate or verify the frozen artifacts:

```sh
cd bulla
PYTHONPATH=src python bench/golden/v0.1/build_suite.py
PYTHONPATH=src python bench/golden/v0.1/run_suite.py --schedules 1000000
python -I scripts/verify_golden.py bench/golden/v0.1
```

The private oracle/nonces file is gitignored and written with owner-only
permissions.  It is not sufficient custody by itself; `custody-status.json`
therefore remains `PENDING_REVIEWER_ENCRYPTION` in this sprint.
