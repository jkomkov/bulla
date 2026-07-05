from __future__ import annotations

from bulla.compute.eval_gap import (
    WITNESS_EVALUATOR,
    build_leaderboard,
)
from bulla.testing.eval_gap_pairs import build_evalgap_fixtures


def test_evalgap_builds_fixtures_with_expected_ranks():
    fixtures = build_evalgap_fixtures(target_ranks=(1, 2, 3), per_rank=2)
    assert len(fixtures) == 6
    assert {f.pair.target_rank for f in fixtures} == {1, 2, 3}
    assert all(f.pair.incoherent_fee == f.pair.target_rank for f in fixtures)
    assert all(f.pair.coherent_fee == 0 for f in fixtures)


def test_witness_evaluator_hits_ceiling():
    fixtures = build_evalgap_fixtures(target_ranks=(1, 2), per_rank=3)
    rows = build_leaderboard(fixtures, q=2)
    witness_rows = [r for r in rows if r.evaluator == WITNESS_EVALUATOR]
    assert witness_rows
    assert all(r.pass_rate == 1.0 for r in witness_rows)


def test_baseline_rows_respect_floor_field():
    fixtures = build_evalgap_fixtures(target_ranks=(1,), per_rank=4)
    rows = build_leaderboard(fixtures, q=4)
    baseline_rows = [r for r in rows if r.evaluator != WITNESS_EVALUATOR]
    assert baseline_rows
    assert all(r.floor_without_witness == 0.25 for r in baseline_rows)

