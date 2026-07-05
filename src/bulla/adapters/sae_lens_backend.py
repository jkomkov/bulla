"""Lazy sae-lens wrapper for HF SAE checkpoint loading (G23 A3 commit 1b).

This module is the SOLE place in bulla.adapters that imports `sae-lens`
and `transformers`. Per the dependency-light invariant:

  * Module import: succeeds with NO heavy deps installed (sae-lens,
    transformers, torch all absent). String type annotations + lazy
    imports inside function bodies + TYPE_CHECKING guards.
  * Function call: requires the [g23-a3] extras (`pip install bulla
    [g23-a3]`). First call into `load_sae_dictionary()` raises a clear
    ImportError with install hint if any heavy dep is missing.

All sae-lens API surface is concentrated here. The rest of the A3
pipeline (`restriction_maps`, `sae_compose`, `sae_baseline`,
`sae_loader.HuggingFaceSAELoader`) consumes the `SAEDictionary` returned
by this module and does NOT import sae-lens directly. This keeps the
test-mock surface to a single module: monkeypatch
`bulla.adapters.sae_lens_backend.load_sae_dictionary` to return a
hand-built `SAEDictionary` and the rest of the A3 pipeline runs in CI
without sae-lens.

# Release registry

Locked at design-time per the G23 A3 plan: only the two SAE checkpoints
listed in `_RELEASE_REGISTRY` are valid for A3's cross-model 2-cover.
Adding a new (model_id, layer) pair requires a deliberate code change
and a re-run of the synthetic-control validation (§3a′ tripwires) — NOT
a runtime configuration. This is a deliberate Mirage-discipline lock.

# ActivationCorpus

The held-out token corpus used to estimate per-feature `activation_p99`.
For A3 Iter-2, this is locked at `monology/pile-uncopyrighted` train
indices 0..199 with seed 20260507 (per §3b of the pre-registration
in `papers/composition-doctrine/sprint_g23_a3_pre_registration.md`).
For Iter-1 synthetic-control validation, callers pass `corpus=None`
which produces a dictionary with `activation_p99 = 0.0` and
`provenance.n_p99_tokens = 0` — sufficient for structural tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bulla.adapters.sae_data import (
    SAEDictionary,
    SAEFeatureData,
    SAEProvenance,
)
from bulla.adapters.sae import SAEFeatureSpec

if TYPE_CHECKING:
    import torch  # noqa: F401  (TYPE_CHECKING-only)


# ── Locked release registry ────────────────────────────────────────────


# (model_id, layer) → (release, sae_id) per the G23 A3 plan + Phase 1
# audit. The two entries below are both publicly available and
# `sae-lens`-loadable as of 2026-05-07.
_RELEASE_REGISTRY: dict[tuple[str, int], tuple[str, str]] = {
    ("gemma2-2b", 20): (
        "gemma-scope-2b-pt-res-canonical",
        "layer_20/width_16k/canonical",
    ),
    ("gpt2-small", 11): (
        # Empirically verified 2026-05-07 against Neuronpedia source-set
        # `gpt2-small/11-res-jb`: saelensRelease=`gpt2-small-res-jb`,
        # saelensSaeId=`blocks.11.hook_resid_pre`, d_sae=24576. The
        # earlier guess of `gpt2-small-resid-post-v5-32k` /
        # `blocks.11.hook_resid_post` (resid_POST, 32k) was a different
        # SAE not hosted on Neuronpedia; using it would have decoupled
        # encoder feature_ids from auto-interp label feature_ids.
        "gpt2-small-res-jb",
        "blocks.11.hook_resid_pre",
    ),
    # Added 2026-06-18 to reach N>=3 INDEPENDENT, capable sources for the
    # representation-holonomy crux. The 2-model set above yields only ONE
    # independent capable vertex — gpt2-small is too weak for grounded MCQ, so it
    # is excluded from the decision loop (geometry only). The two releases below
    # were verified against the live sae_lens pretrained_saes.yaml / Supported-SAEs
    # table (scout 2026-06-18) but are NOT load-tested in this GPU-less environment;
    # REPRESENTATION_GATE_COLAB_RUNBOOK.md Stage 1a load-checks them FIRST.
    # CAVEAT: both SAEs target the BASE (-pt/Base) models, and both base models are
    # GATED on HF (license acceptance + token). Layer 24 ≈ 0.75 depth to roughly
    # match the existing gemma2-2b L20 (0.77).
    ("llama3.1-8b", 24): (
        "llama_scope_lxr_8x",            # OpenMOSS/Fudan — Llama-3.1-8B-Base, 32K (8x)
        "l24r_8x",
    ),
    ("mistral-7b", 24): (
        "mistral-7b-res-wg",             # JoshEngels — Mistral-7B-v0.1 Base, 65536 (16x)
        # This release only exposes hook_resid_PRE (valid ids: blocks.{8,16,24}.hook_resid_pre);
        # resid_pre@24 == hidden_states[24], so this one is exact (no off-by-one).
        "blocks.24.hook_resid_pre",
    ),
    # 4th independent org (Alibaba), added 2026-06-18 to reach FOUR capable sources →
    # C(4,3)=4 model-triples → the loop holonomy varies across items independently of the
    # concept, which the conditional decoupled-strata test needs (3 models = 1 triple =
    # degenerate). Registry release (verified vs live pretrained_saes.yaml); REQUIRES
    # sae-lens >= 6.27 (BatchTopK `threshold` state_dict load was broken before that).
    # Qwen2.5-7B-Instruct is INSTRUCT + Apache-2.0 / UNGATED (no HF token). 28 layers,
    # d_model 3584; layer 19 ≈ 0.68 depth (available resid layers: 3/7/11/15/19/23/27).
    ("qwen2.5-7b", 19): (
        "qwen2.5-7b-instruct-andyrdt",   # andyrdt/Chanin — Qwen2.5-7B-Instruct, 131072 (jumprelu/BatchTopK k=64)
        "resid_post_layer_19_trainer_1",
    ),
}


def supported_models() -> tuple[tuple[str, int], ...]:
    """Locked (model_id, layer) tuples this backend can load."""
    return tuple(sorted(_RELEASE_REGISTRY.keys()))


def release_for(model_id: str, layer: int) -> tuple[str, str]:
    """Resolve (release, sae_id) for a (model_id, layer) pair.

    Raises:
        KeyError: if (model_id, layer) is not in the locked registry.
    """
    key = (model_id, layer)
    if key not in _RELEASE_REGISTRY:
        raise KeyError(
            f"No SAE release registered for {key}. Supported: "
            f"{supported_models()}. To add a new (model, layer), update "
            f"_RELEASE_REGISTRY in bulla/adapters/sae_lens_backend.py "
            f"and re-run the §3a′ synthetic-control validation."
        )
    return _RELEASE_REGISTRY[key]


# ── ActivationCorpus ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ActivationCorpus:
    """Held-out token corpus for activation_p99 estimation.

    Locked at the dataset_id + split + indices + seed + max_tokens_per_doc
    tuple. Re-running against a different corpus produces a different
    SAEDictionary (different p99 values + different
    provenance.n_p99_tokens). Frozen so the corpus identity is hashable
    and can be canonicalized in receipts downstream.

    Pre-registered for G23 A3 Iter-2 (see §3b of pre-registration):
        ActivationCorpus(
            dataset_id="monology/pile-uncopyrighted",
            split="train",
            indices=tuple(range(200)),
            seed=20260507,
            max_tokens_per_doc=512,
        )
    """

    dataset_id: str
    split: str
    indices: tuple[int, ...]
    seed: int
    max_tokens_per_doc: int

    def __post_init__(self) -> None:
        if not self.indices:
            raise ValueError("ActivationCorpus.indices must be non-empty")
        if self.max_tokens_per_doc < 1:
            raise ValueError(
                f"max_tokens_per_doc must be >= 1; got {self.max_tokens_per_doc}"
            )


# ── Errors ─────────────────────────────────────────────────────────────


class SAEBackendImportError(ImportError):
    """Raised when [g23-a3] extras are missing at first call.

    Message includes the install hint so callers know exactly what to do.
    """

    def __init__(self, missing_module: str) -> None:
        super().__init__(
            f"Module {missing_module!r} is required by bulla.adapters."
            f"sae_lens_backend but is not installed. Install the [g23-a3] "
            f"extras: `pip install 'bulla[g23-a3]'`. Or use the "
            f"SyntheticSAELoader for tests that don't require real SAEs."
        )


# ── Public API ─────────────────────────────────────────────────────────


def load_sae_dictionary(
    *,
    release: str,
    sae_id: str,
    model_id: str,
    layer: int,
    activation_corpus: ActivationCorpus | None = None,
) -> SAEDictionary:
    """Load an SAE checkpoint and return its dictionary.

    Args:
        release: sae-lens release name (e.g.
            ``gemma-scope-2b-pt-res-canonical``). Must match
            ``release_for(model_id, layer)[0]``.
        sae_id: sae-lens sae_id within the release (e.g.
            ``layer_20/width_16k/canonical``). Must match
            ``release_for(model_id, layer)[1]``.
        model_id: short model identifier matching the registry, e.g.
            ``gemma2-2b`` or ``gpt2-small``.
        layer: residual-stream layer index for the SAE.
        activation_corpus: optional held-out corpus for activation_p99
            estimation. If None, all features get
            ``activation_p99 = 0.0`` and provenance records
            ``n_p99_tokens = 0``. The synthetic-control validation
            (§3a′) uses None; the real-data calibration spot-check
            (§3a) and sweep (§3b) use the locked
            ``ActivationCorpus(monology/pile-uncopyrighted ...)``.

    Returns:
        ``SAEDictionary`` with one ``SAEFeatureData`` per feature in
        the SAE, ordered by feature_id ascending. The
        ``decoder_matrix`` is the full ``(n_features, d_model)`` weight
        tensor stacked from the loaded SAE.

    Raises:
        SAEBackendImportError: if `sae-lens` or `torch` is not
            installed (the [g23-a3] extras tag is missing).
        KeyError: if (model_id, layer) is not in `_RELEASE_REGISTRY`
            (call ``release_for()`` to validate before this).
        ValueError: if the supplied (release, sae_id) does not match
            the registry entry for (model_id, layer) — the registry
            is the lock, parameters are passed-through validation.
    """
    # Validate against locked registry FIRST (catches typos before HF call)
    expected_release, expected_sae_id = release_for(model_id, layer)
    if release != expected_release:
        raise ValueError(
            f"release={release!r} does not match registry "
            f"{expected_release!r} for (model_id={model_id!r}, layer={layer})"
        )
    if sae_id != expected_sae_id:
        raise ValueError(
            f"sae_id={sae_id!r} does not match registry "
            f"{expected_sae_id!r} for (model_id={model_id!r}, layer={layer})"
        )

    # Lazy import — defer the heavy deps until first call
    try:
        import torch  # noqa: F401
    except ImportError as e:
        raise SAEBackendImportError("torch") from e
    try:
        from sae_lens import SAE  # type: ignore[import-not-found]
    except ImportError as e:
        raise SAEBackendImportError("sae_lens") from e

    # Load the SAE checkpoint via sae-lens
    sae, cfg_dict, _ = SAE.from_pretrained(
        release=release,
        sae_id=sae_id,
        device="cpu",  # caller can `.to(device)` the decoder matrix later
    )

    n_features = sae.cfg.d_sae
    d_model = sae.cfg.d_in

    # Decoder matrix: shape (n_features, d_model). sae.W_dec is the
    # canonical decoder-weight tensor in sae-lens >= 5.0.
    decoder_matrix = sae.W_dec.detach().cpu()

    # Optional activation corpus → per-feature p99 estimation
    if activation_corpus is not None:
        activation_p99_per_feature, n_tokens = _compute_activation_p99(
            sae=sae,
            corpus=activation_corpus,
            model_id=model_id,
            layer=layer,
        )
    else:
        activation_p99_per_feature = [0.0] * n_features
        n_tokens = 0

    # Build provenance — checkpoint SHA via sae.cfg if available, else "unknown"
    sha256 = _sae_checkpoint_sha256(sae) or "sha256:" + "0" * 64
    provenance = SAEProvenance(
        release=release,
        sae_id=sae_id,
        sha256=sha256,
        n_p99_tokens=n_tokens,
    )

    # Assemble SAEFeatureData tuple — feature_id 0..n_features-1, dense
    features = tuple(
        SAEFeatureData(
            spec=SAEFeatureSpec(
                model_id=model_id, layer=layer, feature_id=i,
            ),
            decoder_direction=decoder_matrix[i],
            activation_p99=float(activation_p99_per_feature[i]),
            provenance=provenance,
        )
        for i in range(n_features)
    )

    return SAEDictionary(
        model_id=model_id,
        layer=layer,
        features=features,
        d_model=int(d_model),
        decoder_matrix=decoder_matrix,
    )


def _compute_activation_p99(
    *,
    sae: object,  # sae_lens.SAE — annotated as object to avoid import
    corpus: ActivationCorpus,
    model_id: str,
    layer: int,
) -> tuple[list[float], int]:
    """Compute per-feature 99th-percentile activation magnitude on corpus.

    Returns (p99_per_feature, total_tokens_processed).

    Implementation note: this loads the underlying language model via
    `transformers.AutoModelForCausalLM`, runs a forward pass, captures
    the residual stream at `layer`, encodes via the SAE, and computes
    p99 across token-feature activations. Heavy compute; expected to
    run on Modal A100 in Iter-3, OR locally on GPT-2-Small in Iter-2.

    For Iter-2 calibration spot-check (small probe pairs, N=50), this
    runs locally on CPU in ~5 minutes for GPT-2-Small.
    """
    # Lazy imports
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from datasets import load_dataset
    except ImportError as e:
        raise SAEBackendImportError(str(e.name)) from e

    # Resolve the underlying HF model id
    hf_model_id = _hf_model_id_for(model_id)

    tokenizer = AutoTokenizer.from_pretrained(hf_model_id)
    model = AutoModelForCausalLM.from_pretrained(
        hf_model_id,
        torch_dtype=torch.float32,  # CPU-friendly default
        output_hidden_states=False,
    )
    model.eval()

    # Load corpus
    dataset = load_dataset(
        corpus.dataset_id,
        split=corpus.split,
        streaming=False,
    )

    # Subset to locked indices
    samples = [dataset[i] for i in corpus.indices]

    n_features = sae.cfg.d_sae  # type: ignore[attr-defined]
    # Running max-of-tokens activations — we buffer all activations,
    # then compute p99 over them. For small corpora (200 docs × 512
    # tokens × 16k features × 4 bytes = ~6.5 GB) this is feasible on
    # Modal A100 RAM but tight; for larger corpora, switch to a
    # streaming-quantile estimator (P²-algorithm or Greenwald-Khanna).
    # Iter-2 N=200 is comfortably under this threshold.
    all_activations: list[torch.Tensor] = []
    n_tokens = 0

    for sample in samples:
        text = sample.get("text") or sample.get("content") or ""
        if not text:
            continue

        tokens = tokenizer(
            text,
            max_length=corpus.max_tokens_per_doc,
            truncation=True,
            return_tensors="pt",
        )

        with torch.no_grad():
            outputs = model(
                **tokens,
                output_hidden_states=True,
            )
            # Residual stream at the SAE-trained layer
            residual = outputs.hidden_states[layer]  # (1, T, d_model)
            # Encode via SAE → feature activations (1, T, n_features)
            feat_acts = sae.encode(residual)

        # feat_acts: (1, T, n_features); flatten batch+token → (T, n_features)
        flat = feat_acts.reshape(-1, n_features)
        all_activations.append(flat.detach().cpu())
        n_tokens += int(flat.shape[0])

    if not all_activations:
        # Empty corpus → all p99 = 0
        return [0.0] * n_features, 0

    stacked = torch.cat(all_activations, dim=0)  # (total_tokens, n_features)
    # 99th percentile per feature, ABSOLUTE value
    p99 = torch.quantile(stacked.abs(), q=0.99, dim=0)  # (n_features,)
    return p99.tolist(), n_tokens


def _hf_model_id_for(model_id: str) -> str:
    """Resolve short model_id (e.g. 'gemma2-2b') to HF Hub repo id."""
    if model_id == "gemma2-2b":
        return "google/gemma-2-2b"
    if model_id == "gpt2-small":
        return "gpt2"
    if model_id == "llama3.1-8b":
        return "meta-llama/Llama-3.1-8B"       # BASE (Llama Scope target); GATED on HF
    if model_id == "mistral-7b":
        return "mistralai/Mistral-7B-v0.1"     # BASE (mistral-7b-res-wg target); GATED on HF
    if model_id == "qwen2.5-7b":
        return "Qwen/Qwen2.5-7B-Instruct"      # INSTRUCT; Apache-2.0, UNGATED (no token)
    raise KeyError(
        f"No HF Hub model_id mapping for {model_id!r}. "
        f"Supported: 'gemma2-2b', 'gpt2-small', 'llama3.1-8b', 'mistral-7b', 'qwen2.5-7b'."
    )


def _load_sae_model_tokenizer(
    *, model_id: str, layer: int, device: str = "cpu",
) -> tuple[object, object, object]:
    """Load (sae, model, tokenizer) for one (model_id, layer) pair.

    Heavy: triggers the underlying LLM weights download on first call
    (~5 GB for Gemma-2-2B; ~500 MB for GPT-2-Small). Cached by HF Hub
    on subsequent calls. Used by ``_run_probe_inference`` for the §3a
    calibration spot-check.

    Args:
        model_id: 'gemma2-2b' or 'gpt2-small' (the bulla-internal id;
            resolved to HF repo via ``_hf_model_id_for``).
        layer: layer index. Routed via ``_RELEASE_REGISTRY`` to the
            matching sae-lens release/sae_id.
        device: 'cpu' / 'cuda' / 'mps'. Defaults to 'cpu' for Iter-2.

    Returns:
        Tuple of (sae, model, tokenizer). Types are duck-typed (sae:
        sae-lens SAE; model: transformers PreTrainedModel; tokenizer:
        transformers PreTrainedTokenizer); annotated as ``object`` to
        keep this module's type hints torch-import-free.

    Raises:
        SAEBackendImportError: torch / transformers / sae-lens absent.
        KeyError: (model_id, layer) not in _RELEASE_REGISTRY.
    """
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from sae_lens import SAE  # type: ignore[import-not-found]
    except ImportError as e:
        raise SAEBackendImportError(str(e.name)) from e

    release, sae_id = release_for(model_id, layer)
    sae, _cfg, _sparsity = SAE.from_pretrained(  # type: ignore[attr-defined]
        release=release, sae_id=sae_id, device=device,
    )
    sae = sae.to(device)  # FORCE: from_pretrained's device kwarg is unreliable across
                          # sae_lens versions (left b_dec on cpu -> device-mismatch in .encode)
    hf_model_id = _hf_model_id_for(model_id)
    tokenizer = AutoTokenizer.from_pretrained(hf_model_id)
    # bfloat16 on GPU: float32 for a 7-8B model is ~32 GB of weights and OOMs an A100-40GB.
    dtype = torch.bfloat16 if str(device).startswith("cuda") else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        hf_model_id,
        torch_dtype=dtype,
        output_hidden_states=False,
    )
    model.eval()
    model.to(device)  # original loaded on CPU and never moved -> activations were off-device
    return sae, model, tokenizer


def _run_probe_inference(
    *,
    sae: object,
    model: object,
    tokenizer: object,
    layer: int,
    probe_text: str,
    top_k: int = 50,
    max_tokens: int = 128,
) -> tuple[tuple[int, float], ...]:
    """Run probe text through model + SAE; return top-K (feature_id, max_activation).

    Tokenizes ``probe_text``, runs a forward pass with
    ``output_hidden_states=True``, extracts the residual stream at
    ``layer``, encodes via the SAE, takes the absolute max activation
    across tokens per feature, and returns the top-K features by that
    max-activation magnitude.

    "Max across tokens" rather than "p99 across tokens" because a single
    probe sentence is too short for percentile estimation to be
    meaningful — at ~30-100 tokens, the 99th percentile is just the max.
    The semantics is "which features fire strongly anywhere in this
    probe."

    Args:
        sae: sae-lens SAE object (must have ``.encode(residual)`` method
            returning a tensor of shape ``(batch, tokens, n_features)``).
        model: transformers PreTrainedModel (must support
            ``output_hidden_states=True`` kwarg).
        tokenizer: transformers PreTrainedTokenizer.
        layer: index into ``outputs.hidden_states`` (matches SAE training
            layer; e.g. 20 for Gemma-Scope L20, 11 for GPT-2-Small L11).
        probe_text: single probe sentence to run through the model.
        top_k: number of features to return.
        max_tokens: tokenization truncation; probes are short so 128 is
            plenty.

    Returns:
        Tuple of (feature_id, max_activation) tuples sorted by
        max_activation descending. Length = ``top_k``.

    Raises:
        SAEBackendImportError: torch absent.
    """
    try:
        import torch
    except ImportError as e:
        raise SAEBackendImportError("torch") from e

    tokens = tokenizer(
        probe_text,
        max_length=max_tokens,
        truncation=True,
        return_tensors="pt",
    )
    dev = next(model.parameters()).device
    tokens = {k: v.to(dev) for k, v in tokens.items()}  # inputs must match the model's device
    with torch.no_grad():
        outputs = model(**tokens, output_hidden_states=True)
        # Residual stream at the SAE-trained layer.
        # outputs.hidden_states is indexed [embedding, layer1, layer2, ...]
        # so the residual after layer `L` is at index L (not L+1) for many
        # transformers models. The SAE was trained on a specific hook
        # (e.g. blocks.20.hook_resid_post for Gemma-Scope L20); we trust
        # the convention that hidden_states[L] matches the SAE's hook.
        residual = outputs.hidden_states[layer]   # (1, T, d_model)
        residual = residual.to(next(sae.parameters()).dtype)  # model may be bf16, SAE f32
        feat_acts = sae.encode(residual)           # (1, T, n_features)

    # Max absolute activation across CONTENT tokens, per feature. The BOS/first token's SAE
    # activations are huge and content-INDEPENDENT: including it collapsed every probe to the
    # same ~50 features (diagnosed 2026-06-18 — top features identical across unrelated probes
    # over all tokens, but discriminative once token 0 is dropped). So skip token 0.
    content = feat_acts[:, 1:, :] if feat_acts.shape[1] > 1 else feat_acts
    max_per_feat = content.abs().max(dim=1).values.squeeze(0)  # (n_features,)
    n_features = max_per_feat.shape[0]
    k = min(top_k, n_features)
    top_vals, top_idx = torch.topk(max_per_feat, k=k)
    return tuple(
        (int(idx), float(val))
        for val, idx in zip(top_vals.tolist(), top_idx.tolist())
    )


def _get_feature_activations_for_probe(
    *,
    sae: object,
    model: object,
    tokenizer: object,
    layer: int,
    probe_text: str,
    feature_ids: tuple[int, ...] | list[int],
    max_tokens: int = 128,
) -> dict[int, float]:
    """Run probe through model + SAE; return max-token-activation for *specific* feature_ids.

    Companion to ``_run_probe_inference`` which returns top-K. This
    helper returns activations for caller-specified feature_ids — used
    by the §3a calibration when the active set is the §3b′ disjoint
    pair endpoints (not top-K by activation).

    Returns dict keyed by feature_id; values are absolute max activation
    across tokens for the probe.

    Raises:
        SAEBackendImportError: torch absent.
    """
    try:
        import torch
    except ImportError as e:
        raise SAEBackendImportError("torch") from e

    tokens = tokenizer(
        probe_text, max_length=max_tokens, truncation=True, return_tensors="pt",
    )
    with torch.no_grad():
        outputs = model(**tokens, output_hidden_states=True)
        residual = outputs.hidden_states[layer]    # (1, T, d_model)
        feat_acts = sae.encode(residual)            # (1, T, n_features)
    max_per_feat = feat_acts.abs().max(dim=1).values.squeeze(0)  # (n_features,)
    return {int(fid): float(max_per_feat[fid]) for fid in feature_ids}


def _sae_checkpoint_sha256(sae: object) -> str | None:
    """Best-effort SHA-256 of the loaded SAE checkpoint.

    sae-lens may not always expose checkpoint hashes in a stable form
    across versions. When unavailable, returns None and callers fall
    back to a sentinel SHA in `SAEProvenance.sha256`. This keeps the
    provenance-record contract intact even when the underlying loader
    doesn't surface a hash.
    """
    # sae-lens ≥ 5.x: try `sae.cfg.sha` or similar attribute
    cfg = getattr(sae, "cfg", None)
    if cfg is None:
        return None
    sha = getattr(cfg, "sha", None) or getattr(cfg, "sha256", None)
    if sha and isinstance(sha, str):
        if not sha.startswith("sha256:"):
            return f"sha256:{sha}"
        return sha
    return None
