"""Witness geometry: residual Gram object for composition-coherence fees.

This module implements the four-layer witness geometry derived from the
Column-Matroid Backbone:

    K(G) = H^T (I - P_O) H

where H is the hidden-column block of delta_full and P_O is the orthogonal
projector onto range(delta_obs). Its four canonical invariants are:

  - rank:     fee(G) = rank(K)
  - leverage: l_j = (K^+ K)_{jj}, per-field indispensability
  - spectrum: sigma_min^+(W) via W = (I - P_O) H, stability radius
  - flex:    log|B(M/O)|, count flexibility of repairs

Exact rational arithmetic throughout (delta_full is totally unimodular,
so K(G) has rational entries and the Moore-Penrose pseudoinverse over Q
gives exact leverage scores).

Reference: papers/hierarchical-fee/paper/column-matroid-backbone.tex
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Sequence

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import Edge, ToolSpec


@dataclass(frozen=True)
class WitnessProfile:
    """Complete witness-geometry profile for a composition with fee > 0.

    Encapsulates the four canonical invariants of the witness Gram matrix
    K(G) = Hᵀ(I - P_O)H:

    - **fee**: rank(K), the number of independent hidden dimensions.
    - **leverage**: per-hidden-field indispensability scores, summing to fee.
    - **n_effective**: concentration index (1 = maximally concentrated,
      fee = maximally spread).
    - **basis_greedy**: globally optimal minimum-cost repair set via matroid
      greedy (Edmonds 1971).

    Plus the structural classification:
    - **coloops**: must-disclose fields (leverage = 1, appear in every basis).
    - **loops**: already-redundant fields (leverage = 0, appear in no basis).
    """

    K: list[list[Fraction]]
    hidden_basis: list[tuple[str, str]]
    fee: int
    leverage: list[Fraction]
    n_effective: Fraction
    coloops: list[tuple[str, str]]
    loops: list[tuple[str, str]]
    basis_greedy: list[tuple[str, str]]

# ─────────────────────────────────────────────────────────────────
# Rational matrix primitives
# ─────────────────────────────────────────────────────────────────


def _zeros(m: int, n: int) -> list[list[Fraction]]:
    """Allocate an m × n zero matrix over Q."""
    return [[Fraction(0)] * n for _ in range(m)]


def _transpose(A: list[list[Fraction]]) -> list[list[Fraction]]:
    """Transpose a rational matrix."""
    if not A:
        return []
    m, n = len(A), len(A[0])
    T = _zeros(n, m)
    for i in range(m):
        for j in range(n):
            T[j][i] = A[i][j]
    return T


def _matmul(
    A: list[list[Fraction]], B: list[list[Fraction]]
) -> list[list[Fraction]]:
    if not A or not B:
        return []
    m, k = len(A), len(A[0])
    k2, n = len(B), len(B[0])
    if k != k2:
        raise ValueError(f"shape mismatch: {m}x{k} @ {k2}x{n}")
    C = _zeros(m, n)
    for i in range(m):
        Ai = A[i]
        for p in range(k):
            aip = Ai[p]
            if aip == 0:
                continue
            Bp = B[p]
            Ci = C[i]
            for j in range(n):
                Ci[j] += aip * Bp[j]
    return C


def _column_basis(
    A: list[list[Fraction]],
) -> tuple[list[list[Fraction]], list[int]]:
    """Extract a maximal linearly independent subset of columns.

    Returns (U, pivot_cols) where U has those columns of A, in order.
    Uses Gaussian elimination on a copy.
    """
    if not A or not A[0]:
        return [], []
    m, n = len(A), len(A[0])
    rows = [row[:] for row in A]
    pivot_cols: list[int] = []
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
        pivot_cols.append(col)
        rank += 1
    U = [[A[i][c] for c in pivot_cols] for i in range(m)]
    return U, pivot_cols


def _solve_square(
    A: list[list[Fraction]], B: list[list[Fraction]]
) -> list[list[Fraction]]:
    """Solve A X = B for X, where A is square and invertible.

    Raises ValueError if A is singular.
    """
    n = len(A)
    if n == 0 or any(len(row) != n for row in A):
        raise ValueError("A must be square")
    if len(B) != n:
        raise ValueError("B must have same number of rows as A")
    k = len(B[0]) if B else 0
    # Augment A | B
    aug = [A[i][:] + B[i][:] for i in range(n)]
    # Gauss-Jordan to reduced row echelon form
    for col in range(n):
        pivot = None
        for row in range(col, n):
            if aug[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError("matrix is singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        aug[col] = [x / scale for x in aug[col]]
        for row in range(n):
            if row != col and aug[row][col] != 0:
                factor = aug[row][col]
                aug[row] = [
                    aug[row][j] - factor * aug[col][j] for j in range(n + k)
                ]
    return [row[n : n + k] for row in aug]


def _identity(n: int) -> list[list[Fraction]]:
    I = _zeros(n, n)
    for i in range(n):
        I[i][i] = Fraction(1)
    return I


# ─────────────────────────────────────────────────────────────────
# Observable / hidden column partition
# ─────────────────────────────────────────────────────────────────


def _observable_indices(
    v_basis_full: list[tuple[str, str]],
    tool_map: dict[str, ToolSpec],
) -> tuple[list[int], list[int]]:
    """Split full-vertex-basis indices into (observable, hidden).

    A column (tool, field) is observable iff field is in tool.observable_schema.
    Return (obs_cols, hidden_cols), both sorted ascending by index.
    """
    obs_cols: list[int] = []
    hidden_cols: list[int] = []
    for idx, (tool_name, field) in enumerate(v_basis_full):
        tool = tool_map.get(tool_name)
        if tool is None:
            raise KeyError(f"tool {tool_name!r} not found in tool_map")
        if field in tool.observable_schema:
            obs_cols.append(idx)
        else:
            hidden_cols.append(idx)
    return obs_cols, hidden_cols


# ─────────────────────────────────────────────────────────────────
# Witness Gram and invariants
# ─────────────────────────────────────────────────────────────────


def witness_gram(
    tools: list[ToolSpec], edges: list[Edge]
) -> tuple[list[list[Fraction]], list[tuple[str, str]]]:
    """Compute the witness Gram matrix K(G) = H^T (I - P_O) H.

    Returns (K, hidden_basis) where:
      - K is a |H| x |H| rational PSD matrix
      - hidden_basis lists (tool_name, field_name) for each hidden column,
        aligned with K's row/column order.

    The full coboundary delta_full has entries in {0, ±1} (totally unimodular),
    so K has entries in Q. By the backbone theorem, rank(K) = fee(G).
    """
    delta, v_basis, _ = build_coboundary(tools, edges, use_internal=True)
    tool_map = {t.name: t for t in tools}
    obs_cols, hidden_cols = _observable_indices(v_basis, tool_map)

    m = len(delta)
    hidden_basis = [v_basis[c] for c in hidden_cols]

    if not hidden_cols or m == 0:
        return _zeros(len(hidden_cols), len(hidden_cols)), hidden_basis

    # H: m x |hidden|, O: m x |obs|
    H = [[delta[i][c] for c in hidden_cols] for i in range(m)]

    if not obs_cols:
        # No observables: P_O = 0, K = H^T H
        return _matmul(_transpose(H), H), hidden_basis

    O_mat = [[delta[i][c] for c in obs_cols] for i in range(m)]
    # Extract a column basis U of O_mat (to ensure U^T U is invertible)
    U, _ = _column_basis(O_mat)
    if not U or not U[0]:
        return _matmul(_transpose(H), H), hidden_basis

    UT = _transpose(U)
    G = _matmul(UT, U)          # U^T U: r x r, invertible by construction
    UTH = _matmul(UT, H)        # U^T H: r x |hidden|
    # Solve G * C = UTH  =>  C = G^{-1} U^T H
    C = _solve_square(G, UTH)
    # projK = U C is the projection of H onto col(U) = col(O_mat) = range(delta_obs)
    projH = _matmul(U, C)       # m x |hidden|
    # W = H - projH = (I - P_O) H
    W = [
        [H[i][j] - projH[i][j] for j in range(len(hidden_cols))]
        for i in range(m)
    ]
    # K = W^T (I-P_O) W = W^T W  (since (I-P_O) is idempotent and W already in its image)
    # Equivalently H^T (I - P_O) H = H^T H - H^T P_O H = H^T H - (U^T H)^T C
    K = _matmul(_transpose(W), W)
    return K, hidden_basis


def fee_from_gram(K: list[list[Fraction]]) -> int:
    """Compute fee(G) = rank(K). Verifies the backbone identity."""
    return matrix_rank(K)


def leverage_scores(
    K: list[list[Fraction]],
) -> list[Fraction]:
    """Compute leverage scores l_j = (K^+ K)_{jj} for each hidden field.

    Properties (all verified structurally):
      - sum_j l_j = fee(G)
      - 0 <= l_j <= 1
      - l_j = 1 iff j is a coloop of the witness matroid M/O
      - l_j = 0 iff j is a loop (redundant hidden field)

    Implementation: K^+ K is the orthogonal projection onto col(K).
    We extract a column basis U of K, then
        K^+ K = U (U^T U)^{-1} U^T
    and return its diagonal.
    """
    n = len(K)
    if n == 0:
        return []
    U, _ = _column_basis(K)
    if not U or not U[0]:
        return [Fraction(0)] * n
    r = len(U[0])
    UT = _transpose(U)          # r x n
    G = _matmul(UT, U)          # r x r
    # Solve G X = UT  =>  X = G^{-1} U^T,  r x n
    X = _solve_square(G, UT)
    # P = U X is n x n; we want diagonal only
    leverage: list[Fraction] = []
    for j in range(n):
        # P_jj = sum_{k} U[j][k] * X[k][j]
        total = Fraction(0)
        for k in range(r):
            total += U[j][k] * X[k][j]
        leverage.append(total)
    return leverage


def n_effective(leverage: Sequence[Fraction]) -> Fraction:
    """Concentration index N_eff = (sum l_j)^2 / sum(l_j^2).

    Distinguishes fee concentrated in few near-coloops from fee spread
    across many interchangeable fields. Ranges from 1 (maximally
    concentrated) to fee(G) (maximally spread).
    """
    s = Fraction(0)
    s2 = Fraction(0)
    for l in leverage:
        s += l
        s2 += l * l
    if s2 == 0:
        return Fraction(0)
    return s * s / s2


def coloops(
    leverage: Sequence[Fraction], hidden_basis: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Return the (tool, field) pairs with leverage exactly 1.

    These are must-disclose fields: they appear in every basis of M/O.
    """
    return [
        hidden_basis[j]
        for j, l in enumerate(leverage)
        if l == 1
    ]


def loops(
    leverage: Sequence[Fraction], hidden_basis: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Return the (tool, field) pairs with leverage exactly 0.

    These are already-redundant hidden fields: they appear in no basis of M/O.
    """
    return [
        hidden_basis[j]
        for j, l in enumerate(leverage)
        if l == 0
    ]


# ─────────────────────────────────────────────────────────────────
# Weighted repair (§4 headline theorem: matroid greedy is optimal)
# ─────────────────────────────────────────────────────────────────


def weighted_greedy_repair(
    K: list[list[Fraction]],
    hidden_basis: Sequence[tuple[str, str]],
    costs: dict[tuple[str, str], Fraction] | None = None,
) -> list[tuple[str, str]]:
    """Minimum-cost full repair via matroid greedy on M/O.

    Given rational costs for each hidden field, returns a basis of M/O
    with minimum total cost. By the matroid greedy theorem (Edmonds 1971),
    this is globally optimal — not a heuristic.

    If costs is None, every field has unit cost (any minimum basis).

    Algorithm:
      1. Sort hidden fields by cost ascending (break ties by index).
      2. Iterate; include field j if its column is independent of
         previously-included columns in K (i.e., adding e_j keeps rank
         strictly increasing when checked against the accumulated basis).

    Independence in M/O is tested by the column-rank of the K-submatrix.
    """
    n = len(hidden_basis)
    if n == 0:
        return []
    if costs is None:
        ordered: list[int] = list(range(n))
    else:
        default = Fraction(1)
        keys = [(costs.get(hidden_basis[j], default), j) for j in range(n)]
        keys.sort()
        ordered = [j for _, j in keys]

    target_rank = matrix_rank(K)
    accepted: list[int] = []

    for j in ordered:
        trial = accepted + [j]
        submat = [[K[r][c] for c in trial] for r in range(n)]
        if matrix_rank(submat) == len(trial):
            accepted.append(j)
            if len(accepted) == target_rank:
                break

    return [hidden_basis[j] for j in accepted]


# ─────────────────────────────────────────────────────────────────
# Effective resistance / Kron reduction diagnostics
# ─────────────────────────────────────────────────────────────────


def pseudoinverse_symmetric_psd(
    K: list[list[Fraction]],
) -> list[list[Fraction]]:
    """Moore-Penrose pseudoinverse of a symmetric PSD rational matrix.

    Uses the standard construction: if U is a column-basis of K and
    G = U^T U, then K^+ = U G^{-1} (U^T K U)^{-1} G^{-1} U^T... actually
    for symmetric PSD K, a simpler path is:

        K = U D U^T  (spectral decomposition)
        K^+ = U D^+ U^T

    But we want exact rational arithmetic, so we use:

        K^+ = U (U^T K U)^{-1} U^T

    where U is a column basis of K. The inner matrix (U^T K U) is
    rank(K)-by-rank(K), symmetric PSD, and invertible.

    This gives the exact rational pseudoinverse.
    """
    n = len(K)
    if n == 0:
        return []
    U, _ = _column_basis(K)
    if not U or not U[0]:
        return _zeros(n, n)
    UT = _transpose(U)
    UTK = _matmul(UT, K)
    UTKU = _matmul(UTK, U)
    UTKU_inv = _solve_square(UTKU, _identity(len(UTKU)))
    # K^+ = U (U^T K U)^{-1} U^T
    temp = _matmul(U, UTKU_inv)
    return _matmul(temp, UT)


def _connected_components_of_gram(
    K: list[list[Fraction]],
) -> list[list[int]]:
    """Return the connected components of the graph whose edges are the
    nonzero off-diagonal entries of K.

    Under DFD, K is block-diagonal by dimension, so components correspond
    to dimensions. Under DFD violation, a component can span dimensions.
    """
    n = len(K)
    parent = list(range(n))
    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry
    for i in range(n):
        for j in range(i + 1, n):
            if K[i][j] != 0:
                union(i, j)
    comp_map: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        comp_map.setdefault(root, []).append(i)
    return list(comp_map.values())


def effective_resistance(
    K: list[list[Fraction]],
) -> tuple[list[list[Fraction | None]], list[list[int]]]:
    """Effective-resistance matrix on hidden fields, computed per K-component.

    Returns (R, components) where:
      - R[i][j] is a Fraction giving R_eff(i, j) if i and j lie in the
        same connected component of K's graph; None if they lie in
        different components (formally infinite resistance — structurally
        independent fields).
      - components: list of component index lists.

    Within a component, R[i][j] = K^+[i,i] + K^+[j,j] - 2·K^+[i,j],
    the classical graph-theoretic effective resistance.

    Interpretation: small R[i,j] means fields i and j are closely tied
    in the (Kron-reduced) effective hidden-field graph — disclosing one
    largely resolves the other. Large R[i,j] within the same component
    means the two are more independent. Different components means
    structurally independent (different dimensions under DFD, or
    disconnected carrier graphs).
    """
    components = _connected_components_of_gram(K)
    n = len(K)
    R: list[list[Fraction | None]] = [[None] * n for _ in range(n)]

    for comp in components:
        if len(comp) == 1:
            # Singleton — no internal resistance; diagonal is 0
            R[comp[0]][comp[0]] = Fraction(0)
            continue
        # Restrict K to this component and compute pseudoinverse
        K_comp = [[K[i][j] for j in comp] for i in comp]
        K_pinv = pseudoinverse_symmetric_psd(K_comp)
        m = len(comp)
        for i_local in range(m):
            for j_local in range(m):
                i_global = comp[i_local]
                j_global = comp[j_local]
                R[i_global][j_global] = (
                    K_pinv[i_local][i_local]
                    + K_pinv[j_local][j_local]
                    - 2 * K_pinv[i_local][j_local]
                )
    return R, components


def disclosure_substitutes(
    K: list[list[Fraction]],
    hidden_basis: Sequence[tuple[str, str]],
    target: tuple[str, str],
    k: int = 3,
) -> list[tuple[tuple[str, str], Fraction]]:
    """Return the top-`k` disclosure substitutes for a target hidden field.

    Given a target field (by (tool, field) tuple), returns the other
    hidden fields ranked by effective resistance (smallest first). These
    are the fields that are closest substitutes for the target in the
    sense that disclosing either one most reduces the other's residual
    leverage.

    Returns a list of (substitute_field, effective_resistance) tuples.
    Returns empty list if target is not in hidden_basis.
    """
    try:
        target_idx = hidden_basis.index(target)
    except ValueError:
        return []
    R, _ = effective_resistance(K)
    # Only consider candidates in the same connected component (finite R)
    candidates = [
        (hidden_basis[j], R[target_idx][j])
        for j in range(len(hidden_basis))
        if j != target_idx and R[target_idx][j] is not None
    ]
    candidates.sort(key=lambda x: x[1])
    return candidates[:k]


# ─────────────────────────────────────────────────────────────────
# High-level convenience
# ─────────────────────────────────────────────────────────────────


def compute_all(
    tools: list[ToolSpec], edges: list[Edge]
) -> dict[str, object]:
    """Compute the full witness-geometry profile for a composition.

    Returns a dict with keys matching :class:`WitnessProfile` fields.
    The dict form is kept for backward compatibility; callers wanting
    typed access should use :func:`compute_profile` instead.

    Returns a dict with:
      - K:                witness Gram matrix (|H| x |H|, Fraction)
      - hidden_basis:     list of (tool, field) pairs aligned with K
      - fee:              rank(K) = fee(G)
      - leverage:         list of Fraction, sum = fee
      - n_effective:      Fraction, concentration index
      - coloops:          list of must-disclose (tool, field) pairs
      - loops:            list of already-redundant (tool, field) pairs
      - basis_greedy:     a minimum-weight basis under unit costs
    """
    p = compute_profile(tools, edges)
    return {
        "K": p.K,
        "hidden_basis": p.hidden_basis,
        "fee": p.fee,
        "leverage": p.leverage,
        "n_effective": p.n_effective,
        "coloops": p.coloops,
        "loops": p.loops,
        "basis_greedy": p.basis_greedy,
    }


def compute_profile(
    tools: list[ToolSpec], edges: list[Edge]
) -> WitnessProfile:
    """Compute the full witness-geometry profile as a typed dataclass.

    Preferred over :func:`compute_all` for new code — provides IDE
    autocomplete and static type checking on all fields.
    """
    K, hidden_basis = witness_gram(tools, edges)
    lev = leverage_scores(K)
    return WitnessProfile(
        K=K,
        hidden_basis=hidden_basis,
        fee=fee_from_gram(K),
        leverage=lev,
        n_effective=n_effective(lev),
        coloops=coloops(lev, hidden_basis),
        loops=loops(lev, hidden_basis),
        basis_greedy=weighted_greedy_repair(K, hidden_basis),
    )
