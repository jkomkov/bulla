"""EvalGap pair fixtures with BABEL-linked provenance metadata.

This module keeps EvalGap and BABEL coupled at the corpus level:
- we read BABEL manifest ids for hidden (fee>0) instances,
- generate controlled synthetic pairs at matching target ranks,
- and preserve the BABEL source id in fixture metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from bulla.compute.cocycle_pairs import CocyclePair, TARGET_RANKS, generate_pair_at_rank


@dataclass(frozen=True)
class EvalGapFixture:
    source_id: str
    pair: CocyclePair


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _babel_manifest_path() -> Path:
    return _repo_root() / "benchmark" / "coherence-gym" / "instances" / "holdout_manifest.json"


def load_babel_positive_ids(limit: int = 100) -> list[str]:
    """Load hidden-instance IDs from BABEL holdout manifest.

    Hidden IDs are treated as positive-seed proxies for fee>0 regimes.
    """
    path = _babel_manifest_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    out: list[str] = []
    for group_entries in data.values():
        if not isinstance(group_entries, list):
            continue
        for row in group_entries:
            iid = row.get("id", "")
            if "/hidden_" in iid:
                out.append(iid)
                if len(out) >= limit:
                    return out
    return out


def build_evalgap_fixtures(
    *,
    target_ranks: tuple[int, ...] = TARGET_RANKS,
    per_rank: int = 5,
) -> list[EvalGapFixture]:
    """Create EvalGap fixtures with BABEL-linked IDs.

    If BABEL ids are unavailable, synthetic fallback ids are used but pair
    generation still follows the same controlled-rank contract.
    """
    if per_rank < 1:
        raise ValueError(f"per_rank must be >= 1; got {per_rank}")

    source_ids = load_babel_positive_ids(limit=len(target_ranks) * per_rank)
    if not source_ids:
        source_ids = [
            f"synthetic/fallback/rank_{r}_{j}"
            for r in target_ranks
            for j in range(per_rank)
        ]

    fixtures: list[EvalGapFixture] = []
    cursor = 0
    for rank in target_ranks:
        for _ in range(per_rank):
            source_id = source_ids[cursor % len(source_ids)]
            cursor += 1
            fixtures.append(
                EvalGapFixture(
                    source_id=source_id,
                    pair=generate_pair_at_rank(rank),
                )
            )
    return fixtures

