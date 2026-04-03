"""Trace gap investigation: verify it equals weighted blind-spot count.

The Frobenius trace gap trace(L_full) - trace(L_obs) = ||delta_full||^2_F
- ||delta_obs||^2_F is the sum of squared entries in hidden columns of
the full coboundary matrix.  Since entries are +/-1, this equals the
total count of hidden-endpoint instances across blind spots.

This test verifies:
1. trace_gap == sum(from_hidden + to_hidden) for all blind spots
2. fee > 0  =>  trace_gap > 0 (necessary condition)
3. trace_gap > 0  =/=>  fee > 0 (counterexample)
4. trace_gap is therefore NOT a genuine spectral refinement

The trace gap is NOT added to the Diagnostic dataclass; this test
closes the investigation.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from bulla.coboundary import build_coboundary
from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition

COMPOSITIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "bulla" / "compositions"
BUNDLED_YAMLS = sorted(COMPOSITIONS_DIR.glob("*.yaml"))
BUNDLED_COMPS = [load_composition(path=p) for p in BUNDLED_YAMLS]


def _trace_gap(comp: Composition) -> int:
    """Compute trace(L_full) - trace(L_obs) as sum of squared entries difference.

    Since all coboundary entries are 0 or +/-1, ||delta||^2_F equals the
    count of nonzero entries.  The trace gap is the number of nonzero
    entries in hidden columns (columns in delta_full but not delta_obs).
    """
    delta_obs, _, _ = build_coboundary(
        list(comp.tools), list(comp.edges), use_internal=False
    )
    delta_full, _, _ = build_coboundary(
        list(comp.tools), list(comp.edges), use_internal=True
    )

    def frobenius_sq(mat: list[list[Fraction]]) -> int:
        return sum(int(entry * entry) for row in mat for entry in row)

    return frobenius_sq(delta_full) - frobenius_sq(delta_obs)


def _blind_spot_endpoint_count(comp: Composition) -> int:
    """Count hidden endpoints across all blind spots."""
    diag = diagnose(comp)
    return sum(
        int(bs.from_hidden) + int(bs.to_hidden)
        for bs in diag.blind_spots
    )


class TestTraceGapEqualsBlindsSpotCount:
    """Verify trace_gap == weighted blind-spot count for all bundled compositions."""

    @pytest.mark.parametrize("comp", BUNDLED_COMPS, ids=lambda c: c.name)
    def test_trace_gap_equals_endpoint_count(self, comp: Composition):
        tg = _trace_gap(comp)
        bs_count = _blind_spot_endpoint_count(comp)
        assert tg == bs_count, (
            f"{comp.name}: trace_gap={tg} != endpoint_count={bs_count}"
        )

    @pytest.mark.parametrize("comp", BUNDLED_COMPS, ids=lambda c: c.name)
    def test_fee_positive_implies_trace_gap_positive(self, comp: Composition):
        diag = diagnose(comp)
        tg = _trace_gap(comp)
        if diag.coherence_fee > 0:
            assert tg > 0, (
                f"{comp.name}: fee={diag.coherence_fee} > 0 but trace_gap={tg}"
            )


class TestTraceGapCounterexample:
    """Verify that trace_gap > 0 does NOT imply fee > 0."""

    def test_fee_zero_trace_gap_positive(self):
        """Single edge, one dimension, from_field hidden, to_field observable.

        The hidden column [-1] and observable column [+1] are linearly
        dependent (same row, opposite signs), so rank_full = rank_obs = 1,
        fee = 0.  But the hidden column has one nonzero entry, so
        trace_gap = 1.
        """
        tools = (
            ToolSpec("A", internal_state=("x", "y"), observable_schema=("y",)),
            ToolSpec("B", internal_state=("a",), observable_schema=("a",)),
        )
        edges = (
            Edge(
                from_tool="A",
                to_tool="B",
                dimensions=(
                    SemanticDimension(name="d1", from_field="x", to_field="a"),
                ),
            ),
        )
        comp = Composition("counterexample", tools, edges)

        diag = diagnose(comp)
        tg = _trace_gap(comp)

        assert diag.coherence_fee == 0, f"Expected fee=0, got {diag.coherence_fee}"
        assert tg == 1, f"Expected trace_gap=1, got {tg}"
        assert len(diag.blind_spots) == 1
        assert diag.blind_spots[0].from_hidden is True
        assert diag.blind_spots[0].to_hidden is False


class TestTraceGapDistinguishability:
    """Check if trace_gap distinguishes compositions with the same fee."""

    def test_same_fee_different_trace_gap(self):
        """Two compositions both with fee=1 but different trace gaps.

        Comp A (classic chain A->B->C): A hides x, B transparent, C hides z.
        fee=1, 2 blind spot endpoints => trace_gap=2.

        Comp B (chain with extra hidden dim): A hides x AND p, B transparent,
        C hides z. fee=1, 3 blind spot endpoints => trace_gap=3.
        """
        tools_a = (
            ToolSpec("A", ("x", "p"), ("p",)),
            ToolSpec("B", ("y",), ("y",)),
            ToolSpec("C", ("z", "r"), ("r",)),
        )
        edges_a = (
            Edge("A", "B", (SemanticDimension("d1", "x", "y"),)),
            Edge("B", "C", (SemanticDimension("d2", "y", "z"),)),
        )
        comp_a = Composition("chain_2hidden", tools_a, edges_a)

        tools_b = (
            ToolSpec("A", ("x", "p"), ()),
            ToolSpec("B", ("y", "q"), ("y", "q")),
            ToolSpec("C", ("z",), ()),
        )
        edges_b = (
            Edge("A", "B", (
                SemanticDimension("d1", "x", "y"),
                SemanticDimension("d3", "p", "q"),
            )),
            Edge("B", "C", (SemanticDimension("d2", "y", "z"),)),
        )
        comp_b = Composition("chain_3hidden", tools_b, edges_b)

        diag_a = diagnose(comp_a)
        diag_b = diagnose(comp_b)
        tg_a = _trace_gap(comp_a)
        tg_b = _trace_gap(comp_b)

        assert diag_a.coherence_fee == 1
        assert diag_b.coherence_fee == 1
        assert tg_a == 2
        assert tg_b == 3
        assert tg_a != tg_b, "Same fee, different trace gap — but trivially so"
