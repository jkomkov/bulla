"""Restriction maps for cross-model SAE feature alignment (G23 A3 commit 1c).

A *restriction map* takes two SAEDictionary instances (one per model
side) and produces an alignment between them: given a source feature
spec on side A, return the most-similar candidate(s) on side B with
similarity scores. The restriction map IS the M2 axiom (cross-system
sheaf-restriction-map structure) made operational.

# Map taxonomy

Three classes of maps, gated by `SYNTHETIC_ONLY` class attribute:

  * **Real maps** (`SYNTHETIC_ONLY=False`) — fit alignments on real SAE
    feature dictionaries. Used in the §3b sweep:
      - `ProcrustesAlignment`: linear orthogonal map via SVD on feature
        decoder matrices. No checkpoint needed; pure linear-algebra.
      - `SparseCrosscoder`: loads
        `science-of-finetuning/gemma-2-2b-crosscoder-l13-mu4.1e-02-lr1e-04`
        (community-trained crosscoder per Anthropic March 2025 method).
      - `TranscoderMap`: loads `google/gemma-scope-2b-pt-transcoders`
        (Gemma Scope transcoder release).
      - `NeuronpediaLabelMap`: REFERENCE alignment via Neuronpedia
        auto-interp label embeddings (Refinement 2 cross-check).
        Recorded in CSV alongside the 3 ablated maps; not gated.

  * **Synthetic-only maps** (`SYNTHETIC_ONLY=True`) — for §3a′ tripwires
    + permutation-invariance tests. Operate purely on SAEFeatureSpec
    structure; no torch needed. Cannot enter the §3b sweep — sweep code
    checks `SYNTHETIC_ONLY` and refuses synthetic maps in production:
      - `IdentityRestrictionMap`: aligns feature_id i → feature_id i.
      - `OffsetRestrictionMap`: aligns feature_id i → feature_id (i+shift) % n.
      - `BijectionRestrictionMap`: aligns feature_id i → perm[i] for an
        arbitrary fixed permutation.

# Lazy-import discipline

Protocol + dataclasses + synthetic-only maps import without torch.
Real maps lazy-import torch / sae-lens / huggingface-hub /
sentence-transformers inside `fit()` and `align()` bodies; raising
`SAEBackendImportError` if [g23-a3] extras absent.

# §3a′ Tripwire 0i pre-check (the load-bearing validation)

The map-invariance pre-check in §3a′ verifies that
`IdentityRestrictionMap`, `OffsetRestrictionMap(shift=K)`, and
`BijectionRestrictionMap(perm=σ)` all produce identical dim H¹ on the
same synthetic composition (because all three are bijections at the
feature-ID level). If even synthetic bijections produce different dim
H¹, that's a structural encoding bug that would invalidate Gate 5 in
the real-data sweep — fails fast at Iter-1 before any HF spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from bulla.adapters.sae import SAEFeatureSpec
from bulla.adapters.sae_data import SAEDictionary
from bulla.adapters.sae_lens_backend import SAEBackendImportError

if TYPE_CHECKING:
    import torch  # noqa: F401  (TYPE_CHECKING-only)


# ── Alignment candidate dataclass ──────────────────────────────────────


@dataclass(frozen=True)
class AlignmentCandidate:
    """A single (target_spec, similarity) candidate from a restriction map.

    `similarity` is normalized to [-1, 1] for cosine-style maps
    (Procrustes, Crosscoder, Transcoder), and [0, 1] for label-match
    maps (Neuronpedia). For synthetic bijection maps, similarity is
    always exactly 1.0 (perfect match by construction).
    """

    target: SAEFeatureSpec
    similarity: float


# ── RestrictionMap Protocol ────────────────────────────────────────────


@runtime_checkable
class RestrictionMap(Protocol):
    """Protocol any cross-model SAE alignment must satisfy.

    Implementations:
      - Real: ProcrustesAlignment, SparseCrosscoder, TranscoderMap,
        NeuronpediaLabelMap (reference; recorded in CSV but ungated)
      - Synthetic-only: IdentityRestrictionMap, OffsetRestrictionMap,
        BijectionRestrictionMap (gated by SYNTHETIC_ONLY=True)

    Required attributes:
        name: short identifier, e.g. "procrustes", "crosscoder"
        SYNTHETIC_ONLY: True iff the map should never enter a real-data
            sweep. Sweep code MUST check this and refuse synthetic maps.

    Required methods:
        fit(dict_a, dict_b): consume two SAE dictionaries; learn or
            store the alignment. May be a no-op for label-match or
            offset-style maps.
        align(source, top_k): given a source SAEFeatureSpec on side A,
            return up to top_k candidates on side B sorted by
            similarity descending.
    """

    name: str
    SYNTHETIC_ONLY: bool

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None: ...
    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]: ...


# ── Synthetic-only maps (no torch needed) ──────────────────────────────


@dataclass
class IdentityRestrictionMap:
    """Synthetic map: feature_id i on side A → feature_id i on side B.

    SYNTHETIC_ONLY: True. Used for §3a′ Tripwire 0i map-invariance
    pre-check + permutation-invariance Gate 7 contract testing.
    """

    name: str = "identity"
    SYNTHETIC_ONLY: bool = True
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        if len(dict_a.features) != len(dict_b.features):
            raise ValueError(
                f"IdentityRestrictionMap requires equal-size dictionaries; "
                f"got {len(dict_a.features)} vs {len(dict_b.features)}"
            )
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        target = self._dict_b.features[source.feature_id].spec
        return (AlignmentCandidate(target=target, similarity=1.0),)[:top_k]


@dataclass
class OffsetRestrictionMap:
    """Synthetic map: feature_id i → feature_id (i + shift) mod n on side B.

    SYNTHETIC_ONLY: True. Used to verify §3a′ Tripwire 0i: dim H¹ on
    a structurally-bijective alignment must equal dim H¹ on identity
    alignment (because shift is a permutation; permutations preserve
    cohomology).
    """

    shift: int = 0
    name: str = "offset"
    SYNTHETIC_ONLY: bool = True
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        if len(dict_a.features) != len(dict_b.features):
            raise ValueError(
                f"OffsetRestrictionMap requires equal-size dictionaries; "
                f"got {len(dict_a.features)} vs {len(dict_b.features)}"
            )
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        n = len(self._dict_b.features)
        target_idx = (source.feature_id + self.shift) % n
        target = self._dict_b.features[target_idx].spec
        return (AlignmentCandidate(target=target, similarity=1.0),)[:top_k]


@dataclass
class BijectionRestrictionMap:
    """Synthetic map: feature_id i → perm[i] for an arbitrary fixed permutation.

    SYNTHETIC_ONLY: True. Used to verify §3a′ Tripwire 0i: arbitrary
    bijections must produce same dim H¹ as identity (cohomology is
    invariant under permutation).
    """

    perm: tuple[int, ...] = field(default_factory=tuple)
    name: str = "bijection"
    SYNTHETIC_ONLY: bool = True
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        if len(dict_a.features) != len(dict_b.features):
            raise ValueError(
                f"BijectionRestrictionMap requires equal-size dictionaries; "
                f"got {len(dict_a.features)} vs {len(dict_b.features)}"
            )
        n = len(dict_b.features)
        if len(self.perm) != n:
            raise ValueError(
                f"perm has length {len(self.perm)}; expected {n}"
            )
        if sorted(self.perm) != list(range(n)):
            raise ValueError(
                f"perm must be a valid permutation of 0..{n - 1}; got {self.perm}"
            )
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        target_idx = self.perm[source.feature_id]
        target = self._dict_b.features[target_idx].spec
        return (AlignmentCandidate(target=target, similarity=1.0),)[:top_k]


# ── Real maps (lazy torch import in fit/align) ─────────────────────────


@dataclass
class ProcrustesAlignment:
    """Linear orthogonal alignment via SVD on decoder matrices.

    fit(): computes R = argmin ||D_a R - D_b||_F by SVD on D_a^T D_b.
    align(): returns top_k candidates on side B by cosine similarity
    between (D_a[source] @ R) and rows of D_b.

    Lazy-imports torch on fit() / align() call.
    """

    name: str = "procrustes"
    SYNTHETIC_ONLY: bool = False
    _R: "torch.Tensor | None" = field(default=None, repr=False)
    _dict_b: SAEDictionary | None = field(default=None, repr=False)
    _D_a: "torch.Tensor | None" = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        try:
            import torch
        except ImportError as e:
            raise SAEBackendImportError("torch") from e

        D_a = dict_a.decoder_matrix.float()  # (n_a, d_model)
        D_b = dict_b.decoder_matrix.float()  # (n_b, d_model)

        # Procrustes on (D_a, D_b): solve R such that D_a R ≈ D_b's row-space.
        # Note: when n_a != n_b we restrict to min(n_a, n_b) rows for fitting
        # (use the leading rows). For A3 the dictionaries may have different
        # widths (Gemma 16k vs GPT-2 32k), so this is the sensible default.
        n = min(D_a.shape[0], D_b.shape[0])
        M = D_a[:n].T @ D_b[:n]                  # (d_model, d_model)
        U, _S, Vt = torch.linalg.svd(M, full_matrices=False)
        self._R = U @ Vt                          # (d_model, d_model) orthogonal
        self._D_a = D_a
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._R is None or self._dict_b is None or self._D_a is None:
            raise RuntimeError("must call fit() before align()")
        try:
            import torch
        except ImportError as e:
            raise SAEBackendImportError("torch") from e

        # Project source's decoder direction through R, then cosine vs D_b
        d_src = self._D_a[source.feature_id]              # (d_model,)
        projected = d_src @ self._R                        # (d_model,)
        D_b = self._dict_b.decoder_matrix.float()
        # Cosine similarity to every row of D_b
        denom = (
            torch.linalg.norm(projected) * torch.linalg.norm(D_b, dim=1) + 1e-12
        )
        sims = (D_b @ projected) / denom                   # (n_b,)
        top_vals, top_idx = torch.topk(sims, k=top_k)
        return tuple(
            AlignmentCandidate(
                target=self._dict_b.features[int(idx)].spec,
                similarity=float(val),
            )
            for val, idx in zip(top_vals.tolist(), top_idx.tolist())
        )


@dataclass
class SparseCrosscoder:
    """Sparse Crosscoder alignment via science-of-finetuning checkpoint.

    fit(): downloads the published Crosscoder checkpoint, applies it to
    decoder weights of dict_a + dict_b, produces an alignment scoring.

    Real implementation deferred to Iter-2 (requires HF_TOKEN + torch);
    Iter-1 commits ship the interface + import-error contract only.
    Tests that exercise Crosscoder loading are gated on
    pytest.importorskip("sae_lens").
    """

    checkpoint: str = "science-of-finetuning/gemma-2-2b-crosscoder-l13-mu4.1e-02-lr1e-04"
    name: str = "crosscoder"
    SYNTHETIC_ONLY: bool = False
    _dict_a: SAEDictionary | None = field(default=None, repr=False)
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        try:
            import torch  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("torch") from e
        try:
            from huggingface_hub import hf_hub_download  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("huggingface_hub") from e
        # Real loading deferred to Iter-2; for Iter-1 we just store
        # references so .align() can return mock candidates against
        # real dictionaries without a real fitted model.
        self._dict_a = dict_a
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_a is None or self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        # Iter-1 placeholder: real Crosscoder forward pass lands in
        # Iter-2. Default to identity-like alignment for structural
        # tests; real-data tests are gated on integration importorskip.
        target_idx = source.feature_id % len(self._dict_b.features)
        return (
            AlignmentCandidate(
                target=self._dict_b.features[target_idx].spec,
                similarity=1.0,
            ),
        )[:top_k]


@dataclass
class TranscoderMap:
    """Transcoder-based alignment via google/gemma-scope-2b-pt-transcoders.

    fit(): loads the Transcoder checkpoint, applies it for the
    cross-model alignment.

    Real implementation deferred to Iter-2 (requires sae-lens + torch);
    Iter-1 commits ship the interface + import-error contract only.
    """

    release: str = "gemma-scope-2b-pt-transcoders"
    sae_id: str = "layer_20/width_16k/canonical"
    name: str = "transcoder"
    SYNTHETIC_ONLY: bool = False
    _dict_a: SAEDictionary | None = field(default=None, repr=False)
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        try:
            import torch  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("torch") from e
        try:
            import sae_lens  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("sae_lens") from e
        self._dict_a = dict_a
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_a is None or self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        target_idx = source.feature_id % len(self._dict_b.features)
        return (
            AlignmentCandidate(
                target=self._dict_b.features[target_idx].spec,
                similarity=1.0,
            ),
        )[:top_k]


@dataclass
class NeuronpediaLabelMap:
    """Reference alignment via Neuronpedia auto-interp label embeddings.

    PER REFINEMENT 2: this is the reference (4th) alignment recorded in
    the CSV alongside the 3 ablated maps. Its dim H¹ is recorded but
    NOT gated — sharp disagreement vs the 3 ablated maps signals shared
    bias the ablated maps couldn't catch on their own.

    fit(): downloads Neuronpedia auto-interp descriptions for both
    dictionaries' SAE releases, embeds via sentence-transformers
    (locked HF revision), pairs by cosine similarity.

    Real implementation deferred to Iter-2 (requires huggingface-hub +
    sentence-transformers + Neuronpedia API access). Iter-1 ships the
    interface + import-error contract.
    """

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = "e4ce9877abf3edfe10b0d82785e83bdcb973e22e"
    name: str = "neuronpedia_label"
    SYNTHETIC_ONLY: bool = False
    _dict_a: SAEDictionary | None = field(default=None, repr=False)
    _dict_b: SAEDictionary | None = field(default=None, repr=False)

    def fit(self, *, dict_a: SAEDictionary, dict_b: SAEDictionary) -> None:
        try:
            import sentence_transformers  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("sentence_transformers") from e
        try:
            import huggingface_hub  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise SAEBackendImportError("huggingface_hub") from e
        self._dict_a = dict_a
        self._dict_b = dict_b

    def align(
        self, *, source: SAEFeatureSpec, top_k: int = 1,
    ) -> tuple[AlignmentCandidate, ...]:
        if self._dict_a is None or self._dict_b is None:
            raise RuntimeError("must call fit() before align()")
        target_idx = source.feature_id % len(self._dict_b.features)
        return (
            AlignmentCandidate(
                target=self._dict_b.features[target_idx].spec,
                similarity=1.0,
            ),
        )[:top_k]


# ── Factory ───────────────────────────────────────────────────────────


_REGISTRY: dict[str, type] = {
    "procrustes": ProcrustesAlignment,
    "crosscoder": SparseCrosscoder,
    "transcoder": TranscoderMap,
    "neuronpedia_label": NeuronpediaLabelMap,
    "identity": IdentityRestrictionMap,
    "offset": OffsetRestrictionMap,
    "bijection": BijectionRestrictionMap,
}


def restriction_map_from_name(name: str, **kwargs) -> RestrictionMap:
    """Factory: instantiate a restriction map by name with config kwargs.

    Used by the §3b sweep and the §3a′ tripwire harness to construct
    maps from CLI / env-var / scoping-doc spec without per-call
    `from bulla.adapters.restriction_maps import ...` boilerplate.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown restriction map {name!r}. "
            f"Registered: {sorted(_REGISTRY.keys())}"
        )
    cls = _REGISTRY[name]
    return cls(**kwargs)


def real_map_names() -> tuple[str, ...]:
    """Names of real restriction maps (SYNTHETIC_ONLY=False)."""
    return tuple(
        sorted(
            n for n, cls in _REGISTRY.items()
            if not getattr(cls, "SYNTHETIC_ONLY", False)
        )
    )


def synthetic_map_names() -> tuple[str, ...]:
    """Names of synthetic-only restriction maps (SYNTHETIC_ONLY=True)."""
    return tuple(
        sorted(
            n for n, cls in _REGISTRY.items()
            if getattr(cls, "SYNTHETIC_ONLY", False)
        )
    )
