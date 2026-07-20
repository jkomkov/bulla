from __future__ import annotations

import pytest

from bulla.experimental.adaptive_observability import (
    GenerativeWorld,
    scout_adaptive_observability,
)


def _one_hot_worlds():
    offers = ("a", "b", "c", "d")
    worlds = []
    for index, offer in enumerate(offers):
        observations = {item: item == offer for item in offers}
        worlds.append(GenerativeWorld(str(index), f"outcome-{index}", observations, 1))
    return offers, worlds


def test_exact_adaptive_tree_preserves_safe_leaves_and_improves_static_plan():
    offers, worlds = _one_hot_worlds()
    result = scout_adaptive_observability(worlds, offer_order=offers, max_depth=4)
    assert result.static_safe and result.adaptive_safe
    assert result.static_offer_count == 3
    assert result.adaptive_expected_offers_ppm == 2_250_000
    assert result.disclosure_reduction_ppm == 250_000
    assert result.resolved_probability_ppm == 1_000_000


def test_depth_exhaustion_routes_instead_of_unsafe_terminal_decision():
    offers, worlds = _one_hot_worlds()
    result = scout_adaptive_observability(worlds, offer_order=offers, max_depth=1)
    assert result.adaptive_safe
    assert result.resolved_probability_ppm < 1_000_000
    assert "ROUTE" in str(result.tree)


def test_offer_and_depth_bounds_fail_closed():
    offers = tuple(f"o{i}" for i in range(9))
    worlds = (
        GenerativeWorld("left", "L", {item: False for item in offers}, 1),
        GenerativeWorld("right", "R", {item: True for item in offers}, 1),
    )
    with pytest.raises(ValueError, match="bounds"):
        scout_adaptive_observability(worlds, offer_order=offers)
