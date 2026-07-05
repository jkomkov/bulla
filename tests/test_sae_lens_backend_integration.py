"""Integration tests for sae_lens_backend gated on real sae-lens install.

Skipped automatically if `sae-lens` is not installed (i.e., the [g23-a3]
extras tag was not applied). When run, these tests actually call into
sae-lens to load a small SAE checkpoint and verify the SAEDictionary
construction round-trip.

Run locally with:
    pip install 'bulla[g23-a3]'
    pytest bulla/tests/test_sae_lens_backend_integration.py -v -s

In CI, these tests skip cleanly without installing the [g23-a3] extras.
"""

from __future__ import annotations

import pytest

# Network/heavy integration: loads a real SAE checkpoint (model download). Deselected by
# default (`-m "not network"`); also importorskip'd below if the extra isn't installed.
pytestmark = pytest.mark.network

# Skip the entire module if sae-lens is not installed
sae_lens = pytest.importorskip("sae_lens")
torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")


from bulla.adapters.sae_lens_backend import (
    ActivationCorpus,
    load_sae_dictionary,
    release_for,
)


@pytest.mark.slow
class TestRealSAELoading:
    """Actually call sae-lens. Skipped if [g23-a3] not installed."""

    def test_load_gpt2_small_sae_no_corpus(self):
        """Smallest possible real load: GPT-2-Small SAE, no activation corpus.

        Expected: SAEDictionary with n_features features, all
        activation_p99 = 0.0, provenance.n_p99_tokens = 0.

        Cost: ~5min on CPU; ~1min on GPU. Marked @slow so CI can
        deselect via `-m "not slow"`.
        """
        release, sae_id = release_for("gpt2-small", 11)
        d = load_sae_dictionary(
            release=release,
            sae_id=sae_id,
            model_id="gpt2-small",
            layer=11,
            activation_corpus=None,  # skip activation_p99 estimation
        )
        # GPT-2-Small with this SAE: ~32k features, d_model=768
        assert d.model_id == "gpt2-small"
        assert d.layer == 11
        assert d.d_model == 768
        assert len(d.features) > 0  # don't pin exact count; sae-lens may evolve
        # All activation_p99 should be 0 (corpus=None)
        for f in d.features:
            assert f.activation_p99 == 0.0
            assert f.provenance.n_p99_tokens == 0
            assert f.provenance.release == release


@pytest.mark.slow
@pytest.mark.gpu
class TestRealSAELoadingWithCorpus:
    """End-to-end: load SAE + run activations on small corpus.

    Marked @gpu since Gemma-2-2B realistically needs A100. CI deselect
    via `-m "not gpu"`. Local run requires HF_TOKEN in environment.
    """

    def test_load_gpt2_small_sae_with_tiny_corpus(self):
        """Run a tiny 5-doc corpus through GPT-2-Small SAE.

        This is a small enough load to run on CPU in ~30 minutes for
        full per-feature p99 estimation, or ~5 minutes if we limit to
        the first 1000 features. Useful as a real-data smoke test
        before committing to the full Iter-2 calibration spot-check.
        """
        corpus = ActivationCorpus(
            dataset_id="monology/pile-uncopyrighted",
            split="train",
            indices=tuple(range(5)),  # 5 docs only
            seed=20260507,
            max_tokens_per_doc=128,  # short docs for CPU feasibility
        )
        release, sae_id = release_for("gpt2-small", 11)
        d = load_sae_dictionary(
            release=release,
            sae_id=sae_id,
            model_id="gpt2-small",
            layer=11,
            activation_corpus=corpus,
        )
        assert d.provenance.n_p99_tokens > 0  # type: ignore[union-attr]
        # Some features should have non-zero p99 (the model is
        # actually being run; activations are sparse but not all-zero)
        any_nonzero = any(f.activation_p99 > 0 for f in d.features)
        assert any_nonzero, (
            "No feature activations were non-zero across 5-doc corpus — "
            "either the SAE is degenerate or the activation pipeline broke."
        )
