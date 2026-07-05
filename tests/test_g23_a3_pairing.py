"""Tests for bulla/compute/g23_a3_pairing.py (G23 A3 commit 2).

Pure-Python tests for the §3b′ pairing pipeline's algorithmic core
(top-K selection, disjoint extraction, threshold computation, tripwire
logic, manifest lock). Tests do NOT make network calls or invoke
sentence-transformers / numpy heavy paths.

Tests that exercise the heavy paths (full pipeline run, embedding,
matrix computation) are gated on
``pytest.importorskip("numpy")`` and ``pytest.importorskip("sentence_transformers")``
respectively, and live in the explicit Iter-2 calibration runbook.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bulla.compute.g23_a3_pairing import (
    DEFAULT_N_DISJOINT,
    DEFAULT_TOP_K,
    LOCKED_EMBEDDING_MODEL,
    LOCKED_EMBEDDING_REVISION,
    LOCKED_F1_TOP_K,
    LOCKED_F1_TOP_K_FLOOR,
    LOCKED_SEED,
    LOCKED_SIDES,
    LOCKED_TRIPWIRE_MIN_CANDIDATES,
    LOCKED_TRIPWIRE_TOP200_FLOOR,
    FeatureLabel,
    PairingArtifacts,
    TripwireResult,
    _build_neuronpedia_url,
    _extract_label,
    check_pairing_tripwires,
    count_above,
    disjoint_pair_extraction,
    hash_array_file,
    hash_labels,
    lock_manifest,
    median,
)


# ── Locked constant sanity ────────────────────────────────────────────


class TestLockedConstants:
    """Verify §3b′ locked thresholds match the pre-registration document."""

    def test_embedding_model_locked(self):
        assert LOCKED_EMBEDDING_MODEL == "sentence-transformers/all-MiniLM-L6-v2"

    def test_embedding_revision_pinned(self):
        # 40-char SHA-1 of an HF revision; explicitly NOT "main" / "master"
        assert len(LOCKED_EMBEDDING_REVISION) == 40
        assert all(c in "0123456789abcdef" for c in LOCKED_EMBEDDING_REVISION)
        assert LOCKED_EMBEDDING_REVISION == "e4ce9877abf3edfe10b0d82785e83bdcb973e22e"

    def test_seed_locked_to_canonical_value(self):
        # 20260507 is the canonical seed shared with ActivationCorpus
        # in sae_lens_backend.py
        assert LOCKED_SEED == 20260507

    def test_thresholds_match_preregistration(self):
        # §3b‴ thresholds:
        assert LOCKED_TRIPWIRE_MIN_CANDIDATES == 100_000  # ≥10⁵
        assert LOCKED_TRIPWIRE_TOP200_FLOOR == 0.55
        assert DEFAULT_TOP_K == 200
        assert DEFAULT_N_DISJOINT == 30

    def test_f1_fallback_thresholds(self):
        assert LOCKED_F1_TOP_K == 500
        assert LOCKED_F1_TOP_K_FLOOR == 0.50

    def test_locked_sides_match_a3_pair(self):
        sides = {s["side"]: s for s in LOCKED_SIDES}
        assert "gemma" in sides and "gpt2" in sides
        # Gemma side: sae-lens internal identifiers
        g = sides["gemma"]
        assert g["model_id"] == "gemma2-2b"
        assert g["layer"] == 20
        assert g["release"] == "gemma-scope-2b-pt-res-canonical"
        assert g["sae_id"] == "layer_20/width_16k/canonical"
        assert g["n_features"] == 16384
        # Gemma side: Neuronpedia URL identifiers (must match the locked
        # Step 0 liveness curl in G23-A3-CALIBRATION-RUNBOOK.md).
        assert g["neuronpedia_model"] == "gemma-2-2b"
        assert g["neuronpedia_sae"] == "20-gemmascope-res-16k"
        # GPT-2-Small side (substrate-corrected 2026-05-07 after empirical
        # Neuronpedia probe revealed `gpt2-small-resid-post-v5-32k` and
        # `blocks.11.hook_resid_post` are NOT what Neuronpedia hosts).
        p = sides["gpt2"]
        assert p["model_id"] == "gpt2-small"
        assert p["layer"] == 11
        assert p["release"] == "gpt2-small-res-jb"
        assert p["sae_id"] == "blocks.11.hook_resid_pre"  # PRE not POST
        assert p["n_features"] == 24576                   # 24k not 32k
        # Neuronpedia identifiers (canonical source-set name)
        assert p["neuronpedia_model"] == "gpt2-small"
        assert p["neuronpedia_sae"] == "11-res-jb"        # no -32k suffix


# ── URL construction (must match Step 0 liveness curl in runbook) ────


class TestBuildNeuronpediaURL:
    """`_build_neuronpedia_url` produces the canonical Neuronpedia REST URL.

    This is load-bearing: the calibration runbook's Step 0 curls target
    exactly this URL pattern. Any drift between the constructor and Step
    0 defeats the substrate-failure tripwire (Step 0 PASS while pipeline
    silently produces empty labels).
    """

    @pytest.mark.parametrize("side,expected", [
        (
            "gemma",
            "https://www.neuronpedia.org/api/feature/gemma-2-2b/20-gemmascope-res-16k/0",
        ),
        (
            "gpt2",
            "https://www.neuronpedia.org/api/feature/gpt2-small/11-res-jb/0",
        ),
    ])
    def test_canonical_url_for_both_sides(self, side, expected):
        side_info = next(s for s in LOCKED_SIDES if s["side"] == side)
        url = _build_neuronpedia_url(
            neuronpedia_model=side_info["neuronpedia_model"],
            neuronpedia_sae=side_info["neuronpedia_sae"],
            feature_id=0,
        )
        assert url == expected

    def test_feature_id_appears_in_path(self):
        url = _build_neuronpedia_url(
            neuronpedia_model="gemma-2-2b",
            neuronpedia_sae="20-gemmascope-res-16k",
            feature_id=12345,
        )
        assert url.endswith("/20-gemmascope-res-16k/12345")

    def test_url_pattern_matches_runbook_step_0_format(self):
        # Step 0 in G23-A3-CALIBRATION-RUNBOOK.md uses:
        #   https://www.neuronpedia.org/api/feature/{model}/{sae}/0
        # The constructor must produce exactly this format so the
        # liveness check tests what the pipeline actually queries.
        for side_info in LOCKED_SIDES:
            url = _build_neuronpedia_url(
                neuronpedia_model=side_info["neuronpedia_model"],
                neuronpedia_sae=side_info["neuronpedia_sae"],
                feature_id=0,
            )
            assert url.startswith("https://www.neuronpedia.org/api/feature/")
            # After /api/feature/, the next two path components are
            # neuronpedia_model and neuronpedia_sae (no slashes inside).
            tail = url.split("/api/feature/", 1)[1]
            parts = tail.split("/")
            assert parts[0] == side_info["neuronpedia_model"]
            assert parts[1] == side_info["neuronpedia_sae"]
            assert parts[2] == "0"


class TestExtractLabel:
    """`_extract_label` parses Neuronpedia API response payloads."""

    def test_canonical_explanations_array_shape(self):
        # Neuronpedia returns: {modelId, layer, explanations: [{description, score_v1, ...}]}
        payload = {
            "modelId": "gemma-2-2b",
            "layer": 20,
            "explanations": [
                {"description": "neurons firing on legal terminology", "score_v1": 0.82},
            ],
        }
        assert _extract_label(payload) == "neurons firing on legal terminology"

    def test_takes_first_explanation_when_multiple(self):
        payload = {
            "explanations": [
                {"description": "first description", "score_v1": 0.9},
                {"description": "second description", "score_v1": 0.7},
            ],
        }
        assert _extract_label(payload) == "first description"

    def test_empty_explanations_returns_empty_string(self):
        assert _extract_label({"explanations": []}) == ""

    def test_missing_explanations_falls_back_to_top_level(self):
        # Legacy / alternative shape: top-level `description`
        payload = {"description": "legacy top-level description"}
        assert _extract_label(payload) == "legacy top-level description"

    def test_no_label_anywhere_returns_empty_string(self):
        assert _extract_label({}) == ""
        assert _extract_label({"explanations": [{}]}) == ""

    def test_explanation_alternative_key_text(self):
        # Some Neuronpedia routes return `text` instead of `description`
        payload = {"explanations": [{"text": "via the text field", "score_v1": 0.7}]}
        assert _extract_label(payload) == "via the text field"


# ── Algorithm: median + count_above (pure Python) ─────────────────────


class TestMedian:
    def test_odd_length(self):
        assert median((1.0, 2.0, 3.0)) == 2.0

    def test_even_length_avg(self):
        assert median((1.0, 2.0, 3.0, 4.0)) == 2.5

    def test_unsorted_input(self):
        assert median((5.0, 1.0, 3.0)) == 3.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match=r"undefined on empty"):
            median(())


class TestCountAbove:
    def test_strict_inequality(self):
        # values strictly > threshold
        assert count_above((0.5, 0.6, 0.5, 0.7), 0.55) == 2

    def test_no_above(self):
        assert count_above((0.1, 0.2, 0.3), 0.5) == 0

    def test_all_above(self):
        assert count_above((0.6, 0.7, 0.8), 0.5) == 3


# ── Algorithm: disjoint_pair_extraction (pure Python) ─────────────────


class TestDisjointPairExtraction:
    def test_top_3_no_collision(self):
        # 3 candidates with no collisions; n_target=3 should return all 3
        candidates = (
            (0, 0, 0.9), (1, 1, 0.85), (2, 2, 0.8),
        )
        assert disjoint_pair_extraction(candidates, n_target=3) == (
            (0, 0), (1, 1), (2, 2)
        )

    def test_collision_skipped(self):
        # Candidate (0, 1) shares feature_id 0 with already-accepted (0, 0)
        candidates = (
            (0, 0, 0.9),
            (0, 1, 0.8),  # shares i=0 → skip
            (1, 1, 0.7),  # shares j=1 with (0, 1) — but (0, 1) was skipped, so j=1 still free
            (2, 2, 0.6),
        )
        result = disjoint_pair_extraction(candidates, n_target=4)
        # Walking the list: accept (0,0), skip (0,1) (i=0 used),
        # accept (1,1), accept (2,2). Total 3.
        assert result == ((0, 0), (1, 1), (2, 2))

    def test_n_target_caps_output(self):
        candidates = tuple((i, i, 1.0 - 0.01 * i) for i in range(50))
        out = disjoint_pair_extraction(candidates, n_target=10)
        assert len(out) == 10
        assert out[0] == (0, 0)
        assert out[9] == (9, 9)

    def test_runs_out_returns_partial(self):
        # Only 2 candidates, target 5; should return 2.
        candidates = ((0, 0, 0.9), (1, 1, 0.8))
        out = disjoint_pair_extraction(candidates, n_target=5)
        assert out == ((0, 0), (1, 1))

    def test_empty_input_returns_empty(self):
        assert disjoint_pair_extraction((), n_target=10) == ()

    def test_locked_n_target_default(self):
        # The function's default matches §3b′ locked target of 30.
        candidates = tuple((i, i, 1.0) for i in range(50))
        out = disjoint_pair_extraction(candidates)
        assert len(out) == DEFAULT_N_DISJOINT


# ── hash_labels determinism ────────────────────────────────────────────


class TestHashLabels:
    def test_same_input_same_hash(self):
        a = (
            FeatureLabel(side="gemma", feature_id=0, label="cat"),
            FeatureLabel(side="gemma", feature_id=1, label="dog"),
        )
        b = (
            FeatureLabel(side="gemma", feature_id=0, label="cat"),
            FeatureLabel(side="gemma", feature_id=1, label="dog"),
        )
        assert hash_labels(a) == hash_labels(b)

    def test_order_independent(self):
        # Ordering of FeatureLabel input doesn't change the hash
        # (sorted by feature_id internally)
        a = (
            FeatureLabel(side="gemma", feature_id=1, label="dog"),
            FeatureLabel(side="gemma", feature_id=0, label="cat"),
        )
        b = (
            FeatureLabel(side="gemma", feature_id=0, label="cat"),
            FeatureLabel(side="gemma", feature_id=1, label="dog"),
        )
        assert hash_labels(a) == hash_labels(b)

    def test_label_change_changes_hash(self):
        a = (FeatureLabel(side="gemma", feature_id=0, label="cat"),)
        b = (FeatureLabel(side="gemma", feature_id=0, label="dog"),)
        assert hash_labels(a) != hash_labels(b)

    def test_returns_64_hex_chars(self):
        h = hash_labels((FeatureLabel(side="gemma", feature_id=0, label="x"),))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ── hash_array_file ────────────────────────────────────────────────────


class TestHashArrayFile:
    def test_file_hash(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert hash_array_file(f) == expected


# ── §3b‴ Tripwire logic ───────────────────────────────────────────────


def _mock_artifacts(
    *,
    threshold: float = 0.55,
    top_k: int = 200,
    top_k_min_cosine: float = 0.55,
    n_candidates_above_threshold: int = 100_000,
    n_disjoint_pairs: int = 30,
    fallback: str = "none",
) -> PairingArtifacts:
    return PairingArtifacts(
        threshold=threshold,
        top_k=top_k,
        top_k_min_cosine=top_k_min_cosine,
        n_candidates_above_threshold=n_candidates_above_threshold,
        n_disjoint_pairs=n_disjoint_pairs,
        disjoint_pairs=tuple((i, i) for i in range(n_disjoint_pairs)),
        fallback=fallback,
        gemma_labels_sha256="0" * 64,
        gpt2_labels_sha256="1" * 64,
        embeddings_gemma_sha256="2" * 64,
        embeddings_gpt2_sha256="3" * 64,
    )


class TestPairingTripwires:
    """The 4 §3b‴ tripwires."""

    def test_all_pass(self):
        artifacts = _mock_artifacts()
        results = check_pairing_tripwires(artifacts)
        assert len(results) == 4
        assert all(r.passed for r in results)
        names = {r.name for r in results}
        assert names == {
            "candidate_count", "top_200_cosine",
            "disjoint_30_reachability", "f2_reachability",
        }

    def test_candidate_count_below_floor_fails(self):
        artifacts = _mock_artifacts(n_candidates_above_threshold=99_999)
        results = check_pairing_tripwires(artifacts)
        candidate_count = next(r for r in results if r.name == "candidate_count")
        assert candidate_count.passed is False
        assert candidate_count.measured == 99_999

    def test_top200_cosine_below_floor_fails(self):
        artifacts = _mock_artifacts(top_k_min_cosine=0.54)
        results = check_pairing_tripwires(artifacts)
        top = next(r for r in results if r.name == "top_200_cosine")
        assert top.passed is False
        assert top.measured == 0.54

    def test_disjoint_30_below_floor_fails(self):
        artifacts = _mock_artifacts(n_disjoint_pairs=29)
        results = check_pairing_tripwires(artifacts)
        disj = next(r for r in results if r.name == "disjoint_30_reachability")
        assert disj.passed is False
        assert disj.measured == 29

    def test_f2_reachability_default_passes(self):
        # The default fallback marker is "none"; F2 reachability passes.
        results = check_pairing_tripwires(_mock_artifacts())
        f2 = next(r for r in results if r.name == "f2_reachability")
        assert f2.passed is True

    def test_f2_unreachable_marker_fails(self):
        # When the pipeline explicitly fell back to F1 and F2 is
        # unreachable, the marker fires.
        results = check_pairing_tripwires(_mock_artifacts(fallback="F1_failed_F2_unreachable"))
        f2 = next(r for r in results if r.name == "f2_reachability")
        assert f2.passed is False


# ── lock_manifest ─────────────────────────────────────────────────────


class TestLockManifest:
    """Test §4 manifest substitution + lock-anchor recording."""

    def _write_minimal_pre_reg(self, path: Path) -> None:
        # Mock a tiny pre-registration with the canonical TBD markers
        # in §4 in the canonical order. The lock procedure substitutes
        # these in order against artifact hashes.
        path.write_text(
            "# Pre-reg\n\n"
            "## §0\n\n"
            "| Lock event | hash | commit |\n"
            "|---|---|---|\n"
            "| LOCK (after Iter-2 calibration PASS) | *fill in at lock* | *fill in at lock* |\n\n"
            "## §4 manifest\n\n"
            "| Artifact | Path | SHA-256 | Size |\n"
            "|---|---|---|---|\n"
            "| Pre-reg | x | <TBD-self-hash-at-lock> | n/a |\n"
            "| Gemma JSON | x | <TBD-HASH> | 5 MB |\n"
            "| GPT-2 JSON | x | <TBD-HASH> | 10 MB |\n"
            "| Embeddings gemma | x | <TBD-HASH> | 25 MB |\n"
            "| Embeddings gpt2 | x | <TBD-HASH> | 50 MB |\n"
            "| τ | x | <TBD-HASH> | 1 KB |\n"
        )

    def test_lock_substitutes_tbd_markers(self, tmp_path):
        # v3.3: lock_manifest now uses unique `<TBD-HASH>` marker (was `<TBD>`)
        # + line-aware substitution. Test fixture mirrors the v3.3 marker
        # syntax. See feedback_pre_lock_revision_classes.md for the substitution-
        # shift bug class this fix prevents.
        pre_reg = tmp_path / "preg.md"
        self._write_minimal_pre_reg(pre_reg)

        threshold_path = tmp_path / "g23_a3_pairing_threshold.txt"
        threshold_path.write_text("0.5500000000\n")

        artifacts = _mock_artifacts()
        result = lock_manifest(
            output_dir=tmp_path,
            pre_registration_md=pre_reg,
            artifacts=artifacts,
        )
        text = pre_reg.read_text()
        # All <TBD-HASH> in §4 table cells should be replaced
        assert "<TBD-HASH>" not in text
        assert "<TBD-self-hash-at-lock>" not in text
        # Lock-anchor hash returned
        assert len(result["lock_anchor_sha256"]) == 64
        # The locked hash appears in §0 (replacing "*fill in at lock*")
        assert result["lock_anchor_sha256"] in text
        # Specific artifact hashes appear in §4
        assert artifacts.gemma_labels_sha256 in text
        assert artifacts.gpt2_labels_sha256 in text
        assert artifacts.embeddings_gemma_sha256 in text
        assert artifacts.embeddings_gpt2_sha256 in text


# ── PairingArtifacts dataclass ────────────────────────────────────────


class TestPairingArtifacts:
    def test_to_jsonable_round_trip(self):
        artifacts = _mock_artifacts(n_disjoint_pairs=3)
        d = artifacts.to_jsonable()
        # All required keys present
        for key in [
            "threshold", "top_k", "top_k_min_cosine",
            "n_candidates_above_threshold", "n_disjoint_pairs",
            "disjoint_pairs", "fallback",
            "gemma_labels_sha256", "gpt2_labels_sha256",
            "embeddings_gemma_sha256", "embeddings_gpt2_sha256",
        ]:
            assert key in d
        # JSON-serialisable
        s = json.dumps(d)
        # Round-trip preserves disjoint_pairs as list-of-lists
        assert json.loads(s)["disjoint_pairs"] == [[0, 0], [1, 1], [2, 2]]

    def test_frozen(self):
        artifacts = _mock_artifacts()
        with pytest.raises(Exception):
            artifacts.threshold = 0.0  # type: ignore[misc]


# ── Heavy-path smoke tests (gated on numpy availability) ──────────────


class TestNumpyHeavyPath:
    """Exercise compute_cosine_matrix + top_k_pairs_from_matrix when
    numpy is available. Skipped in numpy-free CI."""

    def test_compute_cosine_matrix_small(self):
        np = pytest.importorskip("numpy")
        from bulla.compute.g23_a3_pairing import compute_cosine_matrix
        emb_a = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        emb_b = np.array([[1.0, 0.0], [0.5, 0.5]], dtype=np.float32)
        C = compute_cosine_matrix(emb_a, emb_b)
        # C[0, 0] = cos((1,0), (1,0)) = 1
        # C[0, 1] = cos((1,0), (0.5,0.5)) = 0.7071
        # C[1, 0] = cos((0,1), (1,0)) = 0
        # C[1, 1] = cos((0,1), (0.5,0.5)) = 0.7071
        assert abs(float(C[0, 0]) - 1.0) < 1e-5
        assert abs(float(C[0, 1]) - 0.7071) < 1e-3
        assert abs(float(C[1, 0]) - 0.0) < 1e-5
        assert abs(float(C[1, 1]) - 0.7071) < 1e-3

    def test_top_k_pairs_from_matrix(self):
        np = pytest.importorskip("numpy")
        from bulla.compute.g23_a3_pairing import top_k_pairs_from_matrix
        # 3x3 matrix; top 2 should be the 2 highest values
        C = np.array([
            [0.9, 0.1, 0.5],
            [0.2, 0.8, 0.3],
            [0.4, 0.6, 0.7],
        ], dtype=np.float32)
        top_2 = top_k_pairs_from_matrix(C, k=2)
        assert len(top_2) == 2
        # Highest: (0, 0, 0.9), then (1, 1, 0.8)
        assert top_2[0][:2] == (0, 0)
        assert top_2[1][:2] == (1, 1)
        # Sorted descending
        assert top_2[0][2] >= top_2[1][2]


# ── Module-import smoke ───────────────────────────────────────────────


def test_module_imports_without_numpy():
    # The module itself must import without numpy / sentence-transformers /
    # huggingface-hub. Heavy imports happen inside function bodies.
    import bulla.compute.g23_a3_pairing as mod
    assert hasattr(mod, "run_pipeline")
    assert hasattr(mod, "check_pairing_tripwires")
    assert hasattr(mod, "lock_manifest")
