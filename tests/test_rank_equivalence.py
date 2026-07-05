"""The GF(2) fast rank equals the exact ℚ rank on every coboundary the corpus builds.

This is the property that licenses the ~600× speedup (a 57-server registry audit went from
~22 minutes to ~2 seconds): the coboundary is a signed incidence matrix, hence totally
unimodular, so its rank is *field-independent* (Schrijver, Thm 19.3). We do NOT trust that
from the docstring — we assert `matrix_rank == matrix_rank_exact` on the real corpus and on
synthetic TU matrices. Where it would ever differ (a non-TU / surrogate-regime matrix) this
test fails loudly rather than letting a wrong fee ship.

It is also the cleanest determinism/recomputability guard in the suite: the fee is canonical
across rank implementations, so a verifier running any field-correct rank recomputes the
same deed.
"""
from __future__ import annotations

import random
from fractions import Fraction
from pathlib import Path

import pytest

import bulla.coboundary as cob
from bulla.coboundary import matrix_rank, matrix_rank_exact

_CORPUS = sorted((Path(__file__).resolve().parent.parent / "compositions").glob("*.yaml"))


def _diagnose_checking_every_rank(comp_path: Path) -> int:
    """Run the real `diagnose` and assert fast == exact on EVERY coboundary it builds,
    across all modules that compute ranks (diagnostic / regime / witness-geometry / …)."""
    import bulla.diagnostic
    import bulla.incremental
    import bulla.proxy
    import bulla.regime
    import bulla.witness_geometry
    import bulla.witness_matroid
    from bulla.diagnostic import diagnose
    from bulla.parser import load_composition

    fast = cob.matrix_rank
    seen = {"n": 0}

    def checked(m: list[list[Fraction]]) -> int:
        r = fast(m)
        e = matrix_rank_exact(m)
        assert r == e, (
            f"GF(2) rank {r} != exact ℚ rank {e} on a "
            f"{len(m)}x{len(m[0]) if m and m[0] else 0} matrix ({comp_path.stem}) — "
            f"a non-TU matrix reached the fast path"
        )
        seen["n"] += 1
        return r

    mods = [cob, bulla.diagnostic, bulla.incremental, bulla.proxy,
            bulla.regime, bulla.witness_geometry, bulla.witness_matroid]
    saved = [(m, getattr(m, "matrix_rank", None)) for m in mods]
    try:
        for m in mods:
            if getattr(m, "matrix_rank", None) is fast:
                m.matrix_rank = checked
        diagnose(load_composition(comp_path))
    finally:
        for m, o in saved:
            if o is not None:
                m.matrix_rank = o
    return seen["n"]


@pytest.mark.parametrize("comp", _CORPUS, ids=lambda p: p.stem)
def test_fast_rank_equals_exact_on_real_composition(comp: Path):
    assert _diagnose_checking_every_rank(comp) > 0   # the rank path was actually exercised


def test_fast_rank_equals_exact_on_synthetic_tu_matrices():
    """Random signed-incidence (TU) matrices at scale — the field-independence property
    holds structurally, not just on the small shipped corpus."""
    rng = random.Random(0)
    for _ in range(300):
        cols = rng.randint(2, 120)
        rows = rng.randint(1, 350)
        m: list[list[Fraction]] = []
        for _ in range(rows):
            a, b = rng.randrange(cols), rng.randrange(cols)
            row = [Fraction(0)] * cols
            if a != b:
                row[a], row[b] = Fraction(1), Fraction(-1)
            else:
                row[a] = Fraction(rng.choice([-1, 1]))
            m.append(row)
        assert matrix_rank(m) == matrix_rank_exact(m)


def test_fallback_on_non_signed_incidence_is_exact_not_gf2():
    """OFF the signed-incidence regime GF(2) is WRONG — and an earlier "TU everywhere"
    shortcut shipped wrong fees here. The hybrid must detect non-TU and take the exact path.
    This is the property whose absence broke 18 fee tests; lock it."""
    from bulla.coboundary import _is_signed_incidence, _rank_gf2
    # two +1's per row -> not a signed incidence matrix -> not totally unimodular
    m = [
        [Fraction(1), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(1)],
        [Fraction(1), Fraction(0), Fraction(1)],
    ]
    assert not _is_signed_incidence(m)
    assert _rank_gf2(m) != matrix_rank_exact(m)        # GF(2) genuinely disagrees (2 vs 3)
    assert matrix_rank(m) == matrix_rank_exact(m)       # ...and the hybrid returns the exact rank


def test_fast_rank_basic_cases():
    assert matrix_rank([]) == 0
    assert matrix_rank([[Fraction(0), Fraction(0)]]) == 0
    assert matrix_rank([[Fraction(1), Fraction(-1)]]) == 1
    # two independent edges -> rank 2
    assert matrix_rank([
        [Fraction(1), Fraction(-1), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(-1)],
    ]) == 2
    # a 3-cycle: the three edge-rows sum to zero -> rank deficient (2, not 3)
    cycle = [
        [Fraction(1), Fraction(-1), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(-1)],
        [Fraction(-1), Fraction(0), Fraction(1)],
    ]
    assert matrix_rank(cycle) == 2 == matrix_rank_exact(cycle)
