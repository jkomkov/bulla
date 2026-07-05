"""Pure-Python coboundary operator and rank computation.

The coboundary matrix δ₀: C⁰ → C¹ is the fundamental algebraic object
in Bulla's measurement layer. It encodes how semantic dimensions flow
between tools: each row is an (edge, dimension) pair, each column is a
(tool, field) pair, and the sign convention is -1 at the source tool,
+1 at the target tool — the oriented boundary of a data flow.

Bulla builds two coboundary matrices for every composition: one using
only observable fields (δ_obs) and one using all fields including
internal state (δ_full). The rank difference rank(δ_full) - rank(δ_obs)
is the coherence fee — the number of independent semantic mismatch
dimensions invisible to pairwise verification.

The *typical* coboundary is a signed incidence matrix — each row has at
most one +1 and one −1 — hence totally unimodular (Schrijver, Theory of
Linear and Integer Programming, Thm 19.3): all minors in {−1, 0, +1} and
rank field-independent, so the fee is the same over Q, F_2, or any field.
``matrix_rank`` exploits this with a GF(2) bit-rank that is ~600× faster
than Fraction Gaussian elimination on large compositions.

CAVEAT (do not over-read the TU claim): coupled / multi-field compositions
produce coboundaries that are NOT signed-incidence and NOT TU, where the
GF(2) rank genuinely differs from the Q rank. ``matrix_rank`` therefore
CHECKS ``_is_signed_incidence`` per matrix and falls back to the exact Q
oracle (``matrix_rank_exact``) off that regime — the field-independence is
verified per call, never assumed (an earlier blanket-TU shortcut shipped
wrong fees). See ``test_rank_equivalence``.
"""

from __future__ import annotations

from fractions import Fraction

from bulla.model import Edge, ToolSpec


def matrix_rank_exact(matrix: list[list[Fraction]]) -> int:
    """The exact ℚ rank via Gaussian elimination over ``Fraction`` — the REFERENCE ORACLE.

    Correct for any matrix, but the rational pivot fill-in is super-polynomial (this was the
    audit's 22-minute hot path: 5.6e9 calls / 5e8 ``Fraction`` allocations on a 57-server
    registry). Production code calls ``matrix_rank`` (the GF(2) fast path); the two are
    asserted bit-for-bit equal on the corpus by ``test_rank_equivalence``. Kept as the
    oracle so the equivalence is *checkable*, not merely claimed."""
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


def _is_signed_incidence(matrix: list[list[Fraction]]) -> bool:
    """True iff every row has entries in {−1, 0, +1} with at most one +1 and one −1 — a
    signed incidence matrix of a directed multigraph, hence **totally unimodular**
    (Schrijver Thm 19.3), hence its GF(2) rank equals its ℚ rank. O(cells), and it is the
    PER-MATRIX gate for the fast path: the field-independence is checked here, never
    assumed. (Coupled / multi-field coboundaries fall outside this regime — there GF(2)
    would return a WRONG rank, so they take the exact path.)"""
    for row in matrix:
        pos = neg = 0
        for x in row:
            if x.denominator != 1:
                return False
            v = x.numerator
            if v == 1:
                pos += 1
            elif v == -1:
                neg += 1
            elif v != 0:
                return False
        if pos > 1 or neg > 1:
            return False
    return True


def _rank_gf2(matrix: list[list[Fraction]]) -> int:
    """Rank over GF(2) via an XOR linear basis (each row packed into one int). Valid ONLY
    on a totally-unimodular matrix (where rank is field-independent); callers must gate on
    ``_is_signed_incidence``. O(rows × rank) bit ops, no ``Fraction`` fill-in."""
    basis: dict[int, int] = {}
    rank = 0
    for row in matrix:
        cur = 0
        for j, v in enumerate(row):
            if v.numerator % 2:      # entry odd over ℤ ⇔ |v| == 1 (the GF(2) image)
                cur |= 1 << j
        while cur:
            hb = cur.bit_length() - 1
            pv = basis.get(hb)
            if pv is None:
                basis[hb] = cur
                rank += 1
                break
            cur ^= pv
    return rank


def matrix_rank(matrix: list[list[Fraction]]) -> int:
    """Exact rank — the DEFAULT, called wherever a coboundary rank (hence the fee) is needed.

    For a signed-incidence (totally unimodular) coboundary the rank is *field-independent*
    (Schrijver Thm 19.3), so it is computed over GF(2): O(rows × rank) bit ops, no
    super-polynomial rational fill-in (the audit's former 22-minute hot path). OFF that
    regime — a non-TU / coupled matrix, where GF(2) ≠ ℚ — it falls back to the exact ``Q``
    path, so the answer is **always** ``matrix_rank_exact``'s answer, just fast where the
    structure permits.

    The regime is **checked per matrix, never assumed** (an earlier "TU everywhere" shortcut
    shipped wrong fees on coupled compositions). ``test_rank_equivalence`` asserts
    ``matrix_rank == matrix_rank_exact`` across the corpus; recomputability is preserved and
    strengthened — a verifier running any field-correct rank recomputes the same deed.
    """
    if not matrix or not matrix[0]:
        return 0
    if _is_signed_incidence(matrix):
        return _rank_gf2(matrix)
    return matrix_rank_exact(matrix)


def _vertex_basis(
    tools: list[ToolSpec],
    use_internal: bool,
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], int]]:
    """Build the C⁰ basis: one column per (tool, field) pair.

    When *use_internal* is True, includes all fields (internal_state);
    when False, includes only observable_schema. Returns the ordered
    basis list and a fast lookup index mapping (tool, field) → column
    index.
    """
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
    """Build the C¹ basis: one row per (edge, dimension) pair.

    Each edge may carry multiple semantic dimensions (e.g., an edge
    between a payment tool and an invoice tool might carry 'amount',
    'currency', and 'date' dimensions). Returns the ordered basis
    list and a fast lookup index.
    """
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

    # Structural invariant: δ is a signed incidence matrix.
    # Each row has at most one +1 and at most one −1 entry.
    # This guarantees total unimodularity and field-independent rank.
    # See papers/hierarchical-fee/correspondence_theorem.md.
    assert all(
        sum(1 for x in row if x > 0) <= 1
        and sum(1 for x in row if x < 0) <= 1
        for row in delta
    ), "Signed incidence invariant violated: row with >1 positive or >1 negative entry"

    return delta, v_basis, e_basis
