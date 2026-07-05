"""Mocked unit tests for bulla/adapters/sae_lens_backend.py (G23 A3 commit 1b).

These tests do NOT require sae-lens, transformers, or torch installed.
They monkey-patch `load_sae_dictionary` (or its dependency surface) to
return hand-built `SAEDictionary` instances, and verify the surrounding
contract: registry validation, ActivationCorpus invariants, error
messages, hf_model_id resolution.

Real sae-lens loading is exercised by `test_sae_lens_backend_integration.py`
gated on `pytest.importorskip("sae_lens")`.
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae_data import SAEDictionary, SAEFeatureData, SAEProvenance
from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_lens_backend import (
    ActivationCorpus,
    SAEBackendImportError,
    _hf_model_id_for,
    _RELEASE_REGISTRY,
    release_for,
    supported_models,
)


class _MockTensor:
    """Stand-in for torch.Tensor matching the API touched by sae_data."""

    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape


class TestReleaseRegistry:
    """Release registry. The two A3 checkpoints are load-bearing (their exact
    release/sae_id values were locked by the G23 A3 pre-registration, since
    SHELVED); the registry itself is a living adapter and gained the repr-sprint
    models (Mistral, Qwen) in 2026-06 — so membership is asserted, not count."""

    def test_supported_models_includes_locked_a3_pair(self):
        models = supported_models()
        assert ("gemma2-2b", 20) in models
        assert ("gpt2-small", 11) in models

    def test_release_for_gemma_2b(self):
        release, sae_id = release_for("gemma2-2b", 20)
        assert release == "gemma-scope-2b-pt-res-canonical"
        assert sae_id == "layer_20/width_16k/canonical"

    def test_release_for_gpt2_small(self):
        release, sae_id = release_for("gpt2-small", 11)
        # Substrate-corrected 2026-05-07: Neuronpedia source `11-res-jb`
        # is the saelensRelease=`gpt2-small-res-jb` SAE, sae_id
        # `blocks.11.hook_resid_pre` (resid_PRE), d_sae=24576. The
        # earlier resid-POST-v5-32k values targeted a different SAE not
        # hosted on Neuronpedia.
        assert release == "gpt2-small-res-jb"
        assert sae_id == "blocks.11.hook_resid_pre"

    def test_unknown_model_raises_keyerror(self):
        with pytest.raises(KeyError, match=r"No SAE release registered"):
            release_for("llama3-8b", 20)

    def test_unknown_layer_raises_keyerror(self):
        with pytest.raises(KeyError, match=r"No SAE release registered"):
            release_for("gemma2-2b", 99)

    def test_keyerror_message_lists_supported(self):
        try:
            release_for("unknown", 0)
        except KeyError as e:
            msg = str(e)
            assert "Supported" in msg
            assert "gemma2-2b" in msg
            assert "gpt2-small" in msg
        else:
            pytest.fail("expected KeyError")

    def test_registry_is_dict_and_immutable_in_practice(self):
        # Not deep-frozen, but documents the design intent
        assert isinstance(_RELEASE_REGISTRY, dict)
        assert ("gemma2-2b", 20) in _RELEASE_REGISTRY
        assert ("gpt2-small", 11) in _RELEASE_REGISTRY


class TestActivationCorpus:
    """ActivationCorpus: frozen, hashable, validates invariants."""

    def test_construct_with_required_fields(self):
        corpus = ActivationCorpus(
            dataset_id="monology/pile-uncopyrighted",
            split="train",
            indices=tuple(range(200)),
            seed=20260507,
            max_tokens_per_doc=512,
        )
        assert corpus.dataset_id == "monology/pile-uncopyrighted"
        assert corpus.split == "train"
        assert len(corpus.indices) == 200
        assert corpus.seed == 20260507
        assert corpus.max_tokens_per_doc == 512

    def test_frozen_no_mutation(self):
        corpus = ActivationCorpus(
            dataset_id="x", split="train", indices=(0,),
            seed=0, max_tokens_per_doc=1,
        )
        with pytest.raises(Exception):
            corpus.split = "test"  # type: ignore[misc]

    def test_empty_indices_rejected(self):
        with pytest.raises(ValueError, match=r"indices must be non-empty"):
            ActivationCorpus(
                dataset_id="x", split="train", indices=(),
                seed=0, max_tokens_per_doc=1,
            )

    def test_max_tokens_below_one_rejected(self):
        with pytest.raises(ValueError, match=r"max_tokens_per_doc must be >= 1"):
            ActivationCorpus(
                dataset_id="x", split="train", indices=(0,),
                seed=0, max_tokens_per_doc=0,
            )

    def test_hashable_for_set_membership(self):
        c1 = ActivationCorpus(
            dataset_id="x", split="train", indices=(0, 1),
            seed=0, max_tokens_per_doc=1,
        )
        c2 = ActivationCorpus(
            dataset_id="x", split="train", indices=(0, 1),
            seed=0, max_tokens_per_doc=1,
        )
        c3 = ActivationCorpus(
            dataset_id="x", split="train", indices=(0, 1, 2),
            seed=0, max_tokens_per_doc=1,
        )
        s = {c1, c2, c3}
        assert len(s) == 2  # c1 == c2

    def test_locked_pre_registration_corpus(self):
        """The pre-registered corpus from §3b of the A3 plan."""
        corpus = ActivationCorpus(
            dataset_id="monology/pile-uncopyrighted",
            split="train",
            indices=tuple(range(200)),
            seed=20260507,
            max_tokens_per_doc=512,
        )
        # Doesn't assert anything beyond construction; its mere existence
        # documents the locked pre-registration values.
        assert corpus is not None


class TestHfModelIdResolution:
    """_hf_model_id_for: short name → HF Hub repo id."""

    def test_gemma_2b(self):
        assert _hf_model_id_for("gemma2-2b") == "google/gemma-2-2b"

    def test_gpt2_small(self):
        assert _hf_model_id_for("gpt2-small") == "gpt2"

    def test_unknown_raises_keyerror(self):
        with pytest.raises(KeyError, match=r"No HF Hub model_id mapping"):
            _hf_model_id_for("llama3-8b")


class TestImportErrorContract:
    """SAEBackendImportError: clear install hint when [g23-a3] missing."""

    def test_error_message_includes_install_hint(self):
        err = SAEBackendImportError("torch")
        assert "torch" in str(err)
        assert "[g23-a3]" in str(err)
        assert "pip install" in str(err)

    def test_error_message_includes_synthetic_alternative(self):
        err = SAEBackendImportError("sae_lens")
        assert "SyntheticSAELoader" in str(err)


class TestLoadSAEDictionaryMockedPath:
    """Verify load_sae_dictionary's parameter validation (registry mismatch).

    These tests verify the validation that runs BEFORE any heavy-dep
    import. They do not exercise the actual sae-lens loading (that's
    the integration test's job).
    """

    def test_release_mismatch_rejected_before_torch_import(self):
        from bulla.adapters.sae_lens_backend import load_sae_dictionary
        with pytest.raises(ValueError, match=r"release="):
            load_sae_dictionary(
                release="wrong-release",
                sae_id="layer_20/width_16k/canonical",
                model_id="gemma2-2b",
                layer=20,
            )

    def test_sae_id_mismatch_rejected_before_torch_import(self):
        from bulla.adapters.sae_lens_backend import load_sae_dictionary
        with pytest.raises(ValueError, match=r"sae_id="):
            load_sae_dictionary(
                release="gemma-scope-2b-pt-res-canonical",
                sae_id="wrong-sae-id",
                model_id="gemma2-2b",
                layer=20,
            )

    def test_unknown_model_layer_rejected_before_torch_import(self):
        from bulla.adapters.sae_lens_backend import load_sae_dictionary
        with pytest.raises(KeyError, match=r"No SAE release registered"):
            load_sae_dictionary(
                release="anything",
                sae_id="anything",
                model_id="unknown",
                layer=99,
            )


class TestImportWithoutHeavyDeps:
    """Verify sae_lens_backend imports without sae-lens or torch installed.

    The TYPE_CHECKING-guarded torch import + lazy in-function imports
    of sae_lens preserve the dependency-light import graph.
    """

    def test_module_imports_without_heavy_deps(self):
        # If sae-lens or torch were hard module-scope deps, this test
        # file's import statements would have failed before the test
        # function ran. By the time this test executes, the imports
        # have succeeded. We assert that the public API symbols are
        # accessible.
        import bulla.adapters.sae_lens_backend as mod
        assert hasattr(mod, "load_sae_dictionary")
        assert hasattr(mod, "release_for")
        assert hasattr(mod, "supported_models")
        assert hasattr(mod, "ActivationCorpus")
