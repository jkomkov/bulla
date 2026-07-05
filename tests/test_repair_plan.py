from __future__ import annotations

import dataclasses
import sys

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.repair import build_witness_guided_plan

# `bulla.repair` the attribute resolves to a re-exported function in
# bulla/__init__.py, not the submodule; fetch the module object directly.
repair_mod = sys.modules["bulla.repair"]


def _build_hidden_cycle(rank: int) -> Composition:
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(rank * 2)
    )
    edges = []
    for i in range(rank):
        a = 2 * i
        b = 2 * i + 1
        edges.append(Edge(f"t{a}", f"t{b}", (SemanticDimension(f"d{i}", "f", "f"),)))
        edges.append(Edge(f"t{b}", f"t{a}", (SemanticDimension(f"d{i}_b", "f", "f"),)))
    return Composition(name=f"repair_rank_{rank}", tools=tools, edges=tuple(edges))


def test_witness_guided_plan_returns_questions_for_positive_fee():
    comp = _build_hidden_cycle(3)
    plan = build_witness_guided_plan(comp)
    assert plan.initial_fee == 3
    assert len(plan.questions) > 0
    assert all(q.prompt for q in plan.questions)


def test_witness_guided_plan_respects_max_questions():
    comp = _build_hidden_cycle(5)
    plan = build_witness_guided_plan(comp, max_questions=2)
    assert len(plan.questions) == 2


def test_witness_guided_plan_falls_back_to_minimum_disclosure_set(monkeypatch):
    """Regression: the fallback at repair.py L526 must resolve
    ``minimum_disclosure_set`` (previously an unimported NameError).

    The normal ``diagnose(include_witness_geometry=True)`` path always
    populates ``disclosure_set`` when fee > 0, so the fallback branch never
    fires in ordinary flow — which is precisely why the missing import stayed
    latent. We force the branch by returning a diagnostic with the disclosure
    set stripped but positive fee, and assert the plan is still built (i.e.
    ``minimum_disclosure_set`` is in scope and recomputes the basis).
    """
    comp = _build_hidden_cycle(3)
    full = diagnose(comp, include_witness_geometry=True)
    assert full.coherence_fee == 3
    assert full.disclosure_set  # non-empty in the normal path

    stripped = dataclasses.replace(full, disclosure_set=())

    def _fake_diagnose(c, **kwargs):
        return stripped

    monkeypatch.setattr(repair_mod, "diagnose", _fake_diagnose)

    plan = build_witness_guided_plan(comp)
    assert plan.initial_fee == 3
    assert len(plan.questions) == 3  # recovered via minimum_disclosure_set fallback
    assert all(q.prompt for q in plan.questions)

