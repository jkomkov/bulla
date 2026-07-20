# Pinned SMTInterpol accelerator

`LOCK.json` records the official Freiburg binary/source URLs and published
SHA-256 values. The jar is not vendored. Download it out of tree, then run:

```sh
PYTHONPATH=bulla/src python bulla/bench/invention/run_smtinterpol.py \
  --jar /path/to/smtinterpol-2.5-1453-gedae1f37.jar \
  --java /path/to/java \
  --split all \
  --output bulla/bench/invention/results/smtinterpol-all.json
```

For every UNSAT two-copy query the adapter performs a second solver call with
`get-proof` and invokes the bundled, separate RESOLUTE checker entry point. A
candidate still becomes executable only after Bulla's exhaustive FRSL-1
semantic verifier accepts it. SAT, timeout, or checker failure is never a
mathematical impossibility certificate. The all-corpus runner uses exhaustive
reference fallback only for negative, partial, and governed-choice exits and
reports native interpolation and fallback counts separately.
