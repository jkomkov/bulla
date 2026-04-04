"""Coherence fee diagnostic: blind spots, bridges, and fee computation."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import (
    BlindSpot,
    BoundaryObligation,
    Bridge,
    Composition,
    Diagnostic,
    Edge,
    SemanticDimension,
    ToolSpec,
)


@dataclass(frozen=True)
class FeeDecomposition:
    """Result of decomposing a coherence fee over a graph partition.

    ``total_fee`` always equals ``sum(local_fees) + boundary_fee``.
    ``boundary_fee`` equals ``rho_full - rho_obs`` where rho is the
    rank of cross-partition rows modulo internal rows. A nonzero
    ``boundary_fee`` means the partition hides blind spots invisible
    at every level but present in the flat expansion.
    """

    total_fee: int
    local_fees: tuple[int, ...]
    boundary_fee: int
    partition: tuple[frozenset[str], ...]
    boundary_edges: int
    rho_obs: int = 0
    rho_full: int = 0


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
                            from_tool=edge.from_tool,
                            to_tool=edge.to_tool,
                        )
                    )

    bridges: list[Bridge] = []
    for bs in blind_spots:
        if bs.from_hidden:
            bridges.append(
                Bridge(
                    field=bs.from_field,
                    add_to=(bs.from_tool,),
                    eliminates=bs.dimension,
                )
            )
        if bs.to_hidden:
            bridges.append(
                Bridge(
                    field=bs.to_field,
                    add_to=(bs.to_tool,),
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
        blind_spots=tuple(blind_spots),
        bridges=tuple(bridges),
        h1_after_bridge=h1_b,
        n_unbridged=n_unbridged,
    )


def _cross_rank_modulo_internal(
    comp: Composition,
    partition: list[frozenset[str]],
    *,
    use_internal: bool,
) -> int:
    """Rank of cross-partition rows modulo internal rows.

    Builds the full coboundary, splits rows into internal (both
    endpoints in one group) and cross (endpoints in different groups),
    then computes rank([internal; cross]) - rank([internal]).
    """
    delta, _, _ = build_coboundary(
        comp.tools, comp.edges, use_internal=use_internal
    )
    if not delta:
        return 0

    # Row ordering matches _edge_basis: for edge in edges: for dim in edge.dimensions:
    is_row_internal: list[bool] = []
    for edge in comp.edges:
        edge_internal = any(edge.from_tool in g and edge.to_tool in g for g in partition)
        for _ in edge.dimensions:
            is_row_internal.append(edge_internal)

    internal_rows = [
        delta[i] for i, internal in enumerate(is_row_internal) if internal
    ]
    all_rows = delta

    rank_internal = matrix_rank(internal_rows) if internal_rows else 0
    rank_all = matrix_rank(all_rows)
    return rank_all - rank_internal


def decompose_fee(
    comp: Composition,
    partition: list[frozenset[str]],
) -> FeeDecomposition:
    """Decompose a composition's coherence fee over a tool-name partition.

    Each element of *partition* is a frozenset of tool names. Every tool
    must appear in exactly one partition element.

    Returns a ``FeeDecomposition`` where ``total_fee`` (the flat fee)
    always equals ``sum(local_fees) + boundary_fee``.

    ``boundary_fee`` is computed independently as ``rho_full - rho_obs``
    where rho is the rank of cross-partition edge rows modulo internal
    edge rows. Non-negativity (``boundary_fee >= 0``) follows from the
    projection argument: the column-projection from full to observable
    fields preserves linear independence modulo internal rows.
    """
    all_names = frozenset(t.name for t in comp.tools)
    partition_union = frozenset().union(*partition)
    if partition_union != all_names:
        raise ValueError(
            f"Partition must cover all tools. Missing: {all_names - partition_union}, "
            f"extra: {partition_union - all_names}"
        )

    tool_map = {t.name: t for t in comp.tools}
    total_fee = diagnose(comp).coherence_fee

    local_fees: list[int] = []
    for group in partition:
        sub_tools = tuple(tool_map[n] for n in sorted(group))
        sub_edges = tuple(
            e for e in comp.edges
            if e.from_tool in group and e.to_tool in group
        )
        if sub_tools:
            sub_comp = Composition(
                f"sub_{'-'.join(sorted(group))}", sub_tools, sub_edges
            )
            local_fees.append(diagnose(sub_comp).coherence_fee)
        else:
            local_fees.append(0)

    boundary_edge_count = sum(
        1 for e in comp.edges
        if not any(e.from_tool in g and e.to_tool in g for g in partition)
    )

    rho_obs = _cross_rank_modulo_internal(comp, partition, use_internal=False)
    rho_full = _cross_rank_modulo_internal(comp, partition, use_internal=True)
    boundary_fee = rho_full - rho_obs

    assert boundary_fee == total_fee - sum(local_fees), (
        f"Block rank formula disagrees with remainder: "
        f"rho_full-rho_obs={boundary_fee} vs total-local={total_fee - sum(local_fees)}"
    )

    return FeeDecomposition(
        total_fee=total_fee,
        local_fees=tuple(local_fees),
        boundary_fee=boundary_fee,
        partition=tuple(partition),
        boundary_edges=boundary_edge_count,
        rho_obs=rho_obs,
        rho_full=rho_full,
    )


@dataclass(frozen=True)
class OpenPort:
    """An unconnected port in a partial composition.

    Represents a future edge to an as-yet-unspecified tool.
    ``dimensions`` lists the semantic dimensions that will flow
    across this edge, with ``from_field`` on the known tool and
    ``to_field`` on the placeholder.
    """

    from_tool: str
    placeholder_name: str
    dimensions: tuple[SemanticDimension, ...]


@dataclass(frozen=True)
class ConditionalDiagnostic:
    """Diagnostic for a partial composition with open ports.

    ``baseline_fee`` is the fee of the known subgraph alone.
    ``worst_case_fee`` is the fee if placeholders disclose nothing.
    ``obligations`` list which fields each placeholder must expose
    for the fee to drop. ``structural_unknowns`` counts open-port
    dimension slots (distinct from epistemic unknowns).
    ``extended_comp`` is the composition with placeholder tools,
    stored so ``resolve_conditional`` can swap placeholders without
    requiring the caller to reconstruct it.
    """

    baseline_diag: Diagnostic
    extended_diag: Diagnostic
    baseline_fee: int
    worst_case_fee: int
    obligations: tuple[BoundaryObligation, ...]
    structural_unknowns: int
    extended_comp: Composition | None = None


def prescriptive_disclosure(
    comp: Composition, fee: int
) -> list[tuple[str, str]]:
    """Return the minimum disclosure set if fee > 0, else empty list.

    This is the canonical lazy guard: when ``fee`` is already known to be
    zero, it skips the coboundary matrix construction entirely.  Both
    the MCP surface (``serve.py``) and the CLI (``bulla gauge``) should
    call this rather than duplicating the ``if fee > 0`` check.
    """
    if fee <= 0:
        return []
    return minimum_disclosure_set(comp)


def minimum_disclosure_set(
    comp: Composition,
) -> list[tuple[str, str]]:
    """Find the minimum set of (tool, field) disclosures to reduce the fee to zero.

    Returns a list of ``(tool_name, field_name)`` pairs. Disclosing each
    field (adding it to the tool's observable schema) is sufficient to
    eliminate all blind spots.  The set has exactly ``fee`` elements — it
    is a basis for the quotient space
    ``col(delta_full) / col(delta_obs)``.

    Greedy column selection finds one such basis.  The result is minimal:
    removing any single disclosure leaves the fee nonzero.

    The returned set is a valid minimum disclosure set but not necessarily
    unique. Different tool orderings may yield different sets of the same
    cardinality.
    """
    delta_obs, v_obs, _ = build_coboundary(
        comp.tools, comp.edges, use_internal=False
    )
    delta_full, v_full, _ = build_coboundary(
        comp.tools, comp.edges, use_internal=True
    )
    rank_obs = matrix_rank(delta_obs)
    rank_full = matrix_rank(delta_full)
    fee = rank_full - rank_obs
    if fee == 0:
        return []

    obs_set = set(v_obs)
    hidden_cols = [
        (col_idx, tool_name, field_name)
        for col_idx, (tool_name, field_name) in enumerate(v_full)
        if (tool_name, field_name) not in obs_set
    ]

    current = [row[:] for row in delta_obs]
    current_rank = rank_obs
    disclosures: list[tuple[str, str]] = []

    # Greedy works because matroid rank is submodular.
    for col_idx, tool_name, field_name in hidden_cols:
        augmented = [row + [delta_full[r][col_idx]] for r, row in enumerate(current)]
        aug_rank = matrix_rank(augmented)
        if aug_rank > current_rank:
            current = augmented
            current_rank = aug_rank
            disclosures.append((tool_name, field_name))
            if current_rank == rank_full:
                break

    return disclosures


def satisfies_obligations(
    tool: ToolSpec,
    obligations: tuple[BoundaryObligation, ...],
) -> tuple[bool, list[str]]:
    """Check whether a tool meets a set of boundary obligations.

    Checks fields only — ignores ``obl.placeholder_tool``.  The caller
    is responsible for filtering obligations to those relevant to the
    tool being checked (e.g. by placeholder name).

    Returns ``(True, [])`` if every obligated field is in the tool's
    observable schema, otherwise ``(False, unmet)`` where *unmet*
    lists human-readable descriptions of unsatisfied obligations.
    """
    unmet = [
        f"{obl.dimension}: {obl.field} not in {tool.name}.observable_schema"
        for obl in obligations
        if obl.field not in tool.observable_schema
    ]
    return (len(unmet) == 0, unmet)


def conditional_diagnose(
    comp: Composition,
    open_ports: list[OpenPort],
) -> ConditionalDiagnostic:
    """Diagnose a partial composition with open ports.

    Desugars to the existing kernel: creates placeholder tools with
    everything hidden, extends the graph, runs ``diagnose`` twice
    (known subgraph and extended graph), and reads off boundary
    obligations from the blind spots on placeholder edges.
    """
    baseline_diag = diagnose(comp)

    placeholders: dict[str, ToolSpec] = {}
    new_edges: list[Edge] = []

    for port in open_ports:
        fields = tuple(d.to_field for d in port.dimensions if d.to_field)
        if port.placeholder_name not in placeholders:
            placeholders[port.placeholder_name] = ToolSpec(
                name=port.placeholder_name,
                internal_state=fields,
                observable_schema=(),
            )
        else:
            existing = placeholders[port.placeholder_name]
            merged = set(existing.internal_state)
            merged.update(fields)
            placeholders[port.placeholder_name] = ToolSpec(
                name=port.placeholder_name,
                internal_state=tuple(sorted(merged)),
                observable_schema=(),
            )

        new_edges.append(Edge(
            from_tool=port.from_tool,
            to_tool=port.placeholder_name,
            dimensions=port.dimensions,
        ))

    extended_tools = comp.tools + tuple(placeholders.values())
    extended_edges = comp.edges + tuple(new_edges)
    extended_comp = Composition(
        name=f"{comp.name}_extended",
        tools=extended_tools,
        edges=extended_edges,
    )

    extended_diag = diagnose(extended_comp)

    obligations: list[BoundaryObligation] = []
    for bs in extended_diag.blind_spots:
        if bs.to_tool in placeholders and bs.to_hidden:
            obligations.append(BoundaryObligation(
                placeholder_tool=bs.to_tool,
                dimension=bs.dimension,
                field=bs.to_field,
            ))

    structural_unknowns = sum(
        len(port.dimensions) for port in open_ports
    )

    return ConditionalDiagnostic(
        baseline_diag=baseline_diag,
        extended_diag=extended_diag,
        baseline_fee=baseline_diag.coherence_fee,
        worst_case_fee=extended_diag.coherence_fee,
        obligations=tuple(obligations),
        structural_unknowns=structural_unknowns,
        extended_comp=extended_comp,
    )


@dataclass(frozen=True)
class Resolution:
    """Result of resolving placeholders in a conditional diagnostic.

    ``fee_delta`` is ``worst_case_fee - resolved_fee`` and is always
    non-negative: a real tool with any observable fields is at least
    as informative as a placeholder with none.
    """

    resolved_diag: Diagnostic
    resolved_fee: int
    fee_delta: int
    met_obligations: tuple[BoundaryObligation, ...]
    remaining_obligations: tuple[BoundaryObligation, ...]


def resolve_conditional(
    cond: ConditionalDiagnostic,
    resolutions: dict[str, ToolSpec],
) -> Resolution:
    """Resolve one or more placeholders in a conditional diagnostic.

    Accepts a ``ConditionalDiagnostic`` (from ``conditional_diagnose``)
    and a mapping of placeholder names to real ``ToolSpec`` objects.
    Rebuilds the composition with the real tools swapped in, runs
    ``diagnose``, and partitions the original obligations into met and
    remaining.

    Supports partial resolution: unresolved placeholders stay as-is.
    """
    if cond.extended_comp is None:
        raise ValueError(
            "ConditionalDiagnostic has no extended_comp; "
            "use conditional_diagnose from bulla >= 0.13.0"
        )

    ext_tool_names = {t.name for t in cond.extended_comp.tools}
    for name in resolutions:
        if name not in ext_tool_names:
            raise ValueError(
                f"Placeholder '{name}' not found in extended composition. "
                f"Available tools: {sorted(ext_tool_names)}"
            )

    resolved_tools = tuple(
        resolutions[t.name] if t.name in resolutions else t
        for t in cond.extended_comp.tools
    )
    resolved_comp = Composition(
        name=f"{cond.extended_comp.name}_resolved",
        tools=resolved_tools,
        edges=cond.extended_comp.edges,
    )

    resolved_diag = diagnose(resolved_comp)

    met: list[BoundaryObligation] = []
    remaining: list[BoundaryObligation] = []
    for obl in cond.obligations:
        if obl.placeholder_tool in resolutions:
            real_tool = resolutions[obl.placeholder_tool]
            if obl.field in real_tool.observable_schema:
                met.append(obl)
            else:
                remaining.append(obl)
        else:
            remaining.append(obl)

    fee_delta = cond.worst_case_fee - resolved_diag.coherence_fee
    assert fee_delta >= 0, (
        f"Fee increased after resolution: worst_case={cond.worst_case_fee}, "
        f"resolved={resolved_diag.coherence_fee}"
    )

    return Resolution(
        resolved_diag=resolved_diag,
        resolved_fee=resolved_diag.coherence_fee,
        fee_delta=fee_delta,
        met_obligations=tuple(met),
        remaining_obligations=tuple(remaining),
    )
