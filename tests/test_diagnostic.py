"""Tests for the diagnostic engine."""

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _auth_pipeline():
    """Fee=0: all dimensions observable."""
    tools = (
        ToolSpec("auth", ("token", "user_id"), ("token", "user_id")),
        ToolSpec("data", ("payload", "user_id"), ("payload", "user_id")),
    )
    edges = (
        Edge("auth", "data", (SemanticDimension("uid", "user_id", "user_id"),)),
    )
    return Composition("auth", tools, edges)


def _blind_pipeline():
    """Fee>0: day_convention hidden on both sides."""
    tools = (
        ToolSpec("provider", ("prices", "day_conv"), ("prices",)),
        ToolSpec("analysis", ("result", "day_conv"), ("result",)),
    )
    edges = (
        Edge(
            "provider",
            "analysis",
            (SemanticDimension("day_match", "day_conv", "day_conv"),),
        ),
    )
    return Composition("financial", tools, edges)


def _cyclic_pipeline():
    """Cyclic: 3 tools in a triangle."""
    tools = (
        ToolSpec("A", ("x", "hidden_a"), ("x",)),
        ToolSpec("B", ("x", "hidden_a"), ("x",)),
        ToolSpec("C", ("x",), ("x",)),
    )
    edges = (
        Edge("A", "B", (SemanticDimension("d1", "hidden_a", "hidden_a"),)),
        Edge("B", "C", (SemanticDimension("d2", "x", "x"),)),
        Edge("C", "A", (SemanticDimension("d3", "x", "x"),)),
    )
    return Composition("cyclic", tools, edges)


class TestDiagnose:
    def test_zero_fee(self):
        diag = diagnose(_auth_pipeline())
        assert diag.coherence_fee == 0
        assert diag.blind_spots == []
        assert diag.n_unbridged == 0

    def test_nonzero_fee(self):
        diag = diagnose(_blind_pipeline())
        assert diag.coherence_fee > 0
        assert len(diag.blind_spots) == 1
        assert diag.blind_spots[0].dimension == "day_match"
        assert diag.n_unbridged == 1

    def test_bridges_recommended(self):
        diag = diagnose(_blind_pipeline())
        assert len(diag.bridges) >= 1
        assert diag.bridges[0].field == "day_conv"

    def test_bridging_reduces_fee(self):
        diag = diagnose(_blind_pipeline())
        fee_after = diag.h1_after_bridge - diag.h1_full
        assert fee_after < diag.coherence_fee

    def test_cyclic_topology(self):
        diag = diagnose(_cyclic_pipeline())
        assert diag.betti_1 == 1
        assert diag.n_edges == 3
        assert diag.n_tools == 3

    def test_blind_spot_fields(self):
        diag = diagnose(_blind_pipeline())
        bs = diag.blind_spots[0]
        assert bs.from_hidden is True
        assert bs.to_hidden is True
        assert bs.from_field == "day_conv"
        assert bs.to_field == "day_conv"

    def test_n_unbridged_matches_blind_spots(self):
        diag = diagnose(_blind_pipeline())
        assert diag.n_unbridged == len(diag.blind_spots)
