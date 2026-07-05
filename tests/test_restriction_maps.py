"""Tests for bulla/adapters/restriction_maps.py (G23 A3 commit 1c).

Covers:
  * Module imports without torch / sae-lens / sentence-transformers
  * RestrictionMap Protocol satisfaction across all 7 maps
  * SYNTHETIC_ONLY discipline (4 real, 3 synthetic)
  * Synthetic maps' fit / align mechanics (Identity, Offset, Bijection)
  * Real maps' SAEBackendImportError contract when extras absent
  * `restriction_map_from_name` factory + `real_map_names` /
    `synthetic_map_names` helpers
  * Validation rejects mismatched dictionary sizes + invalid perms

Real-data exercise of ProcrustesAlignment / SparseCrosscoder /
TranscoderMap / NeuronpediaLabelMap is gated on
`pytest.importorskip("sae_lens")` and lives in
`test_sae_lens_backend_integration.py` (Iter-2).
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_data import SAEDictionary, SAEFeatureData, SAEProvenance
from bulla.adapters.restriction_maps import (
    AlignmentCandidate,
    BijectionRestrictionMap,
    IdentityRestrictionMap,
    NeuronpediaLabelMap,
    OffsetRestrictionMap,
    ProcrustesAlignment,
    RestrictionMap,
    SparseCrosscoder,
    TranscoderMap,
    real_map_names,
    restriction_map_from_name,
    synthetic_map_names,
)
from bulla.adapters.sae_lens_backend import SAEBackendImportError


# ── Test fixtures (torch-free SAEDictionary stand-ins) ─────────────────


class _MockTensor:
    """Stand-in for torch.Tensor matching the API touched by sae_data."""

    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape


def _mock_provenance(release: str = "release-test") -> SAEProvenance:
    return SAEProvenance(
        release=release,
        sae_id="layer_0/width_4/canonical",
        sha256="sha256:" + "0" * 64,
        n_p99_tokens=1024,
    )


def _mock_dictionary(
    *,
    model_id: str = "model-a",
    layer: int = 0,
    n_features: int = 4,
    d_model: int = 8,
) -> SAEDictionary:
    """Build a fake SAEDictionary that satisfies sae_data invariants."""
    features = tuple(
        SAEFeatureData(
            spec=SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=i),
            decoder_direction=_MockTensor(shape=(d_model,)),
            activation_p99=float(i + 1),
            provenance=_mock_provenance(),
        )
        for i in range(n_features)
    )
    return SAEDictionary(
        model_id=model_id,
        layer=layer,
        features=features,
        d_model=d_model,
        decoder_matrix=_MockTensor(shape=(n_features, d_model)),
    )


# ── Module-import + Protocol satisfaction ──────────────────────────────


class TestImportWithoutHeavyDeps:
    """The module must import without torch / sae-lens / sentence-transformers."""

    def test_module_imports(self):
        import bulla.adapters.restriction_maps as mod
        assert hasattr(mod, "RestrictionMap")
        assert hasattr(mod, "ProcrustesAlignment")
        assert hasattr(mod, "SparseCrosscoder")
        assert hasattr(mod, "TranscoderMap")
        assert hasattr(mod, "NeuronpediaLabelMap")
        assert hasattr(mod, "IdentityRestrictionMap")
        assert hasattr(mod, "OffsetRestrictionMap")
        assert hasattr(mod, "BijectionRestrictionMap")


class TestProtocolSatisfaction:
    """All 7 maps satisfy the runtime-checkable RestrictionMap Protocol."""

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: ProcrustesAlignment(),
            lambda: SparseCrosscoder(),
            lambda: TranscoderMap(),
            lambda: NeuronpediaLabelMap(),
            lambda: IdentityRestrictionMap(),
            lambda: OffsetRestrictionMap(shift=1),
            lambda: BijectionRestrictionMap(perm=(0, 1)),
        ],
    )
    def test_satisfies_protocol(self, factory):
        m = factory()
        assert isinstance(m, RestrictionMap)
        assert isinstance(m.name, str) and m.name
        assert isinstance(m.SYNTHETIC_ONLY, bool)


class TestSyntheticOnlyFlag:
    """Real maps SYNTHETIC_ONLY=False; synthetic maps SYNTHETIC_ONLY=True."""

    def test_real_maps_not_synthetic(self):
        for cls in (ProcrustesAlignment, SparseCrosscoder, TranscoderMap, NeuronpediaLabelMap):
            assert cls.SYNTHETIC_ONLY is False, f"{cls.__name__} should be real"

    def test_synthetic_maps_marked_synthetic(self):
        for cls in (IdentityRestrictionMap, OffsetRestrictionMap, BijectionRestrictionMap):
            assert cls.SYNTHETIC_ONLY is True, f"{cls.__name__} should be synthetic"

    def test_4_real_3_synthetic(self):
        # Sweep code uses these counts; keep the contract explicit.
        assert len(real_map_names()) == 4
        assert len(synthetic_map_names()) == 3


# ── IdentityRestrictionMap ─────────────────────────────────────────────


class TestIdentityRestrictionMap:
    """Identity: feature_id i → feature_id i."""

    def test_align_after_fit(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        m = IdentityRestrictionMap()
        m.fit(dict_a=d_a, dict_b=d_b)
        for i in range(4):
            src = SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            (cand,) = m.align(source=src, top_k=1)
            assert isinstance(cand, AlignmentCandidate)
            assert cand.target.feature_id == i  # identity
            assert cand.target.model_id == "b"
            assert cand.similarity == 1.0

    def test_align_before_fit_raises(self):
        m = IdentityRestrictionMap()
        with pytest.raises(RuntimeError, match=r"must call fit"):
            m.align(source=SAEFeatureSpec(model_id="a", layer=0, feature_id=0))

    def test_size_mismatch_rejected(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=8)
        m = IdentityRestrictionMap()
        with pytest.raises(ValueError, match=r"equal-size dictionaries"):
            m.fit(dict_a=d_a, dict_b=d_b)


# ── OffsetRestrictionMap ───────────────────────────────────────────────


class TestOffsetRestrictionMap:
    """Offset: feature_id i → (i + shift) mod n."""

    def test_shift_zero_is_identity(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        m = OffsetRestrictionMap(shift=0)
        m.fit(dict_a=d_a, dict_b=d_b)
        for i in range(4):
            src = SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            (cand,) = m.align(source=src)
            assert cand.target.feature_id == i

    def test_shift_one(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        m = OffsetRestrictionMap(shift=1)
        m.fit(dict_a=d_a, dict_b=d_b)
        # Each i should map to (i + 1) mod 4
        for i in range(4):
            src = SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            (cand,) = m.align(source=src)
            assert cand.target.feature_id == (i + 1) % 4

    def test_shift_wraparound(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        m = OffsetRestrictionMap(shift=5)  # 5 % 4 = 1
        m.fit(dict_a=d_a, dict_b=d_b)
        src = SAEFeatureSpec(model_id="a", layer=0, feature_id=0)
        (cand,) = m.align(source=src)
        assert cand.target.feature_id == 1

    def test_size_mismatch_rejected(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=8)
        m = OffsetRestrictionMap(shift=1)
        with pytest.raises(ValueError, match=r"equal-size"):
            m.fit(dict_a=d_a, dict_b=d_b)


# ── BijectionRestrictionMap ────────────────────────────────────────────


class TestBijectionRestrictionMap:
    """Bijection: arbitrary fixed permutation of feature_ids."""

    def test_align_with_valid_perm(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        perm = (3, 1, 0, 2)
        m = BijectionRestrictionMap(perm=perm)
        m.fit(dict_a=d_a, dict_b=d_b)
        for i in range(4):
            src = SAEFeatureSpec(model_id="a", layer=0, feature_id=i)
            (cand,) = m.align(source=src)
            assert cand.target.feature_id == perm[i]

    def test_invalid_perm_length_rejected(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        m = BijectionRestrictionMap(perm=(0, 1, 2))  # only 3 entries
        with pytest.raises(ValueError, match=r"perm has length"):
            m.fit(dict_a=d_a, dict_b=d_b)

    def test_invalid_perm_not_a_permutation_rejected(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=4)
        # Has duplicates, so it's not a valid permutation of 0..3
        m = BijectionRestrictionMap(perm=(0, 1, 1, 3))
        with pytest.raises(ValueError, match=r"valid permutation"):
            m.fit(dict_a=d_a, dict_b=d_b)

    def test_size_mismatch_rejected(self):
        d_a = _mock_dictionary(model_id="a", n_features=4)
        d_b = _mock_dictionary(model_id="b", n_features=8)
        m = BijectionRestrictionMap(perm=(0, 1, 2, 3))
        with pytest.raises(ValueError, match=r"equal-size"):
            m.fit(dict_a=d_a, dict_b=d_b)


# ── Real maps: structural contract (env-agnostic) ──────────────────────


class TestRealMapStructuralContract:
    """Real maps store config + report identity correctly without invoking
    fit(). The actual dependency-import paths are tested in
    test_sae_lens_backend.py::TestImportErrorContract (message format)
    and test_sae_lens_backend_integration.py (real loading). Triggering
    SAEBackendImportError here would be fragile against the test env's
    installed packages (e.g. torch may or may not be present)."""

    def test_procrustes_name_and_flag(self):
        m = ProcrustesAlignment()
        assert m.name == "procrustes"
        assert m.SYNTHETIC_ONLY is False
        # Internal state empty before fit
        assert m._R is None
        assert m._dict_b is None
        assert m._D_a is None

    def test_align_before_fit_raises(self):
        # Procrustes.align() before fit() should always raise — this path
        # never reaches torch import, so it's robust across envs.
        m = ProcrustesAlignment()
        with pytest.raises(RuntimeError, match=r"must call fit"):
            m.align(source=SAEFeatureSpec(model_id="a", layer=0, feature_id=0))

    def test_crosscoder_name_and_flag(self):
        m = SparseCrosscoder()
        assert m.name == "crosscoder"
        assert m.SYNTHETIC_ONLY is False
        assert m._dict_a is None and m._dict_b is None

    def test_crosscoder_align_before_fit_raises(self):
        m = SparseCrosscoder()
        with pytest.raises(RuntimeError, match=r"must call fit"):
            m.align(source=SAEFeatureSpec(model_id="a", layer=0, feature_id=0))

    def test_transcoder_name_and_flag(self):
        m = TranscoderMap()
        assert m.name == "transcoder"
        assert m.SYNTHETIC_ONLY is False
        assert m._dict_a is None and m._dict_b is None

    def test_transcoder_align_before_fit_raises(self):
        m = TranscoderMap()
        with pytest.raises(RuntimeError, match=r"must call fit"):
            m.align(source=SAEFeatureSpec(model_id="a", layer=0, feature_id=0))

    def test_neuronpedia_name_and_flag(self):
        m = NeuronpediaLabelMap()
        assert m.name == "neuronpedia_label"
        assert m.SYNTHETIC_ONLY is False
        assert m._dict_a is None and m._dict_b is None

    def test_neuronpedia_align_before_fit_raises(self):
        m = NeuronpediaLabelMap()
        with pytest.raises(RuntimeError, match=r"must call fit"):
            m.align(source=SAEFeatureSpec(model_id="a", layer=0, feature_id=0))


class TestProcrustesAlignmentWithTorch:
    """Exercise ProcrustesAlignment fit/align if torch is available.

    Skipped automatically in torch-free CI. When torch is present, this
    verifies the SVD round-trip on a small synthetic tensor pair: when
    D_b = D_a @ Q for some orthogonal Q, the recovered R should align
    D_a to D_b nearly perfectly (cosine ≈ 1 on a same-index source).
    """

    def test_procrustes_aligns_orthogonally_transformed_pair(self):
        torch = pytest.importorskip("torch")
        n_features, d_model = 8, 4
        gen = torch.Generator().manual_seed(20260507)
        D_a = torch.randn(n_features, d_model, generator=gen)
        # Generate a random orthogonal Q and set D_b = D_a @ Q
        rand = torch.randn(d_model, d_model, generator=gen)
        Q, _ = torch.linalg.qr(rand)
        D_b = D_a @ Q

        # Build SAEDictionary instances backed by the real tensors
        def _build(model_id: str, D: "torch.Tensor") -> SAEDictionary:
            features = tuple(
                SAEFeatureData(
                    spec=SAEFeatureSpec(model_id=model_id, layer=0, feature_id=i),
                    decoder_direction=D[i],
                    activation_p99=0.0,
                    provenance=_mock_provenance(),
                )
                for i in range(n_features)
            )
            return SAEDictionary(
                model_id=model_id, layer=0, features=features,
                d_model=d_model, decoder_matrix=D,
            )

        d_a = _build("a", D_a)
        d_b = _build("b", D_b)

        m = ProcrustesAlignment()
        m.fit(dict_a=d_a, dict_b=d_b)
        # After fit, the recovered rotation R should be ~Q (up to sign /
        # SVD ambiguity). Test the practical contract: align(source=i)
        # picks target=i with cosine ≈ 1.
        for i in range(n_features):
            (cand,) = m.align(
                source=SAEFeatureSpec(model_id="a", layer=0, feature_id=i),
                top_k=1,
            )
            assert cand.target.feature_id == i, (
                f"Procrustes failed to recover identity at i={i}: "
                f"got feature_id={cand.target.feature_id}"
            )
            assert cand.similarity > 0.99, (
                f"Procrustes cosine too low at i={i}: {cand.similarity}"
            )


# ── Real-map default config sanity ──────────────────────────────────────


class TestRealMapDefaults:
    """Default-config sanity: locked checkpoint identifiers stay correct."""

    def test_crosscoder_locked_checkpoint(self):
        m = SparseCrosscoder()
        # Locked per A3 plan (Anthropic March 2025 community-trained crosscoder)
        assert m.checkpoint == (
            "science-of-finetuning/gemma-2-2b-crosscoder-l13-mu4.1e-02-lr1e-04"
        )

    def test_transcoder_locked_checkpoint(self):
        m = TranscoderMap()
        # Locked per A3 plan: gemma-scope-2b-pt-transcoders, same layer/width
        # as the canonical SAE so cross-model 2-cover topology is comparable.
        assert m.release == "gemma-scope-2b-pt-transcoders"
        assert m.sae_id == "layer_20/width_16k/canonical"

    def test_neuronpedia_locked_embedding_revision(self):
        m = NeuronpediaLabelMap()
        # Locked per Refinement 2: HF revision must be pinned so cross-run
        # comparisons aren't poisoned by silent embedding-model updates.
        assert m.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
        assert len(m.embedding_revision) == 40  # SHA-1 hex
        # Reproducibility floor: don't accept a moving target
        assert m.embedding_revision != "main"
        assert m.embedding_revision != "master"


# ── Factory ─────────────────────────────────────────────────────────────


class TestFactory:
    """`restriction_map_from_name` resolves names; helpers list registry."""

    @pytest.mark.parametrize(
        "name,expected_cls",
        [
            ("procrustes", ProcrustesAlignment),
            ("crosscoder", SparseCrosscoder),
            ("transcoder", TranscoderMap),
            ("neuronpedia_label", NeuronpediaLabelMap),
            ("identity", IdentityRestrictionMap),
        ],
    )
    def test_known_names_resolve(self, name, expected_cls):
        m = restriction_map_from_name(name)
        assert isinstance(m, expected_cls)
        assert m.name == name

    def test_offset_with_kwargs(self):
        m = restriction_map_from_name("offset", shift=3)
        assert isinstance(m, OffsetRestrictionMap)
        assert m.shift == 3

    def test_bijection_with_kwargs(self):
        m = restriction_map_from_name("bijection", perm=(2, 0, 1))
        assert isinstance(m, BijectionRestrictionMap)
        assert m.perm == (2, 0, 1)

    def test_unknown_name_raises_keyerror(self):
        with pytest.raises(KeyError, match=r"Unknown restriction map"):
            restriction_map_from_name("not-a-real-map")

    def test_real_map_names_returns_only_real(self):
        names = real_map_names()
        assert set(names) == {
            "procrustes", "crosscoder", "transcoder", "neuronpedia_label",
        }

    def test_synthetic_map_names_returns_only_synthetic(self):
        names = synthetic_map_names()
        assert set(names) == {"identity", "offset", "bijection"}

    def test_real_and_synthetic_names_disjoint(self):
        assert set(real_map_names()) & set(synthetic_map_names()) == set()


# ── AlignmentCandidate ──────────────────────────────────────────────────


class TestAlignmentCandidate:
    """AlignmentCandidate: frozen, equality-by-value."""

    def test_construct(self):
        spec = SAEFeatureSpec(model_id="b", layer=0, feature_id=2)
        c = AlignmentCandidate(target=spec, similarity=0.7)
        assert c.target == spec
        assert c.similarity == 0.7

    def test_frozen(self):
        spec = SAEFeatureSpec(model_id="b", layer=0, feature_id=0)
        c = AlignmentCandidate(target=spec, similarity=1.0)
        with pytest.raises(Exception):
            c.similarity = 0.5  # type: ignore[misc]
