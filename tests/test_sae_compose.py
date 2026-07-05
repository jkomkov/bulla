"""Tests for bulla/adapters/sae_compose.py (G23 A3 commit 1d).

Structural tests for the cross-model 2-cover composition builder:
  * `build_cross_model_composition` produces a Composition with
    correct tools, edges, and hidden-field decoder-direction declarations
  * Validation rejects empty sides + out-of-range edge indices
  * `build_cross_model_hub_spoke` is a thin wrapper over the general
    builder; produces the canonical k-spoke structure

The 9 §3a′ tripwires (encoding capability, magnitude recovery, vanishing
control, map-invariance) live in
``test_g23_a3_synthetic_validation.py`` — this file's job is the
structural / API contract.
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae import OBSERVABLE_FIELDS, SAEFeatureSpec
from bulla.adapters.sae_compose import (
    CrossModelComposition,
    build_cross_model_composition,
    build_cross_model_hub_spoke,
)
from bulla.model import Composition


# ── Module-import + sanity ─────────────────────────────────────────────


def test_module_imports_without_torch():
    import bulla.adapters.sae_compose as mod
    assert hasattr(mod, "build_cross_model_composition")
    assert hasattr(mod, "build_cross_model_hub_spoke")
    assert hasattr(mod, "CrossModelComposition")


def test_module_assertions_hold():
    """Module-load assertions verify decoder_direction is hidden."""
    # If the OBSERVABLE_FIELDS contract changed under us, the module
    # would have failed to import (assert at module scope). Reaching
    # here means the assertion passed — but verify directly anyway so
    # the contract is documented in the test suite.
    assert "decoder_direction" not in OBSERVABLE_FIELDS


# ── build_cross_model_composition ──────────────────────────────────────


class TestBuildCrossModelComposition:
    """The core builder + its validation."""

    def test_minimal_2_tool_1_edge(self):
        a = SAEFeatureSpec(model_id="gemma2-2b", layer=20, feature_id=0)
        b = SAEFeatureSpec(model_id="gpt2-small", layer=11, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=((0, 0),),
        )
        assert isinstance(result, CrossModelComposition)
        assert isinstance(result.composition, Composition)
        assert len(result.composition.tools) == 2
        assert len(result.composition.edges) == 1
        # Edge declares decoder_direction (hidden)
        edge = result.composition.edges[0]
        assert edge.from_tool == "gemma2-2b/L20/F0"
        assert edge.to_tool == "gpt2-small/L11/F0"
        assert len(edge.dimensions) == 1
        dim = edge.dimensions[0]
        assert dim.from_field == "decoder_direction"
        assert dim.to_field == "decoder_direction"

    def test_tools_emitted_in_a_then_b_order(self):
        a0 = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        a1 = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=1)
        b0 = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a0, a1), features_b=(b0,), cross_model_edges=(),
        )
        names = tuple(t.name for t in result.composition.tools)
        assert names == ("m_a/L0/F0", "m_a/L0/F1", "m_b/L0/F0")

    def test_provenance_preserved(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        edges = ((0, 0),)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=edges,
        )
        assert result.features_a == (a,)
        assert result.features_b == (b,)
        assert result.cross_model_edges == edges

    def test_default_name_is_deterministic(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=((0, 0),),
        )
        assert result.composition.name == "sae_cross_model_a1_b1_e1"

    def test_custom_name_used(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=((0, 0),),
            name="custom",
        )
        assert result.composition.name == "custom"

    def test_empty_features_a_rejected(self):
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        with pytest.raises(ValueError, match=r"features_a must be non-empty"):
            build_cross_model_composition(
                features_a=(), features_b=(b,), cross_model_edges=(),
            )

    def test_empty_features_b_rejected(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        with pytest.raises(ValueError, match=r"features_b must be non-empty"):
            build_cross_model_composition(
                features_a=(a,), features_b=(), cross_model_edges=(),
            )

    def test_idx_a_out_of_range_rejected(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        with pytest.raises(ValueError, match=r"idx_a=5 out of range"):
            build_cross_model_composition(
                features_a=(a,), features_b=(b,), cross_model_edges=((5, 0),),
            )

    def test_idx_b_out_of_range_rejected(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        with pytest.raises(ValueError, match=r"idx_b=7 out of range"):
            build_cross_model_composition(
                features_a=(a,), features_b=(b,), cross_model_edges=((0, 7),),
            )

    def test_no_edges_is_valid(self):
        a = SAEFeatureSpec(model_id="ma", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="mb", layer=0, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=(),
        )
        assert len(result.composition.edges) == 0


# ── build_cross_model_hub_spoke ────────────────────────────────────────


class TestBuildCrossModelHubSpoke:
    """The hub-and-spoke convenience wrapper."""

    @pytest.mark.parametrize("k", [1, 2, 3, 5, 10])
    def test_k_spokes_topology(self, k):
        result = build_cross_model_hub_spoke(k=k)
        assert isinstance(result, CrossModelComposition)
        # 1 hub + k spokes (cross-model regime: fee = k from k edges)
        assert len(result.composition.tools) == 1 + k
        # k cross-model edges, all from hub (idx_a=0) to spoke i
        assert len(result.composition.edges) == k
        for i, (idx_a, idx_b) in enumerate(result.cross_model_edges):
            assert idx_a == 0
            assert idx_b == i

    def test_default_models_are_a3_locked(self):
        result = build_cross_model_hub_spoke(k=2)
        assert result.features_a[0].model_id == "gemma2-2b"
        assert result.features_a[0].layer == 20
        for spoke in result.features_b:
            assert spoke.model_id == "gpt2-small"
            assert spoke.layer == 11

    def test_invalid_k_rejected(self):
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            build_cross_model_hub_spoke(k=0)

    def test_same_model_layer_rejected(self):
        with pytest.raises(ValueError, match=r"distinct \(model, layer\)"):
            build_cross_model_hub_spoke(
                k=1,
                hub_model="m", hub_layer=0,
                spoke_model="m", spoke_layer=0,
            )

    def test_same_model_different_layer_allowed(self):
        # Cross-layer same-model is technically not "cross-model" but it's
        # not the degenerate (same model, same layer) case. Allow it for
        # multi-layer experiments that use this builder.
        result = build_cross_model_hub_spoke(
            k=1, hub_model="m", hub_layer=10, spoke_model="m", spoke_layer=20,
        )
        assert result is not None

    def test_default_name_encodes_k(self):
        result = build_cross_model_hub_spoke(k=3)
        assert "k3" in result.composition.name


# ── CrossModelComposition equality + frozenness ────────────────────────


class TestCrossModelComposition:
    """The result dataclass: frozen + hashable."""

    def test_frozen(self):
        result = build_cross_model_hub_spoke(k=1)
        with pytest.raises(Exception):
            result.composition = None  # type: ignore[misc]

    def test_equality(self):
        r1 = build_cross_model_hub_spoke(k=2)
        r2 = build_cross_model_hub_spoke(k=2)
        assert r1 == r2
        r3 = build_cross_model_hub_spoke(k=3)
        assert r1 != r3
