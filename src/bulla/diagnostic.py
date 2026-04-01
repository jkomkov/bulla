"""Coherence fee diagnostic: blind spots, bridges, and fee computation."""

from __future__ import annotations

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import BlindSpot, Bridge, Composition, Diagnostic, ToolSpec


def diagnose(comp: Composition) -> Diagnostic:
    """Analyse a composition and return its full diagnostic."""
    tool_map = {t.name: t for t in comp.tools}

    delta_obs, v_obs, e_obs = build_coboundary(
        comp.tools, comp.edges, use_internal=False
    )
    delta_full, v_full, _ = build_coboundary(
        comp.tools, comp.edges, use_internal=True
    )

    rank_obs = matrix_rank(delta_obs)
    rank_full = matrix_rank(delta_full)
    dim_c1 = len(e_obs)
    h1_obs = dim_c1 - rank_obs
    h1_full = dim_c1 - rank_full

    blind_spots: list[BlindSpot] = []
    for edge in comp.edges:
        for dim in edge.dimensions:
            if dim.from_field and dim.to_field:
                f_hid = (
                    dim.from_field
                    not in tool_map[edge.from_tool].observable_schema
                )
                t_hid = (
                    dim.to_field
                    not in tool_map[edge.to_tool].observable_schema
                )
                if f_hid or t_hid:
                    blind_spots.append(
                        BlindSpot(
                            dimension=dim.name,
                            edge=f"{edge.from_tool} \u2192 {edge.to_tool}",
                            from_field=dim.from_field,
                            to_field=dim.to_field,
                            from_hidden=f_hid,
                            to_hidden=t_hid,
                        )
                    )

    bridges: list[Bridge] = []
    for bs in blind_spots:
        edge_parts = bs.edge.split(" \u2192 ")
        if bs.from_hidden:
            bridges.append(
                Bridge(
                    field=bs.from_field,
                    add_to=[edge_parts[0]],
                    eliminates=bs.dimension,
                )
            )
        if bs.to_hidden:
            bridges.append(
                Bridge(
                    field=bs.to_field,
                    add_to=[edge_parts[1]],
                    eliminates=bs.dimension,
                )
            )

    bridged = list(comp.tools)
    for br in bridges:
        new: dict[str, ToolSpec] = {}
        for t in bridged:
            if t.name in br.add_to and br.field not in t.observable_schema:
                new[t.name] = ToolSpec(
                    t.name, t.internal_state, t.observable_schema + (br.field,)
                )
            else:
                new[t.name] = t
        bridged = [new.get(t.name, t) for t in bridged]

    delta_b, _, _ = build_coboundary(bridged, comp.edges, use_internal=False)
    rank_b = matrix_rank(delta_b)
    h1_b = dim_c1 - rank_b

    betti_1 = max(0, len(comp.edges) - len(comp.tools) + 1)

    n_unbridged = sum(
        1
        for edge in comp.edges
        for dim in edge.dimensions
        if dim.from_field
        and dim.to_field
        and (
            dim.from_field not in tool_map[edge.from_tool].observable_schema
            or dim.to_field not in tool_map[edge.to_tool].observable_schema
        )
    )

    return Diagnostic(
        name=comp.name,
        n_tools=len(comp.tools),
        n_edges=len(comp.edges),
        betti_1=betti_1,
        dim_c0_obs=len(v_obs),
        dim_c0_full=len(v_full),
        dim_c1=dim_c1,
        rank_obs=rank_obs,
        rank_full=rank_full,
        h1_obs=h1_obs,
        h1_full=h1_full,
        coherence_fee=h1_obs - h1_full,
        blind_spots=blind_spots,
        bridges=bridges,
        h1_after_bridge=h1_b,
        n_unbridged=n_unbridged,
    )
