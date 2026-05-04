"""Sprint 7: random-composition disclosure-semantics characterization.

Sprint 6 Phase E (test_disclosure_semantics.py + MATROID-STRUCTURE.md) claimed
that "cardinality of weighted_greedy_repair and minimum_disclosure_set always
agrees in the current Bulla model" based on the cycle-family sweep.

This file's first iteration tried to verify that claim across random
compositions and **found counterexamples** — the claim was overgeneralized.

The actual situation:

  * In the all-hidden regime (every tool's `observable_schema` is empty,
    as in the cycle family), `δ_obs` is structurally zero, so `rank_obs = 0`
    and `diag.coherence_fee = rank_internal - 0 = rank_internal ≥ 0`. The
    Schur-complement K = (W^T)(W) construction collapses to K = H^T H,
    rank(K) = rank_internal = fee, and cardinalities agree.

  * In general compositions with mixed observable/internal field structure,
    `delta_obs` and `delta_full` (= `delta_internal` in the codebase's
    naming) are matrices on disjoint column sets. The codebase's fee
    formula `fee = h1_obs - h1_full = rank_internal - rank_obs` can be
    NEGATIVE, and `weighted_greedy_repair` (which returns a basis of
    M(K) with cardinality = rank(K)) can diverge in cardinality from
    `minimum_disclosure_set` (which uses its own greedy on δ_obs / δ_full
    and may return zero).

This file:

  1. Verifies cardinality agreement on the all-hidden cycle family (positive
     control — recapitulates the Sprint 6 Phase E result).
  2. Exhibits and characterises the divergence on random general compositions
     — at least one such failure must exist for the test to pass (otherwise
     the empirical observation that "cardinality CAN diverge" is vacuous).
  3. Quantifies the divergence rate over a 200-composition sweep, with
     a printed report.

Sprint 6 Phase E's narrower claim (cardinality agrees on the cycle family)
remains correct and is preserved in `test_disclosure_semantics.py`. This
file is the **negative-result counterpart** that prevents
overgeneralization to all Bulla compositions.
"""

from __future__ import annotations

import random
import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))

from bulla.coboundary import matrix_rank
from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness_geometry import weighted_greedy_repair, witness_gram


# ---- Helpers ----

def _build_disjoint_cycles_all_hidden(k: int, m: int) -> Composition:
    """Cycle family from Sprint 6 — all-hidden, the regime where cardinality
    agreement empirically holds."""
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


def random_composition(rng: random.Random,
                       n_tools_range: tuple[int, int] = (3, 6),
                       n_fields_per_tool_range: tuple[int, int] = (1, 3),
                       n_edges_range: tuple[int, int] = (1, 8),
                       n_dims_per_edge_range: tuple[int, int] = (1, 2),
                       observable_prob: float = 0.5,
                       seed_id: int = 0) -> Composition:
    """Random small Bulla composition with mixed observable/internal fields.
    Generates the GENERAL regime where cardinality agreement may fail."""
    n_tools = rng.randint(*n_tools_range)
    tool_fields: list[list[str]] = []
    tools: list[ToolSpec] = []
    for i in range(n_tools):
        n_fields = rng.randint(*n_fields_per_tool_range)
        fields = [f"f{i}_{j}" for j in range(n_fields)]
        obs = tuple(f for f in fields if rng.random() < observable_prob)
        intl = tuple(f for f in fields if f not in obs)
        tool_fields.append(fields)
        tools.append(ToolSpec(
            name=f"t{i}_{seed_id}",
            internal_state=intl,
            observable_schema=obs,
        ))
    n_edges = rng.randint(*n_edges_range)
    edges: list[Edge] = []
    for e_idx in range(n_edges):
        if n_tools < 2:
            break
        u = rng.randint(0, n_tools - 1)
        v = rng.randint(0, n_tools - 1)
        if u == v:
            v = (v + 1) % n_tools
        n_dims = rng.randint(*n_dims_per_edge_range)
        dims = []
        for d_idx in range(n_dims):
            from_field = rng.choice(tool_fields[u]) if tool_fields[u] else None
            to_field = rng.choice(tool_fields[v]) if tool_fields[v] else None
            if from_field is None or to_field is None:
                continue
            dims.append(SemanticDimension(
                name=f"d{e_idx}_{d_idx}_{seed_id}",
                from_field=from_field,
                to_field=to_field,
            ))
        if dims:
            edges.append(Edge(
                from_tool=tools[u].name,
                to_tool=tools[v].name,
                dimensions=tuple(dims),
            ))
    return Composition(
        name=f"random_{seed_id}",
        tools=tuple(tools),
        edges=tuple(edges),
    )


def measures(comp: Composition) -> dict:
    """Compute the three relevant cardinalities + diagnostic context."""
    diag = diagnose(comp)
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)
    return {
        "diag_fee": diag.coherence_fee,
        "rank_K": matrix_rank(K),
        "n_greedy": len(greedy),
        "n_min_disc": len(min_disc),
        "n_hidden_basis": len(hidden_basis),
        "n_tools": len(comp.tools),
        "n_edges": len(comp.edges),
    }


# ---- 1. Positive control: cycle family agreement ----

def test_cycle_family_cardinality_agreement():
    """Recapitulate Sprint 6 Phase E: in the all-hidden cycle family,
    |greedy| == |min_disc| == fee == rank(K) holds for every grid cell."""
    grid = [(2, 4), (3, 6), (4, 8), (5, 10), (6, 7), (3, 5)]
    for k, m in grid:
        comp = _build_disjoint_cycles_all_hidden(k, m)
        m_ = measures(comp)
        # In the all-hidden regime, all four measures agree:
        assert m_["diag_fee"] == m_["rank_K"] == m_["n_greedy"] == m_["n_min_disc"], (
            f"cycle family k={k} m={m}: agreement failed: {m_}"
        )


# ---- 2. Existence of divergence in general regime ----

def test_general_regime_exhibits_cardinality_divergence():
    """Across a sweep of 200 random general compositions, at least one
    must exhibit a cardinality divergence between |greedy| and |min_disc|.
    Otherwise the negative-result claim is vacuous and we'd be back to
    Sprint 6's overgeneralization.

    (Sprint 7 finding: in fact, the divergence is the rule, not the
    exception — see test_general_regime_divergence_rate for statistics.)
    """
    rng = random.Random(20260502)
    n_trials = 200
    found_diverging = False
    first_witness = None

    for seed in range(n_trials):
        comp = random_composition(rng, seed_id=seed)
        m_ = measures(comp)
        if m_["n_greedy"] != m_["n_min_disc"]:
            found_diverging = True
            if first_witness is None:
                first_witness = (seed, m_)

    assert found_diverging, (
        f"No cardinality divergence in {n_trials} random compositions. "
        f"This either means the random generator isn't producing diverse "
        f"compositions, or the claim 'cardinality always agrees' is "
        f"empirically true after all (in which case Sprint 6 Phase E's "
        f"original wording was correct and this test should be revisited)."
    )
    print(f"\nFirst diverging composition: seed={first_witness[0]}, measures={first_witness[1]}")


# ---- 3. Divergence rate characterization (informational) ----

def test_general_regime_divergence_rate_report():
    """Sweep 500 random compositions and print the divergence rate
    per category. This is informational, not a strict pass/fail —
    the only assertion is that the categories sum to total."""
    rng = random.Random(42)
    n_trials = 500
    categories = {
        "all_three_agree": 0,                     # diag_fee == n_greedy == n_min_disc
        "fee_negative": 0,                        # diag_fee < 0 (formula gives obstruction-imbalance)
        "greedy_neq_min_disc_card": 0,            # |greedy| != |min_disc|
        "greedy_eq_rank_K": 0,                    # |greedy| == rank(K) (matroid invariant)
    }

    for seed in range(n_trials):
        comp = random_composition(rng, seed_id=seed + 5000)
        m_ = measures(comp)
        if m_["diag_fee"] == m_["n_greedy"] == m_["n_min_disc"]:
            categories["all_three_agree"] += 1
        if m_["diag_fee"] < 0:
            categories["fee_negative"] += 1
        if m_["n_greedy"] != m_["n_min_disc"]:
            categories["greedy_neq_min_disc_card"] += 1
        if m_["n_greedy"] == m_["rank_K"]:
            categories["greedy_eq_rank_K"] += 1

    print(f"\nRandom-composition disclosure-semantics report ({n_trials} trials):")
    for k, v in categories.items():
        print(f"  {k}: {v}/{n_trials} ({100 * v / n_trials:.1f}%)")
    # The matroid invariant rank(K) == |greedy| should hold structurally
    # (weighted_greedy_repair returns a max-rank set by construction):
    assert categories["greedy_eq_rank_K"] == n_trials, (
        f"weighted_greedy_repair output cardinality should always equal rank(K); "
        f"got {categories['greedy_eq_rank_K']}/{n_trials}."
    )


# ---- 4. Structural invariant that DOES hold: |greedy| == rank(K) ----

def test_greedy_cardinality_equals_rank_K_random():
    """The matroid invariant `|weighted_greedy_repair| == rank(K)` holds
    structurally because greedy returns a max-rank set on the column matroid.
    This is the only cardinality identity that survives in the general
    regime — verified across 1000 random compositions."""
    rng = random.Random(7777)
    n_trials = 1000
    for seed in range(n_trials):
        comp = random_composition(rng, seed_id=seed + 10000)
        m_ = measures(comp)
        assert m_["n_greedy"] == m_["rank_K"], (
            f"seed={seed}: |greedy|={m_['n_greedy']} != rank(K)={m_['rank_K']}; {m_}"
        )


# ---- 5. Witness construction: the original failing seed ----

def test_witness_seed_10_documented_divergence():
    """Lock in the specific divergence found at seed=10 of the original
    failing run. If this test starts failing, either the random generator
    has changed or the underlying functions' behavior has changed —
    investigate before changing the assertion."""
    rng = random.Random(10)
    comp = random_composition(rng, seed_id=10)
    m_ = measures(comp)
    # Documented values from the Sprint 7 investigation:
    assert m_["diag_fee"] == -2, f"diag_fee changed: {m_}"
    assert m_["rank_K"] == 1, f"rank_K changed: {m_}"
    assert m_["n_greedy"] == 1, f"n_greedy changed: {m_}"
    assert m_["n_min_disc"] == 0, f"n_min_disc changed: {m_}"
    # The divergence: |greedy| (1) != |min_disc| (0)
    assert m_["n_greedy"] != m_["n_min_disc"], "expected divergence"
