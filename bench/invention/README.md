# Interpolant Envelope Benchmark

This is the frozen, synthetic FRSL-1 gate corpus for the experimental
proof-carrying predicate-invention engine.

- 60 instances across 12 declared seam families.
- 12 instances (20%) are frozen holdout, one per family with rotating outcome
  type.
- Eight adversarial controls cover vacuity, hidden-state leakage,
  contradictory local definitions, topology failure, query-only
  conservativity, authority expansion, noncanonical equivalent syntax, and
  protected-signature breakage.
- The existing Bridge hidden-repair cache is referenced but not copied or
  modified. Its 14/15 exact recovery result is a candidate-generation baseline,
  not an independently verified package baseline.

Regenerate deterministically:

    python bulla/bench/invention/generate.py

Run the design split:

    PYTHONPATH=bulla/src python bulla/bench/invention/run.py --split design \
      --output bulla/bench/invention/results/reference-design-2026-07-18.json

The runner verifies the freeze hashes before evaluation. Holdout execution is
explicit and should occur only after design choices are frozen:

    PYTHONPATH=bulla/src python bulla/bench/invention/run.py --split holdout \
      --output bulla/bench/invention/results/reference-holdout-2026-07-18.json

The official SMTInterpol artifact is hash- and version-pinned in
`../../tools/smtinterpol/LOCK.json`, but the jar and Java runtime are not
vendored. Run the real candidate/proof-checker cross-check with explicit local
paths:

    PYTHONPATH=bulla/src python bulla/bench/invention/run_smtinterpol.py \
      --jar /path/to/pinned-smtinterpol.jar \
      --java /path/to/java \
      --split all \
      --output bulla/bench/invention/results/smtinterpol-all-2026-07-18.json

Classify the holdout residue without tuning it:

    PYTHONPATH=bulla/src python bulla/bench/invention/analyze_marginal_seams.py \
      --output bulla/bench/invention/results/marginal-seams-holdout-2026-07-18.json

Run the independent standard-library-only checker in a fresh process for every
frozen result:

    PYTHONPATH=bulla/src python bulla/bench/invention/run.py \
      --split all \
      --standalone-checker bulla/scripts/verify_invention.py \
      --output bulla/bench/invention/results/reference-all-portability-2026-07-18.json

`external/` contains preregistered intake, blind-adjudication, role-separation,
and freeze contracts. It deliberately contains no synthetic stand-in for an
external result.

Run the frozen certified-refinement scaling grid (four dimensions, twelve
levels, five seeds; 240 cases):

    PYTHONPATH=bulla/src python bulla/bench/invention/run_refinement_scaling.py \
      --output bulla/bench/invention/results/refinement-scaling-2026-07-18.json

The scaling artifact reports synthesis, independent replay, exact planning,
peak memory, proof size, and predicate size. Resource-bound exits remain typed
`INDETERMINATE` observations and are not evidence of semantic impossibility.

All empirical conclusions remain local to this finite synthetic corpus.
