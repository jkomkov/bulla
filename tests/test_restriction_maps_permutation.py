"""Permutation-invariance tests for restriction_maps (G23 A3 commit 1c).

Verifies the structural-bijection property of synthetic-only restriction
maps. This is the load-bearing contract that makes Gate 7
(``dim H¹(SAE_a, π(SAE_a)) = 0`` EXACTLY) achievable in the §3b sweep.

# What this file tests (mechanical, no torch)

Gate 7's full topology check needs `sae_compose.build_cross_model_composition`
which lands in commit 1d. Here we test the prerequisite property at the
map level:

  1. **Bijection round-trip**: applying ``BijectionRestrictionMap(π)``
     followed by ``BijectionRestrictionMap(π⁻¹)`` recovers the original
     ``feature_id`` exactly.
  2. **Offset round-trip**: ``OffsetRestrictionMap(+k)`` followed by
     ``OffsetRestrictionMap(-k)`` recovers the original.
  3. **Identity is a bijection**: ``IdentityRestrictionMap`` is the
     ``π = id`` case of bijection; produces every feature_id exactly once
     across the source dictionary.
  4. **No-collision**: every synthetic map produces a permutation
     (no two source feature_ids map to the same target feature_id).

If any of these fail, Gate 7 is structurally impossible to satisfy
because the encoding loses information at the map level. This is a
faster-failing version of the §3a′ Tripwire 0i map-invariance pre-check
(which lands in commit 1d's ``test_g23_a3_synthetic_validation.py`` with
the full ``audit_encoding_capability()`` integration).
"""

from __future__ import annotations

import random

import pytest

from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_data import SAEDictionary, SAEFeatureData, SAEProvenance
from bulla.adapters.restriction_maps import (
    BijectionRestrictionMap,
    IdentityRestrictionMap,
    OffsetRestrictionMap,
)


class _MockTensor:
    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape


def _mock_dictionary(*, model_id: str, n_features: int) -> SAEDictionary:
    features = tuple(
        SAEFeatureData(
            spec=SAEFeatureSpec(model_id=model_id, layer=0, feature_id=i),
            decoder_direction=_MockTensor(shape=(8,)),
            activation_p99=float(i),
            provenance=SAEProvenance(
                release="r", sae_id="x",
                sha256="sha256:" + "0" * 64, n_p99_tokens=1,
            ),
        )
        for i in range(n_features)
    )
    return SAEDictionary(
        model_id=model_id, layer=0, features=features, d_model=8,
        decoder_matrix=_MockTensor((n_features, 8)),
    )


def _inverse_perm(perm: tuple[int, ...]) -> tuple[int, ...]:
    """π⁻¹: position-of-i in perm."""
    n = len(perm)
    inv = [0] * n
    for i, p in enumerate(perm):
        inv[p] = i
    return tuple(inv)


def _apply_map(map_obj, *, dict_src: SAEDictionary, n: int) -> tuple[int, ...]:
    """Run map.align across all n source features → tuple of target feature_ids."""
    out = []
    for i in range(n):
        src = SAEFeatureSpec(model_id=dict_src.model_id, layer=0, feature_id=i)
        (cand,) = map_obj.align(source=src, top_k=1)
        out.append(cand.target.feature_id)
    return tuple(out)


# ── Bijection round-trip (Gate 7 prerequisite) ─────────────────────────


class TestBijectionRoundTrip:
    """Applying a bijection map then its inverse recovers identity."""

    @pytest.mark.parametrize("perm", [
        (0, 1, 2, 3),               # identity
        (3, 2, 1, 0),               # reversal
        (1, 3, 0, 2),               # arbitrary
        (2, 0, 3, 1),               # arbitrary
    ])
    def test_perm_then_inverse_perm_is_identity(self, perm):
        n = len(perm)
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        d_c = _mock_dictionary(model_id="c", n_features=n)

        forward = BijectionRestrictionMap(perm=perm)
        forward.fit(dict_a=d_a, dict_b=d_b)
        # First-leg image: a→b under π
        first_leg = _apply_map(forward, dict_src=d_a, n=n)
        assert first_leg == perm  # by definition of perm

        # Inverse leg: b→c under π⁻¹
        inverse = BijectionRestrictionMap(perm=_inverse_perm(perm))
        inverse.fit(dict_a=d_b, dict_b=d_c)
        # Now compose: for source feature_id i in a, route through perm[i]
        # in b, then through inverse-perm[perm[i]] in c. Should equal i.
        for i in range(n):
            mid = perm[i]
            src_b = SAEFeatureSpec(model_id="b", layer=0, feature_id=mid)
            (cand,) = inverse.align(source=src_b, top_k=1)
            assert cand.target.feature_id == i, (
                f"Round-trip failed at i={i}: π[i]={mid}, π⁻¹[π[i]]={cand.target.feature_id}"
            )

    def test_random_perm_round_trip(self):
        rng = random.Random(20260507)  # locked seed
        n = 16
        perm = list(range(n))
        rng.shuffle(perm)
        perm_t = tuple(perm)
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        d_c = _mock_dictionary(model_id="c", n_features=n)

        forward = BijectionRestrictionMap(perm=perm_t)
        forward.fit(dict_a=d_a, dict_b=d_b)
        inverse = BijectionRestrictionMap(perm=_inverse_perm(perm_t))
        inverse.fit(dict_a=d_b, dict_b=d_c)

        for i in range(n):
            (mid_cand,) = forward.align(
                source=SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            )
            (final_cand,) = inverse.align(source=mid_cand.target)
            assert final_cand.target.feature_id == i


# ── Offset round-trip ───────────────────────────────────────────────────


class TestOffsetRoundTrip:
    """OffsetRestrictionMap(+k) followed by OffsetRestrictionMap(-k) is identity."""

    @pytest.mark.parametrize("shift,n", [(0, 4), (1, 4), (2, 4), (3, 4), (5, 4), (7, 4)])
    def test_offset_inverse_round_trip(self, shift, n):
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        d_c = _mock_dictionary(model_id="c", n_features=n)
        forward = OffsetRestrictionMap(shift=shift)
        forward.fit(dict_a=d_a, dict_b=d_b)
        # Inverse of +shift mod n is +(n - shift) mod n
        inverse = OffsetRestrictionMap(shift=(n - (shift % n)) % n)
        inverse.fit(dict_a=d_b, dict_b=d_c)

        for i in range(n):
            (mid_cand,) = forward.align(
                source=SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            )
            (final_cand,) = inverse.align(source=mid_cand.target)
            assert final_cand.target.feature_id == i


# ── Identity is the trivial bijection ──────────────────────────────────


class TestIdentityIsBijection:
    """IdentityRestrictionMap == BijectionRestrictionMap(perm=id)."""

    @pytest.mark.parametrize("n", [1, 4, 16, 64])
    def test_identity_image_equals_source(self, n):
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        m = IdentityRestrictionMap()
        m.fit(dict_a=d_a, dict_b=d_b)
        image = _apply_map(m, dict_src=d_a, n=n)
        assert image == tuple(range(n))


# ── No-collision (every synthetic map is a permutation) ─────────────────


class TestNoCollision:
    """Every synthetic map is a true permutation: image is a permutation of
    0..n-1, with no duplicates and no missing values."""

    @pytest.mark.parametrize("n", [4, 16, 32])
    def test_identity_is_permutation(self, n):
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        m = IdentityRestrictionMap()
        m.fit(dict_a=d_a, dict_b=d_b)
        image = _apply_map(m, dict_src=d_a, n=n)
        assert sorted(image) == list(range(n))
        assert len(set(image)) == n  # no duplicates

    @pytest.mark.parametrize("shift,n", [(0, 8), (1, 8), (3, 8), (7, 8), (12, 8)])
    def test_offset_is_permutation(self, shift, n):
        d_a = _mock_dictionary(model_id="a", n_features=n)
        d_b = _mock_dictionary(model_id="b", n_features=n)
        m = OffsetRestrictionMap(shift=shift)
        m.fit(dict_a=d_a, dict_b=d_b)
        image = _apply_map(m, dict_src=d_a, n=n)
        assert sorted(image) == list(range(n))
        assert len(set(image)) == n

    def test_bijection_is_permutation(self):
        rng = random.Random(20260507)
        for trial in range(8):
            n = 16
            perm = list(range(n))
            rng.shuffle(perm)
            d_a = _mock_dictionary(model_id="a", n_features=n)
            d_b = _mock_dictionary(model_id="b", n_features=n)
            m = BijectionRestrictionMap(perm=tuple(perm))
            m.fit(dict_a=d_a, dict_b=d_b)
            image = _apply_map(m, dict_src=d_a, n=n)
            assert sorted(image) == list(range(n))
            assert len(set(image)) == n
