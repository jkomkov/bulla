"""Sprint 8 Phase 2: tests for `bulla.regime` validity predicates.

Verifies the regime classification across:

  - All-hidden cycle family (Sprint 6 toy + parametric cells): 100% well-formed.
  - Curated YAML compositions in bulla/compositions and bulla/audit: 100% well-formed.
  - Random-stress generator: contains both well-formed and ill-formed cases
    (the latter being the empirical witness to the negative-fee regime).

Negative-result tests confirm the regime predicates correctly DETECT
ill-formed compositions — without these, the predicates would be vacuous.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))
sys.path.insert(0, str(REPO / "bulla" / "tests"))

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition
from bulla.regime import (
    RegimeReport,
    classify,
    is_all_hidden,
    is_all_observable,
    is_well_formed_for_fee,
)


# ---- Cycle-family helpers (Sprint 6) ----

def _build_disjoint_cycles(k: int, m: int) -> Composition:
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


# ---- 1. Cycle family: all-hidden, well-formed ----

@pytest.mark.parametrize("k,m", [(2, 4), (2, 5), (3, 6), (4, 8), (5, 10), (6, 7)])
def test_cycle_family_all_hidden(k, m):
    comp = _build_disjoint_cycles(k, m)
    report = classify(comp)
    assert report.is_all_hidden
    assert not report.is_all_observable
    assert report.is_well_formed_for_fee
    assert report.fee_formula == diagnose(comp).coherence_fee
    # The cycle family produces fee = |V| − c > 0, so internal-dominance:
    assert report.has_internal_dominance
    assert not report.has_obs_dominance


# ---- 2. Curated YAML compositions: 100% well-formed ----

@pytest.mark.parametrize("yaml_dir", ["compositions", "audit"])
def test_curated_yaml_compositions_well_formed(yaml_dir):
    """All YAML compositions in bulla/compositions and bulla/audit are
    well-formed for fee. This is the load-bearing real-MCP guarantee."""
    paths = sorted((REPO / "bulla" / yaml_dir).glob("*.yaml"))
    assert paths, f"No YAML compositions found in bulla/{yaml_dir}"
    n_well_formed = 0
    failures = []
    for p in paths:
        try:
            comp = load_composition(p)
            report = classify(comp)
            if report.is_well_formed_for_fee:
                n_well_formed += 1
            else:
                failures.append((p.name, report.fee_formula))
        except Exception:
            pass
    assert not failures, (
        f"Curated compositions in bulla/{yaml_dir} produced ill-formed regimes: {failures}"
    )
    assert n_well_formed > 0


# ---- 3. Random stress: contains BOTH well-formed and ill-formed ----

def test_random_stress_contains_ill_formed():
    """The regime predicates must correctly DETECT ill-formed compositions
    (not just report everything as well-formed). This test produces random
    compositions and asserts that at least one is ill-formed (negative fee).
    Otherwise the predicates would be vacuously true."""
    from test_disclosure_semantics_random import random_composition
    rng = random.Random(20260502)
    n_well_formed = 0
    n_ill_formed = 0
    for seed in range(500):
        comp = random_composition(rng, seed_id=seed)
        report = classify(comp)
        if report.is_well_formed_for_fee:
            n_well_formed += 1
        else:
            n_ill_formed += 1
            # Sanity: the diagnostic also reports negative fee
            assert diagnose(comp).coherence_fee < 0

    assert n_ill_formed > 0, (
        "No ill-formed compositions in 500-trial random sweep — "
        "the regime predicates can't be falsified, suggesting either "
        "the random generator is wrong or the predicates are vacuous."
    )
    assert n_well_formed > 0, "Random sweep produced only ill-formed comps."


# ---- 4. Empty composition: edge case ----

def test_empty_composition():
    """A composition with no tools and no edges has rank_obs = rank_internal = 0,
    fee = 0, and is trivially well-formed."""
    comp = Composition(name="empty", tools=(), edges=())
    report = classify(comp)
    assert report.rank_obs == 0
    assert report.rank_internal == 0
    assert report.fee_formula == 0
    assert report.is_well_formed_for_fee
    assert report.is_all_hidden       # vacuously true
    assert report.is_all_observable   # vacuously true


# ---- 5. Single-tool composition: edge case ----

def test_single_tool_no_edges():
    """A single tool with one hidden field, no edges: trivially well-formed
    (no seam dimensions to constrain)."""
    tool = ToolSpec(name="t0", internal_state=("f",), observable_schema=())
    comp = Composition(name="solo", tools=(tool,), edges=())
    report = classify(comp)
    assert report.fee_formula == 0
    assert report.is_well_formed_for_fee
    assert report.is_all_hidden


# ---- 6. Constructed obs-dominance counterexample ----

def test_constructed_obs_dominance_is_ill_formed():
    """Hand-constructed minimal example with rank_obs > rank_internal.
    Two tools, both with single observable field 'a'; one edge with
    seam dim referencing 'a' on both sides. δ_obs has one row with one
    +1 and one −1 (rank 1); δ_internal has zero columns (no internal
    fields), so rank_internal = 0. Hence fee = 0 − 1 = −1, ill-formed."""
    t1 = ToolSpec(name="t1", internal_state=(), observable_schema=("a",))
    t2 = ToolSpec(name="t2", internal_state=(), observable_schema=("a",))
    edge = Edge(
        from_tool="t1", to_tool="t2",
        dimensions=(SemanticDimension(name="m", from_field="a", to_field="a"),),
    )
    comp = Composition(name="obs_dom", tools=(t1, t2), edges=(edge,))
    report = classify(comp)
    assert report.is_all_observable
    assert not report.is_all_hidden
    assert report.fee_formula == -1
    assert not report.is_well_formed_for_fee
    assert report.has_obs_dominance


# ---- 7. Convenience predicates ----

def test_convenience_predicates_consistent_with_classify():
    """The standalone functions agree with the corresponding fields of `classify`."""
    cases = [
        _build_disjoint_cycles(2, 4),  # all-hidden, well-formed
        # Constructed obs-dominance (from test_6):
        Composition(
            name="case2",
            tools=(
                ToolSpec(name="t1", internal_state=(), observable_schema=("a",)),
                ToolSpec(name="t2", internal_state=(), observable_schema=("a",)),
            ),
            edges=(Edge(
                from_tool="t1", to_tool="t2",
                dimensions=(SemanticDimension(name="m", from_field="a", to_field="a"),),
            ),),
        ),
    ]
    for comp in cases:
        report = classify(comp)
        assert is_all_hidden(comp) == report.is_all_hidden
        assert is_all_observable(comp) == report.is_all_observable
        assert is_well_formed_for_fee(comp) == report.is_well_formed_for_fee
