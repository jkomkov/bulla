"""Unit tests for bulla.bridge_kinds — value vs schema classification."""

from __future__ import annotations

import pytest

from bulla.bridge_kinds import (
    BridgeAdvice,
    classify_for_call,
    summarize_verdict,
)
from bulla.diagnostic import diagnose
from bulla.model import (
    Composition,
    Edge,
    SemanticDimension,
    ToolSpec,
)


def _hidden_composition() -> Composition:
    """fetch emits encoding internally; memory consumes it visibly."""
    producer = ToolSpec(
        name="fetch__get",
        internal_state=("url", "body", "encoding"),
        observable_schema=("url", "body"),
    )
    consumer = ToolSpec(
        name="memory__store",
        internal_state=("content", "encoding"),
        observable_schema=("content", "encoding"),
    )
    edge = Edge(
        from_tool="fetch__get",
        to_tool="memory__store",
        dimensions=(
            SemanticDimension(
                name="encoding", from_field="encoding", to_field="encoding"
            ),
        ),
    )
    return Composition(
        name="hidden_encoding", tools=(producer, consumer), edges=(edge,)
    )


def test_hidden_field_yields_schema_advice():
    comp = _hidden_composition()
    diag = diagnose(comp)
    advices = classify_for_call(
        diag, "memory", "store", arguments={"content": "x", "encoding": "utf-8"}
    )
    assert len(advices) == 1
    a = advices[0]
    assert a.kind == "schema"
    assert a.applicable is False
    assert a.advice["patch"]["action"] == "expose"
    assert a.advice["patch"]["target_tool"] == "fetch__get"
    assert a.advice["patch"]["field"] == "encoding"
    assert a.advice["patch"]["dimension"] == "encoding"


def test_schema_advice_drives_refuse_verdict():
    comp = _hidden_composition()
    diag = diagnose(comp)
    advices = classify_for_call(diag, "memory", "store")
    verdict = summarize_verdict(diag.coherence_fee, advices)
    assert verdict == "refuse", (
        "schema-level obstruction must yield refuse — agent cannot fix at runtime"
    )


def test_empty_diagnostic_yields_safe():
    p = ToolSpec(name="a__t", internal_state=("x",), observable_schema=("x",))
    q = ToolSpec(name="b__t", internal_state=("x",), observable_schema=("x",))
    e = Edge(
        from_tool="a__t",
        to_tool="b__t",
        dimensions=(SemanticDimension(name="x", from_field="x", to_field="x"),),
    )
    comp = Composition(name="clean", tools=(p, q), edges=(e,))
    diag = diagnose(comp)
    advices = classify_for_call(diag, "b", "t")
    assert advices == []
    assert summarize_verdict(diag.coherence_fee, advices) == "safe"


def test_unrelated_tool_returns_no_advice():
    """Tool not in any obstruction edge should get empty advice list."""
    comp = _hidden_composition()
    diag = diagnose(comp)
    advices = classify_for_call(diag, "elsewhere", "unused")
    assert advices == []


def test_advice_round_trips_through_to_dict():
    comp = _hidden_composition()
    diag = diagnose(comp)
    advices = classify_for_call(diag, "memory", "store")
    d = advices[0].to_dict()
    assert d["kind"] == "schema"
    assert d["applicable"] is False
    assert "advice" in d
    assert "patch" in d["advice"]


def test_verdict_no_advices_is_safe_even_when_composition_dirty():
    """Per-call sensitivity: if the call doesn't traverse any
    obstruction, the verdict is 'safe' regardless of the global
    composition fee. Global state surfaces via composition_fee in the
    payload, not via the verdict — preventing the false-positive trap
    where every call gets refused once one obstruction exists."""
    assert summarize_verdict(0, []) == "safe"
    assert summarize_verdict(2, []) == "safe"  # global dirty, this call clean
    assert summarize_verdict(99, []) == "safe"
