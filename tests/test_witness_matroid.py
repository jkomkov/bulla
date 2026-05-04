"""Verification: Bulla's weighted_greedy_repair is the matroid-greedy on M(K).

These tests do NOT prove a new mathematical result. They verify that the
existing implementation in bulla.witness_geometry.weighted_greedy_repair
(which claims, per its docstring citing Edmonds 1971, to be the matroid
greedy on the column matroid of the witness Gram K) actually delivers
on that claim.

Strategy:
  1. Build small explicit witness Gram matrices with known matroid structure
  2. Enumerate all bases via bulla.witness_matroid.all_bases
  3. Compute the minimum-cost basis exhaustively
  4. Assert weighted_greedy_repair returns a basis of equal cost

This is a regression-test layer pinning down the matroid semantics of
Bulla's existing greedy-repair algorithm. Failures here would indicate
either a bug in greedy_repair or a misalignment between the implementation
and the matroid framework it claims to realize.
"""

from fractions import Fraction

import pytest

from bulla.witness_geometry import weighted_greedy_repair
from bulla.witness_matroid import (
    all_bases,
    is_basis,
    is_independent,
    min_cost_basis_exhaustive,
    rank_of_columns,
)


F = Fraction


def _gram(M: list[list[F]]) -> list[list[F]]:
    """Compute K = M^T M for a 2D rational matrix M.

    The witness Gram is symmetric PSD by construction; tests construct K
    via this helper so the K matrices are valid witness Grams (square,
    indexed by hidden-field columns)."""
    if not M or not M[0]:
        return []
    rows = len(M)
    cols = len(M[0])
    return [
        [
            sum(M[r][i] * M[r][j] for r in range(rows))
            for j in range(cols)
        ]
        for i in range(cols)
    ]


# ─────────────────────────────────────────────────────────────────
# Oracle correctness: rank_of_columns, is_independent, is_basis
# ─────────────────────────────────────────────────────────────────


class TestRankOracle:
    def test_empty_columns_rank_zero(self):
        K = [[F(1), F(0)], [F(0), F(1)]]
        assert rank_of_columns(K, []) == 0

    def test_single_column(self):
        K = [[F(1), F(0)], [F(0), F(1)]]
        assert rank_of_columns(K, [0]) == 1
        assert rank_of_columns(K, [1]) == 1

    def test_two_independent_columns(self):
        K = [[F(1), F(0)], [F(0), F(1)]]
        assert rank_of_columns(K, [0, 1]) == 2

    def test_dependent_columns(self):
        # Two identical columns of the underlying matrix produce a Gram
        # K = M^T M where M = [[1, 1]], giving K = [[1, 1], [1, 1]] (rank 1).
        K = _gram([[F(1), F(1)]])
        assert rank_of_columns(K, [0, 1]) == 1


class TestIsIndependent:
    def test_empty_is_independent(self):
        K = [[F(1)]]
        assert is_independent(K, []) is True

    def test_single_nonzero(self):
        K = [[F(1)]]
        assert is_independent(K, [0]) is True

    def test_dependent_pair(self):
        # Underlying M = [[1, 2]] has column 1 = 2 * column 0; K = [[1, 2], [2, 4]]
        K = _gram([[F(1), F(2)]])
        assert is_independent(K, [0, 1]) is False

    def test_three_indep_in_3d(self):
        K = [
            [F(1), F(0), F(0)],
            [F(0), F(1), F(0)],
            [F(0), F(0), F(1)],
        ]
        assert is_independent(K, [0, 1, 2]) is True


class TestIsBasis:
    def test_full_set_is_basis_when_full_rank(self):
        K = [[F(1), F(0)], [F(0), F(1)]]
        assert is_basis(K, [0, 1]) is True

    def test_subset_not_basis(self):
        K = [[F(1), F(0)], [F(0), F(1)]]
        assert is_basis(K, [0]) is False  # rank 2, but |subset|=1

    def test_independent_subset_below_rank_is_not_basis(self):
        K = [
            [F(1), F(0), F(0)],
            [F(0), F(1), F(0)],
            [F(0), F(0), F(1)],
        ]
        assert is_basis(K, [0, 1]) is False  # rank 3, |subset|=2


# ─────────────────────────────────────────────────────────────────
# All-bases enumeration sanity
# ─────────────────────────────────────────────────────────────────


class TestAllBases:
    def test_full_rank_diagonal(self):
        K = [[F(1), F(0), F(0)], [F(0), F(1), F(0)], [F(0), F(0), F(1)]]
        bases = all_bases(K)
        assert bases == [(0, 1, 2)]

    def test_uniform_matroid_rank_2_of_3(self):
        # Underlying M = [[1, 0, 1], [0, 1, 1]]: 3 columns in 2D, all pairs
        # independent => U_{2,3}. Gram K = [[1, 0, 1], [0, 1, 1], [1, 1, 2]].
        K = _gram([[F(1), F(0), F(1)], [F(0), F(1), F(1)]])
        bases = all_bases(K)
        # All C(3, 2) = 3 pairs are bases
        assert sorted(bases) == [(0, 1), (0, 2), (1, 2)]

    def test_one_column_dependent(self):
        # M = [[1, 0, 0], [0, 1, 1]]: columns 1 and 2 identical (dependent),
        # column 0 independent. Gram K = [[1, 0, 0], [0, 1, 1], [0, 1, 1]].
        K = _gram([[F(1), F(0), F(0)], [F(0), F(1), F(1)]])
        bases = all_bases(K)
        # Bases of size 2: {0,1} and {0,2}; not {1,2} (dependent)
        assert sorted(bases) == [(0, 1), (0, 2)]


# ─────────────────────────────────────────────────────────────────
# Greedy = matroid-greedy verification
# ─────────────────────────────────────────────────────────────────


class TestGreedyIsMatroidGreedy:
    """The load-bearing tests. weighted_greedy_repair must return a basis
    of M(K) with minimum total cost — verified against exhaustive search."""

    def test_uniform_costs_returns_a_basis(self):
        # K = identity (3-element ground set, full-rank, single basis)
        K = [
            [F(1), F(0), F(0)],
            [F(0), F(1), F(0)],
            [F(0), F(0), F(1)],
        ]
        hidden_basis = [("t", "f1"), ("t", "f2"), ("t", "f3")]
        result = weighted_greedy_repair(K, hidden_basis)
        assert len(result) == 3
        result_indices = tuple(hidden_basis.index(r) for r in result)
        assert is_basis(K, result_indices)

    def test_weighted_picks_cheapest_basis(self):
        # M = [[1, 0, 1], [0, 1, 1]]: 3 columns in 2D, all pairs are bases.
        # Costs make {0, 1} cheapest. Gram K = [[1, 0, 1], [0, 1, 1], [1, 1, 2]].
        K = _gram([[F(1), F(0), F(1)], [F(0), F(1), F(1)]])
        hidden_basis = [("t", "f0"), ("t", "f1"), ("t", "f2")]
        costs = {
            ("t", "f0"): F(1),
            ("t", "f1"): F(1),
            ("t", "f2"): F(10),
        }
        result = weighted_greedy_repair(K, hidden_basis, costs=costs)
        # Should pick {f0, f1} = cost 2, not {f0, f2} = cost 11 or {f1, f2} = 11
        assert sorted(result) == sorted([("t", "f0"), ("t", "f1")])

    def test_greedy_matches_exhaustive_min_cost_basis(self):
        # M = [[1, 0, 1, 2], [0, 1, 1, 1]]: 4 columns in 2D, every pair indep.
        # Gram K = M^T M.
        K = _gram([
            [F(1), F(0), F(1), F(2)],
            [F(0), F(1), F(1), F(1)],
        ])
        hidden_basis = [("t", f"f{i}") for i in range(4)]
        # Try several cost vectors
        cost_vectors = [
            [F(1), F(2), F(3), F(4)],
            [F(5), F(1), F(1), F(2)],
            [F(10), F(10), F(1), F(1)],
            [F(1, 2), F(3, 4), F(1, 5), F(7)],  # rationals
        ]
        for cv in cost_vectors:
            costs = dict(zip(hidden_basis, cv))
            greedy_result = weighted_greedy_repair(K, hidden_basis, costs=costs)
            greedy_cost = sum(costs[r] for r in greedy_result)

            # Exhaustive min-cost basis
            best_indices, best_cost = min_cost_basis_exhaustive(K, cv)
            assert greedy_cost == best_cost, (
                f"Greedy returned cost {greedy_cost} but exhaustive minimum "
                f"is {best_cost} for costs {cv}"
            )

    def test_greedy_handles_loops(self):
        # A loop is a column with K[j,j] = 0 (and entire column zero).
        # Loops are not in any basis. Check greedy correctly excludes them.
        K = [
            [F(1), F(0), F(0)],
            [F(0), F(1), F(0)],
            [F(0), F(0), F(0)],  # column 2 is a loop
        ]
        hidden_basis = [("t", "f0"), ("t", "f1"), ("t", "f2")]
        result = weighted_greedy_repair(K, hidden_basis)
        # Should pick {f0, f1}, never f2 (loop)
        assert ("t", "f2") not in result
        assert len(result) == 2


# ─────────────────────────────────────────────────────────────────
# Boundary case: matroid threshold guard
# ─────────────────────────────────────────────────────────────────


class TestEnumerationGuard:
    def test_all_bases_refuses_huge_enumeration(self):
        # 100 columns in 50D; C(100, 50) >> 10000
        K = [[F(1) if i == j else F(0) for j in range(100)] for i in range(50)]
        # Pad with extra independent columns to make rank 50 with 100 ground elements
        # Actually identity-like 50x100 won't have rank 50; need to add columns
        # For test purposes, build a deliberately-large case
        with pytest.raises(ValueError, match="refusing to run"):
            all_bases(K)
