"""Unit tests for the holonomy loop-algebra instrument (pure numpy)."""

from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("numpy")  # research-only dep; skip cleanly in the standalone package

import numpy as np

# Make the in-tree package importable without an editable install.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bulla.adapters.holonomy import (  # noqa: E402
    character_gap,
    compose_loop,
    holonomy_frobenius,
    holonomy_principal_angle,
    is_orthogonal,
    loop_deviation,
    planar_rotation,
    procrustes_rotation,
    random_orthogonal,
    scramble_orthogonal,
)


def test_identity_loop_has_zero_holonomy():
    maps = [np.eye(6) for _ in range(3)]
    assert holonomy_frobenius(maps) == 0.0


def test_inverse_pair_closes():
    rng = np.random.default_rng(0)
    R = random_orthogonal(5, rng)
    # Transport by R then by its inverse R^T returns to the start.
    assert holonomy_frobenius([R, R.T]) < 1e-9


def test_known_triangle_recovers_planted_rotation():
    d, theta = 7, 0.4
    rng = np.random.default_rng(1)
    R12 = random_orthogonal(d, rng)
    R23 = random_orthogonal(d, rng)
    target = planar_rotation(d, theta)
    # Choose the closing leg so the loop product is exactly target.
    R31 = target @ np.linalg.inv(R23 @ R12)
    H = compose_loop([R12, R23, R31])
    assert np.allclose(H, target, atol=1e-9)
    expected = float(np.linalg.norm(target - np.eye(d), ord="fro"))
    assert abs(holonomy_frobenius([R12, R23, R31]) - expected) < 1e-9


def test_character_gap_equals_frobenius_squared_for_orthogonal():
    rng = np.random.default_rng(2)
    R = random_orthogonal(8, rng)
    fro_sq = float(np.linalg.norm(R - np.eye(8), ord="fro") ** 2)
    assert abs(character_gap(R) - fro_sq) < 1e-8


def test_compose_preserves_orthogonality():
    rng = np.random.default_rng(3)
    maps = [random_orthogonal(6, rng) for _ in range(5)]
    assert is_orthogonal(compose_loop(maps))


def test_scramble_preserves_magnitude_changes_matrix():
    rng = np.random.default_rng(4)
    R = planar_rotation(6, 0.7)
    S = scramble_orthogonal(R, rng)
    assert is_orthogonal(S)
    # Same ||. - I|| magnitude (spectrum is preserved under conjugation) ...
    assert abs(loop_deviation(R) - loop_deviation(S)) < 1e-8
    # ... but a genuinely different matrix (planes randomised).
    assert not np.allclose(R, S, atol=1e-3)


def test_principal_angle_recovers_planted_rotation():
    assert abs(holonomy_principal_angle(planar_rotation(5, 0.9)) - 0.9) < 1e-8
    assert holonomy_principal_angle(np.eye(5)) < 1e-9


def test_procrustes_recovers_known_rotation():
    rng = np.random.default_rng(5)
    d, n = 6, 40
    A = rng.standard_normal((n, d))
    R0 = random_orthogonal(d, rng)
    B = A @ R0
    assert np.allclose(procrustes_rotation(A, B), R0, atol=1e-8)
