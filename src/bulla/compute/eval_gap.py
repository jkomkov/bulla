"""EvalGap leaderboard utilities (G25).

The leaderboard compares evaluators against two fixed reference lines:
- information floor without witness capacity: 1/q
- witness-assisted ceiling: 1.0
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from bulla.compute.cocycle_pairs import CocyclePair
from bulla.testing.eval_gap_pairs import EvalGapFixture, build_evalgap_fixtures


BASELINE_EVALUATORS: tuple[str, ...] = ("llm_judge", "trace_only", "unit_test")
WITNESS_EVALUATOR: str = "witness_receipt"


@dataclass(frozen=True)
class LeaderboardRow:
    evaluator: str
    rank: int
    total: int
    passed: int
    pass_rate: float
    floor_without_witness: float
    witness_ceiling: float


def _deterministic_guess(pair: CocyclePair, *, q: int) -> int:
    """Pseudo-random guess index in [0, q) based on skeleton hash."""
    h = hashlib.sha256(pair.skeleton_hash.encode()).digest()
    return int.from_bytes(h[:8], "big") % q


def evaluate_fixture(
    fixture: EvalGapFixture,
    *,
    evaluator: str,
    q: int = 2,
) -> bool:
    """Return whether evaluator correctly flags the incoherent member.

    Convention: prediction is an index in [0, q). Index 0 means "incoherent
    first member". Baselines do not inspect witness receipts; witness evaluator
    always succeeds.
    """
    if q < 2:
        raise ValueError(f"q must be >= 2; got {q}")

    if evaluator == WITNESS_EVALUATOR:
        return True
    guess = _deterministic_guess(fixture.pair, q=q)
    return guess == 0


def build_leaderboard(
    fixtures: list[EvalGapFixture] | None = None,
    *,
    q: int = 2,
    evaluators: tuple[str, ...] = BASELINE_EVALUATORS + (WITNESS_EVALUATOR,),
) -> list[LeaderboardRow]:
    """Compute leaderboard rows grouped by evaluator x target rank."""
    if fixtures is None:
        fixtures = build_evalgap_fixtures()
    rows: list[LeaderboardRow] = []
    for evaluator in evaluators:
        for rank in sorted({f.pair.target_rank for f in fixtures}):
            bucket = [f for f in fixtures if f.pair.target_rank == rank]
            passed = sum(1 for f in bucket if evaluate_fixture(f, evaluator=evaluator, q=q))
            total = len(bucket)
            rows.append(
                LeaderboardRow(
                    evaluator=evaluator,
                    rank=rank,
                    total=total,
                    passed=passed,
                    pass_rate=(passed / total) if total else 0.0,
                    floor_without_witness=1.0 / q,
                    witness_ceiling=1.0,
                )
            )
    return rows


def leaderboard_to_json(rows: list[LeaderboardRow]) -> str:
    payload = [
        {
            "evaluator": r.evaluator,
            "rank": r.rank,
            "total": r.total,
            "passed": r.passed,
            "pass_rate": r.pass_rate,
            "floor_without_witness": r.floor_without_witness,
            "witness_ceiling": r.witness_ceiling,
        }
        for r in rows
    ]
    return json.dumps(payload, indent=2)

