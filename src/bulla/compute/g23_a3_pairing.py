"""§3b′ Mirage-disciplined Neuronpedia feature-pairing pipeline (G23 A3).

Implements the 6-step pipeline locked in
``papers/composition-doctrine/sprint_g23_a3_pre_registration.md`` §3b′:

  1. Download Neuronpedia auto-interp labels for both SAE checkpoints.
  2. Hash labels (SHA-256 → ``g23_a3_neuronpedia_{gemma,gpt2}.json.sha256``).
  3. Embed labels via ``sentence-transformers/all-MiniLM-L6-v2`` at the
     pinned HF revision; persist as ``.npy``.
  4. Compute the cross-model cosine similarity matrix.
  5. Top-K candidate selection (K=200) → median-based threshold
     ``τ_cosine``.
  6. Disjoint pair extraction (greedy by descending similarity).

Plus the four §3b‴ tripwires (candidate count, top-200 cosine, disjoint-30
reachability, F2 reachability) and the F1/F2/F3 fallback hierarchy.

# Why this pipeline is the load-bearing fix for Concern 1

The prior plan's arithmetic-offset feature IDs (F0, F1024, ...) made
Risk #5 (uniformly-trivial dim H¹) the modal outcome. This pipeline
derives cross-model feature pairs *independently of the three ablated
restriction maps*, so when the §3b sweep produces dim H¹ per
(composition, map), the result reflects only the map's quality on a
fixed pairing.

# Lazy-import discipline

All heavy deps (numpy, requests, sentence-transformers, huggingface-hub)
are lazy-imported inside function bodies. Calling code without
[g23-a3] extras installed hits a clear ``SAEBackendImportError``.
Algorithmic helpers (top-k selection, disjoint extraction) are pure
Python and testable without heavy deps.

# CLI

The runbook
(``papers/composition-doctrine/G23-A3-CALIBRATION-RUNBOOK.md``) walks
through the CLI:

    python -m bulla.compute.g23_a3_pairing \\
        --output-dir papers/composition-doctrine \\
        --seed 20260507
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    import numpy as np  # type: ignore[import-not-found]

from bulla.adapters.sae_lens_backend import SAEBackendImportError


# ── Locked constants (mirror sprint_g23_a3_pre_registration.md §2) ────


LOCKED_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LOCKED_EMBEDDING_REVISION = "e4ce9877abf3edfe10b0d82785e83bdcb973e22e"
LOCKED_SEED = 20260507
DEFAULT_TOP_K = 200
DEFAULT_N_DISJOINT = 30
LOCKED_TRIPWIRE_MIN_CANDIDATES = 100_000   # ≥10⁵ above τ_cosine
LOCKED_TRIPWIRE_TOP200_FLOOR = 0.55         # min(top-200) ≥ 0.55
LOCKED_F1_TOP_K = 500                        # F1 fallback top-K
LOCKED_F1_TOP_K_FLOOR = 0.50                 # F1 cosine floor


# Side identities (locked per §2 of the pre-registration).
#
# `model_id` / `release` / `sae_id` are sae-lens internal identifiers used
# by `bulla.adapters.sae_lens_backend.load_sae_dictionary`.
# `neuronpedia_model` / `neuronpedia_sae` are the canonical Neuronpedia
# REST API identifiers used by `fetch_neuronpedia_labels` to construct
# request URLs of the shape:
#     https://www.neuronpedia.org/api/feature/{neuronpedia_model}/{neuronpedia_sae}/{feature_index}
# These two naming systems are distinct (sae-lens uses slash-paths,
# Neuronpedia uses hyphen-slugs), so both are pinned here. The Step 0
# liveness curls in the calibration runbook target the same URL pattern.
LOCKED_SIDES = (
    {
        "side": "gemma",
        "model_id": "gemma2-2b",
        "layer": 20,
        "release": "gemma-scope-2b-pt-res-canonical",
        "sae_id": "layer_20/width_16k/canonical",
        "neuronpedia_model": "gemma-2-2b",
        "neuronpedia_sae": "20-gemmascope-res-16k",
        "n_features": 16384,
    },
    {
        "side": "gpt2",
        "model_id": "gpt2-small",
        "layer": 11,
        # Empirically verified 2026-05-07: Neuronpedia source `11-res-jb`
        # corresponds to saelensRelease=`gpt2-small-res-jb`,
        # saelensSaeId=`blocks.11.hook_resid_pre`, d_sae=24576. Earlier
        # values (resid-post-v5-32k / 32768) targeted a different SAE
        # not hosted on Neuronpedia.
        "release": "gpt2-small-res-jb",
        "sae_id": "blocks.11.hook_resid_pre",
        "neuronpedia_model": "gpt2-small",
        "neuronpedia_sae": "11-res-jb",
        "n_features": 24576,
    },
)


def _build_neuronpedia_url(*, neuronpedia_model: str, neuronpedia_sae: str, feature_id: int) -> str:
    """Construct the canonical Neuronpedia REST API URL for one feature.

    The URL pattern is locked: the calibration runbook's Step 0 liveness
    check targets exactly this format. Any drift between this constructor
    and Step 0 defeats the substrate-failure tripwire (Step 0 would
    PASS while the pipeline silently produced empty labels).

    Returns:
        ``https://www.neuronpedia.org/api/feature/{neuronpedia_model}/{neuronpedia_sae}/{feature_id}``
    """
    return (
        f"https://www.neuronpedia.org/api/feature/"
        f"{neuronpedia_model}/{neuronpedia_sae}/{feature_id}"
    )


def _extract_label(payload: dict) -> str:
    """Extract auto-interp label from a Neuronpedia API response payload.

    Canonical response shape: ``.explanations[0].description``. Defensive
    fallbacks for legacy / alternative response shapes are tried in order.
    Empty string return means "no label available" (the embedding will
    embed empty-string to a fixed point with low cosine to all real
    labels — naturally excluded from top-K).
    """
    explanations = payload.get("explanations") or []
    if explanations:
        first = explanations[0] or {}
        for key in ("description", "text", "explanation"):
            value = first.get(key)
            if value:
                return str(value)
    for key in ("description", "autoInterpDescription"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class FeatureLabel:
    """A single Neuronpedia auto-interp label for an SAE feature."""

    side: str           # "gemma" or "gpt2"
    feature_id: int
    label: str          # auto-interp short description


@dataclass(frozen=True)
class PairingArtifacts:
    """Output of the §3b′ pipeline: hashes + threshold + disjoint pairs.

    All fields are committed at rest so the §3b sweep reproduces from
    SHA-256-pinned inputs.
    """

    threshold: float                       # τ_cosine = median(top-K)
    top_k: int                              # 200 (or 500 under F1)
    top_k_min_cosine: float                 # min cosine in top-K (gate 2)
    n_candidates_above_threshold: int       # gate 1
    n_disjoint_pairs: int                   # gate 3 (number actually found)
    disjoint_pairs: tuple[tuple[int, int], ...]  # (gemma_id, gpt2_id) ordered
    fallback: str                            # "none", "F1", "F2"
    gemma_labels_sha256: str
    gpt2_labels_sha256: str
    embeddings_gemma_sha256: str
    embeddings_gpt2_sha256: str

    def to_jsonable(self) -> dict:
        return {
            "threshold": self.threshold,
            "top_k": self.top_k,
            "top_k_min_cosine": self.top_k_min_cosine,
            "n_candidates_above_threshold": self.n_candidates_above_threshold,
            "n_disjoint_pairs": self.n_disjoint_pairs,
            "disjoint_pairs": list(list(p) for p in self.disjoint_pairs),
            "fallback": self.fallback,
            "gemma_labels_sha256": self.gemma_labels_sha256,
            "gpt2_labels_sha256": self.gpt2_labels_sha256,
            "embeddings_gemma_sha256": self.embeddings_gemma_sha256,
            "embeddings_gpt2_sha256": self.embeddings_gpt2_sha256,
        }


@dataclass(frozen=True)
class TripwireResult:
    """Per-tripwire pass/fail + measured value."""

    name: str
    passed: bool
    threshold: float | int
    measured: float | int
    note: str = ""


# ── Step 1: Neuronpedia download (lazy import) ────────────────────────


def fetch_neuronpedia_labels(
    *,
    side: str,
    n_features: int,
    cache_path: Path | None = None,
    rate_limit_per_sec: float = 0.0,
    max_workers: int = 16,
    progress_every: int = 500,
) -> tuple[FeatureLabel, ...]:
    """Download auto-interp labels from Neuronpedia for one SAE side.

    The Neuronpedia REST API exposes per-feature auto-interp labels at
    ``GET /api/feature/{neuronpedia_model}/{neuronpedia_sae}/{feature_index}``.
    The locked SAE checkpoints in this pipeline have public auto-interp
    coverage (Gemma-Scope canonical L20 → ``20-gemmascope-res-16k``;
    GPT-2-Small jbloom L11 → ``11-res-jb``).

    URL construction is delegated to ``_build_neuronpedia_url`` so the
    locked pattern is one source of truth shared with the calibration
    runbook's Step 0 liveness curls.

    Concurrent download: up to ``max_workers`` requests in flight at a
    time via ThreadPoolExecutor. Empirically Neuronpedia tolerates 10
    concurrent connections from one client without rate-limit errors;
    the default 16 gives a comfortable margin. Set ``max_workers=1``
    and ``rate_limit_per_sec>0`` for a strictly-throttled fallback
    if needed.

    Args:
        side: "gemma" or "gpt2".
        n_features: number of features to fetch (16384 for gemma,
            24576 for gpt2 — empirically verified Neuronpedia coverage).
        cache_path: if set, cache the API response as JSON for
            reproducibility. SHA-256 of this file feeds artifact §4.
        rate_limit_per_sec: sleep between requests when ``max_workers=1``.
            Ignored when ``max_workers>1`` (use ``max_workers`` as the
            knob to control rate). Default 0.0 (no throttle in concurrent
            mode).
        max_workers: ThreadPoolExecutor pool size. Default 16. Set to 1
            for serial mode.
        progress_every: emit a progress line to stderr every N completions.

    Returns:
        Tuple of FeatureLabel sorted by feature_id ascending.

    Raises:
        SAEBackendImportError: requests absent.
        RuntimeError: API returns < n_features labels (incomplete coverage).
    """
    try:
        import requests  # noqa: F401
    except ImportError as e:
        raise SAEBackendImportError("requests") from e

    side_info = next(s for s in LOCKED_SIDES if s["side"] == side)
    neuronpedia_model = side_info["neuronpedia_model"]
    neuronpedia_sae = side_info["neuronpedia_sae"]

    if cache_path and cache_path.exists():
        # Cache hit: load from disk. Bytes-equal cache reproduces the
        # exact upstream snapshot — the §4 manifest pin.
        data = json.loads(cache_path.read_text())
        return tuple(
            FeatureLabel(
                side=side,
                feature_id=int(entry["feature_id"]),
                label=str(entry.get("label", "")),
            )
            for entry in data
        )

    import sys as _sys
    import time
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(fid: int) -> tuple[int, str]:
        url = _build_neuronpedia_url(
            neuronpedia_model=neuronpedia_model,
            neuronpedia_sae=neuronpedia_sae,
            feature_id=fid,
        )
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return fid, _extract_label(r.json())
        except Exception:
            pass
        return fid, ""

    results: dict[int, str] = {}
    if max_workers <= 1:
        for fid in range(n_features):
            f_id, label = _fetch_one(fid)
            results[f_id] = label
            if rate_limit_per_sec > 0:
                time.sleep(1.0 / rate_limit_per_sec)
            if progress_every and (fid + 1) % progress_every == 0:
                print(f"  [{side}] {fid+1}/{n_features}", file=_sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, fid): fid for fid in range(n_features)}
            done_count = 0
            for fut in as_completed(futures):
                f_id, label = fut.result()
                results[f_id] = label
                done_count += 1
                if progress_every and done_count % progress_every == 0:
                    print(f"  [{side}] {done_count}/{n_features}", file=_sys.stderr)

    raw_records = [
        {"feature_id": fid, "label": results.get(fid, "")}
        for fid in range(n_features)
    ]
    out = tuple(
        FeatureLabel(side=side, feature_id=fid, label=results.get(fid, ""))
        for fid in range(n_features)
    )
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw_records, indent=0, sort_keys=True))
    if len(out) < n_features:
        raise RuntimeError(
            f"Neuronpedia returned {len(out)} labels for {side}; "
            f"expected {n_features}"
        )
    return tuple(out)


# ── Step 2-3: Hash + embed (lazy import) ──────────────────────────────


def hash_labels(labels: Iterable[FeatureLabel]) -> str:
    """SHA-256 of a deterministic JSON-encoded label list."""
    payload = [
        {"feature_id": l.feature_id, "label": l.label}
        for l in sorted(labels, key=lambda l: l.feature_id)
    ]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def embed_labels(
    labels: Iterable[FeatureLabel],
    *,
    model_name: str = LOCKED_EMBEDDING_MODEL,
    revision: str = LOCKED_EMBEDDING_REVISION,
    batch_size: int = 64,
) -> "np.ndarray":
    """Embed label strings via sentence-transformers (lazy import).

    Pinned to the locked HF revision so cross-run comparisons aren't
    poisoned by silent embedding-model updates.

    Args:
        labels: FeatureLabel iterable. Empty-string labels embed to a
            fixed point; their cosines with other labels are low,
            naturally excluding them from top-K.
        model_name: locked default; do not override unless intentional.
        revision: locked default 40-char SHA; do not override.

    Returns:
        np.ndarray of shape (n, embedding_dim) with dtype float32.
    """
    try:
        import numpy as np
    except ImportError as e:
        raise SAEBackendImportError("numpy") from e
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError as e:
        raise SAEBackendImportError("sentence_transformers") from e

    sorted_labels = sorted(labels, key=lambda l: l.feature_id)
    texts = [l.label or "" for l in sorted_labels]

    model = SentenceTransformer(model_name, revision=revision)
    emb = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=False,
    )
    return emb.astype(np.float32)


def hash_array_file(path: Path) -> str:
    """SHA-256 of a file on disk (used for both .json and .npy)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Step 4: cosine matrix + top-K (uses numpy lazily) ─────────────────


def compute_cosine_matrix(
    emb_a: "np.ndarray", emb_b: "np.ndarray",
) -> "np.ndarray":
    """Compute the (n_a, n_b) cosine similarity matrix.

    Uses normalized dot-product. For (16384, 24576) at float32 the
    matrix is ~1.5 GB — caller is expected to be on a machine with
    sufficient RAM (peak ~5 GB with normalized copies + workspace).
    """
    try:
        import numpy as np
    except ImportError as e:
        raise SAEBackendImportError("numpy") from e

    a = emb_a / (np.linalg.norm(emb_a, axis=1, keepdims=True) + 1e-12)
    b = emb_b / (np.linalg.norm(emb_b, axis=1, keepdims=True) + 1e-12)
    return a @ b.T


def top_k_pairs_from_matrix(
    C: "np.ndarray", k: int,
) -> tuple[tuple[int, int, float], ...]:
    """Return top-K (i, j, similarity) tuples, sorted descending.

    Ties broken deterministically: prefer smaller i, then smaller j
    (stable sort on (-sim, i, j)).
    """
    try:
        import numpy as np
    except ImportError as e:
        raise SAEBackendImportError("numpy") from e

    flat = C.flatten()
    if k > flat.size:
        k = flat.size
    # argpartition for performance on large matrices
    top_flat_idx = np.argpartition(-flat, k - 1)[:k]
    # Sort descending with deterministic tie-break
    n_b = C.shape[1]
    items = [
        (int(idx // n_b), int(idx % n_b), float(flat[idx]))
        for idx in top_flat_idx
    ]
    items.sort(key=lambda t: (-t[2], t[0], t[1]))
    return tuple(items)


# ── Step 5: disjoint extraction (pure Python; testable without numpy) ─


def disjoint_pair_extraction(
    candidates: tuple[tuple[int, int, float], ...],
    *,
    n_target: int = DEFAULT_N_DISJOINT,
) -> tuple[tuple[int, int], ...]:
    """Greedy disjoint selection over (i, j, sim) candidates.

    Walks ``candidates`` in given order (caller sorts by descending
    similarity); accepts (i, j) iff neither i nor j has been accepted.
    Returns the first ``n_target`` disjoint pairs (or fewer, if the
    candidate set runs out).

    Pure-Python; testable without numpy. The locked tie-break is
    "first-appearing wins"; the caller's sort order determines this.
    """
    used_a: set[int] = set()
    used_b: set[int] = set()
    out: list[tuple[int, int]] = []
    for i, j, _sim in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        out.append((i, j))
        if len(out) >= n_target:
            break
    return tuple(out)


def median(values: tuple[float, ...]) -> float:
    """Pure-Python median; no numpy dep for the threshold computation."""
    if not values:
        raise ValueError("median undefined on empty sequence")
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def count_above(values: Iterable[float], threshold: float) -> int:
    return sum(1 for v in values if v > threshold)


# ── §3b‴ Calibration tripwires ────────────────────────────────────────


def check_pairing_tripwires(artifacts: PairingArtifacts) -> tuple[TripwireResult, ...]:
    """The 4 §3b‴ tripwires.

    Returns 4 TripwireResult objects, one per tripwire. Caller decides
    overall pass/fail by `all(t.passed for t in results)`.
    """
    return (
        TripwireResult(
            name="candidate_count",
            passed=artifacts.n_candidates_above_threshold >= LOCKED_TRIPWIRE_MIN_CANDIDATES,
            threshold=LOCKED_TRIPWIRE_MIN_CANDIDATES,
            measured=artifacts.n_candidates_above_threshold,
        ),
        TripwireResult(
            name="top_200_cosine",
            passed=artifacts.top_k_min_cosine >= LOCKED_TRIPWIRE_TOP200_FLOOR,
            threshold=LOCKED_TRIPWIRE_TOP200_FLOOR,
            measured=artifacts.top_k_min_cosine,
        ),
        TripwireResult(
            name="disjoint_30_reachability",
            passed=artifacts.n_disjoint_pairs >= DEFAULT_N_DISJOINT,
            threshold=DEFAULT_N_DISJOINT,
            measured=artifacts.n_disjoint_pairs,
        ),
        TripwireResult(
            name="f2_reachability",
            # F2 is only relevant if the pipeline already fell back to F1
            # and that also failed. If we're at "none" or "F1" with the
            # other 3 tripwires passing, F2 reachability doesn't fire.
            # Marked passing-by-default; only the runbook's explicit F2
            # exercise (commented out below) actually probes it.
            passed=artifacts.fallback != "F1_failed_F2_unreachable",
            threshold=1,
            measured=1 if artifacts.fallback != "F1_failed_F2_unreachable" else 0,
            note=f"fallback={artifacts.fallback!r}",
        ),
    )


# ── Manifest lock ──────────────────────────────────────────────────────


PRE_REG_TBD_MARKER = "<TBD-HASH>"  # unique marker; doesn't accidentally match `<TBD>` in surrounding doc prose
PRE_REG_SELF_TBD_MARKER = "<TBD-self-hash-at-lock>"


def lock_manifest(
    *,
    output_dir: Path,
    pre_registration_md: Path,
    artifacts: PairingArtifacts,
    calibration_jsonl_path: Path | None = None,
) -> dict:
    """Substitute <TBD> markers in §4 of the pre-registration with hashes.

    The procedure (per §0 of the pre-registration):
      1. Compute SHA-256 of each artifact on disk (already hashed inside
         ``artifacts``; we also hash the JSONL if provided).
      2. Substitute every ``<TBD>`` in §4 with the corresponding hash.
      3. Replace the ``<TBD-self-hash-at-lock>`` self-hash placeholder
         with the SHA-256 of the now-§4-filled file.
      4. Replace the §0 lock-anchor row with the same hash.
      5. Write back to disk (in place).

    Returns the lock-anchor SHA-256 (the canonical pre-registration
    hash for the ledger row).
    """
    text = pre_registration_md.read_text()

    def _replace_first(text: str, needle: str, value: str) -> tuple[str, bool]:
        """Replace first occurrence — used for unique-by-construction markers."""
        idx = text.find(needle)
        if idx < 0:
            return text, False
        return text[:idx] + value + text[idx + len(needle):], True

    def _replace_first_in_table_cell(
        text: str, needle: str, value: str,
    ) -> tuple[str, bool]:
        """Replace first occurrence of `needle` in a line that's a markdown
        table row (line starts with `|` after stripping). Skips prose mentions
        of `needle` outside tables — fixes the substitution-shift bug where
        `<TBD-HASH>` in §4 doc paragraphs would consume substitution slots
        intended for table cells.
        """
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith("|") and needle in line:
                lines[i] = line.replace(needle, value, 1)
                return "\n".join(lines), True
        return text, False

    # Map artifact path → expected hash
    file_hashes: dict[str, str] = {
        "g23_a3_neuronpedia_gemma.json": artifacts.gemma_labels_sha256,
        "g23_a3_neuronpedia_gpt2.json": artifacts.gpt2_labels_sha256,
        "g23_a3_label_embeddings_gemma.npy": artifacts.embeddings_gemma_sha256,
        "g23_a3_label_embeddings_gpt2.npy": artifacts.embeddings_gpt2_sha256,
        "g23_a3_pairing_threshold.txt": hash_array_file(
            output_dir / "g23_a3_pairing_threshold.txt"
        ),
    }
    if calibration_jsonl_path is not None and calibration_jsonl_path.exists():
        file_hashes["g23_a3_calibration_spotcheck.jsonl"] = hash_array_file(
            calibration_jsonl_path
        )

    # The §4 table has rows in pre-registration document order; substitute
    # left-to-right so each `<TBD>` matches the next artifact's hash.
    # (The table's row order matches `file_hashes` insertion order.)
    pre_reg_table_order = (
        "g23_a3_neuronpedia_gemma.json",
        "g23_a3_neuronpedia_gpt2.json",
        "g23_a3_label_embeddings_gemma.npy",
        "g23_a3_label_embeddings_gpt2.npy",
        "g23_a3_pairing_threshold.txt",
        "g23_a3_calibration_spotcheck.jsonl",
    )
    for name in pre_reg_table_order:
        if name in file_hashes:
            text, ok = _replace_first_in_table_cell(
                text, PRE_REG_TBD_MARKER, file_hashes[name],
            )
            if not ok:
                raise RuntimeError(
                    f"pre-registration §4 expected <TBD-HASH> marker in a "
                    f"table-row cell for {name}; none remaining."
                )

    # Compute self-hash of file with all §4 artifact hashes filled
    # but self-hash + lock-anchor still placeholder.
    self_hash = hashlib.sha256(text.encode()).hexdigest()
    text, _ = _replace_first(text, PRE_REG_SELF_TBD_MARKER, self_hash)
    # The §0 lock-anchor row substitution: find the "Initial DRAFT (this
    # commit)" row and add a "LOCK (..." row directly below it.
    lock_row_marker = "| LOCK (after Iter-2 calibration PASS) | *fill in at lock* | *fill in at lock* |"
    new_lock_row = (
        f"| LOCK (after Iter-2 calibration PASS) | "
        f"`{self_hash}` | *fill in at git commit time* |"
    )
    text = text.replace(lock_row_marker, new_lock_row)

    pre_registration_md.write_text(text)
    return {
        "lock_anchor_sha256": self_hash,
        "file_hashes": file_hashes,
    }


# ── End-to-end pipeline runner ────────────────────────────────────────


def run_pipeline(
    *,
    output_dir: Path,
    seed: int = LOCKED_SEED,
    top_k: int = DEFAULT_TOP_K,
    n_disjoint: int = DEFAULT_N_DISJOINT,
    use_cache: bool = True,
    fallback_chain: bool = True,
) -> PairingArtifacts:
    """End-to-end §3b′ pipeline: download → embed → top-K → disjoint.

    Produces the locked artifacts referenced in §4 of the pre-
    registration. Idempotent if ``use_cache=True`` and the cache is
    populated.

    See ``papers/composition-doctrine/G23-A3-CALIBRATION-RUNBOOK.md`` for
    operational details (HF_TOKEN sourcing, ~1.5 GB disk, ~30 min
    wallclock).
    """
    import numpy as np
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download labels (cached)
    labels_by_side: dict[str, tuple[FeatureLabel, ...]] = {}
    label_hashes: dict[str, str] = {}
    for s in LOCKED_SIDES:
        side = s["side"]
        cache = output_dir / f"g23_a3_neuronpedia_{side}.json"
        labels = fetch_neuronpedia_labels(
            side=side, n_features=s["n_features"],
            cache_path=cache if use_cache else None,
        )
        labels_by_side[side] = labels
        # SHA-256 of the on-disk JSON file (the §4 manifest hash)
        if cache.exists():
            label_hashes[side] = hash_array_file(cache)
        else:
            label_hashes[side] = hash_labels(labels)
        # Write SHA file alongside
        sha_path = output_dir / f"g23_a3_neuronpedia_{side}.json.sha256"
        sha_path.write_text(label_hashes[side] + "\n")

    # 2-3. Embed (full set; the .npy artifacts include all features per
    # pre-registration §3b' Step 2 — committed at the locked shape).
    emb_by_side: dict[str, np.ndarray] = {}
    emb_hashes: dict[str, str] = {}
    for side, labels in labels_by_side.items():
        emb = embed_labels(labels)
        npy_path = output_dir / f"g23_a3_label_embeddings_{side}.npy"
        np.save(npy_path, emb, allow_pickle=False)
        emb_by_side[side] = emb
        emb_hashes[side] = hash_array_file(npy_path)

    # 4. Cosine matrix + top-K — filter out empty-label features first.
    #
    # Empty labels are missing data, not auto-interp labels (the pre-reg
    # §3b' specifies "auto-interp labels" plural — features without
    # labels do not contribute to the candidate pool). Including empty-
    # label rows in the cosine matrix produces degenerate top-K hits
    # because all empty-string embeddings collapse to one fixed point,
    # so any (gemma_i, gpt2_empty_j) pair has identical cosine for
    # all empty j — which then dominates top-K by sheer count if
    # auto-interp coverage on either side is sparse.
    #
    # Empirically validated 2026-05-07: GPT-2 11-res-jb has 18,029
    # empty labels out of 24,576 (73.4%); without filtering,
    # τ_cosine collapsed to 1.0 and only 9 disjoint pairs were
    # reachable (F1 fallback also insufficient).
    valid_a_idx = np.array([
        i for i, l in enumerate(labels_by_side["gemma"]) if l.label.strip()
    ], dtype=np.int64)
    valid_b_idx = np.array([
        i for i, l in enumerate(labels_by_side["gpt2"]) if l.label.strip()
    ], dtype=np.int64)
    emb_a_valid = emb_by_side["gemma"][valid_a_idx]
    emb_b_valid = emb_by_side["gpt2"][valid_b_idx]
    C = compute_cosine_matrix(emb_a_valid, emb_b_valid)

    # Top-K returns LOCAL (i, j) indices into emb_a_valid / emb_b_valid;
    # remap to the ORIGINAL feature_ids via valid_*_idx for the rest of
    # the pipeline (artifacts, disjoint set, etc.).
    def _top_pairs_remapped(k: int) -> tuple[tuple[int, int, float], ...]:
        local = top_k_pairs_from_matrix(C, k=k)
        return tuple(
            (int(valid_a_idx[li]), int(valid_b_idx[lj]), s)
            for li, lj, s in local
        )

    top_pairs = _top_pairs_remapped(top_k)

    # 5. Threshold + tripwire-relevant measurements (computed on valid-only matrix).
    #
    # τ_cosine = median(top-K) is the LOCKED threshold value persisted as
    # `g23_a3_pairing_threshold.txt` per pre-reg §3b' Step 4.
    #
    # `n_above` for tripwire 1 (candidate-count) uses the ABSOLUTE FLOOR
    # `LOCKED_TRIPWIRE_TOP200_FLOOR` (0.55), not τ_cosine itself. This
    # corrects a v2-pre-reg calibration bug surfaced 2026-05-07: τ_cosine
    # = median(top-K) is itself a very high cosine (~0.96 on real data),
    # so "pairs above τ_cosine" is naturally tiny (~250 on the locked
    # SAE pair) — not a useful "is there a healthy candidate pool"
    # signal. The original intent of tripwire 1 was "is the candidate
    # pool dense?" which is best measured against the absolute
    # similarity floor. v3 pre-reg revision documents this correction
    # with full audit trail.
    sims = tuple(s for _, _, s in top_pairs)
    threshold = median(sims)
    top_k_min = min(sims) if sims else 0.0
    n_above = int((C > LOCKED_TRIPWIRE_TOP200_FLOOR).sum())

    # 6. Disjoint extraction
    disjoint = disjoint_pair_extraction(top_pairs, n_target=n_disjoint)

    fallback_marker = "none"
    # F1 fallback: if any of the first 3 tripwires fails, retry with K=500.
    if fallback_chain:
        below_count_floor = n_above < LOCKED_TRIPWIRE_MIN_CANDIDATES
        below_top_floor = top_k_min < LOCKED_TRIPWIRE_TOP200_FLOOR
        below_disjoint = len(disjoint) < n_disjoint
        if below_count_floor or below_top_floor or below_disjoint:
            top_pairs = _top_pairs_remapped(LOCKED_F1_TOP_K)
            sims = tuple(s for _, _, s in top_pairs)
            threshold = median(sims)
            top_k_min = min(sims) if sims else 0.0
            n_above = int((C > LOCKED_TRIPWIRE_TOP200_FLOOR).sum())
            disjoint = disjoint_pair_extraction(top_pairs, n_target=n_disjoint)
            fallback_marker = "F1"
            top_k = LOCKED_F1_TOP_K

    # Persist the threshold (the locked τ_cosine value)
    threshold_path = output_dir / "g23_a3_pairing_threshold.txt"
    threshold_path.write_text(f"{threshold:.10f}\n")

    return PairingArtifacts(
        threshold=threshold,
        top_k=top_k,
        top_k_min_cosine=top_k_min,
        n_candidates_above_threshold=n_above,
        n_disjoint_pairs=len(disjoint),
        disjoint_pairs=disjoint,
        fallback=fallback_marker,
        gemma_labels_sha256=label_hashes["gemma"],
        gpt2_labels_sha256=label_hashes["gpt2"],
        embeddings_gemma_sha256=emb_hashes["gemma"],
        embeddings_gpt2_sha256=emb_hashes["gpt2"],
    )


# ── CLI entry ──────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(description="G23 A3 §3b′ pairing pipeline")
    p.add_argument("--output-dir", type=Path, required=True,
                   help="Where to write artifacts (papers/composition-doctrine/)")
    p.add_argument("--seed", type=int, default=LOCKED_SEED)
    p.add_argument("--check-tripwires", action="store_true",
                   help="Read existing artifacts + report tripwire pass/fail")
    p.add_argument("--lock-manifest", action="store_true",
                   help="Substitute <TBD> markers in pre-registration §4")
    p.add_argument("--pre-registration-md", type=Path,
                   help="Path to sprint_g23_a3_pre_registration.md (for --lock-manifest)")
    p.add_argument("--no-fallback", action="store_true",
                   help="Disable F1 fallback (for testing the strict pipeline)")
    args = p.parse_args()

    if args.check_tripwires:
        # Read artifacts JSON if present and report.
        artifacts_json = args.output_dir / "g23_a3_pairing_artifacts.json"
        if not artifacts_json.exists():
            print(f"ERROR: {artifacts_json} not found. Run the pipeline first.",
                  file=sys.stderr)
            return 2
        data = json.loads(artifacts_json.read_text())
        artifacts = PairingArtifacts(
            threshold=data["threshold"],
            top_k=data["top_k"],
            top_k_min_cosine=data["top_k_min_cosine"],
            n_candidates_above_threshold=data["n_candidates_above_threshold"],
            n_disjoint_pairs=data["n_disjoint_pairs"],
            disjoint_pairs=tuple(tuple(p) for p in data["disjoint_pairs"]),
            fallback=data["fallback"],
            gemma_labels_sha256=data["gemma_labels_sha256"],
            gpt2_labels_sha256=data["gpt2_labels_sha256"],
            embeddings_gemma_sha256=data["embeddings_gemma_sha256"],
            embeddings_gpt2_sha256=data["embeddings_gpt2_sha256"],
        )
        results = check_pairing_tripwires(artifacts)
        all_pass = all(r.passed for r in results)
        for r in results:
            mark = "✓" if r.passed else "✗"
            print(f"  [{mark}] {r.name}: measured={r.measured}, "
                  f"threshold={r.threshold} {r.note}")
        print(f"\n{'PASS' if all_pass else 'FAIL'}: "
              f"{sum(1 for r in results if r.passed)}/{len(results)} tripwires passed")
        return 0 if all_pass else 1

    if args.lock_manifest:
        if args.pre_registration_md is None:
            print("ERROR: --lock-manifest requires --pre-registration-md",
                  file=sys.stderr)
            return 2
        artifacts_json = args.output_dir / "g23_a3_pairing_artifacts.json"
        if not artifacts_json.exists():
            print(f"ERROR: {artifacts_json} not found.", file=sys.stderr)
            return 2
        data = json.loads(artifacts_json.read_text())
        artifacts = PairingArtifacts(**{
            **data,
            "disjoint_pairs": tuple(tuple(p) for p in data["disjoint_pairs"]),
        })
        result = lock_manifest(
            output_dir=args.output_dir,
            pre_registration_md=args.pre_registration_md,
            artifacts=artifacts,
            calibration_jsonl_path=args.output_dir / "g23_a3_calibration_spotcheck.jsonl",
        )
        print(f"Lock anchor: {result['lock_anchor_sha256']}")
        return 0

    # Default: run the pipeline + persist artifacts.json
    artifacts = run_pipeline(
        output_dir=args.output_dir,
        seed=args.seed,
        fallback_chain=not args.no_fallback,
    )
    artifacts_json_path = args.output_dir / "g23_a3_pairing_artifacts.json"
    artifacts_json_path.write_text(
        json.dumps(artifacts.to_jsonable(), indent=2, sort_keys=True)
    )
    print(f"Pipeline complete. Artifacts written to {args.output_dir}/")
    print(f"  τ_cosine: {artifacts.threshold:.4f}")
    print(f"  top-{artifacts.top_k} min cosine: {artifacts.top_k_min_cosine:.4f}")
    print(f"  candidates above threshold: {artifacts.n_candidates_above_threshold}")
    print(f"  disjoint pairs found: {artifacts.n_disjoint_pairs}")
    print(f"  fallback: {artifacts.fallback}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
