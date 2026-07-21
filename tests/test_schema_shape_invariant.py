"""Sprint 9: schema-shape invariant — projective observables ⇒ fee ≥ 0.

Sprint 8 documented an empirical observation: real-MCP composition pairs
never produce negative `coherence_fee`, while random-stress compositions
do 39.3% of the time. The user's review observed:

    > `is_well_formed_for_fee` is the measured rank predicate. The probable
    > structural condition explaining real-MCP nonnegativity is that
    > observable fields are a projection of the full tool field surface
    > (`observable_schema ⊆ internal_state`) rather than an independent
    > /disjoint declaration. The random-stress generator violated this shape.

This file verifies the structural condition empirically:

  1. **Schema-shape audit** — predicate `has_projective_observables(comp)`
     (true iff observable_schema ⊆ internal_state per tool) holds for
     100% of real-MCP corpora and 0.2% of Sprint 7's random stress
     generator (which used disjoint partitions).

  2. **Well-formed random generator** — repaired generator where
     `internal_state` is the FULL field set per tool and
     `observable_schema` is a SUBSET. Across 1000 trials, EVERY
     composition produced is `has_projective_observables` AND
     `is_well_formed_for_fee` (no negative fees).

  3. **Theorem candidate** (proof in `papers/composition-doctrine/sprint9_schema_shape_invariant.md`):
     For any composition G where observable_schema(t) ⊆ internal_state(t)
     for every tool t, `rank_obs(G) ≤ rank_internal(G)`, hence
     `coherence_fee(G) ≥ 0`.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

BULLA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT / "tests"))

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.regime import classify, has_projective_observables


# ---- Well-formed random generator (Sprint 9 repair) ----

def well_formed_random_composition(
    rng: random.Random,
    n_tools_range: tuple[int, int] = (3, 6),
    n_fields_per_tool_range: tuple[int, int] = (1, 4),
    n_edges_range: tuple[int, int] = (1, 8),
    n_dims_per_edge_range: tuple[int, int] = (1, 2),
    observable_prob: float = 0.5,
    seed_id: int = 0,
) -> Composition:
    """Repaired random generator (Sprint 9): observable_schema is a SUBSET
    of internal_state per tool, not a disjoint partition.

    Difference from Sprint 7's `random_composition`:
      - Sprint 7: `intl = fields - obs` (disjoint partition)
      - Sprint 9: `intl = fields` (full surface), `obs ⊆ fields` (random subset)

    This matches the real-MCP convention where `internal_state` is the full
    tool field surface and `observable_schema` is the subset visible at the
    seam boundary.
    """
    n_tools = rng.randint(*n_tools_range)
    tool_fields: list[list[str]] = []
    tools: list[ToolSpec] = []
    for i in range(n_tools):
        n_fields = rng.randint(*n_fields_per_tool_range)
        fields = [f"f{i}_{j}" for j in range(n_fields)]
        # Repair: internal_state is the FULL field surface.
        intl = tuple(fields)
        # observable_schema is a random subset of internal_state.
        obs = tuple(f for f in fields if rng.random() < observable_prob)
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
        name=f"wf_random_{seed_id}",
        tools=tuple(tools),
        edges=tuple(edges),
    )


# ---- 1. Real corpora satisfy the schema-shape invariant ----

@pytest.mark.parametrize("yaml_dir", ["compositions", "audit"])
def test_curated_yaml_compositions_have_projective_observables(yaml_dir):
    """All curated YAML compositions in bulla/compositions and bulla/audit
    satisfy observable_schema ⊆ internal_state per tool. This is the
    structural property; rank non-negativity is the consequence."""
    from bulla.parser import load_composition
    paths = sorted((BULLA_ROOT / yaml_dir).glob("*.yaml"))
    assert paths, f"No YAML in bulla/{yaml_dir}"
    for p in paths:
        comp = load_composition(p)
        assert has_projective_observables(comp), (
            f"{p.name} fails projective observables: "
            f"{[(t.name, set(t.observable_schema) - set(t.internal_state)) for t in comp.tools]}"
        )


# ---- 2. Repaired random generator produces only well-formed compositions ----

def test_well_formed_random_always_projective():
    """The repaired generator always produces compositions with
    observable_schema ⊆ internal_state per tool."""
    rng = random.Random(20260502)
    for seed in range(500):
        comp = well_formed_random_composition(rng, seed_id=seed)
        assert has_projective_observables(comp)


def test_well_formed_random_implies_nonneg_fee():
    """**Theorem candidate (empirical verification, 1000 trials):**

    If `has_projective_observables(comp)` then
    `coherence_fee(comp) >= 0` (equivalently `rank_internal >= rank_obs`).

    This is the load-bearing structural-to-rank implication.

    Across 1000 well-formed-random compositions, ZERO produce negative
    fee. Combined with the proof in
    `papers/composition-doctrine/sprint9_schema_shape_invariant.md`,
    this elevates the empirical observation of Sprint 8 ("real-MCP fees
    are never negative") to the structural conclusion ("compositions
    with projective observables have non-negative fee").
    """
    rng = random.Random(20260502)
    n_negative = 0
    n_total = 1000
    failures = []
    for seed in range(n_total):
        comp = well_formed_random_composition(rng, seed_id=seed)
        report = classify(comp)
        # Pre-condition: projective observables
        assert has_projective_observables(comp)
        # Conclusion: non-negative fee
        if report.fee_formula < 0:
            n_negative += 1
            if len(failures) < 3:
                failures.append((seed, report))
    assert n_negative == 0, (
        f"{n_negative}/{n_total} well-formed-random compositions produced "
        f"negative fee — schema-shape invariant does NOT imply fee >= 0. "
        f"First failures: {failures}"
    )


# ---- 3. Sprint 7's disjoint-partition generator violates the invariant ----

def test_sprint7_random_violates_projective_observables():
    """Confirm that Sprint 7's random generator (disjoint partition) does
    NOT satisfy projective observables — without this, the test_disclosure_semantics_random
    counterexamples would be unexplained."""
    from test_disclosure_semantics_random import random_composition
    rng = random.Random(20260502)
    n_total = 100
    n_projective = 0
    for seed in range(n_total):
        comp = random_composition(rng, seed_id=seed)
        if has_projective_observables(comp):
            n_projective += 1
    # Random partition between obs and intl produces non-projective comps
    # almost always (only edge case: all fields go to obs, leaving intl empty —
    # vacuously projective if intl is empty *and* obs is also empty, otherwise not).
    # Empirically only ~0.2% are projective.
    assert n_projective < n_total * 0.05, (
        f"Sprint 7 disjoint-partition generator unexpectedly projective in "
        f"{n_projective}/{n_total} cases — the random partition shouldn't "
        f"satisfy the schema-shape invariant in general."
    )


# ---- 4. Hand-crafted edge cases ----

def test_observable_schema_equals_internal_state_is_projective():
    """Tool with observable_schema == internal_state is projective (subset is
    not strict). This is a common real-MCP pattern (fully-observable tool)."""
    t = ToolSpec(name="t", internal_state=("a", "b"), observable_schema=("a", "b"))
    comp = Composition(name="case", tools=(t,), edges=())
    assert has_projective_observables(comp)
    report = classify(comp)
    assert report.is_well_formed_for_fee


def test_empty_observable_schema_is_projective():
    """Empty observable_schema is vacuously a subset (cycle-family case)."""
    t = ToolSpec(name="t", internal_state=("f",), observable_schema=())
    comp = Composition(name="cycle_member", tools=(t,), edges=())
    assert has_projective_observables(comp)


def test_disjoint_observable_internal_violates_projective():
    """Disjoint observable_schema and internal_state — the Sprint 7
    failure mode."""
    t = ToolSpec(name="t", internal_state=("hidden",), observable_schema=("visible",))
    comp = Composition(name="bad", tools=(t,), edges=())
    assert not has_projective_observables(comp)
