# Independent Reproduction Packet

Give an external implementer:

- `bulla/spec/semantic-finality-v0.1-experimental.md`;
- every `*.blind.json` file in this directory;
- the canonical JSON v2 rule in the profile.

Do **not** provide `scripts/verify_semantic_finality.py`, any `*.internal.json`
file, or `answer-key.internal.json`. The implementer must independently produce
canonicalization, authority verification, reserve recomputation, finality
decisions, conflict replay, and stale-term handling. Compare only after their
outputs are frozen.

Current external status: `BLOCKED_AWAITING_INDEPENDENT_IMPLEMENTER`. No external
reproduction result is claimed in this repository snapshot.
