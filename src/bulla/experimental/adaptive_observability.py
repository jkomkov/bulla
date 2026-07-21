"""Bounded adaptive-observability scout (never an action gate).

At most eight Boolean offers and depth four are explored exactly.  Terminal
leaves are either a uniquely determined declared outcome or ROUTE; the scout
cannot create an unsafe rely/refuse leaf.  A declared integer-weight prior is
used only to compare expected disclosure/latency inside the experiment.
"""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError


MAX_OFFERS = 8
MAX_DEPTH = 4


@dataclass(frozen=True)
class GenerativeWorld:
    world_id: str
    outcome: str
    observations: Mapping[str, bool]
    prior_weight: int

    def __post_init__(self) -> None:
        if not self.world_id or not self.outcome or self.prior_weight <= 0:
            raise InventionError("generative world requires id, outcome, and positive weight")
        object.__setattr__(self, "observations", dict(self.observations))


@dataclass(frozen=True)
class AdaptiveScoutResult:
    case_hash: str
    offer_count: int
    max_depth: int
    static_offer_count: int
    adaptive_expected_offers_ppm: int
    disclosure_reduction_ppm: int
    latency_reduction_ppm: int
    static_safe: bool
    adaptive_safe: bool
    resolved_probability_ppm: int
    tree: Mapping[str, Any]

    @property
    def result_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "tree": dict(self.tree)}


def _terminal(worlds: Sequence[GenerativeWorld]) -> str | None:
    outcomes = {world.outcome for world in worlds}
    return next(iter(outcomes)) if len(outcomes) == 1 else None


def _static_minimum(worlds: Sequence[GenerativeWorld], offers: Sequence[str]) -> tuple[str, ...] | None:
    for size in range(len(offers) + 1):
        for selected in itertools.combinations(offers, size):
            cells: dict[tuple[bool, ...], set[str]] = {}
            for world in worlds:
                cells.setdefault(tuple(world.observations[item] for item in selected), set()).add(world.outcome)
            if all(len(outcomes) == 1 for outcomes in cells.values()):
                return selected
    return None


def scout_adaptive_observability(
    worlds: Sequence[GenerativeWorld], *, offer_order: Sequence[str], max_depth: int = MAX_DEPTH,
) -> AdaptiveScoutResult:
    worlds = tuple(worlds)
    offers = tuple(offer_order)
    if not worlds or len(offers) > MAX_OFFERS or not 0 <= max_depth <= MAX_DEPTH:
        raise InventionError("adaptive scout bounds exceeded")
    if any(set(world.observations) != set(offers) for world in worlds):
        raise InventionError("every world must define every declared offer")
    total_weight = sum(world.prior_weight for world in worlds)
    static = _static_minimum(worlds, offers)

    @functools.lru_cache(maxsize=None)
    def solve(indices: tuple[int, ...], remaining: tuple[str, ...], depth: int):
        selected_worlds = tuple(worlds[index] for index in indices)
        terminal = _terminal(selected_worlds)
        weight = sum(world.prior_weight for world in selected_worlds)
        if terminal is not None:
            return (weight, Fraction(0), {"leaf": terminal, "safe": True})
        if depth == 0 or not remaining:
            return (0, Fraction(0), {"leaf": "ROUTE", "safe": True, "outcomes": sorted({w.outcome for w in selected_worlds})})
        candidates = []
        for offer in remaining:
            left = tuple(index for index in indices if not worlds[index].observations[offer])
            right = tuple(index for index in indices if worlds[index].observations[offer])
            if not left or not right:
                continue
            next_remaining = tuple(item for item in remaining if item != offer)
            l_resolved, l_cost, l_tree = solve(left, next_remaining, depth - 1)
            r_resolved, r_cost, r_tree = solve(right, next_remaining, depth - 1)
            expected_cost = Fraction(weight, 1) + l_cost + r_cost
            candidates.append((l_resolved + r_resolved, -expected_cost, offer, expected_cost, l_tree, r_tree))
        if not candidates:
            return (0, Fraction(0), {"leaf": "ROUTE", "safe": True, "outcomes": sorted({w.outcome for w in selected_worlds})})
        resolved, _, offer, cost, left_tree, right_tree = max(candidates, key=lambda item: (item[0], item[1], item[2]))
        return (resolved, cost, {"offer": offer, "false": left_tree, "true": right_tree})

    resolved, weighted_cost, tree = solve(tuple(range(len(worlds))), offers, max_depth)
    static_count = len(static) if static is not None else 0
    adaptive_expected = weighted_cost / total_weight
    if static_count:
        reduction = Fraction(static_count, 1) - adaptive_expected
        reduction_ppm = max(0, int(reduction * 1_000_000 / static_count))
    else:
        reduction_ppm = 0
    case_document = {
        "worlds": [
            {"world_id": item.world_id, "outcome": item.outcome, "observations": dict(item.observations), "prior_weight": item.prior_weight}
            for item in worlds
        ],
        "offer_order": list(offers), "max_depth": max_depth,
        "prior": "declared-integer-weights-normalized-exactly",
    }
    return AdaptiveScoutResult(
        case_hash=canonical_hash(case_document), offer_count=len(offers), max_depth=max_depth,
        static_offer_count=static_count,
        adaptive_expected_offers_ppm=int(adaptive_expected * 1_000_000),
        disclosure_reduction_ppm=reduction_ppm, latency_reduction_ppm=reduction_ppm,
        static_safe=static is not None, adaptive_safe=True,
        resolved_probability_ppm=int(Fraction(resolved, total_weight) * 1_000_000),
        tree=tree,
    )
