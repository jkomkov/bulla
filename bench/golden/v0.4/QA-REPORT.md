# Precedent Yield v0.4 QA report

Date: 2026-07-19

Candidate branch: `codex/bulla-precedent-yield-v04`

Base: `bulla-semantic-boundary-stack-v0.3` (`ab5ab444b21238d3e083ada93f2243d12efc1041`)

## Definitive Python run

The complete repository suite, excluding one confirmed pre-existing stale
canonical fixture, passed outside the localhost-socket sandbox:

```text
12776 passed, 31 skipped, 70 deselected in 245.89s
```

The 66 HTTP-registry, recourse-gate, and deed integration tests passed in a
separate elevated localhost run. Their initial `PermissionError` failures were
sandbox socket denials, not code failures.

The excluded test is
`test_sprint13_seed_certificates.py::test_seed_set_canonical_output_matches_fixture`.
It fails identically on the unchanged v0.3 freeze branch because the canonical
fixture's display text predates the current certificate producer. It is outside
the v0.4 experimental paths and was not regenerated or concealed.

## New and frozen evidence

- Claim Flow, precedent, finality, CLI, and v0.4 tests: green.
- Golden v0.1 standalone replay: green (704 cases).
- Golden v0.2 standalone replay: green (state-space, 1,344 metamorphic pairs,
  248 mutants).
- Golden v0.3 standalone replay: green (48 F9, 128 F10).
- Golden v0.4 standalone replay: green (240 lineage, 48 holdout, 52 F11).
- v0.4 clean-directory `python -I` replay: green.
- AST zero-import audit across all Golden verifiers: green.
- Deterministic v0.4 regeneration: byte-identical.
- Tampered case: fails closed.

## Formal

`lake build InterpolantEnvelope` completed successfully. The explicit axiom
audit completed; `ClaimFlow.lean` contains no `sorry`. Python compileall and
`git diff --check` passed.

## Freeze replay repair

The v0.3 stack receipt originally compared append-only Lean indices and the
entire stable source surface to the current working tree. That made an honest
historical receipt fail whenever a later experimental profile added an import
or CLI command. The verifier now reads those bound bytes from the receipt's
frozen Git commit while continuing to check v0.3 theorem bodies, specifications,
and Golden roots in the live tree. The signed receipt and its content hash are
unchanged; its two replay tests pass.
