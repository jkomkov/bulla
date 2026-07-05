"""Tests for bulla/adapters/sae_loader.py and the A2 smoke test (G23 Stage A A2).

Three test groups:
  1. SyntheticSAELoader: deterministic, well-formed, no API.
  2. HuggingFaceSAELoader: stubbed pending A3 wiring; correct error.
  3. default_loader(): env-var-driven backend selection works.
  4. A2 smoke test: 1 composition × 8 features × 12 cross-layer edges
     → finite coherence_fee, decompose_fee runs cleanly.

The A2 smoke test is the Stage A week-2 deliverable per the G23 plan.
It verifies the loader → composition-builder → diagnostic pipeline runs
end-to-end on a structurally-realistic single-model multi-layer
composition without requiring HF credentials or model weights.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from bulla.adapters.sae import (
    SAEFeatureSpec,
    build_multi_layer_composition,
)
from bulla.adapters.sae_loader import (
    HuggingFaceSAELoader,
    SAELoader,
    SyntheticSAELoader,
    default_loader,
)
from bulla.diagnostic import decompose_fee, diagnose


class TestSyntheticSAELoader:
    """Deterministic in-memory loader; no API calls."""

    def test_satisfies_loader_protocol(self):
        loader = SyntheticSAELoader()
        assert isinstance(loader, SAELoader)

    def test_returns_n_features_in_order(self):
        loader = SyntheticSAELoader()
        features = loader.load_features(
            model_id="m", layer=0, n_features=5
        )
        assert len(features) == 5
        for i, f in enumerate(features):
            assert f.feature_id == i
            assert f.model_id == "m"
            assert f.layer == 0

    def test_deterministic_across_calls(self):
        a = SyntheticSAELoader().load_features(
            model_id="gpt2-small", layer=11, n_features=8
        )
        b = SyntheticSAELoader().load_features(
            model_id="gpt2-small", layer=11, n_features=8
        )
        assert a == b

    def test_n_features_below_one_rejected(self):
        with pytest.raises(ValueError, match=r"n_features must be >= 1"):
            SyntheticSAELoader().load_features(
                model_id="m", layer=0, n_features=0
            )

    def test_zero_seed_is_default(self):
        assert SyntheticSAELoader().seed == 0


class TestSyntheticSAELoaderDictionary:
    """SyntheticSAELoader.load_dictionary: lazy torch import + determinism."""

    def test_load_dictionary_returns_well_formed_dict(self):
        torch = pytest.importorskip("torch")
        loader = SyntheticSAELoader()
        d = loader.load_dictionary(model_id="m", layer=0)
        # Default config: 64 features, d_model=8
        assert d.model_id == "m"
        assert d.layer == 0
        assert len(d.features) == 64
        assert d.d_model == 8
        # decoder_matrix is a real torch.Tensor of correct shape
        assert d.decoder_matrix.shape == (64, 8)
        # All features have feature_id matching position
        for i, f in enumerate(d.features):
            assert f.spec.feature_id == i
            assert f.activation_p99 == 0.0  # no corpus → 0
        # Provenance recorded
        assert d.features[0].provenance.n_p99_tokens == 0

    def test_load_dictionary_with_corpus_marker_sets_p99(self):
        pytest.importorskip("torch")
        # The SyntheticSAELoader treats `activation_corpus is not None` as
        # a signal to populate placeholder positive p99 values; the
        # marker can be any non-None object since the synthetic backend
        # doesn't actually consume it.
        loader = SyntheticSAELoader()
        d = loader.load_dictionary(
            model_id="m", layer=0, activation_corpus=object(),
        )
        for f in d.features:
            assert f.activation_p99 == 1.0
        assert d.features[0].provenance.n_p99_tokens == 1024

    def test_load_dictionary_deterministic_per_seed(self):
        torch = pytest.importorskip("torch")
        loader_a = SyntheticSAELoader(seed=42)
        loader_b = SyntheticSAELoader(seed=42)
        d_a = loader_a.load_dictionary(model_id="m", layer=0)
        d_b = loader_b.load_dictionary(model_id="m", layer=0)
        # Same seed → identical decoder tensor
        assert torch.equal(d_a.decoder_matrix, d_b.decoder_matrix)

    def test_load_dictionary_varies_per_layer(self):
        torch = pytest.importorskip("torch")
        loader = SyntheticSAELoader(seed=42)
        d_l0 = loader.load_dictionary(model_id="m", layer=0)
        d_l1 = loader.load_dictionary(model_id="m", layer=1)
        # Different layer → different tensor (no degenerate hash collision)
        assert not torch.equal(d_l0.decoder_matrix, d_l1.decoder_matrix)


class TestHuggingFaceSAELoader:
    """A3-wired: load_features is identifier-only (cheap, parity with
    Synthetic); load_dictionary delegates to sae_lens_backend (heavy)."""

    def test_satisfies_loader_protocol(self):
        loader = HuggingFaceSAELoader(hf_token="dummy")
        assert isinstance(loader, SAELoader)

    def test_load_features_returns_identifier_only_specs(self):
        # load_features no longer raises NotImplementedError after A3
        # commit 1f. It returns identifier-only SAEFeatureSpec instances
        # (no network call); parity with SyntheticSAELoader. Real
        # tensor loading happens in load_dictionary.
        loader = HuggingFaceSAELoader(hf_token="dummy")
        features = loader.load_features(
            model_id="gemma2-2b", layer=20, n_features=4
        )
        assert len(features) == 4
        for i, f in enumerate(features):
            assert f.model_id == "gemma2-2b"
            assert f.layer == 20
            assert f.feature_id == i

    def test_load_features_n_below_one_rejected(self):
        loader = HuggingFaceSAELoader()
        with pytest.raises(ValueError, match=r"n_features must be >= 1"):
            loader.load_features(model_id="gemma2-2b", layer=20, n_features=0)

    def test_no_hf_token_constructable(self):
        """Construction without token is allowed; load_features still works."""
        loader = HuggingFaceSAELoader()
        assert loader.hf_token is None
        # load_features doesn't need the token (no network call)
        features = loader.load_features(model_id="gpt2-small", layer=11, n_features=2)
        assert len(features) == 2

    def test_load_dictionary_unknown_model_raises_keyerror(self):
        # Routes through release_for(); unknown model is rejected
        # before any heavy import.
        loader = HuggingFaceSAELoader(hf_token="dummy")
        with pytest.raises(KeyError, match=r"No SAE release registered"):
            loader.load_dictionary(model_id="llama3-8b", layer=20)

    def test_load_dictionary_invalid_corpus_type_rejected(self):
        loader = HuggingFaceSAELoader(hf_token="dummy")
        with pytest.raises(TypeError, match=r"activation_corpus must be"):
            loader.load_dictionary(
                model_id="gemma2-2b", layer=20,
                activation_corpus="not-an-ActivationCorpus",
            )


class TestDefaultLoader:
    """Env-var-driven backend selection."""

    def test_no_env_var_returns_synthetic(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BULLA_SAE_BACKEND", None)
            assert isinstance(default_loader(), SyntheticSAELoader)

    def test_synthetic_env_var_returns_synthetic(self):
        with mock.patch.dict(os.environ, {"BULLA_SAE_BACKEND": "synthetic"}):
            assert isinstance(default_loader(), SyntheticSAELoader)

    def test_huggingface_env_var_with_token_returns_huggingface(self):
        with mock.patch.dict(
            os.environ,
            {"BULLA_SAE_BACKEND": "huggingface", "HF_TOKEN": "tok"},
        ):
            loader = default_loader()
            assert isinstance(loader, HuggingFaceSAELoader)
            assert loader.hf_token == "tok"

    def test_huggingface_env_var_without_token_raises(self):
        with mock.patch.dict(
            os.environ,
            {"BULLA_SAE_BACKEND": "huggingface"},
            clear=False,
        ):
            os.environ.pop("HF_TOKEN", None)
            with pytest.raises(RuntimeError, match=r"HF_TOKEN is not set"):
                default_loader()

    def test_unknown_backend_rejected(self):
        with mock.patch.dict(os.environ, {"BULLA_SAE_BACKEND": "modal"}):
            with pytest.raises(ValueError, match=r"Unrecognised BULLA_SAE_BACKEND"):
                default_loader()


class TestMultiLayerCompositionBuilder:
    """build_multi_layer_composition unit tests."""

    def test_empty_features_rejected(self):
        with pytest.raises(ValueError, match=r"features must be non-empty"):
            build_multi_layer_composition(
                features=(),
                cross_layer_edges=(),
            )

    def test_out_of_range_edge_rejected(self):
        feats = (
            SAEFeatureSpec(model_id="m", layer=0, feature_id=0),
            SAEFeatureSpec(model_id="m", layer=1, feature_id=0),
        )
        with pytest.raises(ValueError, match=r"out of range"):
            build_multi_layer_composition(
                features=feats,
                cross_layer_edges=((0, 5),),
            )

    def test_each_edge_has_two_natural_m2_dimensions(self):
        feats = SyntheticSAELoader().load_features(
            model_id="m", layer=0, n_features=2
        )
        comp = build_multi_layer_composition(
            features=feats, cross_layer_edges=((0, 1),)
        )
        assert len(comp.edges) == 1
        edge = comp.edges[0]
        dim_names = {d.from_field for d in edge.dimensions}
        assert dim_names == {"identifier", "activation_p99"}

    def test_default_name_encodes_counts(self):
        feats = SyntheticSAELoader().load_features(
            model_id="m", layer=0, n_features=3
        )
        comp = build_multi_layer_composition(
            features=feats, cross_layer_edges=((0, 1), (1, 2))
        )
        assert comp.name == "sae_multi_layer_n3_e2"

    def test_explicit_name_used(self):
        feats = SyntheticSAELoader().load_features(
            model_id="m", layer=0, n_features=2
        )
        comp = build_multi_layer_composition(
            features=feats,
            cross_layer_edges=((0, 1),),
            name="my_test_comp",
        )
        assert comp.name == "my_test_comp"


class TestA2SmokeTest:
    """G23 Stage A A2 smoke test: end-to-end pipeline runs cleanly.

    Per the SAE/MI plan A2 spec:
        "Smoke test: 1 composition × 8 features × 12 cross-layer
         edges → finite coherence_fee. Verify
         bulla.diagnostic.decompose_fee decomposes cleanly."

    The composition is single-model (Gemma 2 2B-shaped) with 8 features
    spread across 2 layers (4 in L_a, 4 in L_b) and 12 cross-layer
    edges. With only natural-M2 dimensions on edges, the expected
    coherence_fee is exactly 0 — the smoke test verifies the pipeline
    runs end-to-end and produces the structurally-correct fee.
    """

    @staticmethod
    def _build_a2_composition():
        """Build the canonical A2 smoke-test composition.

        Single synthetic-Gemma model, 4 features at layer 0 (indices
        0-3) + 4 features at layer 1 (indices 4-7), 12 cross-layer
        edges spanning layer-0 → layer-1 (each layer-0 feature
        connects to 3 of the 4 layer-1 features).
        """
        loader = SyntheticSAELoader()
        layer_0 = loader.load_features(
            model_id="synthetic-gemma2-2b", layer=0, n_features=4
        )
        layer_1 = loader.load_features(
            model_id="synthetic-gemma2-2b", layer=1, n_features=4
        )
        # Concatenate features: indices 0-3 are layer 0; 4-7 are layer 1.
        # But SAEFeatureSpec uses (model, layer, feature_id) so we re-id.
        features = layer_0 + tuple(
            SAEFeatureSpec(
                model_id="synthetic-gemma2-2b", layer=1, feature_id=i
            )
            for i in range(4)
        )
        # 12 cross-layer edges: each L0 feature → 3 of 4 L1 features.
        cross_layer_edges = tuple(
            (src, tgt)
            for src in range(4)
            for tgt_offset in range(3)
            for tgt in [4 + ((src + tgt_offset) % 4)]
        )
        assert len(cross_layer_edges) == 12
        return build_multi_layer_composition(
            features=features,
            cross_layer_edges=cross_layer_edges,
            name="a2_smoke_test",
        )

    def test_composition_has_expected_shape(self):
        comp = self._build_a2_composition()
        assert len(comp.tools) == 8
        assert len(comp.edges) == 12

    def test_diagnose_returns_finite_fee(self):
        comp = self._build_a2_composition()
        diag = diagnose(comp)
        # Finite means non-negative integer (well-formed regime).
        assert isinstance(diag.coherence_fee, int)
        assert diag.coherence_fee >= 0

    def test_diagnose_returns_zero_fee_on_natural_m2_surface(self):
        """All-observable-field edges → no obstruction → fee=0.

        This is the structurally-correct result for the A2 smoke test:
        the pipeline doesn't synthesise spurious obstruction on a
        natural-M2 multi-layer composition. (The hub-and-spoke
        positive control in test_adapters_sae.py is where designed
        fee>0 is verified.)
        """
        diag = diagnose(self._build_a2_composition())
        assert diag.coherence_fee == 0, (
            f"A2 smoke test expects fee=0 on natural-M2 edges; "
            f"got fee={diag.coherence_fee}. The pipeline produced "
            f"spurious obstruction on a multi-layer composition."
        )

    def test_decompose_fee_runs_cleanly(self):
        """decompose_fee on the A2 composition runs without error."""
        comp = self._build_a2_composition()
        # Partition: layer 0 vs layer 1.
        layer_0_names = frozenset(t.name for t in comp.tools[:4])
        layer_1_names = frozenset(t.name for t in comp.tools[4:])
        partition = [layer_0_names, layer_1_names]
        decomposition = decompose_fee(comp, partition)
        # On the natural-M2 surface, total fee is 0 and decomposes
        # trivially: local fees are 0 within each layer (no within-
        # layer edges), boundary fee is 0 (cross-layer edges declare
        # observable dims only).
        assert decomposition.total_fee == 0
        assert decomposition.boundary_fee == 0

    def test_a2_gate_pipeline_runs_end_to_end(self):
        """Atomic A2 PASS check: loader → builder → diagnostic → decompose.

        If this test passes, the SAE adapter pipeline runs cleanly
        end-to-end on a structurally-realistic single-model multi-
        layer composition. A3 (cross-model 2-cover with restriction-
        map ablation) is unblocked from the structural-pipeline
        precondition.
        """
        comp = self._build_a2_composition()
        diag = diagnose(comp)
        assert diag.coherence_fee == 0, "A2 smoke test FAILED: pipeline obstruction"
        assert diag.n_tools == 8
        assert diag.n_edges == 12
        assert diag.blind_spots == ()  # no hidden-field claims = no blind spots
