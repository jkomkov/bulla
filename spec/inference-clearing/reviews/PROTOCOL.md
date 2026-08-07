# Protocol review

- **Scope:** evidence taxonomy, term and lineage binding, trust context, witness proofs, policy staging, parser parity, and hostile cases.
- **Reviewed commit:** `8e60342d`.
- **Material findings:** the browser did not bind a checkpoint proof issuer to the declared witness operator; the publisher could rewrite the provider process claim; required artifact shapes and model-presence rules diverged across Python, Node, and browser, including an untyped Python failure.
- **Correction:** `f4e497de` binds witness proof identity to the context-accepted operator, binds the execution report and process claim under `inference.accept`, aligns provider-kind, adapter, retained-model, and closed-object checks, and adds transient hostile vectors for each counterexample.
- **Regression evidence:** 39 focused Python tests, 10 browser tests, standalone Node parity, four cross-checker hostile cases, typed failures without tracebacks, deterministic generation, and the finite Lean model.
- **Recheck:** the same reviewer reported `PASS` with no remaining material counterexample.

This review establishes the internal experimental implementation gate. It does not establish foreign reproduction, historical provider execution, answer truth, denominator completeness, or settlement execution.

## Completion recheck

- **Scope:** presentation-level funds epistemics, generated reproduction commands, provider substitution invariants, and authenticated comprehension scoring.
- **Material findings:** the initial human-evidence schemas could authenticate neither the coordinator nor participant records; after that repair, the scorer still accepted schema-invalid scalar values and could emit a result outside its published wire contract.
- **Correction:** `5f808eb2` keeps the wire-level settlement report unchanged, adds `funds_movement: NOT_ESTABLISHED` only to the generated presentation projection, runs the exact published commands in a clean environment, authenticates gate and reader evidence from an external coordinator context, validates every input and derived result against the frozen schema subset, and atomically publishes only a conforming result.
- **Regression evidence:** 52 focused Python tests, including fully re-signed hostile records with an integer attempt ID and a non-hex preview commit; 11 browser tests; exact Python/Node comparison; deterministic generation; distribution and workflow-policy gates.
- **Recheck:** the same protocol reviewer reported `PASS`; no material counterexample remains in the affected lane.

## Clearing-reveal promotion recheck

- **Scope:** evidence versus execution, eligibility versus authorization,
  provider-contact language, fail-closed disclosure, and comprehension-gate
  governance.
- **Material findings:** the first presentation disclosed its clearing verdict
  before local verification, mislabeled an output-binding fact, and described a
  changed gate consequence without a separately bound policy.
- **Correction:** the verdict now mounts only after report parity and the
  request audit succeed; output comparison reflects the frozen report; and
  `promotion-policy.json` narrowly supersedes repository promotion governance
  while preserving the content-addressed protocol and authenticated scorer.
- **Recheck:** the trust-semantics reviewer reported `PASS`. The source profile,
  checkers, fixtures, and reproduction-kit bytes did not change.
