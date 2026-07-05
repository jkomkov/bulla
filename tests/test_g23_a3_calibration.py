"""Tests for bulla/compute/g23_a3_calibration.py (G23 A3 commit 2).

Tests for the §3a calibration spot-check: probe pair locking, tripwire
logic, JSONL I/O. The actual SAE-inference path
(``run_calibration_on_probe``) requires sae-lens and is not exercised
here — it's tested via the runbook on the user's machine with HF
credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla.compute.g23_a3_calibration import (
    ALL_PROBES,
    DEFAULT_TOP_N_FEATURES,
    DEFAULT_SEED,
    LOCKED_PROBES_P1,
    LOCKED_PROBES_P2,
    CalibrationSample,
    CalibrationTripwireResult,
    check_calibration_tripwires,
)


# ── Locked probe text sanity ──────────────────────────────────────────


class TestLockedProbes:
    """Probes match the pre-registration §3a verbatim."""

    def test_p1_5_factual_recall_probes(self):
        assert len(LOCKED_PROBES_P1) == 5
        for pid, cat, _text in LOCKED_PROBES_P1:
            assert pid.startswith("P1.")
            assert cat == "factual_recall"

    def test_p2_5_instruction_following_probes(self):
        assert len(LOCKED_PROBES_P2) == 5
        for pid, cat, _text in LOCKED_PROBES_P2:
            assert pid.startswith("P2.")
            assert cat == "instruction_following"

    def test_all_probes_distinct_text(self):
        texts = [text for _, _, text in ALL_PROBES]
        assert len(set(texts)) == 10  # all distinct

    def test_locked_probe_text(self):
        # Spot-check the first probe of each category to lock the text
        # exactly as it appears in §3a of the pre-registration.
        assert LOCKED_PROBES_P1[0] == (
            "P1.1", "factual_recall",
            "Paris is the capital of France.",
        )
        assert LOCKED_PROBES_P2[0] == (
            "P2.1", "instruction_following",
            "List three primary colors.",
        )

    def test_default_top_n_features_locked(self):
        # §3a locks top-50 features per side per probe
        assert DEFAULT_TOP_N_FEATURES == 50

    def test_default_seed_locked(self):
        assert DEFAULT_SEED == 20260507


# ── §3a tripwire logic ────────────────────────────────────────────────


def _mock_sample(
    *,
    probe_id: str = "P1.1",
    probe_category: str = "factual_recall",
    dim_h1: int = 5,
    procrustes_loss: float = 1.5,
    b0: float = 0.5,
    n_a: int = 30,
    n_b: int = 30,
) -> CalibrationSample:
    return CalibrationSample(
        probe_id=probe_id, probe_category=probe_category,
        probe_text=f"text-{probe_id}",
        dim_h1=dim_h1, procrustes_loss=procrustes_loss, b0=b0,
        n_features_a_used=n_a, n_features_b_used=n_b,
    )


def _balanced_samples(
    *, p1_dim_h1=5, p2_dim_h1=10,
    p1_b0=0.5, p2_b0=0.6,
) -> tuple[CalibrationSample, ...]:
    """5 P1 samples + 5 P2 samples; canonical happy-path baseline.

    Default P1 b0 = 0.5, P2 b0 = 0.6 — non-equal so v3.2's B0-based
    tripwire 5 (probe_category_distinguishability) passes by default.
    """
    p1 = [
        _mock_sample(
            probe_id=f"P1.{i+1}", probe_category="factual_recall",
            dim_h1=p1_dim_h1, b0=p1_b0,
        )
        for i in range(5)
    ]
    p2 = [
        _mock_sample(
            probe_id=f"P2.{i+1}", probe_category="instruction_following",
            dim_h1=p2_dim_h1, b0=p2_b0,
        )
        for i in range(5)
    ]
    return tuple(p1 + p2)


class TestCalibrationTripwires:
    """The 5 §3a tripwires."""

    def test_all_pass_on_balanced_samples(self):
        samples = _balanced_samples(p1_dim_h1=5, p2_dim_h1=10)
        results = check_calibration_tripwires(samples)
        names = {r.name for r in results}
        # 5 named tripwires:
        assert names == {
            "dim_h1_p1_in_band", "dim_h1_p2_in_band",
            "procrustes_finite", "b0_finite_positive",
            "probe_category_distinguishability",
        }
        assert all(r.passed for r in results), [
            (r.name, r.measured) for r in results
        ]

    def test_dim_h1_p1_below_band_fails(self):
        samples = _balanced_samples(p1_dim_h1=0, p2_dim_h1=10)
        # max dim_h1 over P1 = 0, which is < 1; tripwire 1 fails
        results = check_calibration_tripwires(samples)
        assert next(r for r in results if r.name == "dim_h1_p1_in_band").passed is False

    def test_dim_h1_p1_above_band_fails(self):
        samples = _balanced_samples(p1_dim_h1=51, p2_dim_h1=10)
        # max dim_h1 over P1 = 51, > 50; tripwire 1 fails
        results = check_calibration_tripwires(samples)
        assert next(r for r in results if r.name == "dim_h1_p1_in_band").passed is False

    def test_dim_h1_p2_band_check(self):
        samples = _balanced_samples(p1_dim_h1=5, p2_dim_h1=51)
        results = check_calibration_tripwires(samples)
        assert next(r for r in results if r.name == "dim_h1_p2_in_band").passed is False

    def test_procrustes_nan_fails(self):
        nan = float("nan")
        samples = list(_balanced_samples())
        # Replace P1.1's procrustes_loss with NaN
        samples[0] = CalibrationSample(
            probe_id="P1.1", probe_category="factual_recall",
            probe_text="x", dim_h1=5, procrustes_loss=nan, b0=0.5,
            n_features_a_used=10, n_features_b_used=10,
        )
        results = check_calibration_tripwires(tuple(samples))
        assert next(r for r in results if r.name == "procrustes_finite").passed is False

    def test_procrustes_inf_fails(self):
        samples = list(_balanced_samples())
        samples[0] = CalibrationSample(
            probe_id="P1.1", probe_category="factual_recall",
            probe_text="x", dim_h1=5, procrustes_loss=float("inf"), b0=0.5,
            n_features_a_used=10, n_features_b_used=10,
        )
        results = check_calibration_tripwires(tuple(samples))
        assert next(r for r in results if r.name == "procrustes_finite").passed is False

    def test_procrustes_above_1e6_fails(self):
        samples = list(_balanced_samples())
        samples[0] = CalibrationSample(
            probe_id="P1.1", probe_category="factual_recall",
            probe_text="x", dim_h1=5, procrustes_loss=1e7, b0=0.5,
            n_features_a_used=10, n_features_b_used=10,
        )
        results = check_calibration_tripwires(tuple(samples))
        assert next(r for r in results if r.name == "procrustes_finite").passed is False

    def test_b0_zero_fails(self):
        # B0 = 0 → b0_finite_positive fails
        samples = list(_balanced_samples())
        samples[0] = CalibrationSample(
            probe_id="P1.1", probe_category="factual_recall",
            probe_text="x", dim_h1=5, procrustes_loss=1.0, b0=0.0,
            n_features_a_used=10, n_features_b_used=10,
        )
        for s in samples[1:]:
            if s.probe_category == "factual_recall":
                # All P1 samples have b0=0 → max(b0) = 0
                samples[samples.index(s)] = CalibrationSample(
                    probe_id=s.probe_id, probe_category=s.probe_category,
                    probe_text=s.probe_text, dim_h1=s.dim_h1,
                    procrustes_loss=s.procrustes_loss, b0=0.0,
                    n_features_a_used=s.n_features_a_used,
                    n_features_b_used=s.n_features_b_used,
                )
        results = check_calibration_tripwires(tuple(samples))
        assert next(r for r in results if r.name == "b0_finite_positive").passed is False

    def test_b0_equal_p1_p2_fails_distinguishability(self):
        # v3.2 reformulation: tripwire 5 is now B0-based (was dim_h1-based).
        # Under reading (b) of pre-reg §3a, dim_h1 is constant across probes
        # by construction (= cross-model 2-cover topology, probe-independent).
        # Equal B0 across categories → tripwire 5 fails.
        samples = _balanced_samples(p1_b0=0.5, p2_b0=0.5)
        results = check_calibration_tripwires(samples)
        assert next(
            r for r in results if r.name == "probe_category_distinguishability"
        ).passed is False

    def test_b0_differing_p1_p2_passes_distinguishability(self):
        # v3.2: when B0 differs between P1 and P2 by > 1% of max(B0),
        # tripwire 5 passes. Build samples with P1 b0=0.5, P2 b0=0.6.
        p1 = [
            _mock_sample(probe_id=f"P1.{i+1}", probe_category="factual_recall",
                         dim_h1=5, b0=0.5)
            for i in range(5)
        ]
        p2 = [
            _mock_sample(probe_id=f"P2.{i+1}", probe_category="instruction_following",
                         dim_h1=5, b0=0.6)
            for i in range(5)
        ]
        results = check_calibration_tripwires(tuple(p1 + p2))
        # |0.5 - 0.6| = 0.10, threshold = 0.01 * max(0.5, 0.6) = 0.006 → pass
        assert next(
            r for r in results if r.name == "probe_category_distinguishability"
        ).passed is True

    def test_missing_p1_returns_failure_marker(self):
        # Only P2 samples provided
        only_p2 = tuple(
            _mock_sample(
                probe_id=f"P2.{i+1}", probe_category="instruction_following",
            )
            for i in range(5)
        )
        results = check_calibration_tripwires(only_p2)
        assert len(results) == 1
        assert results[0].name == "missing_probe_category"
        assert results[0].passed is False

    def test_missing_p2_returns_failure_marker(self):
        only_p1 = tuple(
            _mock_sample(
                probe_id=f"P1.{i+1}", probe_category="factual_recall",
            )
            for i in range(5)
        )
        results = check_calibration_tripwires(only_p1)
        assert len(results) == 1
        assert results[0].name == "missing_probe_category"


# ── CalibrationSample I/O ────────────────────────────────────────────


class TestCalibrationSample:
    def test_to_jsonable_round_trip(self):
        s = _mock_sample()
        d = s.to_jsonable()
        for key in [
            "probe_id", "probe_category", "probe_text",
            "dim_h1", "procrustes_loss", "b0",
            "n_features_a_used", "n_features_b_used",
        ]:
            assert key in d
        # JSON-serialisable
        json.dumps(d)

    def test_frozen(self):
        s = _mock_sample()
        with pytest.raises(Exception):
            s.dim_h1 = 99  # type: ignore[misc]


# ── Module-import smoke ───────────────────────────────────────────────


def test_module_imports_without_torch():
    import bulla.compute.g23_a3_calibration as mod
    assert hasattr(mod, "ALL_PROBES")
    assert hasattr(mod, "check_calibration_tripwires")
    assert hasattr(mod, "run_calibration_on_probe")
    assert hasattr(mod, "run_full_calibration")
