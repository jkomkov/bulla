#!/usr/bin/env python3
"""LangGraph + Bulla integration demo.

Demonstrates that Bulla diagnoses convention risks *above* the
transport layer -- a LangGraph graph validates schemas, but Bulla
catches hidden convention mismatches that LangGraph cannot see.

Requirements (not project dependencies -- install only to run this demo):
    pip install langgraph>=0.3,<0.4 langchain-core>=0.3,<0.4

Usage:
    python examples/langgraph_demo.py

The manual annotation step below (internal_state vs observable_schema)
is automated by `bulla gauge` (see Sprint 17).
"""

from __future__ import annotations

import json
import sys
from typing import Any, TypedDict

try:
    from langgraph.graph import StateGraph, END
except ImportError:
    print(
        "This demo requires langgraph and langchain-core.\n"
        "Install them to run: pip install 'langgraph>=0.3,<0.4' 'langchain-core>=0.3,<0.4'\n"
        "These are NOT project dependencies -- only needed for this example.",
        file=sys.stderr,
    )
    sys.exit(1)

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


# ── LangGraph state and tool nodes ──────────────────────────────────

class PipelineState(TypedDict, total=False):
    trade_id: str
    amount: float
    currency: str
    converted_amount: float
    compliance_result: str
    risk_score: float


def price_fetch(state: PipelineState) -> dict[str, Any]:
    """Fetch trade price. Internally uses a rounding_mode to truncate."""
    return {"amount": 1234.567, "currency": "USD"}


def currency_convert(state: PipelineState) -> dict[str, Any]:
    """Convert currency. Hidden: rounding_mode determines truncation."""
    return {"converted_amount": round(state["amount"] * 0.85, 2)}


def compliance_check(state: PipelineState) -> dict[str, Any]:
    """Check compliance. Hidden: jurisdiction determines which rules apply."""
    return {"compliance_result": "pass"}


def risk_assess(state: PipelineState) -> dict[str, Any]:
    """Assess risk. Hidden: risk_model_version determines score calibration."""
    return {"risk_score": 0.12}


# ── Build the LangGraph graph ───────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)
    graph.add_node("price_fetch", price_fetch)
    graph.add_node("currency_convert", currency_convert)
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("risk_assess", risk_assess)

    graph.set_entry_point("price_fetch")
    graph.add_edge("price_fetch", "currency_convert")
    graph.add_edge("currency_convert", "compliance_check")
    graph.add_edge("compliance_check", "risk_assess")
    graph.add_edge("risk_assess", END)

    return graph


# ── Extract Bulla composition from LangGraph topology ────────────────
#
# This manual annotation step maps each LangGraph node to a Bulla
# ToolSpec with internal_state and observable_schema.  The convention
# dimensions on each edge are the semantic contracts that LangGraph
# cannot validate (timezone, rounding_mode, jurisdiction, etc.).
#
# `bulla gauge` (Sprint 17) will automate this annotation by
# introspecting MCP tool schemas at runtime.

TOOL_ANNOTATIONS: dict[str, dict[str, list[str]]] = {
    "price_fetch": {
        "internal_state": ["amount", "currency", "rounding_mode"],
        "observable_schema": ["amount", "currency"],
    },
    "currency_convert": {
        "internal_state": [
            "converted_amount", "rounding_mode", "exchange_rate_source",
        ],
        "observable_schema": ["converted_amount"],
    },
    "compliance_check": {
        "internal_state": [
            "compliance_result", "jurisdiction", "threshold_currency",
        ],
        "observable_schema": ["compliance_result"],
    },
    "risk_assess": {
        "internal_state": [
            "risk_score", "risk_model_version", "confidence_interval",
        ],
        "observable_schema": ["risk_score"],
    },
}

EDGE_CONVENTIONS: list[dict[str, Any]] = [
    {
        "from": "price_fetch",
        "to": "currency_convert",
        "dimensions": [
            {"name": "rounding_mode", "from_field": "rounding_mode",
             "to_field": "rounding_mode"},
        ],
    },
    {
        "from": "currency_convert",
        "to": "compliance_check",
        "dimensions": [
            {"name": "amount_rounding", "from_field": "converted_amount",
             "to_field": "threshold_currency"},
        ],
    },
    {
        "from": "compliance_check",
        "to": "risk_assess",
        "dimensions": [
            {"name": "regulatory_framework", "from_field": "jurisdiction",
             "to_field": "risk_model_version"},
        ],
    },
]


def extract_composition() -> Composition:
    """Build a Bulla Composition from the annotated LangGraph graph."""
    tools = tuple(
        ToolSpec(
            name=name,
            internal_state=tuple(ann["internal_state"]),
            observable_schema=tuple(ann["observable_schema"]),
        )
        for name, ann in TOOL_ANNOTATIONS.items()
    )

    edges = tuple(
        Edge(
            from_tool=e["from"],
            to_tool=e["to"],
            dimensions=tuple(
                SemanticDimension(
                    name=d["name"],
                    from_field=d["from_field"],
                    to_field=d["to_field"],
                )
                for d in e["dimensions"]
            ),
        )
        for e in EDGE_CONVENTIONS
    )

    return Composition(name="langgraph-trade-pipeline", tools=tools, edges=edges)


# ── Main: run graph then diagnose ────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("LangGraph + Bulla Integration Demo")
    print("=" * 60)

    # 1. Build and compile the LangGraph graph
    graph = build_graph()
    app = graph.compile()
    print("\n[1] LangGraph graph compiled successfully.")
    print("    Nodes: price_fetch -> currency_convert -> compliance_check -> risk_assess")

    # 2. Run the graph (LangGraph validates schemas -- all pass)
    result = app.invoke({"trade_id": "T-001"})
    print(f"\n[2] LangGraph execution result:")
    print(f"    {json.dumps(result, indent=6)}")
    print("    LangGraph: all schemas validated, no errors.")

    # 3. Extract Bulla composition and diagnose
    comp = extract_composition()
    diag = diagnose(comp)

    print(f"\n[3] Bulla coherence diagnostic:")
    print(f"    Coherence fee:     {diag.coherence_fee}")
    print(f"    Blind spots:       {len(diag.blind_spots)}")
    print(f"    Bridges suggested: {len(diag.bridges)}")
    print(f"    H1 (observable):   {diag.h1_obs}")
    print(f"    H1 (full):         {diag.h1_full}")

    if diag.blind_spots:
        print(f"\n[4] Hidden conventions LangGraph cannot see:")
        for bs in diag.blind_spots:
            side = []
            if bs.from_hidden:
                side.append(f"{bs.from_tool}.{bs.from_field} (hidden)")
            if bs.to_hidden:
                side.append(f"{bs.to_tool}.{bs.to_field} (hidden)")
            print(f"    - {bs.dimension}: {bs.edge}  [{', '.join(side)}]")

    # 4. Minimum disclosure set
    from bulla.diagnostic import minimum_disclosure_set
    mds = minimum_disclosure_set(comp)
    print(f"\n[5] Minimum disclosure set ({len(mds)} fields to expose):")
    for tool, field in mds:
        print(f"    - {tool}.{field}")

    print(f"\n[6] Summary:")
    print(f"    LangGraph validates: schemas (types, required fields)")
    print(f"    Bulla catches:       {diag.coherence_fee} hidden convention dimensions")
    print(f"    Fix:                 expose {len(mds)} fields to reach fee = 0")
    print("=" * 60)


if __name__ == "__main__":
    main()
