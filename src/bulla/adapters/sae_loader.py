"""Backend-agnostic SAE feature loader (G23 Stage A A2 + A3 deliverable).

A2 (Stage A week 2) needs the loader interface stable so:
  * A1 controls run without any external dependency (synthetic backend).
  * The A2 single-model multi-layer prototype smoke test runs without
    HuggingFace credentials (synthetic backend).
  * A3 (cross-model 2-cover with restriction-map ablation) swaps in the
    HuggingFace backend by setting one env var, with no code change
    elsewhere.

Two backends:

  * ``SyntheticSAELoader`` — deterministic, no API calls, no compute.
    For tests, A1 controls, and A2 smoke tests. Emits well-formed
    SAEFeatureSpec instances for any (model_id, layer, n_features)
    combination. Determinism: identical inputs produce identical
    outputs byte-for-byte. ``load_dictionary`` produces a synthetic
    ``SAEDictionary`` with deterministic placeholder tensors via lazy
    torch import (raises ``SAEBackendImportError`` if torch absent).

  * ``HuggingFaceSAELoader`` — A3-wired. ``load_features`` returns
    identifier-only SAEFeatureSpec instances (same as Synthetic for
    parity); ``load_dictionary`` delegates to
    ``sae_lens_backend.load_sae_dictionary`` for the real HF download
    via the sae-lens library. Both methods require the [g23-a3] extras;
    ``SAEBackendImportError`` raised otherwise.

Default backend selection via ``default_loader()``:
  * BULLA_SAE_BACKEND=synthetic (default): SyntheticSAELoader
  * BULLA_SAE_BACKEND=huggingface: HuggingFaceSAELoader (requires HF_TOKEN)

Per the G23 plan, A2 smoke test target:
  1 composition × 8 features × 12 cross-layer edges → finite coherence_fee.

The synthetic backend supports all of this; no API keys required to
verify the A2 smoke test passes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from bulla.adapters.sae import SAEFeatureSpec

if TYPE_CHECKING:
    from bulla.adapters.sae_data import SAEDictionary


@runtime_checkable
class SAELoader(Protocol):
    """Protocol any SAE loader backend must satisfy.

    Implementations: ``SyntheticSAELoader`` (deterministic, in-memory),
    ``HuggingFaceSAELoader`` (A3 — loads from HF Hub via sae-lens).

    Two-method API:
      * ``load_features``: lightweight, identifier-only. No tensor
        data; no network call. Same on both backends.
      * ``load_dictionary``: heavyweight. Returns SAEDictionary with
        real decoder tensors + activation_p99 statistics. Synthetic
        backend produces deterministic placeholder tensors;
        HuggingFace backend downloads a real SAE checkpoint.
    """

    def load_features(
        self,
        *,
        model_id: str,
        layer: int,
        n_features: int,
    ) -> tuple[SAEFeatureSpec, ...]:
        """Return a tuple of n_features SAEFeatureSpec instances.

        Args:
            model_id: model identifier (e.g., 'gemma2-2b', 'gpt2-small',
                or any synthetic id like 'synthetic-model-a').
            layer: layer index (e.g., 0, 5, 11, 20).
            n_features: number of features to return; must be >= 1.

        Returns:
            Tuple of n_features SAEFeatureSpec instances with feature_id
            in [0, n_features).

        Raises:
            ValueError: if n_features < 1.
            RuntimeError: if backend prerequisites are unmet (e.g.,
                HF_TOKEN missing for HuggingFace backend).
        """
        ...

    def load_dictionary(
        self,
        *,
        model_id: str,
        layer: int,
        activation_corpus: object | None = None,
    ) -> "SAEDictionary":
        """Return a full SAEDictionary with decoder tensors + activation_p99.

        Synthetic backend: deterministic placeholder tensors via torch
        (lazy import; raises SAEBackendImportError if torch absent).
        HuggingFace backend: delegates to
        ``sae_lens_backend.load_sae_dictionary`` for the real HF
        download. ``activation_corpus`` (if provided) drives
        per-feature activation_p99 estimation on real activations.

        Args:
            model_id: 'gemma2-2b' or 'gpt2-small' for HF backend; any
                identifier for the Synthetic backend.
            layer: layer index. For HF: must match the locked release
                registry (gemma2-2b/L20, gpt2-small/L11).
            activation_corpus: optional ActivationCorpus (per
                ``sae_lens_backend.ActivationCorpus``). If None, all
                ``activation_p99`` values are 0.0.

        Returns:
            SAEDictionary with `len(features)` features (16k or 32k
            depending on the SAE checkpoint) and a real
            decoder_matrix tensor.

        Raises:
            SAEBackendImportError: required heavy dependencies absent.
            KeyError: HF backend with unknown (model_id, layer) pair.
        """
        ...


@dataclass(frozen=True)
class SyntheticSAELoader:
    """Deterministic synthetic loader. No API or compute required.

    For tests, A1 controls, and A2 smoke tests. Identical
    (model_id, layer, n_features) input produces identical
    SAEFeatureSpec sequence output, byte-for-byte. The ``seed`` field
    drives synthetic decoder-tensor generation in ``load_dictionary``
    (so two SyntheticSAELoader instances with the same seed produce
    identical SAEDictionary tensors).

    ``load_dictionary`` lazy-imports torch; raises
    ``SAEBackendImportError`` if torch is absent (since SAEDictionary
    requires real tensors for its ``decoder_matrix`` field).
    """

    seed: int = 0
    n_features_default: int = 64
    d_model_default: int = 8

    def load_features(
        self,
        *,
        model_id: str,
        layer: int,
        n_features: int,
    ) -> tuple[SAEFeatureSpec, ...]:
        if n_features < 1:
            raise ValueError(f"n_features must be >= 1; got {n_features}")
        return tuple(
            SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=i)
            for i in range(n_features)
        )

    def load_dictionary(
        self,
        *,
        model_id: str,
        layer: int,
        activation_corpus: object | None = None,
    ) -> "SAEDictionary":
        """Build a deterministic synthetic SAEDictionary with placeholder tensors.

        Tensors are generated from a torch ``Generator`` seeded with
        ``self.seed + hash((model_id, layer)) & 0xFFFFFFFF`` for
        repeatable but per-(model, layer) variation. ``activation_p99``
        is set to ``0.0`` for every feature when ``activation_corpus``
        is None (matches the HF backend's empty-corpus convention);
        otherwise to a deterministic positive value.
        """
        try:
            import torch
        except ImportError as e:
            from bulla.adapters.sae_lens_backend import SAEBackendImportError
            raise SAEBackendImportError("torch") from e

        from bulla.adapters.sae_data import (
            SAEDictionary,
            SAEFeatureData,
            SAEProvenance,
        )

        n = self.n_features_default
        d_model = self.d_model_default
        seed = (self.seed + hash((model_id, layer))) & 0xFFFFFFFF
        gen = torch.Generator().manual_seed(seed)
        decoder_matrix = torch.randn(n, d_model, generator=gen)

        # When a corpus is provided, give synthetic features a small
        # positive activation_p99 so downstream baselines don't see
        # all-zero. Without a corpus, follow the HF-backend convention
        # of activation_p99=0.0.
        p99_default = 0.0
        n_p99_tokens = 0
        if activation_corpus is not None:
            p99_default = 1.0
            n_p99_tokens = 1024  # synthetic placeholder

        provenance = SAEProvenance(
            release=f"synthetic/{model_id}/L{layer}",
            sae_id=f"layer_{layer}/synthetic",
            sha256="sha256:" + "0" * 64,
            n_p99_tokens=n_p99_tokens,
        )

        features = tuple(
            SAEFeatureData(
                spec=SAEFeatureSpec(
                    model_id=model_id, layer=layer, feature_id=i,
                ),
                decoder_direction=decoder_matrix[i],
                activation_p99=p99_default,
                provenance=provenance,
            )
            for i in range(n)
        )
        return SAEDictionary(
            model_id=model_id,
            layer=layer,
            features=features,
            d_model=d_model,
            decoder_matrix=decoder_matrix,
        )


@dataclass(frozen=True)
class HuggingFaceSAELoader:
    """HuggingFace-hosted SAE loader. A3-wired.

    ``load_features`` returns identifier-only SAEFeatureSpec instances
    (cheap; no network call; same as Synthetic for parity).

    ``load_dictionary`` delegates to
    ``sae_lens_backend.load_sae_dictionary`` for the real HF download
    via the sae-lens library. Loads Gemma-Scope-2
    (``gemma-scope-2b-pt-res-canonical``) for ``gemma2-2b`` at L20 or
    jbloom GPT-2-Small (``gpt2-small-res-jb``, blocks.11.hook_resid_pre,
    24,576 features) for ``gpt2-small`` at L11 — the locked A3 SAE pair
    matching Neuronpedia auto-interp coverage.

    Both methods require the [g23-a3] extras for any HF-backed call.
    The hf_token field is forwarded to sae-lens' download path via the
    HF_TOKEN environment variable convention; ``hf_token=None`` is
    allowed if HF_TOKEN is set externally.
    """

    hf_token: str | None = None

    def load_features(
        self,
        *,
        model_id: str,
        layer: int,
        n_features: int,
    ) -> tuple[SAEFeatureSpec, ...]:
        """Return n_features identifier-only SAEFeatureSpec instances.

        Same behavior as ``SyntheticSAELoader.load_features``; no
        network call. Real decoder tensors are loaded via
        ``load_dictionary``.
        """
        if n_features < 1:
            raise ValueError(f"n_features must be >= 1; got {n_features}")
        return tuple(
            SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=i)
            for i in range(n_features)
        )

    def load_dictionary(
        self,
        *,
        model_id: str,
        layer: int,
        activation_corpus: object | None = None,
    ) -> "SAEDictionary":
        """Delegate to sae_lens_backend for the real HF download.

        Lazy-imports the sae-lens backend module so this loader can be
        constructed (and load_features called) without [g23-a3] extras
        installed. Only load_dictionary requires the heavy deps.
        """
        from bulla.adapters.sae_lens_backend import (
            ActivationCorpus,
            load_sae_dictionary,
            release_for,
        )

        if activation_corpus is not None and not isinstance(
            activation_corpus, ActivationCorpus
        ):
            raise TypeError(
                f"activation_corpus must be ActivationCorpus or None; "
                f"got {type(activation_corpus).__name__}"
            )
        release, sae_id = release_for(model_id, layer)
        return load_sae_dictionary(
            release=release,
            sae_id=sae_id,
            model_id=model_id,
            layer=layer,
            activation_corpus=activation_corpus,
        )


def default_loader() -> SAELoader:
    """Return the default SAE loader for the current environment.

    Selection rule:
        * BULLA_SAE_BACKEND env var = 'synthetic' (default) or unset:
          returns SyntheticSAELoader.
        * BULLA_SAE_BACKEND = 'huggingface' AND HF_TOKEN is set:
          returns HuggingFaceSAELoader (currently stubbed; will be
          functional after A3 wiring).
        * BULLA_SAE_BACKEND = 'huggingface' AND HF_TOKEN missing:
          raises RuntimeError to surface the missing credential before
          any compute is attempted.
        * BULLA_SAE_BACKEND = anything else: raises ValueError.

    Raises:
        RuntimeError: BULLA_SAE_BACKEND=huggingface but HF_TOKEN missing.
        ValueError: BULLA_SAE_BACKEND set to an unrecognised value.
    """
    backend = os.environ.get("BULLA_SAE_BACKEND", "synthetic")
    if backend == "synthetic":
        return SyntheticSAELoader()
    if backend == "huggingface":
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "BULLA_SAE_BACKEND=huggingface but HF_TOKEN is not set. "
                "Either set HF_TOKEN or use BULLA_SAE_BACKEND=synthetic."
            )
        return HuggingFaceSAELoader(hf_token=token)
    raise ValueError(
        f"Unrecognised BULLA_SAE_BACKEND={backend!r}; "
        "expected 'synthetic' or 'huggingface'."
    )
