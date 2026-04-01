"""Pure-Python coboundary operator and rank computation.

No numpy dependency. Uses fractions.Fraction for exact arithmetic
on small matrices typical of real compositions (<30 rows, <50 cols).
"""

from __future__ import annotations

from fractions import Fraction

from bulla.model import Edge, ToolSpec


def matrix_rank(matrix: list[list[Fraction]]) -> int:
    """Gaussian elimination to compute rank. Exact arithmetic, no tolerance."""
    if not matrix or not matrix[0]:
        return 0
    rows = [row[:] for row in matrix]
    m = len(rows)
    n = len(rows[0])
    rank = 0
    for col in range(n):
        pivot = None
        for row in range(rank, m):
            if rows[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        scale = rows[rank][col]
        rows[rank] = [x / scale for x in rows[rank]]
        for row in range(m):
            if row != rank and rows[row][col] != 0:
                factor = rows[row][col]
                rows[row] = [
                    rows[row][j] - factor * rows[rank][j] for j in range(n)
                ]
        rank += 1
    return rank


def _vertex_basis(
    tools: list[ToolSpec],
    use_internal: bool,
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], int]]:
    basis: list[tuple[str, str]] = []
    index: dict[tuple[str, str], int] = {}
    for t in tools:
        dims = t.internal_state if use_internal else t.observable_schema
        for d in dims:
            index[(t.name, d)] = len(basis)
            basis.append((t.name, d))
    return basis, index


def _edge_basis(
    edges: list[Edge],
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], int]]:
    basis: list[tuple[str, str]] = []
    index: dict[tuple[str, str], int] = {}
    for edge in edges:
        label = f"{edge.from_tool}\u2192{edge.to_tool}"
        for dim in edge.dimensions:
            index[(label, dim.name)] = len(basis)
            basis.append((label, dim.name))
    return basis, index


def build_coboundary(
    tools: list[ToolSpec],
    edges: list[Edge],
    *,
    use_internal: bool,
) -> tuple[list[list[Fraction]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Build the coboundary matrix delta-0: C^0 -> C^1.

    Returns (matrix, vertex_basis, edge_basis) where matrix is a list of
    lists of Fraction values.
    """
    v_basis, v_idx = _vertex_basis(tools, use_internal)
    e_basis, e_idx = _edge_basis(edges)
    tool_map = {t.name: t for t in tools}

    n_rows = len(e_basis)
    n_cols = len(v_basis)
    delta: list[list[Fraction]] = [
        [Fraction(0)] * n_cols for _ in range(n_rows)
    ]

    for edge in edges:
        label = f"{edge.from_tool}\u2192{edge.to_tool}"
        from_dims = (
            tool_map[edge.from_tool].internal_state
            if use_internal
            else tool_map[edge.from_tool].observable_schema
        )
        to_dims = (
            tool_map[edge.to_tool].internal_state
            if use_internal
            else tool_map[edge.to_tool].observable_schema
        )

        for dim in edge.dimensions:
            row = e_idx[(label, dim.name)]
            if dim.from_field and dim.from_field in from_dims:
                delta[row][v_idx[(edge.from_tool, dim.from_field)]] = Fraction(-1)
            if dim.to_field and dim.to_field in to_dims:
                delta[row][v_idx[(edge.to_tool, dim.to_field)]] = Fraction(1)

    return delta, v_basis, e_basis
