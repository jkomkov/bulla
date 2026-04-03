"""Smoke test for the adversarial submodularity survey script.

Imports the core functions and runs a minimal survey to catch
regressions in coboundary.py or model.py that would silently
break the evidence for the submodularity disproof.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from adversarial_submodularity_survey import (
    _boundary_fee_fast,
    all_binary_partitions,
    classify_rows,
    random_composition,
    survey,
)

from bulla.coboundary import build_coboundary


class TestSurveyCoreFunctions:
    """Unit tests for the survey's core helpers."""

    def test_random_composition_produces_valid_output(self):
        rng = random.Random(99)
        comp = random_composition(rng)
        if comp is not None:
            assert len(comp.tools) >= 3
            assert len(comp.edges) >= 1

    def test_classify_rows_length_matches_edge_dimensions(self):
        rng = random.Random(100)
        for _ in range(20):
            comp = random_composition(rng)
            if comp is None:
                continue
            names = sorted(t.name for t in comp.tools)
            parts = list(all_binary_partitions(names))
            if not parts:
                continue
            part = parts[0]
            rows = classify_rows(comp, part)
            expected = sum(len(e.dimensions) for e in comp.edges)
            assert len(rows) == expected

    def test_boundary_fee_fast_non_negative(self):
        rng = random.Random(101)
        for _ in range(20):
            comp = random_composition(rng)
            if comp is None:
                continue
            names = sorted(t.name for t in comp.tools)
            parts = list(all_binary_partitions(names))
            if not parts:
                continue

            tools_list = list(comp.tools)
            edges_list = list(comp.edges)
            d_obs, _, _ = build_coboundary(tools_list, edges_list, use_internal=False)
            d_full, _, _ = build_coboundary(tools_list, edges_list, use_internal=True)

            if not d_obs and not d_full:
                continue

            part = parts[0]
            row_int = classify_rows(comp, part)
            bf = _boundary_fee_fast(d_obs, d_full, row_int)
            assert bf >= 0


class TestSurveySmoke:
    """Minimal end-to-end run to verify the survey doesn't crash."""

    def test_survey_runs_without_error(self):
        violations = survey(n_compositions=10, seed=12345, max_pairs_per_comp=5)
        assert isinstance(violations, list)
