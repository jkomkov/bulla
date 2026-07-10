# Scope & falsifications — what bulla does *not* do

bulla's brand is *recomputable honesty*: a verdict you can re-derive rather than
take on trust. A tool that asks to be trusted about verification owes the same
standard to its own claims. So this page ships the negative results — including the
ones that retired a framing bulla used to lead with.

## The one that matters: the coherence fee is not an execution-failure predictor

For a while, bulla was described as if the **coherence fee** caught real breakage that
schema validation misses ("schema validation: 0 problems, bulla: 22", "catches this
before execution"). That causal reading is **not supported by the evidence.** The fee is
a **disclosure / omission** measure — *how much convention two composed tools leave
undisclosed at their seam* — computed from schemas alone. That is a real and useful
thing. It is **not** the same as predicting that the composition will fail at runtime.

### What the fee *is* (characterized, on-main)

- An **exact additive decomposition over convention dimensions**, `fee = Σ_d fee_d`
  (e.g. `path_convention: 13, id_offset: 6, …`) — a structured convention-distance /
  omission measure with genuine per-dimension resolution.
  → [`../papers/coherence-cliff/results/convention_distance_collapse.md`](https://github.com/jkomkov/res-agentica/blob/main/papers/coherence-cliff/results/convention_distance_collapse.md)

### What the fee is *not* — three on-main negatives, stated separately

These are **distinct** results about **distinct** questions. Do not read them as one.

1. **It does not beat a cheap baseline on the real corpus (structural).**
   On the real registry corpus (38 servers → 703 compositions, 289 cyclic), the cycle
   girth distribution is **100% girth-3**, and a depth-3 *bounded-local* baseline recovers
   **100%** of the obstruction (`frac_cyclic_depth3_recovers_full_obstruction: 1.0`). The
   fee's higher structure earns nothing the cheap baseline doesn't already get here.
   Pre-registered verdict: **`OUTCOME_4_BOUNDED`**.
   → [`dissociation_pre_registration.md`](https://github.com/jkomkov/res-agentica/blob/main/papers/coherence-cliff/dissociation_pre_registration.md) · [`dissociation_stage0_girth.json`](https://github.com/jkomkov/res-agentica/blob/main/papers/coherence-cliff/results/dissociation_stage0_girth.json)

2. **Where real execution labels exist, `fee=0` does not mean execution-safe.**
   Over an execution-independent grid labelled by **real file I/O** (encoding / EOL /
   path-rooting), `fee=0` compositions breached **30/36** at real execution — the fee is
   blind to value-level conventions. (`fee>0` breached 3/4; the fee did not separate
   breaking seams from safe ones. Per the artifact's own note, these rates are artifacts
   of the *authored* tool set — evidence the blind spot is broad, not a corpus estimate.)
   → [`calibration/execution_gap.py`](calibration/execution_gap.py) · [`calibration/results/execution_gap.json`](calibration/results/execution_gap.json)

3. **(A different question) Static-detectable misalignment is rare in the corpus.**
   A *separate* pre-registered probe asked how often real compositions carry a
   divergently-typed shared predicate-like field a static check could flag: **17/703
   (2.4%)**, below the pre-registered **5%** bar → pre-registered **NEGATIVE**. This is a
   *static-detectability floor*, **not** the execution-predictor test above — listed for
   completeness and kept distinct.
   → [`calibration/predicate_spike/RESULT.md`](calibration/predicate_spike/RESULT.md)

> **The execution-labelled kill-test** (a fee-vs-baseline-vs-jsonschema battery scored by
> real round-trips) strengthens (1)–(2) directly: the fee's fire is ~independent of whether
> a break occurs — **likelihood ratio ≈ 1.07** (vs 1.0 = no information), and it is worse
> than an always-fire baseline (F1 0.754 < 0.822) and 100% blind out-of-pack. Pre-registered
> (design frozen before results; see the harness's own self-reported freeze correction), with
> real Python round-trip labels the predictors never see.
> → [`calibration/packs_killtest/`](calibration/packs_killtest/) (`RESULT.md` · `PRE-REGISTRATION.md`)

## What none of this impugns

- **The mathematics.** The fee is a genuine invariant (a coboundary rank; the additive
  decomposition and the Tarski-duality / Lean results stand). What is retired is the
  *product claim* that it predicts execution failure — not the math.
- **The record & recourse layer.** The `ActionReceipt` (authority, bounds, recomputable
  verdict, recourse), `bulla coverage` (receipted vs. an anchor you did not mint), the
  append-only registry, and the retention asymmetry are **untouched** by any of the above —
  they are the load-bearing claims, and they do not depend on the fee predicting anything.

## One sentence

The fee measures what two tools leave **undisclosed**; it does not predict what will **break** —
and bulla is honest about the difference because a verification tool that hides its own
negative results is exactly the thing it warns you about.
