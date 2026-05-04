"""Witness matroid: thin oracle layer over the witness Gram.

The witness Gram K(G) ∈ Mat_{|H|×|H|}(ℚ) is a symmetric PSD rational matrix
indexed by hidden fields. By Edmonds (1971), the columns of K span a
representable matroid M(K) on the hidden-field ground set, with
independence given by linear independence of the corresponding columns.

This module provides three oracle functions for that matroid:

  - rank_of_columns(K, indices): rank of the K-submatrix on the given columns
  - is_independent(K, indices): whether the column subset is linearly independent
  - is_basis(K, indices): whether the subset is a maximal independent set,
    equivalently |indices| == rank(K) and is_independent(K, indices)

These are wrappers over the existing matrix_rank implementation in
bulla.coboundary; they exist to give a clean matroid-theoretic vocabulary
to code that previously had to compute submatrix ranks ad-hoc.

The existing weighted_greedy_repair function in bulla.witness_geometry
already implements the matroid-greedy on this matroid (per its docstring,
citing Edmonds 1971). The verification tests in
bulla/tests/test_witness_matroid.py confirm this empirically by:

  1. Enumerating ALL bases of M(K) on small examples (rank ≤ 4)
  2. Computing minimum-cost basis via exhaustive search
  3. Asserting weighted_greedy_repair returns a basis of equal cost

No new mathematical structure is invented here; this module is verification
scaffolding. See bulla/docs/MATROID-STRUCTURE.md for the formal statement
of which matroid Bulla is computing on, and the relationship between
weighted_greedy_repair (matroid basis selection) and minimum_disclosure_set
(observable-coboundary-rank augmentation).
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from itertools import combinations

from bulla.coboundary import matrix_rank


def rank_of_columns(
    K: Sequence[Sequence[Fraction]],
    indices: Sequence[int],
) -> int:
    """Return the rank of the K-submatrix restricted to the given columns.

    For a symmetric PSD K, this equals the rank of the rows-by-given-columns
    submatrix, which equals the dimension of the column-span of those columns.
    """
    if not indices:
        return 0
    n_rows = len(K)
    submat = [[K[r][c] for c in indices] for r in range(n_rows)]
    return matrix_rank(submat)


def is_independent(
    K: Sequence[Sequence[Fraction]],
    indices: Sequence[int],
) -> bool:
    """Return True iff the given columns are linearly independent in K.

    Equivalently, |indices| equals rank_of_columns(K, indices). The empty
    set is trivially independent.
    """
    return rank_of_columns(K, indices) == len(indices)


def is_basis(
    K: Sequence[Sequence[Fraction]],
    indices: Sequence[int],
) -> bool:
    """Return True iff the given columns form a basis of M(K).

    A basis is a maximal independent set: |indices| equals rank(K) and the
    columns are linearly independent.
    """
    target = matrix_rank(_as_matrix(K))
    return len(indices) == target and is_independent(K, indices)


def all_bases(
    K: Sequence[Sequence[Fraction]],
) -> list[tuple[int, ...]]:
    """Enumerate ALL bases of the column matroid M(K) on |columns(K)|.

    Returns a sorted list of tuples (in lexicographic order on indices),
    each tuple representing one basis as a set of column indices.

    Intended for verification on small examples (matroid rank ≤ ~6); the
    enumeration is C(n, rank) which grows quickly. Raises ValueError if
    n choose rank exceeds 10000.
    """
    n = _ncols(K)
    target = matrix_rank(_as_matrix(K))
    from math import comb
    if target > n:
        return []
    if comb(n, target) > 10_000:
        raise ValueError(
            f"all_bases would enumerate C({n}, {target}) = {comb(n, target)} "
            f"subsets; refusing to run beyond 10000."
        )
    bases: list[tuple[int, ...]] = []
    for subset in combinations(range(n), target):
        if is_independent(K, subset):
            bases.append(subset)
    return bases


def min_cost_basis_exhaustive(
    K: Sequence[Sequence[Fraction]],
    costs: Sequence[Fraction],
) -> tuple[tuple[int, ...], Fraction]:
    """Find the minimum-cost basis by exhaustive enumeration.

    Returns (basis_indices, total_cost). Used for verification of
    weighted_greedy_repair's correctness on small examples.

    Raises ValueError on the same threshold as all_bases.
    """
    bases = all_bases(K)
    if not bases:
        return ((), Fraction(0))
    best = min(bases, key=lambda b: sum(costs[i] for i in b))
    return (best, sum(costs[i] for i in best))


# ─────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────


def _ncols(K: Sequence[Sequence[Fraction]]) -> int:
    if not K:
        return 0
    return len(K[0])


def _as_matrix(K: Sequence[Sequence[Fraction]]) -> list[list[Fraction]]:
    return [list(row) for row in K]


__all__ = [
    "rank_of_columns",
    "is_independent",
    "is_basis",
    "all_bases",
    "min_cost_basis_exhaustive",
]
