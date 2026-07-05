"""Holonomy of orthogonal restriction-map loops (pure numpy).

The representation-layer instrument for the grounded representation-holonomy
gate (see ``papers/coherence-cliff/holonomy_pre_registration.md``).

Given a directed cycle of agents ``v1 -> v2 -> ... -> vk -> v1`` and the
orthogonal Procrustes alignment map on each edge, this module composes the
maps around the loop and measures the deviation of the loop product from the
identity. That deviation is the *holonomy*: a nonzero value is the obstruction
to a globally consistent frame -- the representational analogue of the
seam-complex ``H^1`` the program formalizes for symbolic conventions
(``CompositionDoctrine.RepresentationalSheaf``). For an orthogonal loop
product ``H`` the squared Frobenius deviation equals the character gap
``||H - I||_F^2 = 2 (d - tr H)`` (``CoherenceSeverity``), so the instrument is
gauge-invariant under per-vertex reframing.

Pure linear algebra: numpy only, no torch and no bulla-internal imports. The
live experiment fits
:class:`bulla.adapters.restriction_maps.ProcrustesAlignment` on SAE
dictionaries (torch) and feeds its orthogonal ``_R`` matrices, converted to
numpy, into :func:`compose_loop`. The synthetic instrument-sensitivity
controls build known orthogonal matrices directly. Keeping this module
torch-free lets those controls run with zero heavy dependencies -- the same
discipline that let the dissociation girth instrument self-validate.

Convention: edge maps are supplied in transport order
``[R_12, R_23, ..., R_k1]`` where ``R_ij`` transports a vector from agent
``i``'s frame into agent ``j``'s frame. The loop product is then
``H = R_k1 ... R_23 R_12`` (each successive map left-multiplies).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

__all__ = [
    "is_orthogonal",
    "procrustes_rotation",
    "compose_loop",
    "loop_deviation",
    "holonomy_frobenius",
    "character_gap",
    "holonomy_principal_angle",
    "random_orthogonal",
    "planar_rotation",
    "scramble_orthogonal",
]


def is_orthogonal(R: np.ndarray, tol: float = 1e-8) -> bool:
    """True iff ``R`` is square and ``R^T R = I`` to within ``tol``."""
    R = np.asarray(R, dtype=float)
    if R.ndim != 2 or R.shape[0] != R.shape[1]:
        return False
    d = R.shape[0]
    return bool(np.allclose(R.T @ R, np.eye(d), atol=tol))


def procrustes_rotation(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Orthogonal ``R`` minimising ``||A R - B||_F`` (orthogonal Procrustes).

    Mirrors :meth:`ProcrustesAlignment.fit`: ``R = U V^T`` from the SVD of
    ``A^T B``. ``A`` and ``B`` are ``(n, d)`` stacks of row vectors (decoder
    rows or contextual feature activations); the returned map is ``(d, d)``
    and orthogonal. When the row counts differ the leading ``min(n_a, n_b)``
    rows are used, matching the live adapter.
    """
    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    n = min(A.shape[0], B.shape[0])
    M = A[:n].T @ B[:n]
    U, _s, Vt = np.linalg.svd(M, full_matrices=False)
    return U @ Vt


def compose_loop(maps: Sequence[np.ndarray]) -> np.ndarray:
    """Loop product ``maps[-1] @ ... @ maps[1] @ maps[0]``.

    ``maps`` are the edge maps in transport order; each successive map
    left-multiplies (transport composes on the left). The product of
    orthogonal maps is orthogonal.
    """
    if len(maps) == 0:
        raise ValueError("compose_loop requires at least one map")
    d = np.asarray(maps[0]).shape[0]
    H = np.eye(d)
    for R in maps:
        H = np.asarray(R, dtype=float) @ H
    return H


def loop_deviation(
    product: np.ndarray, reference: np.ndarray | None = None
) -> float:
    """Frobenius distance ``||product - reference||_F`` (reference defaults to I).

    With ``reference = I`` this is the closed-loop holonomy. With ``reference``
    set to the *direct* alignment between a path's endpoints it is the
    open-path residual -- the negative control that shares per-edge magnitudes
    but drops the closure constraint.
    """
    product = np.asarray(product, dtype=float)
    if reference is None:
        reference = np.eye(product.shape[0])
    return float(np.linalg.norm(product - reference, ord="fro"))


def holonomy_frobenius(maps: Sequence[np.ndarray]) -> float:
    """Closed-loop holonomy ``||compose_loop(maps) - I||_F``."""
    return loop_deviation(compose_loop(maps))


def character_gap(H: np.ndarray) -> float:
    """``2 (d - tr H)`` -- equals ``||H - I||_F^2`` for orthogonal ``H``.

    The gauge-invariant severity scalar of ``CoherenceSeverity``; depends only
    on the loop's rotation angles, not on the choice of per-vertex frame.
    """
    H = np.asarray(H, dtype=float)
    d = H.shape[0]
    return float(2.0 * (d - np.trace(H)))


def holonomy_principal_angle(H: np.ndarray) -> float:
    """Largest rotation angle of the orthogonal loop product, in radians.

    The spectral refinement reserved for the pre-registered *Redirected*
    outcome: the eigenvalues of an orthogonal ``H`` are ``e^{+/- i theta}``;
    this returns ``max |theta|``. Zero iff ``H`` fixes every direction (a
    coherent loop).
    """
    H = np.asarray(H, dtype=float)
    eig = np.linalg.eigvals(H)
    return float(np.max(np.abs(np.angle(eig))))


def random_orthogonal(d: int, rng: np.random.Generator) -> np.ndarray:
    """Haar-distributed orthogonal matrix via QR of a Gaussian (sign-fixed).

    The sign correction on the diagonal of ``R`` makes the QR factor genuinely
    Haar-distributed (Mezzadri 2007).
    """
    z = rng.standard_normal((d, d))
    q, r = np.linalg.qr(z)
    q = q * np.sign(np.diag(r))
    return q


def planar_rotation(d: int, angle: float, i: int = 0, j: int = 1) -> np.ndarray:
    """Identity except a rotation by ``angle`` in the ``(i, j)`` plane."""
    R = np.eye(d)
    c, s = np.cos(angle), np.sin(angle)
    R[i, i] = c
    R[j, j] = c
    R[i, j] = -s
    R[j, i] = s
    return R


def scramble_orthogonal(R: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Random orthogonal with the SAME ``||R - I||_F`` but randomised planes.

    Conjugation ``Q R Q^T`` by a Haar orthogonal ``Q`` preserves the spectrum
    (hence the trace, hence the character gap and ``||R - I||_F``) while
    destroying the loop-closure relationship ``R`` had with the other edges.
    This is the spectrum-matched scramble negative control: it isolates whether
    holonomy responds to *closure* or merely to per-edge magnitude.
    """
    R = np.asarray(R, dtype=float)
    Q = random_orthogonal(R.shape[0], rng)
    return Q @ R @ Q.T
