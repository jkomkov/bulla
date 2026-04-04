"""Sprint 25 tests: The Obligation Wire (Bulla v0.25.0).

Covers:
- BoundaryObligation.source_edge + to_dict()
- WitnessReceipt.boundary_obligations conditional hash/serialization
- boundary_obligations_from_decomposition()
- check_obligations() three-way classification
- merge_receipt_obligations() additive accumulation
- Obligation propagation across chain (unmet parent + own new)
- Backward compatibility (pre-v0.25.0 receipts verify unchanged)
- Obligation lifecycle demo smoke test
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    check_obligations,
    decompose_fee,
    diagnose,
)
from bulla.merge import merge_receipt_obligations
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    SemanticDimension,
    ToolSpec,
)
from bulla.witness import verify_receipt_integrity, witness


def _two_server_comp() -> tuple[Composition, list[frozenset[str]]]:
    """Two-server composition with boundary_fee > 0."""
    tools = (
        ToolSpec("storage__read", ("path", "offset"), ("path",)),
        ToolSpec("storage__write", ("path", "content"), ("path", "content")),
        ToolSpec("api__fetch", ("url", "offset"), ("url",)),
        ToolSpec("api__search", ("query", "limit"), ("query", "limit")),
    )
    edges = (
        Edge(
            "storage__read", "api__fetch",
            (
                SemanticDimension("path_type", "path", "url"),
                SemanticDimension("pagination", "offset", "offset"),
            ),
        ),
    )
    comp = Composition("test-two-server", tools, edges)
    partition = [
        frozenset(["storage__read", "storage__write"]),
        frozenset(["api__fetch", "api__search"]),
    ]
    return comp, partition


class TestBoundaryObligationModel:
    def test_source_edge_default_empty(self):
        obl = BoundaryObligation("group", "dim", "field")
        assert obl.source_edge == ""

    def test_source_edge_set(self):
        obl = BoundaryObligation("group", "dim", "field", source_edge="A -> B")
        assert obl.source_edge == "A -> B"

    def test_to_dict_without_source_edge(self):
        obl = BoundaryObligation("group", "dim", "field")
        d = obl.to_dict()
        assert d == {"placeholder_tool": "group", "dimension": "dim", "field": "field"}
        assert "source_edge" not in d

    def test_to_dict_with_source_edge(self):
        obl = BoundaryObligation("group", "dim", "field", source_edge="A -> B")
        d = obl.to_dict()
        assert d["source_edge"] == "A -> B"

    def test_frozen(self):
        obl = BoundaryObligation("group", "dim", "field")
        with pytest.raises(AttributeError):
            obl.field = "other"


class TestBoundaryObligationsOnReceipt:
    def test_none_obligations_omitted_from_dict(self):
        comp, _ = _two_server_comp()
        diag = diagnose(comp)
        receipt = witness(diag, comp, boundary_obligations=None)
        d = receipt.to_dict()
        assert "boundary_obligations" not in d

    def test_obligations_included_in_dict(self):
        comp, _ = _two_server_comp()
        diag = diagnose(comp)
        obls = (BoundaryObligation("g", "d", "f", "edge"),)
        receipt = witness(diag, comp, boundary_obligations=obls)
        d = receipt.to_dict()
        assert "boundary_obligations" in d
        assert d["boundary_obligations"] == [{"placeholder_tool": "g", "dimension": "d", "field": "f", "source_edge": "edge"}]

    def test_receipt_with_obligations_verifies(self):
        comp, _ = _two_server_comp()
        diag = diagnose(comp)
        obls = (
            BoundaryObligation("storage", "pagination", "offset", "A -> B"),
            BoundaryObligation("api", "pagination", "offset", "A -> B"),
        )
        receipt = witness(diag, comp, boundary_obligations=obls)
        assert verify_receipt_integrity(receipt.to_dict())

    def test_different_obligations_produce_different_hashes(self):
        comp, _ = _two_server_comp()
        diag = diagnose(comp)
        obls_a = (BoundaryObligation("g", "d", "f1"),)
        obls_b = (BoundaryObligation("g", "d", "f2"),)
        r_a = witness(diag, comp, boundary_obligations=obls_a)
        r_b = witness(diag, comp, boundary_obligations=obls_b)
        assert r_a.receipt_hash != r_b.receipt_hash

    def test_pre_v025_receipt_integrity_unchanged(self):
        """Pre-v0.25.0 receipts (no boundary_obligations) verify correctly."""
        comp, _ = _two_server_comp()
        diag = diagnose(comp)
        receipt = witness(diag, comp)
        d = receipt.to_dict()
        assert "boundary_obligations" not in d
        assert verify_receipt_integrity(d)


class TestBoundaryObligationsFromDecomposition:
    def test_produces_obligations_when_boundary_fee_positive(self):
        comp, partition = _two_server_comp()
        diag = diagnose(comp)
        decomp = decompose_fee(comp, partition)
        assert decomp.boundary_fee > 0

        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        assert len(obls) > 0

    def test_obligation_fields(self):
        comp, partition = _two_server_comp()
        diag = diagnose(comp)
        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        for obl in obls:
            assert obl.placeholder_tool in ("storage", "api")
            assert obl.dimension == "pagination"
            assert obl.field == "offset"
            assert obl.source_edge != ""

    def test_deduplication(self):
        comp, partition = _two_server_comp()
        diag = diagnose(comp)
        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        keys = [(o.placeholder_tool, o.dimension, o.field) for o in obls]
        assert len(keys) == len(set(keys))

    def test_no_obligations_when_no_blind_spots(self):
        tools = (
            ToolSpec("a__t1", ("x",), ("x",)),
            ToolSpec("b__t2", ("x",), ("x",)),
        )
        edges = (Edge("a__t1", "b__t2", (SemanticDimension("d", "x", "x"),)),)
        comp = Composition("clean", tools, edges)
        partition = [frozenset(["a__t1"]), frozenset(["b__t2"])]
        diag = diagnose(comp)
        assert diag.coherence_fee == 0
        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        assert len(obls) == 0


class TestCheckObligations:
    def test_met_when_field_observable(self):
        obls = (BoundaryObligation("g", "d", "x"),)
        tools = (ToolSpec("t", ("x",), ("x",)),)
        comp = Composition("c", tools, ())
        met, unmet, irr = check_obligations(obls, comp)
        assert len(met) == 1
        assert len(unmet) == 0
        assert len(irr) == 0

    def test_unmet_when_field_internal_only(self):
        obls = (BoundaryObligation("g", "d", "x"),)
        tools = (ToolSpec("t", ("x",), ()),)
        comp = Composition("c", tools, ())
        met, unmet, irr = check_obligations(obls, comp)
        assert len(met) == 0
        assert len(unmet) == 1
        assert len(irr) == 0

    def test_irrelevant_when_field_absent(self):
        obls = (BoundaryObligation("g", "d", "x"),)
        tools = (ToolSpec("t", ("y",), ("y",)),)
        comp = Composition("c", tools, ())
        met, unmet, irr = check_obligations(obls, comp)
        assert len(met) == 0
        assert len(unmet) == 0
        assert len(irr) == 1

    def test_mixed_classification(self):
        obls = (
            BoundaryObligation("g", "d1", "a"),
            BoundaryObligation("g", "d2", "b"),
            BoundaryObligation("g", "d3", "c"),
        )
        tools = (
            ToolSpec("t1", ("a", "b"), ("a",)),
            ToolSpec("t2", ("d",), ("d",)),
        )
        comp = Composition("c", tools, ())
        met, unmet, irr = check_obligations(obls, comp)
        assert len(met) == 1
        assert met[0].field == "a"
        assert len(unmet) == 1
        assert unmet[0].field == "b"
        assert len(irr) == 1
        assert irr[0].field == "c"


class TestMergeReceiptObligations:
    def test_accumulation(self):
        r1 = {
            "boundary_obligations": [
                {"placeholder_tool": "g1", "dimension": "d1", "field": "f1", "source_edge": "e1"},
            ],
        }
        r2 = {
            "boundary_obligations": [
                {"placeholder_tool": "g2", "dimension": "d2", "field": "f2", "source_edge": "e2"},
            ],
        }
        result = merge_receipt_obligations([r1, r2])
        assert result is not None
        assert len(result) == 2

    def test_deduplication_keeps_first(self):
        r1 = {
            "boundary_obligations": [
                {"placeholder_tool": "g", "dimension": "d", "field": "f", "source_edge": "edge-1"},
            ],
        }
        r2 = {
            "boundary_obligations": [
                {"placeholder_tool": "g", "dimension": "d", "field": "f", "source_edge": "edge-2"},
            ],
        }
        result = merge_receipt_obligations([r1, r2])
        assert result is not None
        assert len(result) == 1
        assert result[0].source_edge == "edge-1"

    def test_none_when_no_obligations(self):
        r1 = {"inline_dimensions": {"dimensions": {}}}
        r2 = {"inline_dimensions": {"dimensions": {}}}
        result = merge_receipt_obligations([r1, r2])
        assert result is None

    def test_mixed_with_and_without_obligations(self):
        r1 = {
            "boundary_obligations": [
                {"placeholder_tool": "g", "dimension": "d", "field": "f"},
            ],
        }
        r2 = {"inline_dimensions": {"dimensions": {}}}
        result = merge_receipt_obligations([r1, r2])
        assert result is not None
        assert len(result) == 1


class TestObligationPropagation:
    """Test the full propagation rule: receipt obligations = unmet parent + own new."""

    def test_propagation_across_chain(self):
        comp_a, partition_a = _two_server_comp()
        diag_a = diagnose(comp_a)
        obls_a = boundary_obligations_from_decomposition(comp_a, partition_a, diag_a)
        assert len(obls_a) > 0

        receipt_a = witness(diag_a, comp_a, boundary_obligations=obls_a)

        tools_b = (
            ToolSpec("api__fetch", ("url", "offset"), ("url",)),
            ToolSpec("render__view", ("template", "offset"), ("template", "offset")),
        )
        edges_b = (
            Edge("api__fetch", "render__view",
                 (SemanticDimension("layout", "url", "template"),)),
        )
        comp_b = Composition("agent-b", tools_b, edges_b)

        met, unmet, irr = check_obligations(obls_a, comp_b)
        assert len(met) + len(unmet) + len(irr) == len(obls_a)
        assert len(met) > 0

    def test_receipt_chain_carries_obligations(self):
        comp_a, partition_a = _two_server_comp()
        diag_a = diagnose(comp_a)
        obls_a = boundary_obligations_from_decomposition(comp_a, partition_a, diag_a)

        receipt_a = witness(diag_a, comp_a, boundary_obligations=obls_a)
        receipt_a_dict = receipt_a.to_dict()

        assert "boundary_obligations" in receipt_a_dict
        assert len(receipt_a_dict["boundary_obligations"]) == len(obls_a)

        reconstructed = tuple(
            BoundaryObligation(
                o["placeholder_tool"], o["dimension"], o["field"],
                o.get("source_edge", ""),
            )
            for o in receipt_a_dict["boundary_obligations"]
        )
        assert len(reconstructed) == len(obls_a)


class TestObligationDemo:
    def test_demo_runs_without_error(self):
        result = subprocess.run(
            [sys.executable, "scripts/run_obligation_demo.py"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"Demo failed:\n{result.stdout}\n{result.stderr}"
        assert "Obligation propagation:" in result.stdout
        assert "VALID" in result.stdout
        assert "BROKEN" not in result.stdout
