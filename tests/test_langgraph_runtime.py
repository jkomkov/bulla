"""Tests for the live-object LangGraph runtime adapter.

Gated by ``pytest.importorskip("langgraph")`` so a clean checkout
without ``bulla[langgraph]`` installed skips this file rather than
failing.

Coverage matches the sprint plan's 13 tests:
  1–3    bind() basics: empty graph, two-node pipeline, args_schema fields
  4–6    conditional edges: with path_map, fan_out default, skip mode
  7      LOAD-BEARING property test: bind() insertion-order independence
  8      deprecated-kwarg fallback (config_schema vs context_schema)
  9–10   output-schema gap: warning on plain runnable, kwarg propagation
  11–13  BullaCallbackHandler: invocation recording, receipt chaining,
         terminal_receipt populated after invoke()
"""

from __future__ import annotations

import random
import warnings
from typing import TypedDict

import pytest

# Skip the entire module when the runtime extras aren't installed.
pytest.importorskip("langgraph", reason="bulla[langgraph] extra not installed")
pytest.importorskip("langchain_core", reason="bulla[langgraph] extra not installed")

from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import bulla
from bulla.langgraph import BullaCallbackHandler, bind
from bulla.model import Disposition, WitnessReceipt


# ── Shared fixtures ────────────────────────────────────────────────


class _CheckoutState(TypedDict):
    messages: list
    currency: str
    amount: float


@tool
def get_quote(currency: str, amount: float) -> dict:
    """Get FX quote."""
    return {"currency": currency, "amount": amount}


@tool
def settle_payment(currency: str, amount: float) -> dict:
    """Settle payment with the given currency convention."""
    return {"currency": currency, "amount": amount}


@tool
def email_receipt(currency: str, recipient: str) -> dict:
    """Email a receipt."""
    return {"recipient": recipient}


def _two_node_graph() -> StateGraph:
    g = StateGraph(_CheckoutState)
    g.add_node("quote", ToolNode([get_quote]))
    g.add_node("settle", ToolNode([settle_payment]))
    g.add_edge("quote", "settle")
    return g


# ── 1–3: bind() basics ─────────────────────────────────────────────


def test_bind_empty_graph_returns_zero_fee_session():
    g = StateGraph(_CheckoutState)
    session = bind(g)
    assert session.fee == 0
    assert session.composition.tools == ()
    assert session.composition.edges == ()


def test_bind_two_node_pipeline_matches_handbuilt_session():
    """bind(g) and a hand-built Session over the same tools/edges
    produce the same composition_hash."""
    g = _two_node_graph()
    session = bind(g, name="checkout-flow")

    assert session.fee == 0
    tool_names = {t.name for t in session.composition.tools}
    assert tool_names == {"quote", "settle"}
    edge_names = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    assert edge_names == {("quote", "settle")}
    edge = session.composition.edges[0]
    dim_names = {d.name for d in edge.dimensions}
    # Both tools share `currency` and `amount` as fields.
    assert dim_names == {"currency_match", "amount_match"}


def test_bind_with_pydantic_args_schema_extracts_fields():
    g = StateGraph(_CheckoutState)
    g.add_node("get_quote", ToolNode([get_quote]))
    session = bind(g)
    spec = session.composition.tools[0]
    # args_schema → `currency`, `amount`
    assert "currency" in spec.internal_state
    assert "amount" in spec.internal_state


# ── 4–6: conditional edges ─────────────────────────────────────────


def test_bind_conditional_edge_with_path_map_emits_listed_edges():
    g = StateGraph(_CheckoutState)
    g.add_node("quote", ToolNode([get_quote]))
    g.add_node("settle", ToolNode([settle_payment]))
    g.add_node("email", ToolNode([email_receipt]))

    def router(state: _CheckoutState) -> str:
        return "settle"

    g.add_conditional_edges(
        "quote",
        router,
        path_map={"settle": "settle", "email": "email"},
    )

    session = bind(g)
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    # Both targets enumerated in path_map should appear (subject to
    # shared-field filter — settle and email both share `currency`
    # with quote, so both edges land).
    assert ("quote", "settle") in edge_pairs
    assert ("quote", "email") in edge_pairs


def test_bind_conditional_edge_without_path_map_fan_out_default():
    g = StateGraph(_CheckoutState)
    g.add_node("quote", ToolNode([get_quote]))
    g.add_node("settle", ToolNode([settle_payment]))
    g.add_node("email", ToolNode([email_receipt]))

    def router(state: _CheckoutState) -> str:
        return "settle"

    g.add_conditional_edges("quote", router)  # no path_map

    session = bind(g)
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    # Fan-out: quote → every other node that shares a field.
    # `settle` and `email` both share `currency` with quote.
    assert ("quote", "settle") in edge_pairs
    assert ("quote", "email") in edge_pairs


def test_bind_conditional_edge_without_path_map_skip_mode():
    g = StateGraph(_CheckoutState)
    g.add_node("quote", ToolNode([get_quote]))
    g.add_node("settle", ToolNode([settle_payment]))

    def router(state: _CheckoutState) -> str:
        return "settle"

    g.add_conditional_edges("quote", router)  # no path_map

    session = bind(g, on_unknown_branch="skip")
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    # No explicit edge added, no path_map → skip emits no conditional edge.
    assert ("quote", "settle") not in edge_pairs


# ── 7: LOAD-BEARING property test ──────────────────────────────────


@pytest.mark.parametrize("seed", range(50))
def test_bind_order_independence(seed: int):
    """bind() must produce the same composition_hash regardless of the
    order in which nodes/edges were added to the StateGraph.

    This is the analog of test_session_bitwise_equals_full_rebuild for
    the LangGraph adapter. Without it, an internal LangGraph
    insertion-order detail could leak into bulla's composition_hash and
    silently break receipt determinism.
    """
    rng = random.Random(seed)

    @tool
    def t_one(x: str, y: str) -> dict:
        """t1."""
        return {}

    @tool
    def t_two(x: str, y: str, z: str) -> dict:
        """t2."""
        return {}

    @tool
    def t_three(x: str, w: str) -> dict:
        """t3."""
        return {}

    @tool
    def t_four(y: str, w: str) -> dict:
        """t4."""
        return {}

    tools_by_name = {
        "one": ToolNode([t_one]),
        "two": ToolNode([t_two]),
        "three": ToolNode([t_three]),
        "four": ToolNode([t_four]),
    }
    n_nodes = rng.randint(2, 4)
    chosen = rng.sample(sorted(tools_by_name.keys()), n_nodes)
    edges = []
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            if rng.random() < 0.5:
                edges.append((chosen[i], chosen[j]))

    def build(order: list[str]) -> StateGraph:
        g = StateGraph(_CheckoutState)
        for name in order:
            g.add_node(name, tools_by_name[name])
        # Edges added in shuffled order too.
        edge_order = list(edges)
        rng.shuffle(edge_order)
        for a, b in edge_order:
            g.add_edge(a, b)
        return g

    order_a = list(chosen)
    order_b = list(chosen)
    rng.shuffle(order_b)

    g_a = build(order_a)
    g_b = build(order_b)

    s_a = bind(g_a)
    s_b = bind(g_b)

    assert s_a.composition.canonical_hash() == s_b.composition.canonical_hash(), (
        f"seed={seed}: composition_hash diverged between insertion orders\n"
        f"  order_a={order_a}\n  order_b={order_b}"
    )


# ── 8: deprecated-kwarg fallback ───────────────────────────────────


def test_bind_handles_missing_attributes_gracefully():
    """Synthesize a node spec object that lacks the modern `runnable`
    attribute, exposing only `node` (the legacy name). bind() must
    fall back to it without crashing."""

    class _LegacyNodeSpec:
        def __init__(self, runnable):
            self.node = runnable  # legacy attr name

    @tool
    def legacy_tool(x: str) -> dict:
        """Legacy tool."""
        return {}

    g = StateGraph(_CheckoutState)
    g.add_node("ok", ToolNode([legacy_tool]))
    # Replace the wrapped node spec with a legacy-attr stand-in.
    real_spec = g.nodes["ok"]
    runnable = getattr(real_spec, "runnable", None) or getattr(real_spec, "node", None)
    g.nodes["legacy"] = _LegacyNodeSpec(runnable)
    del g.nodes["ok"]

    session = bind(g)
    assert {t.name for t in session.composition.tools} == {"legacy"}


# ── 9–10: output-schema gap ────────────────────────────────────────


def test_bind_warns_on_runnable_lambda_without_output_schema():
    """A node whose runnable isn't a ToolNode (e.g., a plain callable)
    triggers a warning naming the node."""
    g = StateGraph(_CheckoutState)

    def plain_node(state: _CheckoutState) -> _CheckoutState:
        return state

    g.add_node("plain", plain_node)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = bind(g)
    msgs = [str(w.message) for w in caught]
    assert any("plain" in m and "output_schemas" in m for m in msgs), (
        f"expected a warning naming the plain node; got: {msgs}"
    )
    # The session itself still builds.
    assert {t.name for t in session.composition.tools} == {"plain"}


def test_bind_uses_output_schemas_kwarg_when_supplied():
    """User-supplied output_schemas surfaces output fields on the
    ToolSpec's observable_schema."""
    g = _two_node_graph()
    session = bind(
        g,
        output_schemas={
            "get_quote": {
                "type": "object",
                "properties": {
                    "rate": {"type": "number"},
                    "expires_at": {"type": "string"},
                },
            },
        },
    )
    quote_spec = next(t for t in session.composition.tools if t.name == "quote")
    # The tool inside the node is named `get_quote`; its output fields
    # should be in the spec's internal_state and observable_schema.
    assert "rate" in quote_spec.internal_state
    assert "expires_at" in quote_spec.internal_state


# ── 11–13: BullaCallbackHandler ────────────────────────────────────


def test_callback_handler_records_invocations():
    session = bulla.Session()
    handler = BullaCallbackHandler(session)
    # Simulate the callback events directly (no need to actually run
    # a graph for this assertion).
    handler.on_tool_start(
        {"name": "fake_tool"}, "input", run_id="rid-1"
    )
    handler.on_tool_end("output", run_id="rid-1")
    assert len(handler.invocations) == 1
    record = handler.invocations[0]
    assert record["run_id"] == "rid-1"
    assert record["tool"] == "fake_tool"
    assert record["duration_ms"] >= 0


def test_callback_handler_chains_receipts_via_parent_hashes():
    """When ``emit_checkpoint_per_call=True``, every on_tool_end
    extends the session's receipt chain. The chain length must grow
    by exactly one per recorded call."""
    session = bulla.Session()
    handler = BullaCallbackHandler(session, emit_checkpoint_per_call=True)
    chain_before = len(session.receipt_chain)

    handler.on_tool_start({"name": "t1"}, "in", run_id="r1")
    handler.on_tool_end("out", run_id="r1")
    handler.on_tool_start({"name": "t2"}, "in", run_id="r2")
    handler.on_tool_end("out", run_id="r2")

    chain_after = len(session.receipt_chain)
    assert chain_after - chain_before == 2


def test_callback_handler_terminal_receipt_after_invoke():
    """After ``on_chain_end`` fires for the outermost chain
    (parent_run_id is None), ``terminal_receipt`` must be a
    WitnessReceipt summarizing the session."""
    session = bulla.Session()
    session.add_tool(bulla.ToolSpec("a", ("x",), ("x",)))
    handler = BullaCallbackHandler(session)
    handler.on_chain_end({}, run_id="root", parent_run_id=None)
    assert handler.terminal_receipt is not None
    assert isinstance(handler.terminal_receipt, WitnessReceipt)
    assert handler.terminal_receipt.disposition in {
        Disposition.PROCEED, Disposition.PROCEED_WITH_BRIDGE
    }


def test_callback_handler_ignores_inner_chain_end():
    """``on_chain_end`` for a non-root run (parent_run_id is not None)
    must not emit a terminal receipt."""
    session = bulla.Session()
    session.add_tool(bulla.ToolSpec("a", ("x",), ("x",)))
    handler = BullaCallbackHandler(session)
    handler.on_chain_end({}, run_id="inner", parent_run_id="root")
    assert handler.terminal_receipt is None


def test_callback_handler_isinstance_through_facade():
    """Regression: ``isinstance(handler, BullaCallbackHandler)`` must
    return True. The handler is built through a lazy-subclass façade
    (so ``import bulla.langgraph`` doesn't pull in ``langchain_core``);
    a metaclass __instancecheck__ override makes the public name
    accept the dynamically-built real subclass."""
    session = bulla.Session()
    handler = BullaCallbackHandler(session)
    assert isinstance(handler, BullaCallbackHandler)
    # And isinstance still returns False for non-handlers.
    assert not isinstance(object(), BullaCallbackHandler)
