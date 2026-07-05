"""Tests for bulla/adapters/sae_baseline.py (G23 A3 commit 1e).

Pure-Python aggregation tests — no torch / sae-lens needed. Verifies:
  * `compute_b0_baseline` aggregates `activation_p99` across the
    features referenced by composition edges
  * Three aggregation modes (mean, max, geometric_mean) produce the
    expected values on hand-computed cases
  * `all_zero` flag fires when every feature has activation_p99=0
    (typically: SAE loaded with activation_corpus=None)
  * Validation rejects model_id / feature_id mismatches between
    dictionary and composition
  * Empty edges → B0 = 0 with all_zero=True (gate-6 should skip)

Real-data calibration of the baseline against an actual activation
corpus lives in Iter-2 (commit 2 pre-registration §3a calibration).
"""

from __future__ import annotations

import math

import pytest

from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_compose import build_cross_model_composition
from bulla.adapters.sae_data import SAEDictionary, SAEFeatureData, SAEProvenance
from bulla.adapters.sae_baseline import (
    B0Baseline,
    _aggregate,
    compute_b0_baseline,
)


class _MockTensor:
    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape


def _mock_dictionary(
    *,
    model_id: str,
    layer: int = 0,
    n_features: int = 4,
    activation_p99_per_feature: tuple[float, ...] | None = None,
) -> SAEDictionary:
    if activation_p99_per_feature is None:
        activation_p99_per_feature = tuple(float(i + 1) for i in range(n_features))
    assert len(activation_p99_per_feature) == n_features
    features = tuple(
        SAEFeatureData(
            spec=SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=i),
            decoder_direction=_MockTensor(shape=(8,)),
            activation_p99=activation_p99_per_feature[i],
            provenance=SAEProvenance(
                release="r", sae_id="x",
                sha256="sha256:" + "0" * 64, n_p99_tokens=1024,
            ),
        )
        for i in range(n_features)
    )
    return SAEDictionary(
        model_id=model_id, layer=layer, features=features,
        d_model=8, decoder_matrix=_MockTensor((n_features, 8)),
    )


# ── _aggregate helper ───────────────────────────────────────────────────


class TestAggregate:
    """The pure-aggregation helper."""

    def test_mean(self):
        assert _aggregate((1.0, 2.0, 3.0), "mean") == 2.0

    def test_max(self):
        assert _aggregate((0.5, 3.0, 1.0), "max") == 3.0

    def test_geometric_mean(self):
        # gm(1, 4) = 2
        assert _aggregate((1.0, 4.0), "geometric_mean") == pytest.approx(2.0)

    def test_geometric_mean_skips_zeros(self):
        # gm(0, 4, 16) → drops the zero, gm(4, 16) = 8
        assert _aggregate(
            (0.0, 4.0, 16.0), "geometric_mean"
        ) == pytest.approx(math.sqrt(64.0))

    def test_geometric_mean_all_zero_returns_zero(self):
        assert _aggregate((0.0, 0.0, 0.0), "geometric_mean") == 0.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match=r"empty values"):
            _aggregate((), "mean")

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match=r"nonneg"):
            _aggregate((1.0, -0.5), "mean")

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match=r"unknown aggregation"):
            _aggregate((1.0,), "median")  # type: ignore[arg-type]


# ── compute_b0_baseline ─────────────────────────────────────────────────


class TestComputeB0Baseline:
    """End-to-end: dict + composition → baseline."""

    def test_mean_aggregation_minimal_case(self):
        # Side A: 1 feature with activation_p99 = 2.0
        # Side B: 1 feature with activation_p99 = 4.0
        # Edge: hub → spoke
        # Mean = (2 + 4) / 2 = 3.0
        d_a = _mock_dictionary(model_id="m_a", n_features=1,
                               activation_p99_per_feature=(2.0,))
        d_b = _mock_dictionary(model_id="m_b", n_features=1,
                               activation_p99_per_feature=(4.0,))
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp, aggregation="mean",
        )
        assert isinstance(b0, B0Baseline)
        assert b0.value == 3.0
        assert b0.aggregation == "mean"
        assert b0.n_features_a == 1
        assert b0.n_features_b == 1
        assert b0.all_zero is False

    def test_only_referenced_features_contribute(self):
        # Side A has 4 features; only feature 0 referenced.
        # Should aggregate ONLY feature 0's value, not all 4.
        d_a = _mock_dictionary(model_id="m_a", n_features=4,
                               activation_p99_per_feature=(10.0, 100.0, 100.0, 100.0))
        d_b = _mock_dictionary(model_id="m_b", n_features=4,
                               activation_p99_per_feature=(20.0, 100.0, 100.0, 100.0))
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp, aggregation="mean",
        )
        # Only features (0, 0) referenced; their values are (10, 20)
        assert b0.value == pytest.approx(15.0)

    def test_all_zero_flag(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=1,
                               activation_p99_per_feature=(0.0,))
        d_b = _mock_dictionary(model_id="m_b", n_features=1,
                               activation_p99_per_feature=(0.0,))
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp,
        )
        assert b0.value == 0.0
        assert b0.all_zero is True

    def test_no_edges_returns_zero_with_all_zero_flag(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=1)
        d_b = _mock_dictionary(model_id="m_b", n_features=1)
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=(),
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp,
        )
        assert b0.value == 0.0
        assert b0.all_zero is True
        assert b0.n_features_a == 0
        assert b0.n_features_b == 0

    def test_max_aggregation(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=2,
                               activation_p99_per_feature=(2.0, 5.0))
        d_b = _mock_dictionary(model_id="m_b", n_features=2,
                               activation_p99_per_feature=(7.0, 3.0))
        spec_a0 = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_a1 = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=1)
        spec_b0 = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        spec_b1 = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=1)
        comp = build_cross_model_composition(
            features_a=(spec_a0, spec_a1),
            features_b=(spec_b0, spec_b1),
            cross_model_edges=((0, 0), (1, 1)),  # references all 4 features
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp, aggregation="max",
        )
        assert b0.value == 7.0  # max of (2, 5, 7, 3)
        assert b0.aggregation == "max"

    def test_geometric_mean_aggregation(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=1,
                               activation_p99_per_feature=(2.0,))
        d_b = _mock_dictionary(model_id="m_b", n_features=1,
                               activation_p99_per_feature=(8.0,))
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        b0 = compute_b0_baseline(
            dict_a=d_a, dict_b=d_b, composition=comp,
            aggregation="geometric_mean",
        )
        # gm(2, 8) = 4
        assert b0.value == pytest.approx(4.0)


class TestComputeB0BaselineValidation:
    """Reject mismatched dicts / out-of-range feature_ids."""

    def test_model_id_mismatch_a_rejected(self):
        d_a = _mock_dictionary(model_id="actual_a", n_features=1)
        d_b = _mock_dictionary(model_id="m_b", n_features=1)
        # Composition uses different model_id than dict_a
        spec_a = SAEFeatureSpec(model_id="other_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        with pytest.raises(ValueError, match=r"model_id="):
            compute_b0_baseline(dict_a=d_a, dict_b=d_b, composition=comp)

    def test_model_id_mismatch_b_rejected(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=1)
        d_b = _mock_dictionary(model_id="actual_b", n_features=1)
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        spec_b = SAEFeatureSpec(model_id="other_b", layer=0, feature_id=0)
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        with pytest.raises(ValueError, match=r"model_id="):
            compute_b0_baseline(dict_a=d_a, dict_b=d_b, composition=comp)

    def test_feature_id_out_of_range_rejected(self):
        d_a = _mock_dictionary(model_id="m_a", n_features=1)
        d_b = _mock_dictionary(model_id="m_b", n_features=1)
        # spec_a references feature_id=5 but dict_a only has 1 feature
        spec_a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=5)
        spec_b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        # build_cross_model_composition doesn't validate against dicts
        # (it doesn't see them); the feature_id mismatch surfaces in
        # compute_b0_baseline's _pull.
        comp = build_cross_model_composition(
            features_a=(spec_a,), features_b=(spec_b,),
            cross_model_edges=((0, 0),),
        )
        with pytest.raises(ValueError, match=r"feature_id=5 out of range"):
            compute_b0_baseline(dict_a=d_a, dict_b=d_b, composition=comp)


# ── B0Baseline dataclass ───────────────────────────────────────────────


class TestB0Baseline:
    """B0Baseline: frozen, hashable, equality-by-value."""

    def test_frozen(self):
        b0 = B0Baseline(
            value=1.0, aggregation="mean",
            n_features_a=1, n_features_b=1, all_zero=False,
        )
        with pytest.raises(Exception):
            b0.value = 2.0  # type: ignore[misc]

    def test_equality(self):
        a = B0Baseline(
            value=1.0, aggregation="mean",
            n_features_a=1, n_features_b=1, all_zero=False,
        )
        b = B0Baseline(
            value=1.0, aggregation="mean",
            n_features_a=1, n_features_b=1, all_zero=False,
        )
        assert a == b

    def test_module_imports_without_torch(self):
        import bulla.adapters.sae_baseline as mod
        assert hasattr(mod, "B0Baseline")
        assert hasattr(mod, "compute_b0_baseline")
        assert hasattr(mod, "_aggregate")
