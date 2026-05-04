"""Sprint 6 Phase E: weighted_greedy_repair vs minimum_disclosure_set
characterized **on the all-hidden cycle family**.

Bulla has two "compute a disclosure set" functions that look similar
but operate at different levels:

    weighted_greedy_repair(K, hidden_basis, costs)   -- bulla.witness_geometry
        Returns a min-cost basis of the column matroid M(K) via Edmonds 1971
        greedy. Output cardinality = rank(K). [Structural invariant.]

    minimum_disclosure_set(comp)                     -- bulla.diagnostic
        Greedily augments the observable coboundary δ_obs with hidden columns
        from δ_full until ranks match. Output cardinality is
        regime-dependent.

This file pins down the **all-hidden regime** characterization:

  1. In the all-hidden regime (every tool's observable_schema is empty,
     as in the cycle family), the codebase's fee formula
     `fee = h1_obs − h1_full = rank_internal − 0 = rank_internal ≥ 0`,
     and the construction K = H^T H gives rank(K) = rank_internal = fee.
     So |greedy| == |min_disc| == fee in this regime.

  2. Choice of basis (which fields each picks) can differ — this is normal
     matroid behavior. With cost vector specified, weighted_greedy_repair
     can pick any minimum-cost basis.

  3. **Sprint 7 correction**: the original Sprint 6 wording generalized
     this agreement to "the current Bulla model." That generalization
     was wrong — see `test_disclosure_semantics_random.py` for the
     random-composition characterization showing 91.6% of general
     compositions have |greedy| != |min_disc|.

This file is *verification, not discovery* — it pins down the operational
relationship on the cycle family. The general-regime characterization
lives in `test_disclosure_semantics_random.py`; the documentation in
`bulla/docs/MATROID-STRUCTURE.md` was updated in Sprint 7 to reflect both.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))

from bulla.coboundary import matrix_rank
from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness_geometry import weighted_greedy_repair, witness_gram


# ---- Helpers (Sprint 6 cycle-family construction) ----

def _build_disjoint_cycles(k: int, m: int) -> Composition:
    """A_{k,m}: k disjoint m-cycles on km tools, all-hidden field 'f'."""
    n = k * m
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(n)
    )
    edges = []
    for c in range(k):
        for i in range(m):
            u = c * m + i
            v = c * m + (i + 1) % m
            edges.append(Edge(
                from_tool=f"t{u}", to_tool=f"t{v}",
                dimensions=(SemanticDimension(name="f_match", from_field="f", to_field="f"),),
            ))
    return Composition(name=f"A_{k}_{m}", tools=tools, edges=tuple(edges))


def _build_single_cycle(k: int, m: int) -> Composition:
    """B_{k,m}: one km-cycle on km tools, all-hidden field 'f'."""
    n = k * m
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(n)
    )
    edges = tuple(
        Edge(
            from_tool=f"t{i}", to_tool=f"t{(i + 1) % n}",
            dimensions=(SemanticDimension(name="f_match", from_field="f", to_field="f"),),
        )
        for i in range(n)
    )
    return Composition(name=f"B_{k}_{m}", tools=tools, edges=edges)


# ---- Test 1: cardinality always agrees in the current model ----

@pytest.mark.parametrize("k,m,expected_fee", [
    (2, 4, 6),
    (2, 6, 10),
    (3, 6, 15),
    (4, 8, 28),
    (5, 10, 45),
    (6, 4, 18),
])
def test_cardinality_agreement_disjoint_cycles(k, m, expected_fee):
    """For each (k, m), greedy basis cardinality and min-disclosure cardinality
    both equal fee. This is the load-bearing agreement guarantee."""
    comp = _build_disjoint_cycles(k, m)
    diag = diagnose(comp)
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))

    assert diag.coherence_fee == expected_fee, \
        f"k={k} m={m}: diagnose fee {diag.coherence_fee} != expected {expected_fee}"
    assert matrix_rank(K) == expected_fee, \
        f"k={k} m={m}: rank(K) {matrix_rank(K)} != fee {expected_fee}"

    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)

    assert len(greedy) == expected_fee, \
        f"k={k} m={m}: |greedy| {len(greedy)} != fee {expected_fee}"
    assert len(min_disc) == expected_fee, \
        f"k={k} m={m}: |min_disc| {len(min_disc)} != fee {expected_fee}"


@pytest.mark.parametrize("k,m,expected_fee", [
    (2, 4, 7),
    (3, 6, 17),
    (5, 10, 49),
])
def test_cardinality_agreement_single_cycle(k, m, expected_fee):
    """Same agreement claim on the single-cycle B family."""
    comp = _build_single_cycle(k, m)
    diag = diagnose(comp)
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))

    assert diag.coherence_fee == expected_fee
    assert matrix_rank(K) == expected_fee

    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)

    assert len(greedy) == expected_fee
    assert len(min_disc) == expected_fee


# ---- Test 2: bases (sets of fields) can differ under different orderings ----

def test_bases_can_differ_under_reversed_cost():
    """Within the same composition, weighted_greedy_repair with default
    ordering and with reversed costs picks different sets of fields,
    both of cardinality fee. This is normal matroid behavior."""
    comp = _build_disjoint_cycles(2, 4)  # Sprint 5 toy, fee = 6
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))

    basis_default = weighted_greedy_repair(K, hidden_basis)

    n = len(hidden_basis)
    costs_reversed = {f: Fraction(n - i) for i, f in enumerate(hidden_basis)}
    basis_reversed = weighted_greedy_repair(K, hidden_basis, costs_reversed)

    # Both should be cardinality 6 (= fee)
    assert len(basis_default) == 6
    assert len(basis_reversed) == 6

    # The two sets are different (otherwise the cost ordering didn't matter)
    assert set(basis_default) != set(basis_reversed), \
        "expected different bases under reversed cost ordering"


def test_default_greedy_matches_min_disclosure_on_uniform_costs():
    """Empirical observation: when both algorithms use the natural index
    ordering of `hidden_basis`, they produce the same basis on cycle
    families. This is not a theorem (greedy is sensitive to tiebreaking),
    but it documents the codebase's current default behavior."""
    comp = _build_disjoint_cycles(2, 4)
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)
    assert set(greedy) == set(min_disc), \
        "default greedy and min_disclosure_set disagreed on uniform-cost cycle family"


# ---- Test 3: rank(K) = fee invariant (the "exact-regime agreement condition") ----

@pytest.mark.parametrize("comp_factory", [
    lambda: _build_disjoint_cycles(2, 4),
    lambda: _build_disjoint_cycles(3, 6),
    lambda: _build_single_cycle(4, 8),
    lambda: _build_disjoint_cycles(6, 10),
])
def test_rank_K_equals_fee_in_all_hidden_regime(comp_factory):
    """In the **all-hidden regime** (every tool's `observable_schema` is
    empty, as in the cycle family), the codebase's fee formula reduces
    to `fee = rank_internal − 0 = rank_internal ≥ 0`, and `K = H^T H`
    has `rank(K) = rank_internal = fee`. So `rank(K) == diag.coherence_fee`
    holds in this regime.

    Sprint 7 caveat: this identity is **NOT** a general invariant of
    `bulla.model`. In compositions with mixed observable/internal field
    structure, the formula `fee = h1_obs − h1_full = rank_internal − rank_obs`
    can be negative (when observable side has more obstruction), and
    the three measures `diag.coherence_fee`, `rank(K)`, and
    `|minimum_disclosure_set|` diverge.

    See `bulla/tests/test_disclosure_semantics_random.py` for the
    random-composition characterization (91.6% of random compositions
    show `|greedy| ≠ |min_disc|`; 40.8% have negative fee).
    """
    comp = comp_factory()
    diag = diagnose(comp)
    K, _ = witness_gram(list(comp.tools), list(comp.edges))
    rank_K = matrix_rank(K)
    assert rank_K == diag.coherence_fee, \
        f"rank(K) = {rank_K} != fee = {diag.coherence_fee}"


# ---- Test 4: zero-fee compositions trivially agree ----

def test_zero_fee_no_disclosure_needed():
    """When fee = 0, both functions return empty sets (no disclosure
    required). Edge case."""
    # Single tool with no edges: trivially zero-fee
    tools = (ToolSpec(name="t0", internal_state=("f",), observable_schema=()),)
    edges = ()
    comp = Composition(name="trivial", tools=tools, edges=edges)
    diag = diagnose(comp)
    assert diag.coherence_fee == 0

    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)

    assert greedy == []
    assert min_disc == []


# ---- Test 5: small documented divergence — cost-driven basis swap ----

def test_basis_choice_responds_to_cost_with_documented_swap():
    """A concrete case where 'expensive' fields are forced out and
    'cheap' fields are forced in — even though both bases are valid.
    This is the operational difference: cost-aware greedy vs cost-blind
    min_disclosure_set."""
    comp = _build_disjoint_cycles(2, 4)
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))

    # Make one specific field very expensive (cost 1000), all others cheap (cost 1)
    expensive_field = hidden_basis[0]  # ('t0', 'f')
    costs = {f: (Fraction(1000) if f == expensive_field else Fraction(1))
             for f in hidden_basis}

    greedy_cost_aware = weighted_greedy_repair(K, hidden_basis, costs)
    greedy_default = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)

    # All three same cardinality (= fee = 6)
    assert len(greedy_cost_aware) == len(greedy_default) == len(min_disc) == 6

    # Cost-aware greedy excludes the expensive field if possible
    if len(hidden_basis) > 6:  # there are more fields than basis size — exclusion is possible
        assert expensive_field not in greedy_cost_aware, \
            "expected cost-aware greedy to exclude the expensive field"

    # The default greedy and min_disc may include or exclude the expensive field
    # (no claim made — depends on iteration order).
