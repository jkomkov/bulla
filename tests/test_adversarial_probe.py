"""Tests for the adversarial seam-probing harness (WS7).

Validates the systematic probe grid against the seam_backend:
  - All 12 single-dimension probes run without dropping
  - Multi-dimension discriminating pairs produce the expected fee/consequence
  - The correctness oracle classifies each cell correctly
  - JSONL output is valid
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from calibration.harness.adversarial_probe import (
    Consequence,
    ProbeResult,
    classify_consequence,
    default_probes,
    multi_dimension_probes,
    run_probes,
    write_jsonl,
)


@pytest.fixture(scope="module")
def single_dim_results():
    return asyncio.run(run_probes(default_probes()))


@pytest.fixture(scope="module")
def multi_dim_results():
    return asyncio.run(run_probes(multi_dimension_probes()))


def test_no_probes_dropped(single_dim_results):
    dropped = [r.label for r in single_dim_results if r.dropped]
    assert not dropped, f"probes failed to run: {dropped}"


def test_grid_has_12_probes(single_dim_results):
    assert len(single_dim_results) == 12


def test_hidden_mismatch_is_silent_corruption(single_dim_results):
    """Hidden mismatches in permissive mode produce wrong answers (silent corruption)."""
    for r in single_dim_results:
        if not r.visible and r.mismatch:
            assert r.consequence == Consequence.SILENT_CORRUPTION, (
                f"{r.label}: hidden mismatch should be silent_corruption, "
                f"got {r.consequence}"
            )


def test_visible_mismatch_is_correct_pass(single_dim_results):
    """Visible mismatches are normalized — result is correct."""
    for r in single_dim_results:
        if r.visible and r.mismatch:
            assert r.consequence == Consequence.CORRECT_PASS, (
                f"{r.label}: visible mismatch should be correct_pass, "
                f"got {r.consequence}"
            )


def test_no_mismatch_is_expected_clean(single_dim_results):
    """No-mismatch probes should be clean."""
    for r in single_dim_results:
        if not r.mismatch:
            assert r.consequence == Consequence.EXPECTED_CLEAN, (
                f"{r.label}: no mismatch should be expected_clean, "
                f"got {r.consequence}"
            )


def test_hidden_mismatch_has_positive_fee(single_dim_results):
    """Hidden mismatch probes should have fee > 0."""
    for r in single_dim_results:
        if not r.visible and r.mismatch:
            assert r.fee > 0, f"{r.label}: hidden mismatch should have fee > 0"


def test_visible_mismatch_has_zero_fee(single_dim_results):
    """Visible mismatch probes should have fee = 0 (no hidden coupling)."""
    for r in single_dim_results:
        if r.visible and r.mismatch:
            assert r.fee == 0, (
                f"{r.label}: visible-only mismatch should have fee=0, got {r.fee}"
            )


def test_hidden_mismatch_is_failure(single_dim_results):
    """Hidden mismatches should be classified as failures by is_failure."""
    for r in single_dim_results:
        if not r.visible and r.mismatch:
            assert r.consequence.is_failure, (
                f"{r.label}: hidden mismatch should be is_failure"
            )


def test_visible_mismatch_is_not_failure(single_dim_results):
    """Visible mismatches (correctly normalized) are not failures."""
    for r in single_dim_results:
        if r.visible and r.mismatch:
            assert not r.consequence.is_failure, (
                f"{r.label}: visible mismatch should not be is_failure"
            )


def test_multi_dim_discriminating_pairs(multi_dim_results):
    """Multi-dimension probes create pairs with same Hamming but different fee."""
    dropped = [r for r in multi_dim_results if r.dropped]
    assert not dropped, f"multi-dim probes dropped: {[r.label for r in dropped]}"
    clean = {r.label: r for r in multi_dim_results if r.label.endswith("clean")}
    hidden = {r.label: r for r in multi_dim_results if r.label.endswith("hid")}
    for c_label, c_result in clean.items():
        h_label = c_label.replace("clean", "hid")
        if h_label not in hidden:
            continue
        h_result = hidden[h_label]
        assert h_result.fee >= c_result.fee, (
            f"discriminating pair {c_label}/{h_label}: "
            f"hidden fee ({h_result.fee}) should >= clean fee ({c_result.fee})"
        )


def test_classify_consequence_taxonomy():
    assert classify_consequence(False, False, None) == Consequence.EXPECTED_CLEAN
    assert classify_consequence(True, True, None) == Consequence.GUARDED_REJECT
    assert classify_consequence(True, False, True) == Consequence.CORRECT_PASS
    assert classify_consequence(True, False, False) == Consequence.SILENT_CORRUPTION
    # No mismatch always yields EXPECTED_CLEAN regardless of other flags
    assert classify_consequence(False, True, False) == Consequence.EXPECTED_CLEAN


def test_consequence_is_failure():
    assert Consequence.GUARDED_REJECT.is_failure
    assert Consequence.SILENT_CORRUPTION.is_failure
    assert not Consequence.CORRECT_PASS.is_failure
    assert not Consequence.EXPECTED_CLEAN.is_failure


def test_write_jsonl(tmp_path: Path, single_dim_results):
    out = tmp_path / "probes.jsonl"
    write_jsonl(single_dim_results, out)
    lines = out.read_text().strip().splitlines()
    assert len(lines) == len(single_dim_results)
    for line in lines:
        data = json.loads(line)
        assert "label" in data
        assert "consequence" in data
        assert "fee" in data
        assert "fee_d" in data
        # consequence serializes as string value
        Consequence(data["consequence"])
