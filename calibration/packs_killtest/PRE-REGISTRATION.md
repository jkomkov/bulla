# Packs execution kill-test — pre-registration

**The design was committed before any result exists.** House rule (the burned lesson: a
result squash-committed with its pre-registration is not a pre-registration): *this file* —
the hypothesis, the three real-execution oracles, the three blind predictors, the
thresholds, and the decision rule — was committed in a commit (`3fc9e4dd`) that contains
**zero results**. The predictors are computed **blind to the label** (the label comes from
a real Python round-trip the predictors never see).

> **Correction (2026-07-08, self-reported).** An earlier version of this section claimed
> "*this file, and the frozen harness,* are committed in a commit that contains zero
> results." That was **false**: the git history shows `packs_killtest.py` (the harness)
> first appears in the *results* commit (`60fcb6d1`), ~17 minutes after the pre-reg commit —
> the *design* was frozen before results, the *harness* was not. The verdict is unaffected
> (the labels are real execution round-trips, the predictors are blind, and the likelihood
> ratio recomputes from `result.json`), but the freeze ceremony was weaker than stated.
> **Go-forward rule (now standard):** commit the harness *in* the pre-reg commit alongside
> the design, and OTS-stamp that commit for an external timestamp — a commitment device,
> not a narrative genre.

Provenance line (verification discipline): the conventions, values, and the
foreclosure claims below are pinned to files at `origin/main` HEAD `2d238122`
(worktree `research/packs-execution-killtest`), not to memory or a rendered page.

---

## 0. What this test is NOT (scope, so the reader can't inflate it)

The composition-level **cohomology fee** as a predictor of execution failure is
**already foreclosed on the real corpus** — this test does not re-open it:

- `papers/coherence-cliff/dissociation_pre_registration.md` set up "does the schema
  fee beat a cheap bounded-local baseline on real compositions"; it halted at its
  Stage-0 gate with **Outcome-4 BOUNDED** (`bulla/calibration/results/twisted_frustration_bounded_check.json` @ `b2634f4`): all 289 cyclic real
  compositions are triangle-generated (girth-3) ⇒ the fee is provably depth-3-local
  ⇒ a cheap depth-3 baseline recovers 100% of the obstruction **by construction**.
- `bulla/calibration/results/convention_distance_collapse.md`: the fee **equals** the
  schema Hamming convention-distance analytically (corr = 1 on the tested substrate).
- `bulla/calibration/execution_gap.py`: where real execution labels exist, `fee = 0`
  **misses 30/36 (83%)** of real round-trip breaches.

So "fee ≫ dumb baseline" (the argument's Outcome 1) is **off the table**. Because the
fee ≡ Hamming convention-distance, the honest object of study is not "the fee" but the
**convention-mismatch signal itself** (which bulla computes by *inference*, see §2).
This test adjudicates the argument's **Outcome 2 (bulla is honestly a convention-linter
— ship the humble product, drop the math) vs Outcome 3 (stop)**.

## 1. Binding question

> When a real convention mismatch would cause a real execution failure, does bulla's
> **inferred** convention signal detect it — with high enough precision/recall to be a
> shippable linter, **beating a dumb name+type-divergence baseline**, and catching what
> a JSON-Schema validator provably cannot?

Two arms, **reported separately, never blended**:

- **Arm M (mechanism, positive control WITH a baseline — the new part).** Given a
  convention break is present, do {bulla, dumb, jsonschema} detect it? Real values,
  real parsers, real execution labels. This is the first time the repo's positive
  controls get a competing baseline and a precision/recall/AUC number.
- **Arm P (prevalence gate) on the real corpus.** How often do convention-carrying
  shared-field pairs with a *buildable-oracle* divergence actually occur across the 38
  real MCP manifests? Gates generalization; reproduces/extends the predicate floor
  (`bulla/calibration/predicate_spike/`, 2.4%) through the inference+oracle lens.

## 2. The three predictors (fixed definitions, computed blind to the label)

For each case = (producer tool schema, consumer tool schema, a shared convention-carrying
field, and a real value the producer emits for it):

1. **bulla** (`bulla.infer.classifier.classify_tool_rich` on each tool, three signals:
   field-name regex + description keywords + JSON-Schema structure). Fires **positive**
   iff bulla infers a convention dimension on the shared field in *both* tools and either
   (a) the inferred dimension/pack differs, or (b) the emitted value violates the pack
   value-set the consumer's field is inferred to require. Confidence tier
   (`declared`/`inferred`/`unknown`) is recorded for the continuous/AUC view.
2. **dumb** (stripped baseline, no pack taxonomy, ~20 lines): fires **positive** iff the
   field name appears in both schemas AND the two schemas differ in (`type` OR `enum` OR
   `format`) for that field. This is "shared field, undeclared/divergent shape" — the
   signal available without bulla's packs.
3. **jsonschema** (the thing bulla claims to beat): validate the producer's **real emitted
   value** against the consumer's field schema with a strict validator. Fires **positive**
   iff validation fails. (Expected ≈ 0 on same-JSON-type divergences — a date string is a
   valid string; a currency alpha code is a valid string — which is the whole point.)

## 3. The oracle (the label — real execution, no model, no LLM, no Bernoulli)

Producer emits real value `v` under convention A; consumer strict-parses/deserializes
under convention B; **label = positive (real failure) iff** the round-trip **raises** OR
returns a value semantically `≠ v` (silent corruption). Three conventions, each an
unambiguous real Python execution:

| convention | encodings (A vs B) | real oracle | positive when |
|---|---|---|---|
| `date_format` | ISO-8601 `%Y-%m-%d` · US `%m/%d/%Y` · EU `%d/%m/%Y` · compact `%Y%m%d` | `datetime.strptime(v, fmt_B)` | raises, OR parses to an instant ≠ the intended date (e.g. `2024-01-02` read US↔EU) |
| `currency_code` (ISO-4217) | alpha-3 `"USD"` · numeric `"840"` | consumer maps `v` via the real ISO-4217 table (pack `known_values`) to the other rep | lookup/`int()` raises, OR maps to a different currency |
| `unit_scale` | meters↔feet · cents↔dollars | consumer interprets `v` under its unit; compare to true magnitude | `|consumer_value − true| > 1e-9` |

Values are **real** (ISO-4217 from `bulla/src/bulla/packs/seed/iso-4217.yaml` `known_values`;
a fixed deterministic list of real dates incl. ambiguous ones; fixed real magnitudes). No
randomness (`Math.random`/`Date.now` are unavailable and would break reproducibility anyway).

**Schema realism (anti "authored-easy" guard).** Each case's field name + description +
JSON-Schema shape is drawn from a **spectrum of realism**, and results are **stratified** by it:
- `rich`  — name + a description that names the standard ("ISO 4217 currency code");
- `medium`— name + a generic description ("currency code");
- `bare`  — name only, generic type (`"currency"`, `type: string`), no description.
This measures exactly **when inference works vs is blind** — the honest product boundary
(and the mechanism behind `execution_gap.py`'s 83% miss: bare/undeclared fields).

## 4. Trap arm (silent false-negatives — the honest boundary)

Cases whose convention is **not in bulla's packs** (e.g. a bespoke enum ordering, a
sort-direction convention absent from `base.yaml`) but which still **break at execution**.
bulla is *expected* to miss these; the **silent-FN rate** = fraction of real breaks bulla
does not flag. This number IS the product's stated blind spot (extends `execution_gap.py`).

## 5. Metrics + the pre-committed bars (fixed now, before results)

Per predictor, on Arm M (balanced ~50/50 break/no-break, target ≥ 40 cases/convention +
≥ 20 traps): **precision, recall, F1**; for bulla's confidence-tier ordering, **AUC**.

Decision tree (evaluated in order):

- **G-jsonschema:** jsonschema recall on same-type breaks is expected ≈ 0. If jsonschema
  recall ≥ 0.5, bulla adds little over standard validation → lean Outcome 3. (Record it.)
- **G-mechanism (bulla works at all):** bulla recall ≥ **0.80** AND precision ≥ **0.90** on
  the union of `rich`+`medium` cases. Fail ⇒ the detector is not shippable ⇒ **Outcome 3 (stop).**
- **G-baseline (packs earn their keep):** bulla F1 − dumb F1 on Arm M:
  - `≥ +0.10` ⇒ the pack taxonomy adds real signal over naive divergence.
  - `0 ≤ Δ < 0.10` ⇒ **ship the ~20-line dumb heuristic, drop the packs** (Outcome 2-minimal).
  - `< 0` ⇒ dumb wins ⇒ drop packs; reconsider whether anything ships.
- **G-prevalence (Arm P, does the disease occur in the wild):** share of real
  convention-carrying shared-field pairs across the 38 manifests with an oracle-confirmable
  divergence ≥ **5%** (continuity with the predicate floor's bar).

**Verdict mapping:**
- G-mechanism ✔ **and** G-baseline `≥ +0.10` **and** G-prevalence ✔
  → **Outcome 2: bulla is honestly a convention-linter.** Ship the humble product
    (inference + packs, drop the cohomology framing). The math stays a research asset.
- G-mechanism ✔ but G-prevalence ✘
  → **Outcome 3-external:** detector works, disease absent from our corpus → cannot
    establish demand internally → the honest move is a design partner's live traffic
    (now pitched from strength: "a detector that provably catches real breaks").
- G-mechanism ✔ but G-baseline `< +0.10`
  → **ship the dumb heuristic, retire the packs+cohomology as product.**
- G-mechanism ✘
  → **Outcome 3: stop.** Record the null loudly.

The bar for the sprint's own honesty: **whatever fires, it is recorded and honored** —
this is `proofs-not-memos`; the deliverable is the number on real executed data with a
baseline, not a plan.

## 6. Anti-degeneracy guards (the "100% triangle-generated" lesson)

- **Spot-check 10 random Arm-M cases by hand** (fixed seed-free selection = every k-th case
  by a deterministic index): confirm the oracle label is correct and the case is not
  trivially separable by construction. Recorded in RESULT.md.
- **Report both arms separately.** Arm M is mechanism (can the detector detect?); Arm P is
  prevalence (does it matter?). A working detector on an empty disease is Outcome-3, not a win.
- **NFKC-normalize** all field-name matching (the confusables class).
- No case is labeled by anything but the real `strptime`/`int`/table-lookup/arithmetic
  outcome. If an oracle cannot be built for a convention, that convention is **excluded and
  noted**, never labeled by hand.

## 7. Deliverables

`packs_killtest.py` (the harness — committed with the results, see the Correction above,
not frozen before them), `result.json` (per-case + aggregate), `RESULT.md`
(the number, the verdict per §5, the 10-case spot-check, the honest boundary from §4).
Committed after this file, in a separate commit.
