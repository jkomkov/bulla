# Causal Answerability hostile review ledger

Classification: internal captive attack pass.

| Attack | Disposition | Bound artifact |
|---|---|---|
| Change `claimed_at` and recompute unsigned hashes | Rejected by occurrence proof | `spec/vectors/v04-occurrence-bound.json` |
| Transplant occurrence or authority proof | Rejected by domain and signer binding | `tests/test_action_receipt_v04.py` |
| Duplicate or oversized JSON members | Rejected before crypto | `src/bulla/receipt_parser.py` |
| Substitute stronger adapter capabilities after intent | Rejected before I/O | `tests/test_action_boundary.py` |
| Reuse an idempotency key for a different request | Rejected as collision | `tests/test_action_boundary.py` |
| Swap recourse envelope during lifecycle | Rejected before append | `tests/test_action_boundary.py` |
| Crash after remote effect | Preserved unresolved; query/retry cannot duplicate verified rail effect | `tests/test_action_boundary.py` |
| Collapse contradictory provider outcomes | Appends `CONFLICT`; no truth selection | `tests/test_action_boundary.py` |
| Let opener issue a finding | Rejected without forum token | `tests/test_executable_challenge.py` |
| Let forum execute remedy | Rejected without separate settlement token | `tests/test_executable_challenge.py` |
| Price a categorical bar | Rejected by the effect warrant | `tests/test_executable_challenge.py` |
| Rewrite target, parent, state summary, or epoch | Replay rejection or staleness | `tests/test_executable_challenge.py`, Golden F13 |

Unclosed external questions remain adapter conformance, real integration burden,
forum availability, and whether an authorized remedy is institutionally honored.
