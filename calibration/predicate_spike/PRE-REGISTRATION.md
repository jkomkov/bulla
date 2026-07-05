# Predicate-spike pre-registration

**Recorded before the corpus is opened for the divergence count** (the house rule: pick the
falsifier and its unit first, so the result cannot be graded to taste). Committed as its own
commit on `research/predicate-spike`, ahead of any `predicate_floor.py` output.

## The bet

The certification tier of the predicate layer earns its existence only if agents are actually
exchanging **misaligned predicates** — the same classifier word meaning different things across a
seam. The coherence fee already catches *undeclared* predicates (a hidden dimension raises the
fee); the tier's new value is catching *misaligned-but-observable* ones, which the fee cannot see.

## The pre-registered falsifier (the FLOOR — binds here)

**Unit: compositions** (the census is pairwise, 703 real-schema MCP compositions; predicate-like
fields cluster by server domain, so "% of compositions" and "% of fields" diverge — the unit is
fixed to compositions before looking).

**Threshold: ≥ 5% of the 703 compositions (≈ 35)** must each contain **≥ 1 observable,
divergently-typed, shared predicate-like field** — else the certification tier is a *solution
without a disease* and the result enters the ledger as a first-class NEGATIVE (ship only the
banner + the merged reservation; do not build the tier).

### Definitions (fixed now)

- **Predicate-like field** — a field whose JSON-Schema type is `boolean`, or `string` with an
  `enum`, or `integer`/`number` (threshold-typed), **or** whose name matches a classifier pattern
  (`is_*`, `has_*`, `*_status`, `*_state`, `*_level`, `*_flag`, `*_type`, `*_priority`,
  `*_mode`, `*_role`, `*_tier`, or a literal in {urgent, priority, severity, risk, eligible,
  approved, active, enabled, verified, valid, category, tier, role, mode, status, level, state}).
  Matching is **NFKC-normalized, case-folded** (the confusables rule).
- **Shared** — the same normalized field name appears in an observable field of BOTH tools of a
  composition.
- **Divergently-typed** — the two tools declare that shared field with a different JSON-Schema
  `type`, a different `enum` set, or a different numeric range.

## The three probes (run AFTER this file is committed)

- **(a-floor)** the divergently-typed shared predicate-like count vs the bar above — the
  *detectable floor* of the disease. **The falsifier binds only here.**
- **(a-ceiling)** a hand-audit of ~20 *same-type* shared predicate-like fields, reading field
  descriptions for meaning divergence — the qualitative *ceiling*, i.e. what only partner traffic
  can measure (the type-invisible case). Reported as an estimate, not a number the falsifier uses.
- **(a-mutation)** mutation incidence from the registry manifests' git history.
  **DETERMINED NULL (recorded honestly, before the floor run):** `git log -- .../registry/manifests/`
  shows **one commit** — the manifests are a single snapshot with no field-level drift history. So
  the mutation axis is **not measurable on our internal corpus**; it is measurable only on a design
  partner's *live* traffic. This is stated as a null, not dressed up — and naming precisely what
  only live traffic reveals ("your certificates are voided by drift you can't see") is itself the
  recruitment case for shadow mode.

## Honesty rules for the report

- The floor count is the *floor*, not the disease; a passing floor is not yet evidence of the
  tier's core claim (the type-invisible case is the ceiling, only partner-measurable).
- The one-pager headline claims exactly what the 3 hand-built DisagreementWitnesses prove
  (item-level divergence for those three), never what the scan merely suggests.
- The detector labels every hit `candidate` and names its heuristic inline — a name-pattern +
  schema-divergence heuristic is not a certified finding.
