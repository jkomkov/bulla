# Bulla Golden Gate v0.2

Profile: `bulla.golden-suite/0.2-experimental`

Golden Gate v0.2 is an experimental benchmark for typed, correct abstention and reproducible semantic control. It preserves Golden v0.1 and adds stronger internal evidence plus an externally controlled evaluation protocol.

The repository contains executable internal work and external-role packets. It intentionally does not fabricate curators, adjudicators, a clean-room implementer, hidden cases, ratings, custody keys, cross-platform observations, or an external reveal. Until those roles act, the external gate is `BLOCKED_MISSING_EXTERNAL_PARTICIPANTS`.

## Run

From `bulla/`:

```sh
PYTHONPATH=src python3 bench/golden/v0.2/run_suite.py
PYTHONPATH=src python3 bench/golden/v0.2/run_scaling.py
PYTHONPATH=src python3 bench/golden/v0.2/run_drift.py
PYTHONPATH=src python3 scripts/verify_golden_v02.py bench/golden/v0.2
```

The generated reports are deterministic except for explicitly recorded elapsed-time and peak-memory observations. Report hashes exclude those observational coordinates.

## Claim boundary

Current repository evidence is internally verified/captive. Reviewer-originated replay, independent implementation parity, found-data adjudication, and non-local portability cells remain external operations and may remain blocked.
