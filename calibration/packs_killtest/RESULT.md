# Packs execution kill-test — RESULT

**VERDICT: Outcome 3 (stop) for the fee/packs as an execution-failure predictor.**
bulla's convention signal is a **presence flag, not a mismatch detector**: on real
round-trip execution labels it has **essentially no discriminative power**
(likelihood ratio ≈ 1.07), scores **worse than a trivial always-fire baseline**, and
is **100% blind out-of-pack**. The only signal with real precision is a ~15-line
schema-diff that needs no packs and no cohomology. The math remains a sound *disclosure*
metric; it is not an *agreement* predictor, and the product claim conflated the two.

Harness: `packs_killtest.py` (frozen). Data: `result.json`. bulla @ `origin/main`
`2d238122` (0.41.0). 759 cases (567 in-pack: date_format/amount_unit/encoding; 192
traps: currency-code/length/geo — out-of-pack by design). Labels = real Python
round-trips (`strptime` / arithmetic / `encode`-`decode` / table lookup); no model, no LLM.
Break prevalence in-pack = 0.698.

## The numbers (in-pack conventions, real execution labels)

| predictor | precision | recall | F1 | note |
|---|---|---|---|---|
| **always-fire** (trivial) | 0.698 | 1.000 | **0.822** | fire on every convention seam |
| **bulla** (fee OR structural) | 0.722 | 0.788 | **0.754** | *below always-fire* |
| **dumb** (schema type/pattern diff) | **1.000** | 0.222 | 0.364 | high-precision, low-recall |
| **jsonschema** (strict validate value) | 0.955 | 0.212 | 0.347 | ≈ dumb; misses 79% |

On the **undeclared** subset (medium+bare, i.e. the case schema-validation is *supposed*
to miss): dumb = jsonschema = **0.000 recall** (nothing visible), and bulla is unchanged
at 0.722/0.788 — i.e. it contributes only its presence-flag null, not real detection.

## The decisive statement (base-rate-free)

bulla fires on **77.6%** of real breaks and **72.7%** of real non-breaks →
**likelihood ratio ≈ 1.07 ≈ 1** → the signal is (within noise) **independent of whether
a break actually occurs.** Its apparent precision (0.722) is just the break base-rate
(0.698); its apparent recall (0.788) is just its firing rate. A detector whose fire is
independent of the label is not a detector. The fee/structural channels:

- **fee channel:** fires on convention *presence* — MATCH fire-rate 0.727 ≈ MISMATCH 0.776
  (it cannot tell an agreeing seam from a disagreeing one; both have a hidden inferred
  convention, and `_find_shared_dimensions` keys on the dimension *name*, never the encoding).
- **structural-contradiction channel:** fired on **0** of 567 in-pack cases
  (`contradiction_score=0` even for ISO-vs-US dates with divergent `pattern`s) — it is too
  conservative to catch same-type convention divergence, which is exactly the interesting case.

## Pre-registered decision tree (PRE-REGISTRATION.md §5), evaluated

- **G-jsonschema** (does schema-validation miss real breaks?): jsonschema recall **0.212** <
  0.5 ✔ — 79% of real breaks are invisible to a strict validator. *The premise "there is
  something to catch" holds.*
- **G-mechanism** (bulla recall ≥ 0.80 AND precision ≥ 0.90): **FAILS** (R=0.788, P=0.722), and
  decisively **F1 0.754 < always-fire 0.822** with **LR≈1**. ⇒ **Outcome 3 (stop)** for
  bulla/fee as a convention-mismatch execution predictor.
- **G-baseline** (do the packs earn their keep?): bulla "beats" dumb on F1 (+0.39) **only by
  firing indiscriminately**; on the metric a linter lives or dies by — precision — **dumb 1.000
  ≫ bulla 0.722**. The pack taxonomy adds noise, not signal. The honest detector is the
  ~15-line schema-diff (which is jsonschema-adjacent, no packs).
- **G-prevalence** (Arm P): not re-run here — already answered NEGATIVE internally
  (`predicate_spike/RESULT.md` 2.4% < 5%; `execution_gap.py` fee misses 83%). Arm M is the
  mechanism arm; it shows the detector wouldn't work *even if* prevalence were high.

## Trap arm — the honest boundary

Out-of-pack conventions (currency-code, physical length, geo coordinate-order): bulla missed
**96/96 (100%)** real breaks. This extends `execution_gap.py`'s 83% blind-spot: bulla is
blind to any convention outside its base pack, by construction.

## What this means (and does not)

- **The fee is a sound *disclosure/omission* metric** (it fires when a tool depends on an
  inferred convention it doesn't expose). That is a real, defensible thing — but it is **not**
  "predicts execution failure," because *presence of an undisclosed convention ≠ disagreement
  between two conventions*. The showcase framing ("Schema validation: 0 problems. Bulla: 22")
  counts *flagged conventions*, not *found breaks* — a category error this test isolates.
- **Confirms + mechanism-explains the prior internal negatives:** `convention_distance_collapse`
  (fee ≡ Hamming — because both are presence counts), `execution_gap` (83% miss — out-of-pack
  + undeclared blindness), Outcome-4 BOUNDED (girth-3 ⇒ fee depth-3-local). This is the same
  wall, reached from the execution-label side.
- **Does NOT claim** the math is wrong or worthless — the disclosure geometry, the Tarski
  duality, the Lean proofs stand as research. It claims the **fee/packs are not a
  differentiated execution-failure-prediction product.**

## Fairness log (adversarial corrections made *before* scoring — so the null is bulla's, not mine)

1. **Extra observable field per tool.** A lone convention field hit guard.py:429's
   empty-observable fallback (forced observable ⇒ fee can never fire). Fixed with a neutral
   `note` field so the convention field is genuinely hidden. *Without this, bulla's fee would
   have been unfairly 0 everywhere.*
2. **Measured bulla's FULL output, not just the fee.** guard.py:403 states the coboundary
   never sees visible fields; the `StructuralDiagnostic` is the visible-incompatibility channel.
   "bulla fires" = fee>0 **OR** contradiction>0. (Structural still fired 0×, but it was given
   the chance.)
3. **Recognized conventions + keyword-rich descriptions.** In-pack conventions use bulla's
   actual base-pack keywords (`date_format`/`amount_unit`/`encoding`) so inference fairly fires
   (confirmed: `confidence="declared"`). currency/length/geo are genuinely out-of-pack → trap arm.
4. **Realism stratification (rich/medium/bare)** — and the result is *identical* across all three
   (bulla differs on 0/41 case-groups): bulla keys on field **name** and ignores the
   description/schema signal for the fee. Not a bug — verified rich≠bare schemas, same output.
5. **jsonschema validator:** the sandbox blocks installing `jsonschema` (PEP 668), so a
   stdlib strict `type`/`pattern`/`enum` validator was used. For these schemas it is
   byte-equivalent to `jsonschema` (JSON-Schema `pattern` = `re.search`); the P/R would not move.

## Spot-check (pre-registered: 10 cases, every ~75th, labels independently recomputed)

All 10 labels verified correct against an independent round-trip; cases are non-degenerate
(mix of match/mismatch, break/no-break, in-pack/trap). Representative:
`[0]` date iso→iso, no break — **bulla fires anyway (false positive)**;
`[75]` date eu→us, break — bulla fires (but fires on matches too);
`[150]` encoding utf-8→latin-1, break — **bulla misses**;
`[450]` currency alpha→numeric, break — bulla misses (out-of-pack);
`[750]` geo lat/lng→lng/lat, break — bulla misses (out-of-pack).

## Recommendation

The pre-registered gate fired **stop** for the productized fee. Do not build the transparency
log / second verifier / bond on this core. The residual honest product is a ~15-line
high-precision schema-diff convention linter — not differentiated enough to justify a build,
and strictly a "stricter jsonschema." The only thing that could reopen productization is a
**design partner's live traffic** showing undeclared convention divergence that is both
prevalent *and* costly — and this test shows the current detector wouldn't catch it anyway
(name-only, blind to agreement, blind out-of-pack). So the honest next move is **external
contact, not more internal machinery** — the same conclusion the routing go/no-go reached,
now with an execution-labeled proof behind it.
