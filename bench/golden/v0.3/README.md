# Golden Gate v0.3: Boundary Proof

Golden v0.3 adds two experimental families without changing or regenerating the
frozen v0.1 and v0.2 packets:

- **F9 (48 cases):** separates three substantive boundaries—semantic,
  grounding, and authority—from the procedure's derivation status.
- **F10 (128 cases):** stresses checked predicate compression with irrelevant
  vocabulary, sparse interactions, parity frontiers, and deliberately hidden
  ground variables.

The packet also reports the certified coverage of all 70 v0.2 `PARTIAL` exits
and a 24-case definition/observation scout.  It remains captive evidence.  No
reviewer-originated oracle, clean-room parity result, or independent
adjudication is manufactured here.

Rebuild and independently replay:

```sh
PYTHONPATH=bulla/src python bulla/bench/golden/v0.3/run_boundary_suite.py
python -I bulla/scripts/verify_golden_v03.py bulla/bench/golden/v0.3
```

The second command imports no Bulla package.  It recomputes the F9 oracle
rules, all F10 formula hashes and safe regions, the v0.2 partial denominator,
the scout decision rule, and every manifest hash.

Status: **internally verified / captive**. External replay remains
`BLOCKED_MISSING_EXTERNAL_PARTICIPANTS`.
