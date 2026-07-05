# Predicate-spike result — a pre-registered NEGATIVE

**The falsifier fired.** Per `PRE-REGISTRATION.md` (committed before the corpus was opened), the
floor probe measures the *detectable* misalignment floor and the falsifier binds there.

```
corpus:  38 real-schema servers → 703 compositions
FLOOR:   17 compositions (2.4%) with ≥1 observable, divergently-typed, shared predicate-like field
BAR:     ≥ 5% (35)  →  FAIL  →  ledger NEGATIVE
```

## What the negative means (stated precisely)

1. **The detectable-static floor is below the bar, and softer than 2.4% on inspection.** The
   sharpest "divergences" are generic name collisions, not predicate misalignments: `type`
   (search-mode `auto/fast` in one server vs an object type vs an alert enum `DISRUPTION/MAINTENANCE`
   vs a log-level enum in others), `limit`/`page` (pagination, `number` vs `integer`). These are
   *different predicates that share a name*, not *the same predicate meaning different things*. The
   genuine misalignment signal detectable by static schema analysis is therefore even smaller.

2. **Do not build the certification tier on static evidence.** This is the pre-registered
   consequence: a tier whose premise (rampant, detectable predicate misalignment) is not present in
   the corpus is a solution without a (measurable-here) disease. Ship only the banner (#117) and the
   merged `kind` reservation (#120); the tier, its `bulla predicate scan` productization, and the
   recruitment one-pager are **not built**.

3. **What the negative does NOT falsify — and why it sharpens the external-contact thesis.** The
   floor is the *type-divergent* subset. The disease the tier actually targets is the **type-invisible
   ceiling** — two servers both declaring `urgent: boolean`, identical types, different meanings —
   which no static schema check can see (36 same-type shared predicate-like instances exist here as
   ceiling fodder, but meaning divergence among them is not machine-measurable). And the **mutation
   axis** is null internally (the registry manifests are a single-snapshot commit — no drift history).
   Both are, by construction, measurable **only on a design partner's live traffic**. So the honest
   conclusion is stronger than "no demand": *we cannot even establish the tier's premise from our own
   static corpus; only live partner traffic can.* The pull-gate is not just for building the tier —
   it is now required to confirm the disease exists at all.

## Disposition

- **Banner #117** and **`kind` reservation #120** stand.
- **Certification tier / DSL / SMT / negotiation verbs / `bulla predicate scan` product / recruitment
  one-pager** — NOT built (pre-registered negative honored).
- This file + `predicate_floor.py` + `floor_result.json` are the committed record: a first-class
  negative, and the empirical backing for the plan's existing pull-gate.
- Re-open the tier only when a design partner's live traffic exhibits type-invisible misalignment or
  measurable drift — the exact thing the static corpus cannot show.

*The pre-registered floor was picked before the corpus was opened; 2.4% < 5% is reported as it
landed. Moving the bar after the fact would be the one thing the whole method exists to prevent.*
