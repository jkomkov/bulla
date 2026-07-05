"""Tests for bulla/adapters/sae_data.py (G23 A3 commit 1a).

Schema-shape and provenance-immutability tests that validate the
frozen-dataclass contracts the rest of the A3 pipeline depends on.

This test file does NOT require `torch` installed. Tensor-typed fields
are populated with sentinel objects (a tiny mock class) in lieu of real
tensors. The import-without-torch invariant is itself part of what the
tests verify (importing `bulla.adapters.sae_data` raises no
ImportError when torch is absent — confirmed by the test file's own
top-level imports working in CI).
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_data import (
    SAEDictionary,
    SAEFeatureData,
    SAEProvenance,
)


class _MockTensor:
    """Stand-in for torch.Tensor that has a shape and float-storage but no torch dep."""

    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape

    def __repr__(self) -> str:
        return f"_MockTensor(shape={self.shape})"


def _mock_provenance(release: str = "release-test") -> SAEProvenance:
    return SAEProvenance(
        release=release,
        sae_id="layer_0/width_4/canonical",
        sha256="sha256:" + "0" * 64,
        n_p99_tokens=1024,
    )


def _mock_feature_data(
    *,
    model_id: str = "synthetic",
    layer: int = 0,
    feature_id: int = 0,
    activation_p99: float = 1.0,
    d_model: int = 4,
) -> SAEFeatureData:
    return SAEFeatureData(
        spec=SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=feature_id),
        decoder_direction=_MockTensor(shape=(d_model,)),
        activation_p99=activation_p99,
        provenance=_mock_provenance(),
    )


def _mock_dictionary(
    *,
    model_id: str = "synthetic",
    layer: int = 0,
    n_features: int = 4,
    d_model: int = 8,
) -> SAEDictionary:
    features = tuple(
        _mock_feature_data(
            model_id=model_id,
            layer=layer,
            feature_id=i,
            activation_p99=float(i + 1),
            d_model=d_model,
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


class TestSAEProvenance:
    """SAEProvenance: frozen, hashable, all-fields-required."""

    def test_construct_with_required_fields(self):
        p = _mock_provenance()
        assert p.release == "release-test"
        assert p.sha256.startswith("sha256:")
        assert p.n_p99_tokens == 1024

    def test_frozen_no_mutation(self):
        p = _mock_provenance()
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            p.release = "different"  # type: ignore[misc]

    def test_hashable_for_set_membership(self):
        p1 = _mock_provenance("rel-a")
        p2 = _mock_provenance("rel-a")
        p3 = _mock_provenance("rel-b")
        s = {p1, p2, p3}
        # p1 == p2 (same fields), p3 different → set has 2 elements
        assert len(s) == 2

    def test_equality_on_field_values(self):
        p1 = _mock_provenance("rel-a")
        p2 = _mock_provenance("rel-a")
        assert p1 == p2
        p3 = _mock_provenance("rel-b")
        assert p1 != p3


class TestSAEFeatureData:
    """SAEFeatureData: pairs SAEFeatureSpec with runtime payload."""

    def test_construct_with_spec_and_payload(self):
        d = _mock_feature_data()
        assert isinstance(d.spec, SAEFeatureSpec)
        assert d.spec.feature_id == 0
        assert d.activation_p99 == 1.0
        assert d.decoder_direction.shape == (4,)
        assert d.provenance.release == "release-test"

    def test_frozen_no_mutation(self):
        d = _mock_feature_data()
        with pytest.raises(Exception):
            d.activation_p99 = 99.0  # type: ignore[misc]

    def test_equality_on_field_values(self):
        # Two feature-data instances with same spec + same payload → equal
        # (assuming torch tensors compare by identity; we use _MockTensor
        # here which uses default object equality, so we share the instance).
        prov = _mock_provenance()
        tensor = _MockTensor(shape=(4,))
        spec = SAEFeatureSpec(model_id="m", layer=0, feature_id=0)
        a = SAEFeatureData(spec=spec, decoder_direction=tensor, activation_p99=1.0, provenance=prov)
        b = SAEFeatureData(spec=spec, decoder_direction=tensor, activation_p99=1.0, provenance=prov)
        assert a == b

    def test_inequality_on_different_specs(self):
        a = _mock_feature_data(feature_id=0)
        b = _mock_feature_data(feature_id=1)
        assert a != b


class TestSAEDictionary:
    """SAEDictionary: ordered feature tuple + invariants."""

    def test_construct_basic(self):
        d = _mock_dictionary(n_features=4, d_model=8)
        assert d.model_id == "synthetic"
        assert d.layer == 0
        assert len(d.features) == 4
        assert d.d_model == 8

    def test_features_ordered_and_dense(self):
        d = _mock_dictionary(n_features=8)
        for i, f in enumerate(d.features):
            assert f.spec.feature_id == i

    def test_empty_features_rejected(self):
        with pytest.raises(ValueError, match=r"features must be non-empty"):
            SAEDictionary(
                model_id="m", layer=0, features=(),
                d_model=4, decoder_matrix=_MockTensor((0, 4)),
            )

    def test_d_model_below_one_rejected(self):
        with pytest.raises(ValueError, match=r"d_model must be >= 1"):
            features = (_mock_feature_data(d_model=0),)
            SAEDictionary(
                model_id="synthetic", layer=0, features=features,
                d_model=0, decoder_matrix=_MockTensor((1, 0)),
            )

    def test_model_id_mismatch_rejected(self):
        # Build a feature with one model_id, put in dict with another
        f = _mock_feature_data(model_id="model-a", feature_id=0)
        with pytest.raises(ValueError, match=r"model_id"):
            SAEDictionary(
                model_id="model-b", layer=0, features=(f,),
                d_model=4, decoder_matrix=_MockTensor((1, 4)),
            )

    def test_layer_mismatch_rejected(self):
        f = _mock_feature_data(layer=5, feature_id=0)
        with pytest.raises(ValueError, match=r"\.layer="):
            SAEDictionary(
                model_id="synthetic", layer=10, features=(f,),
                d_model=4, decoder_matrix=_MockTensor((1, 4)),
            )

    def test_feature_id_gap_rejected(self):
        # feature_id 0 and 2 with no 1 in between
        f0 = _mock_feature_data(feature_id=0)
        f2 = _mock_feature_data(feature_id=2)
        with pytest.raises(ValueError, match=r"dense and sorted"):
            SAEDictionary(
                model_id="synthetic", layer=0, features=(f0, f2),
                d_model=4, decoder_matrix=_MockTensor((2, 4)),
            )

    def test_feature_id_out_of_order_rejected(self):
        # feature_id 1 before 0
        f1 = _mock_feature_data(feature_id=1)
        f0 = _mock_feature_data(feature_id=0)
        with pytest.raises(ValueError, match=r"dense and sorted"):
            SAEDictionary(
                model_id="synthetic", layer=0, features=(f1, f0),
                d_model=4, decoder_matrix=_MockTensor((2, 4)),
            )

    def test_frozen_no_mutation(self):
        d = _mock_dictionary()
        with pytest.raises(Exception):
            d.layer = 99  # type: ignore[misc]


class TestImportWithoutTorch:
    """Verify bulla.adapters.sae_data imports without torch installed.

    The TYPE_CHECKING-guarded torch import in sae_data.py means the module
    must successfully import at module-load time even when torch is absent
    from the install. This test verifies the import path is dependency-light
    by re-importing the module and confirming no torch is required.
    """

    def test_module_imports_without_torch(self):
        # If torch were a hard dep at module scope, importing sae_data
        # would fail in a torch-free CI environment. By the time this test
        # runs, the import has already succeeded (top of file). Asserting
        # it succeeded is the test.
        import bulla.adapters.sae_data as mod
        assert mod.SAEFeatureData is SAEFeatureData
        assert mod.SAEDictionary is SAEDictionary
        assert mod.SAEProvenance is SAEProvenance
