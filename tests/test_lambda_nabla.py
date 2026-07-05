"""Tests for the lambda_nabla elaborator (bulla.lambda_nabla).

Validates that Bulla's deployed coherence-fee checker, presented as the
lambda_nabla type-coherence oracle, satisfies the paper's theorems
(papers/refinement-types/paper.md):

  * Repair duality (Thm 3.7):  |minimum coercion set| == grade.
  * Elaboration soundness (Cor 5.5):  the elaborated composition has grade 0.

both on worked MCP-style examples and over randomly fuzzed well-formed
compositions.
"""

import random

import pytest

from bulla.lambda_nabla import (
    check_elaboration_soundness,
    check_repair_duality,
    elaborate,
    typecheck,
    typecheck_report,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _pair(name, cal_obs, inv_obs, dims, universe=("date", "timezone", "currency")):
    cal = ToolSpec("calendar", universe, cal_obs)
    inv = ToolSpec("invoice", universe, inv_obs)
    edge = Edge(
        "calendar",
        "invoice",
        tuple(SemanticDimension(d, from_field=d, to_field=d) for d in dims),
    )
    return Composition(name, (cal, inv), (edge,))


# ---- worked MCP-style examples ----------------------------------------------

def test_coherent_typechecks():
    # every convention on the edge is declared (observable) at both endpoints
    c = _pair("coherent", ("date", "timezone"), ("date", "timezone"), ["timezone"])
    v = typecheck(c)
    assert v.coherent and v.grade == 0 and v.coercions == ()


def test_hidden_timezone_needs_one_coercion():
    # [CD] sec 1: calendar emits a date with implicit timezone; invoice reads it.
    c = _pair("tz-hidden", ("date",), ("date",), ["timezone"])
    v = typecheck(c)
    assert not v.coherent
    assert v.grade == 1
    assert len(v.coercions) == 1
    # the coercion declares the timezone convention on one endpoint
    (tool, field), = v.coercions
    assert field == "timezone"


def test_two_hidden_dims_need_two_coercions():
    c = _pair("tz+cur", ("date",), ("date",), ["timezone", "currency"])
    v = typecheck(c)
    assert v.grade == 2
    assert len(v.coercions) == 2
    assert {f for _, f in v.coercions} == {"timezone", "currency"}


def test_elaborate_makes_coherent():
    c = _pair("tz-hidden", ("date",), ("date",), ["timezone"])
    elaborated, coercions = elaborate(c)
    assert len(coercions) == 1
    assert typecheck(elaborated).coherent  # grade 0 after elaboration


def test_elaborate_is_noop_on_coherent():
    c = _pair("coherent", ("date", "timezone"), ("date", "timezone"), ["timezone"])
    elaborated, coercions = elaborate(c)
    assert coercions == ()
    assert elaborated is c


def test_repair_duality_and_soundness_examples():
    for c in (
        _pair("c0", ("date", "timezone"), ("date", "timezone"), ["timezone"]),
        _pair("c1", ("date",), ("date",), ["timezone"]),
        _pair("c2", ("date",), ("date",), ["timezone", "currency"]),
    ):
        assert check_repair_duality(c)
        assert check_elaboration_soundness(c)


def test_typecheck_report():
    comps = [
        _pair("coh", ("date", "timezone"), ("date", "timezone"), ["timezone"]),
        _pair("inc1", ("date",), ("date",), ["timezone"]),
        _pair("inc2", ("date",), ("date",), ["timezone", "currency"]),
    ]
    rep = typecheck_report(comps)
    assert rep == {
        "total": 3,
        "coherent": 1,
        "elaborated": 2,
        "total_coercions": 3,
    }


# ---- randomized property test over well-formed compositions ------------------

_POOL = ("date", "timezone", "currency", "amount", "unit", "id", "score")


def _random_comp(rng: random.Random, idx: int) -> Composition:
    n = rng.randint(2, 5)
    tools = []
    for i in range(n):
        k = rng.randint(2, len(_POOL))
        universe = tuple(rng.sample(_POOL, k))
        # well-formed: observable_schema subset of internal_state
        n_obs = rng.randint(0, len(universe))
        obs = tuple(rng.sample(universe, n_obs))
        tools.append(ToolSpec(f"t{i}", universe, obs))
    edges = []
    for _ in range(rng.randint(1, n)):
        a, b = rng.sample(range(n), 2)
        ua = set(tools[a].internal_state)
        ub = set(tools[b].internal_state)
        shared = sorted(ua & ub)
        if not shared:
            continue
        dims = tuple(
            SemanticDimension(d, from_field=d, to_field=d)
            for d in rng.sample(shared, rng.randint(1, len(shared)))
        )
        edges.append(Edge(f"t{a}", f"t{b}", dims))
    if not edges:  # ensure at least one edge with a shared dimension
        edges.append(Edge("t0", "t1", (SemanticDimension(_POOL[0],
                          from_field=_POOL[0], to_field=_POOL[0]),)))
    return Composition(f"rand{idx}", tuple(tools), tuple(edges))


def test_random_repair_duality_and_soundness():
    rng = random.Random(20260603)
    tested = 0
    for idx in range(2000):
        c = _random_comp(rng, idx)
        v = typecheck(c)
        if v.grade < 0:
            # Defensive guard for the ill-formed-for-fee regime (rank_obs >
            # rank_full), where coherence_fee is not a fee. The generator keeps
            # observable_schema subset of internal_state, so this regime is
            # (empirically, on this seed) never hit -- but we guard rather than
            # assume. The paper's guarantees only hold in the well-formed regime.
            continue
        tested += 1
        # repair duality: exactly `grade` coercions
        assert len(v.coercions) == v.grade, (c.name, v.grade, v.coercions)
        # elaboration soundness: grade 0 after inserting the disclosure normal form
        assert check_elaboration_soundness(c), c.name
    assert tested > 200  # sanity: we actually exercised many compositions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
