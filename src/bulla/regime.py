"""Bulla regime classification — well-formedness predicates for `coherence_fee`.

The codebase computes `coherence_fee = h1_obs − h1_internal = rank_internal − rank_obs`
in `bulla.diagnostic`. This formula is well-defined for any composition expressible
in `bulla.model`, but is only **interpretable as a fee** (non-negative scalar
representing latent obstruction the user pays to coordinate the seam) when
`rank_internal ≥ rank_obs`.

Sprint 8's regime audit (`bulla/calibration/scripts/sprint8_regime_audit.py`)
empirically established:

  * 0/967 real-MCP composition pairs have negative fee (curated + registry-pair).
  * 39.3% of *random-stress* compositions produced by the broad `bulla.model`
    API have negative fee.

So the "general-model" regime can produce ill-formed compositions, but the
"real-MCP" regime never does in practice. This module defines the predicates
that distinguish the regimes — for use in tests, doc generation, and (if the
project decides to add API validation) in user-facing warnings.

Module is a thin read-only oracle: no new mathematics, just packaging the
empirical regime boundaries from Sprint 8 in a form callers can query.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import Composition


@dataclass(frozen=True)
class RegimeReport:
    """Per-composition regime measurement and classification.

    Three layers, reflecting the Sprint 8/9 separation between
    **definition**, **measurement**, and **explanation**:

    - **Measurement** (rank-based): `is_well_formed_for_fee = (rank_internal >= rank_obs)`.
      This is the predicate that controls whether `coherence_fee` is non-negative.
      Equivalent to `fee_formula >= 0`. Useful as a runtime check.

    - **Explanation** (schema-shape): `has_projective_observables = (∀ t: observable_schema(t) ⊆ internal_state(t))`.
      This is the structural condition that *implies* well-formedness via the
      Sprint 9 schema-shape theorem: projective observables ⇒ rank_obs ≤ rank_internal.
      Useful as a static check on tool definitions, before any matrix is built.

    - **Regime category** (dominance flags): `has_internal_dominance`,
      `has_balanced_ranks`, `has_obs_dominance` partition the space by sign
      of `fee_formula`. Useful for reporting/classification.
    """
    rank_obs: int
    rank_internal: int
    fee_formula: int             # = rank_internal − rank_obs (matches diag.coherence_fee)
    is_all_hidden: bool          # every tool's observable_schema is empty
    is_all_observable: bool      # every tool's internal_state is empty
    has_internal_dominance: bool # rank_internal > rank_obs (fee > 0)
    has_balanced_ranks: bool     # rank_internal == rank_obs (fee == 0)
    has_obs_dominance: bool      # rank_obs > rank_internal (fee < 0; ill-formed)
    is_well_formed_for_fee: bool # rank_internal >= rank_obs (fee >= 0) — measured
    has_projective_observables: bool  # observable_schema ⊆ internal_state per tool — structural
    has_dfd_conservative: bool        # all seam dims have from_field == to_field — Sprint 11
    has_chp_conservative: bool        # each (tool, field) referenced ≤1 per direction — Sprint 11
    is_exact_regime_conservative: bool  # has_dfd_conservative AND has_chp_conservative


def classify(comp: Composition) -> RegimeReport:
    """Compute the regime classification of `comp` from its coboundary ranks.

    The returned report's `is_well_formed_for_fee` field is the predicate to
    consult when deciding whether to interpret `diag.coherence_fee` as a fee
    or as a signed obstruction imbalance.
    """
    delta_obs, _, _ = build_coboundary(
        list(comp.tools), list(comp.edges), use_internal=False
    )
    delta_internal, _, _ = build_coboundary(
        list(comp.tools), list(comp.edges), use_internal=True
    )
    rank_obs = matrix_rank(delta_obs)
    rank_internal = matrix_rank(delta_internal)
    fee = rank_internal - rank_obs

    is_all_hidden = all(len(t.observable_schema) == 0 for t in comp.tools)
    is_all_observable = all(len(t.internal_state) == 0 for t in comp.tools)
    has_int_dom = rank_internal > rank_obs
    has_balanced = rank_internal == rank_obs
    has_obs_dom = rank_obs > rank_internal
    is_well_formed = (rank_internal >= rank_obs)
    has_projective = all(
        set(t.observable_schema).issubset(set(t.internal_state))
        for t in comp.tools
    )
    # Sprint 11: forward to module-level conservative detectors so the
    # report includes DFD/CHP/exact-regime-conservative classifications
    # alongside the schema-shape and rank predicates.
    dfd_c = has_dfd_conservative(comp)
    chp_c = has_chp_conservative(comp)
    exact_c = dfd_c and chp_c

    return RegimeReport(
        rank_obs=rank_obs,
        rank_internal=rank_internal,
        fee_formula=fee,
        is_all_hidden=is_all_hidden,
        is_all_observable=is_all_observable,
        has_internal_dominance=has_int_dom,
        has_balanced_ranks=has_balanced,
        has_obs_dominance=has_obs_dom,
        is_well_formed_for_fee=is_well_formed,
        has_projective_observables=has_projective,
        has_dfd_conservative=dfd_c,
        has_chp_conservative=chp_c,
        is_exact_regime_conservative=exact_c,
    )


# ---- Convenience predicates (single-bool variants) ----

def is_all_hidden(comp: Composition) -> bool:
    """True iff every tool has empty `observable_schema`. The cycle family
    of Sprint 6 lives entirely in this regime; real MCP compositions never
    do (every real tool has at least one observable field)."""
    return all(len(t.observable_schema) == 0 for t in comp.tools)


def is_all_observable(comp: Composition) -> bool:
    """True iff every tool has empty `internal_state`. Compositions in
    this regime have rank_internal = 0 and produce fee ≤ 0."""
    return all(len(t.internal_state) == 0 for t in comp.tools)


def is_well_formed_for_fee(comp: Composition) -> bool:
    """True iff `rank_internal ≥ rank_obs`, i.e. the codebase's fee
    formula `fee = rank_internal − rank_obs` produces a non-negative
    value that can be interpreted as a coherence fee.

    This is the **measured** predicate. The **structural** predicate
    that explains why real-MCP compositions satisfy it is
    `has_projective_observables` (Sprint 9): if every tool's
    `observable_schema` is a subset of its `internal_state`, then
    `is_well_formed_for_fee` is automatic.

    Empirical scope (Sprint 8 regime audit):
      * Real MCP composition pairs (n = 967): 100% well-formed.
      * Curated bulla/compositions + bulla/audit (n = 14): 100% well-formed.
      * Cycle family (n = 70): 100% well-formed (all-hidden regime).
      * Sprint 7 random stress (disjoint partition): 60.7% well-formed.
      * Sprint 9 well-formed random (projective observables): 100%
        well-formed across 1000 trials.

    For any composition that fails this predicate, `diag.coherence_fee`
    will be negative and should NOT be interpreted as a fee. It is a
    signed obstruction imbalance from the formula
    `h1_obs − h1_internal`. The structural fix (rather than runtime
    clamping) is to repair tool definitions so `has_projective_observables`
    holds.
    """
    return classify(comp).is_well_formed_for_fee


def has_dfd_conservative(comp: Composition) -> bool:
    """**Conservative sufficient condition for Disjoint Field Decomposition (DFD).**

    Sprint 11 (paper §3.5 — Definition 3.10's operational sufficient conditions):

      DFD says the latent presheaf decomposes as a direct sum over
      independent field-types, each acting on disjoint contexts.

    Operationally on `bulla.model.Composition`, the cleanest sufficient
    condition is:

      Every seam dimension's `from_field` equals its `to_field` —
      i.e., each `SemanticDimension(name, from_field, to_field)` ties
      a single field-name to itself across the seam, never crossing
      field-types.

    When this holds, the witness Gram K(G) is block-diagonal by
    field-name, and the latent presheaf decomposes accordingly.

    This is **sufficient but not necessary**: a composition can satisfy
    abstract DFD without all seam dimensions being type-preserving (e.g.,
    via more complex bijection structure). The conservative test is the
    practical one for Bulla's current model.

    Cross-corpus empirical scope (Sprint 11 audit):
      * Cycle family: 100% DFD-conservative (every dim is `f_match(f → f)`).
      * Curated bulla/compositions: ~all DFD-conservative (real MCP edges
        link compatible field-types).
      * Sprint 7 random stress: most violate (random `from_field`/`to_field`
        pairings).
    """
    for edge in comp.edges:
        for dim in edge.dimensions:
            if dim.from_field is not None and dim.to_field is not None:
                if dim.from_field != dim.to_field:
                    return False
    return True


def has_chp_conservative(comp: Composition) -> bool:
    """**Conservative sufficient condition for Class-Homogeneous Partition (CHP).**

    Sprint 11 (paper §3.5):

      CHP says within each field-type, the convention dimension at every
      context is one-dimensional.

    Operationally, the cleanest sufficient condition on
    `bulla.model.Composition` is:

      Each `(tool, field)` pair is referenced by at most one seam
      dimension as the `from`-side, and at most one as the `to`-side.

    When this holds, the convention at each (tool, field) is at most
    one-dimensional in each direction; the latent cochain complex then
    has the rank-counting decomposition without correction terms.

    This is **sufficient but not necessary**: a composition can satisfy
    abstract CHP via richer multi-dimensional structure that still
    decomposes one-dimensionally per field-type. The conservative test
    flags the most common multi-reference violation.
    """
    from_count: dict[tuple[str, str], int] = {}
    to_count: dict[tuple[str, str], int] = {}
    for edge in comp.edges:
        for dim in edge.dimensions:
            if dim.from_field is not None:
                key = (edge.from_tool, dim.from_field)
                from_count[key] = from_count.get(key, 0) + 1
                if from_count[key] > 1:
                    return False
            if dim.to_field is not None:
                key = (edge.to_tool, dim.to_field)
                to_count[key] = to_count.get(key, 0) + 1
                if to_count[key] > 1:
                    return False
    return True


def is_exact_regime_conservative(comp: Composition) -> bool:
    """Conservative sufficient condition for the exact regime
    (paper §3.5 Theorem 2 of [Komkov 2026 Hierarchical Decomposition]):

      DFD + CHP ⇒ exact regime.

    Returns `has_dfd_conservative(comp) AND has_chp_conservative(comp)`.

    Compositions satisfying this predicate get the strongest matroid
    guarantees: `weighted_greedy_repair` and `minimum_disclosure_set`
    cardinalities agree, and the bases coincide as disclosure sets.
    """
    return has_dfd_conservative(comp) and has_chp_conservative(comp)


def has_projective_observables(comp: Composition) -> bool:
    """True iff every tool's `observable_schema` is a subset of its
    `internal_state`.

    This is the **structural** schema-shape invariant identified in
    Sprint 9. It is the condition that *explains* why real-MCP
    compositions never produce negative fee:

      Theorem (Sprint 9 schema-shape implication, proof in
      `papers/composition-doctrine/sprint9_schema_shape_invariant.md`).
      If `has_projective_observables(comp)` then
      `is_well_formed_for_fee(comp)` (equivalently `coherence_fee >= 0`).

    The converse does NOT hold: some compositions satisfy
    `is_well_formed_for_fee` without `has_projective_observables`
    (e.g., disjoint partitions where the rank inequality happens to
    hold by coincidence — but this is rare in the Sprint 7 random
    stress: 0.2% projective vs 60.7% well-formed).

    Use this predicate as a STATIC check on tool definitions, before
    any matrix is built. It tells you whether you're inside the
    real-MCP regime where `coherence_fee` is well-defined as a fee.

    Empirical scope (Sprint 9):
      * Real MCP corpora (n = 967): 100% projective.
      * Cycle family (n = 70): 100% projective (vacuous: empty obs).
      * Sprint 7 random stress (disjoint partition): 0.2% projective.
      * Sprint 9 well-formed random: 100% projective by construction.
    """
    return all(
        set(t.observable_schema).issubset(set(t.internal_state))
        for t in comp.tools
    )


# ---- Sprint 10: validation surface ----

@dataclass(frozen=True)
class RegimeViolation:
    """A specific regime-invariant violation discovered in a composition.

    Currently the only violation kind is `projective_observables`: a
    tool whose `observable_schema` is not a subset of its
    `internal_state`. Future violation kinds (e.g., DFD/CHP) can be
    added as additional `kind` values without breaking callers.
    """
    kind: str               # e.g. "projective_observables"
    tool_name: str
    fields: tuple[str, ...] # specific fields violating the invariant
    description: str        # human-readable explanation


def validate_regime(comp: Composition) -> list[RegimeViolation]:
    """Return a list of **schema-shape** violations found in `comp`. Empty
    list means the composition is **schema-shape valid** (i.e. projective
    observables hold for every tool — Sprint 9).

    Sprint 12 wording fix: this function name is broader than what it
    actually checks. The function only validates the projective-observables
    predicate (the structural condition that implies fee non-negativity).
    It does NOT validate DFD-conservative, CHP-conservative, exact-
    regime-conservative, or any other lattice predicate added in Sprint 11.

    For those, use `bulla.regime.classify(comp)` which returns the full
    `RegimeReport`, or the convenience predicates
    (`has_dfd_conservative` etc.) directly.

    The function is kept named `validate_regime` for backward
    compatibility with the Sprint 10 API; future sprints may rename it
    to `validate_schema_shape` or extend it to validate additional
    predicates (returning `RegimeViolation` instances with new `kind`
    values — non-breaking).

    Use this from CLI handlers, tests, or any callsite that wants to
    surface tooling-side schema-shape violations BEFORE interpreting
    `diag.coherence_fee` as a fee.

    Currently checked (single invariant):
      * `projective_observables` per tool: `observable_schema ⊆ internal_state`.
    """
    violations: list[RegimeViolation] = []
    for t in comp.tools:
        obs = set(t.observable_schema)
        intl = set(t.internal_state)
        not_in_internal = obs - intl
        if not_in_internal:
            violations.append(RegimeViolation(
                kind="projective_observables",
                tool_name=t.name,
                fields=tuple(sorted(not_in_internal)),
                description=(
                    f"tool `{t.name}` exposes observable field(s) "
                    f"{sorted(not_in_internal)} that are not in its "
                    f"internal_state. By the schema-shape invariant "
                    f"(Sprint 9), this can produce negative coherence_fee "
                    f"and break the fee interpretation. Add the missing "
                    f"fields to internal_state, or remove them from "
                    f"observable_schema."
                ),
            ))
    return violations


def format_regime_warning(
    comp: Composition,
    source_path: str | None = None,
) -> str | None:
    """Format a one-line warning string for `comp`'s regime violations,
    or None if the composition is **schema-shape valid** (passes
    `validate_regime`, which currently only checks projective observables —
    NOT the full lattice). Sprint 12 wording: avoid "regime-valid"
    here too — the function only validates one predicate of the lattice.

    `source_path` is the file path the composition was loaded from, when
    known. The "next step" line of the warning suggests
    `bulla regime <source_path>` for an interactive classification —
    using the actual path the user typed, NOT `comp.name` (which is the
    YAML composition name, not a path).

    Sprint 12 fix: previously suggested `bulla regime {comp.name}`,
    which would fail because `comp.name` is the YAML `name:` field, not
    a filesystem path. The CLI helper now passes the path explicitly.

    Suitable for CLI output. Example output (with source_path):

        "WARNING: composition `pipeline.yaml` has 2 schema-shape violation(s).
         Run `bulla regime path/to/pipeline.yaml` for details. The
         coherence_fee value below is a SIGNED OBSTRUCTION IMBALANCE,
         not a fee."
    """
    violations = validate_regime(comp)
    if not violations:
        return None
    fee = classify(comp).fee_formula
    fee_label = (
        "SIGNED OBSTRUCTION IMBALANCE (NOT a fee — see schema-shape warning)"
        if fee < 0 else "coherence_fee"
    )
    n = len(violations)
    msg_lines = [
        f"WARNING: composition `{comp.name}` has {n} schema-shape "
        f"violation(s) (projective_observables predicate fails).",
    ]
    for v in violations[:5]:
        msg_lines.append(f"  - tool `{v.tool_name}`: observable fields "
                         f"{list(v.fields)} not in internal_state.")
    if n > 5:
        msg_lines.append(f"  - ... and {n - 5} more.")
    # Sprint 12 review fix: shell-quote the path so the suggested
    # command stays valid for paths containing spaces or shell
    # metacharacters.
    next_step = (
        f"`bulla regime {shlex.quote(source_path)}`" if source_path
        else "`bulla regime <path-to-this-yaml>`"
    )
    msg_lines.append(
        f"  Reading `{fee_label}` below at your own risk. Run "
        f"{next_step} for a per-predicate classification, or see "
        f"`bulla/docs/REGIME.md` for what the regime warning means and "
        f"how to fix the tool definition."
    )
    return "\n".join(msg_lines)
