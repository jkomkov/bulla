# Scope & falsifications — what bulla does *not* do

bulla's brand is *recomputable honesty*: a verdict you can re-derive rather than
take on trust. A tool that asks to be trusted about verification owes the same
standard to its own claims. So this page ships the negative results — including the
ones that retired a framing bulla used to lead with.

## Withdrawn: Glyph website deployment receipt automation (2026-07-27)

**Status:** withdrawn. Receipt generation, public release, and attestation are
`BLOCKED_UNIMPLEMENTED`. Binding to deployed output is `NOT_COMPUTED`.

Glyph previously contained merged automation that could mint a
`website.deploy` ActionReceipt after a GitHub/Vercel deployment-status event.
The receipt could pass its own digest or signature checks. Those checks did not
establish the central deployment claim.

### What failed

- The original receipt recorded the digest of a CI-local rebuild as
  `build_digest`. It did not hash the bytes deployed by Vercel or the bytes
  served at the reported URL.
- An HTTP response or SSO redirect established endpoint behavior only. It did
  not bind the reported URL to the commit or to the locally rebuilt bytes.
- Signed and publishing revisions supplied `GLYPH_DEPLOY_KEY` and
  `contents: write` authority to a reusable workflow selected from the deployed,
  event-named commit. That created a path for event-selected code to access the
  signing capability and release authority. The repository record does **not**
  establish that the secret was exfiltrated; it establishes that the isolation
  boundary was unsound.
- Later revisions authenticated the GitHub deployment metadata, removed the
  direct-response overclaim, removed signing material and publication authority,
  and labeled the candidate digest-only. One defect remained: the deployed
  commit still selected the verifier and minter that interpreted the event.

Receipt integrity was therefore compatible with a false inference: an intact
receipt could prove what the selected workflow recorded without proving the
identity of the deployed or served artifact.

### Resolution

The event-triggered generator, signer, publisher, and current public receipt
claim were removed. Current repository gates reject the reviewed direct and ordinary
constructions that could restore that path. Those static gates do not prove
absolute absence across encoded, indirect, external, or unrecognized
constructions.

A future deployment receipt requires a trusted release path that is not
selected by the deployed commit, a provider- or artifact-derived deployed-byte
manifest, exact served-output reconciliation, isolated signing authority, and
non-clobbering retained evidence. Until those mechanisms exist, no
`website.deploy` candidate or verification rung is produced.

### Historical repository artifacts

The GitHub release assets produced before withdrawal remain byte-for-byte
unchanged as historical evidence. They are not current deployment evidence.
Each release title and body is marked `WITHDRAWN` and links to this record.
The repository and its release pages are access-controlled; they are not the
public disclosure surface. The corresponding public correction is
[`glyphstandard.com/status/deployment-receipt-withdrawal`](https://glyphstandard.com/status/deployment-receipt-withdrawal).
[`withdrawn-deployment-receipts.json`](https://glyphstandard.com/status/withdrawn-deployment-receipts.json)
enumerates every matching release observed on 2026-07-27, its original mutable
metadata hashes, and the retained asset digest. The registry classifies the
asset policy as `PRESERVED_UNMODIFIED_AS_HISTORICAL_EVIDENCE`.

The complete counterexample history, correction, tests, and residual limits are
recorded under
[`CP-B4-DEPLOY-13`](spec/control-plane-alpha/reviews/review-ledger.json).
The blocked-state guard is
[`glyph/scripts/check-deployment-receipt-blocked.mjs`](https://github.com/jkomkov/res-agentica/blob/main/glyph/scripts/check-deployment-receipt-blocked.mjs).
The audit trail is the
[unattended workflow](https://github.com/jkomkov/res-agentica/commit/41aa4e5eb69b87929550d622cf7f667d6df49408),
[deployment-evidence correction](https://github.com/jkomkov/res-agentica/commit/71e79a3653428dd42371b09f33d8f97932180afc),
[digest-only isolation](https://github.com/jkomkov/res-agentica/commit/c09fed3cf738fc6faf494da1e983744a7d8bf2db),
and
[withdrawal in commit `9636a67a`](https://github.com/jkomkov/res-agentica/commit/9636a67a4f56409bf4e17ef06277b177a77c2b96).

## Withdrawn: the coherence fee as an execution-failure predictor

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
