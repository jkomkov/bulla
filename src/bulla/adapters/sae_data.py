"""SAE feature runtime-data carrier for G23 Stage A A3.

Companion to `bulla/adapters/sae.py` (frozen-contract identity-only
SAEFeatureSpec) — this module adds the runtime data SAE features need
for the cross-model 2-cover with restriction-map ablation: actual
decoder direction tensors, activation statistics, and provenance
bound to the SAE checkpoint and held-out activation corpus.

# Why a separate type and not `SAEFeatureSpec` extension

Per the G23 A3 plan (commit 1a, ``project_g24_next_phase.md`` /
``review-where-we-are-ancient-peach.md``):

  * `SAEFeatureSpec` is already a frozen, hashable, identifier-only
    dataclass that A1 controls and A2 multi-layer compositions rely on.
    Adding tensor fields would (1) break frozen-dataclass equality and
    hash semantics — two specs with same `(model_id, layer, feature_id)`
    but slightly different float tensors would no longer compare equal,
    breaking `Composition.canonical_hash()`; (2) force `torch` into the
    import path of every consumer of `sae.py`, including A1 controls
    that must run with no heavy deps; (3) couple structural identity to
    runtime data.

  * `SAEFeatureData` separates structural identity (`spec`) from
    runtime payload (`decoder_direction`, `activation_p99`, `provenance`).
    `SAEFeatureSpec` stays untouched as the canonical seam identifier;
    compositions still consume `SAEFeatureSpec`; restriction maps in
    `bulla.adapters.restriction_maps` consume `SAEDictionary` (a
    `SAEFeatureData`-keyed view).

# Lazy-import discipline

This module imports without `torch` installed. The torch type annotations
on `SAEFeatureData.decoder_direction` and `SAEDictionary.decoder_matrix`
are string forms (`"torch.Tensor"`); actual instantiation requires
`pip install bulla[g23-a3]` (per `pyproject.toml`). When torch is
absent, importing `sae_data` succeeds but constructing an
`SAEFeatureData` with a `decoder_direction` value will fail at the
caller's site — not here.

This keeps the `bulla.adapters` import graph dependency-light at module
scope. Only the construction and tensor-arithmetic call sites pay for
the heavy deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # `torch.Tensor` is referenced only in type annotations (string form
    # in dataclass fields). When type-checking is active (mypy / pyright)
    # this resolves to the real torch type. At runtime, we never need the
    # symbol — the dataclass storage is just an opaque object.
    import torch  # noqa: F401  (TYPE_CHECKING-only)

from bulla.adapters.sae import SAEFeatureSpec


@dataclass(frozen=True)
class SAEProvenance:
    """Provenance metadata for a loaded SAE feature.

    Records which SAE release the feature came from, the canonical
    `sae_id` identifying the layer / width / variant within that release,
    a content hash of the SAE checkpoint at load time (so re-loading
    against a different checkpoint version is detectable), and the
    number of tokens used to estimate the `activation_p99` quantile
    (so re-running with a different corpus size is also detectable).

    Frozen and hashable so receipts containing `SAEProvenance` can be
    canonicalized in `Composition.canonical_hash()` downstream.
    """

    release: str           # e.g. "gemma-scope-2b-pt-res-canonical"
    sae_id: str            # e.g. "layer_20/width_16k/canonical"
    sha256: str            # checkpoint content hash, "sha256:<64-hex>"
    n_p99_tokens: int      # number of tokens the p99 estimate is based on


@dataclass(frozen=True)
class SAEFeatureData:
    """Runtime payload for a single SAE feature.

    Pairs a frozen `SAEFeatureSpec` (structural identity) with the
    runtime data the cross-model restriction-map ablation needs:

      * `decoder_direction`: the SAE's decoder weight column for this
        feature, shape ``(d_model,)``. Tensor type carried as a string
        annotation so this module imports without torch.
      * `activation_p99`: the 99th-percentile activation magnitude on a
        held-out reference distribution. Used both as the M2 observable
        ``activation_p99`` field (per `INTERNAL_FIELDS` /
        `OBSERVABLE_FIELDS` in `sae.py`) and as a downstream restriction-
        map weighting hint.
      * `provenance`: SHA-bound provenance record (see `SAEProvenance`).

    Frozen and hashable. Two `SAEFeatureData` instances with identical
    field values compare equal; this lets `bulla.adapters.sae_compose`
    de-duplicate across cross-model compositions cleanly.

    Note on hashability with tensor fields: `dataclass(frozen=True)`
    generates a default `__hash__` based on `tuple(field values)`, which
    delegates to each field's `__hash__`. `torch.Tensor` is NOT hashable
    by default (raises TypeError on hash). Callers that need to put
    `SAEFeatureData` in a set or dict key should hash via
    ``hash(data.spec)`` instead — `SAEFeatureSpec` is hashable by
    construction. Equality (`==`) on `SAEFeatureData` works regardless.
    """

    spec: SAEFeatureSpec
    decoder_direction: "torch.Tensor"
    activation_p99: float
    provenance: SAEProvenance


@dataclass(frozen=True)
class SAEDictionary:
    """An ordered tuple of SAE feature data + cached decoder matrix.

    Carries the full feature dictionary for one (model, layer, SAE
    release) tuple. Restriction maps in `bulla.adapters.restriction_maps`
    consume two `SAEDictionary` instances (one per model side) and fit
    an alignment between them.

    Fields:

      * `model_id`: e.g. ``"gemma2-2b"``, ``"gpt2-small"``. Matches the
        `SAEFeatureSpec.model_id` of every entry in `features`.
      * `layer`: e.g. ``20``, ``11``. Matches the
        `SAEFeatureSpec.layer` of every entry in `features`.
      * `features`: ordered tuple of `SAEFeatureData`, sorted by
        `feature_id` ascending. Length defines the dictionary size
        (e.g. 16384 for Gemma-Scope canonical, 24576 for jbloom GPT-2
        ``gpt2-small-res-jb`` resid-pre).
      * `d_model`: the model's residual-stream dimensionality (e.g.
        2304 for Gemma-2-2B, 768 for GPT-2-Small).
      * `decoder_matrix`: a ``(n_features, d_model)`` tensor obtained
        by stacking `feature.decoder_direction` over all features in
        order. Cached on the dictionary so restriction maps don't
        rebuild it on every `align()` call.

    Frozen for the same canonicalization reasons as `SAEFeatureSpec`.

    Class-level invariants (validated in `__post_init__`):

      * Every `feature.spec.model_id == self.model_id`
      * Every `feature.spec.layer == self.layer`
      * `feature_id` values are 0..len(features)-1 (dense, sorted)
      * `decoder_matrix.shape[0] == len(features)` (when torch present)
    """

    model_id: str
    layer: int
    features: tuple[SAEFeatureData, ...]
    d_model: int
    decoder_matrix: "torch.Tensor"

    def __post_init__(self) -> None:
        # Lightweight invariants that DON'T require torch.
        if not self.features:
            raise ValueError("SAEDictionary.features must be non-empty")

        for i, f in enumerate(self.features):
            if f.spec.model_id != self.model_id:
                raise ValueError(
                    f"feature[{i}].spec.model_id={f.spec.model_id!r} "
                    f"!= dictionary.model_id={self.model_id!r}"
                )
            if f.spec.layer != self.layer:
                raise ValueError(
                    f"feature[{i}].spec.layer={f.spec.layer} "
                    f"!= dictionary.layer={self.layer}"
                )
            if f.spec.feature_id != i:
                raise ValueError(
                    f"feature[{i}].spec.feature_id={f.spec.feature_id} "
                    f"!= position-in-tuple={i}; features must be dense and sorted"
                )

        if self.d_model < 1:
            raise ValueError(f"d_model must be >= 1; got {self.d_model}")
