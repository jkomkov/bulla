# Routed-inference local handoff demo

This offline demo runs the harness, router, provider, and relier as separate signing
processes, then copies only the generated bundle and zero-Bulla checker into an
isolated stranger-verifier directory. It is not a live provider, witness, settlement,
or network integration.

From the `bulla/` directory with the identity extra installed:

```sh
PYTHONPATH=src python3 examples/routed-inference/run_demo.py --fixture-keys
PYTHONPATH=src python3 examples/routed-inference/run_demo.py \
  --fixture-keys --fault provider-substitution
PYTHONPATH=src python3 examples/routed-inference/run_demo.py \
  --fixture-keys --fault budget-overrun
```

Omit `--fixture-keys` to generate ephemeral keys. Deterministic keys exist only so the
test suite can reproduce the demonstration; they are public fixtures and must never be
used for real authority.

The runner exits zero when the stranger obtains the expected result. The two fault
modes are successful demonstrations when the independently run checker returns the
expected `VIOLATES` codes.
