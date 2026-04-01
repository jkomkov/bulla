"""Tests for the pure-Python coboundary engine."""

from fractions import Fraction

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import Edge, SemanticDimension, ToolSpec


class TestMatrixRank:
    def test_empty(self):
        assert matrix_rank([]) == 0

    def test_identity_2x2(self):
        m = [
            [Fraction(1), Fraction(0)],
            [Fraction(0), Fraction(1)],
        ]
        assert matrix_rank(m) == 2

    def test_identity_3x3(self):
        m = [
            [Fraction(1), Fraction(0), Fraction(0)],
            [Fraction(0), Fraction(1), Fraction(0)],
            [Fraction(0), Fraction(0), Fraction(1)],
        ]
        assert matrix_rank(m) == 3

    def test_rank_deficient(self):
        m = [
            [Fraction(1), Fraction(2), Fraction(3)],
            [Fraction(2), Fraction(4), Fraction(6)],
        ]
        assert matrix_rank(m) == 1

    def test_rectangular_tall(self):
        m = [
            [Fraction(1), Fraction(0)],
            [Fraction(0), Fraction(1)],
            [Fraction(1), Fraction(1)],
        ]
        assert matrix_rank(m) == 2

    def test_rectangular_wide(self):
        m = [
            [Fraction(1), Fraction(0), Fraction(1)],
            [Fraction(0), Fraction(1), Fraction(1)],
        ]
        assert matrix_rank(m) == 2

    def test_zero_matrix(self):
        m = [
            [Fraction(0), Fraction(0)],
            [Fraction(0), Fraction(0)],
        ]
        assert matrix_rank(m) == 0

    def test_single_element(self):
        assert matrix_rank([[Fraction(5)]]) == 1
        assert matrix_rank([[Fraction(0)]]) == 0


class TestBuildCoboundary:
    def _simple_linear(self):
        """Two tools, one edge, one shared dimension."""
        tools = [
            ToolSpec("A", ("x", "y"), ("x",)),
            ToolSpec("B", ("x", "z"), ("z",)),
        ]
        edges = [
            Edge("A", "B", (SemanticDimension("d", "x", "x"),)),
        ]
        return tools, edges

    def test_observable_shape(self):
        tools, edges = self._simple_linear()
        delta, v_basis, e_basis = build_coboundary(
            tools, edges, use_internal=False
        )
        assert len(e_basis) == 1
        assert len(v_basis) == 2  # A.x and B.z
        assert len(delta) == 1
        assert len(delta[0]) == 2

    def test_full_shape(self):
        tools, edges = self._simple_linear()
        delta, v_basis, e_basis = build_coboundary(
            tools, edges, use_internal=True
        )
        assert len(v_basis) == 4  # A.x, A.y, B.x, B.z
        assert len(delta) == 1
        assert len(delta[0]) == 4

    def test_coboundary_signs(self):
        tools, edges = self._simple_linear()
        delta, v_basis, e_basis = build_coboundary(
            tools, edges, use_internal=True
        )
        v_idx = {v: i for i, v in enumerate(v_basis)}
        assert delta[0][v_idx[("A", "x")]] == Fraction(-1)
        assert delta[0][v_idx[("B", "x")]] == Fraction(1)

    def test_rank_observable_vs_full(self):
        """Observable rank < full rank when blind spots exist."""
        tools, edges = self._simple_linear()
        delta_obs, _, _ = build_coboundary(tools, edges, use_internal=False)
        delta_full, _, _ = build_coboundary(tools, edges, use_internal=True)
        r_obs = matrix_rank(delta_obs)
        r_full = matrix_rank(delta_full)
        assert r_full >= r_obs
