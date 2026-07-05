# Live-Execution Positive Control for the Per-Dimension Fee

**Status:** Construct-validity check (positive control). **Not** a generalization
result. The backends are purpose-built so that hidden convention mismatches fail
at runtime; this establishes that the end-to-end encoding behaves as the witness
theory claims, under real subprocess execution with fee-blind failure labels. It
does **not** establish that the fee predicts failures on real-world MCP servers —
that requires non-constructed failure labels and is explicitly deferred.

## Method

Two independent measurement channels per seam (`calibration/harness/`):

- **Schema channel** — `fee`, per-dimension `fee_d`
  (`bulla.diagnostic.decompose_fee_by_dimension`), and *observable convention
  distance* (the mismatches a pairwise schema checker can see), all computed from
  the advertised schemas without executing anything.
- **Execution channel** — a real `producer` subprocess emits a payload; a real
  `consumer` subprocess consumes it (`seam_backend.py`, minimal MCP stdio via
  `bulla.live_proxy.BackendServer`). The binary failure label is read from
  whether the consumer's JSON-RPC reply carried an `error`. Hidden mismatches
  raise genuine exceptions (`UnicodeDecodeError`, `IndexError`, `ValueError`);
  visible mismatches are normalized and succeed. No LLM judges anything; the
  label never inspects the fee.

## Results (9/9 runnable, 0 dropped)

| seam | fee | obs.dist | failed | fee_by_dim |
|------|----:|---------:|:------:|------------|
| hidden_mismatch_encoding | 1 | 0 | **True** | `{encoding: 1}` |
| hidden_mismatch_index | 1 | 0 | **True** | `{index: 1}` |
| hidden_mismatch_unit | 1 | 0 | **True** | `{unit: 1}` |
| visible_mismatch_encoding | 0 | 1 | False | `{encoding: 0}` |
| visible_mismatch_unit | 0 | 1 | False | `{unit: 0}` |
| hidden_match_encoding | 1 | 0 | False | `{encoding: 1}` |
| clean_visible_match | 0 | 0 | False | `{unit: 0}` |
| pair_visible_handled | 1 | 1 | False | `{encoding: 0, index: 1}` |
| pair_hidden_lurks | 1 | 0 | **True** | `{encoding: 0, index: 1}` |

## What this supports (and only this)

- **R — fee is a sound filter (perfect recall).** Every `fee == 0` seam runs
  without failure; every real failure sits on a dimension with `fee_d >= 1`.
- **B — the failures are invisible to observable distance.** All three
  hidden-mismatch failures have observable distance `0`: a pairwise schema
  checker would pass them. Conversely, the seams with positive observable
  distance are the *handled* (visible) ones that do **not** fail — observable
  distance points the wrong way.
- **P — the fee is deliberately imprecise on value match.** `hidden_match_encoding`
  has `fee = 1` but does not fail: the fee marks an unobservable at-risk
  *coupling*, not a confirmed value mismatch. The `pair_*` seams make this sharp —
  identical `fee_by_dim` (`{encoding:0, index:1}`), opposite real outcomes,
  because the fee cannot see whether the hidden index conventions actually differ.

## What it does NOT support

- No claim about real-world frequency, base rates, or distribution.
- No AUC / discrimination claim against a baseline on natural data.
- The construct works; whether it is *useful* on wild servers is open and needs
  non-constructed labels.

Reproduce: `python -m calibration.harness.live_validation`
Asserted by: `bulla/tests/test_live_validation_positive_control.py`
