from __future__ import annotations

from bulla.compute.cocycle_pairs import TARGET_RANKS, compute_rank_delta, generate_pair_at_rank


def _observable_surface(comp) -> tuple:
    tools = tuple(
        sorted((t.name, tuple(sorted(t.observable_schema))) for t in comp.tools)
    )
    edges = tuple(
        sorted(
            (
                e.from_tool,
                e.to_tool,
                tuple(sorted(d.name for d in e.dimensions)),
            )
            for e in comp.edges
        )
    )
    return tools, edges


def test_generate_pair_hits_target_rank():
    for rank in TARGET_RANKS:
        pair = generate_pair_at_rank(rank)
        assert pair.incoherent_fee == rank
        assert pair.coherent_fee == 0
        assert pair.incoherent.name != pair.coherent.name
        assert pair.skeleton_hash
        assert _observable_surface(pair.incoherent) == _observable_surface(pair.coherent)


def test_compute_rank_delta_matches_target_rank():
    pair = generate_pair_at_rank(5)
    delta = compute_rank_delta(pair.incoherent, pair.coherent)
    assert delta == 5

