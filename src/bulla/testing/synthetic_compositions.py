"""Synthetic composition builders + encoding-capability audits.

The synthetic-control pattern (build composition with known fee = k,
verify exact recovery) is the **universal soundness check** for any
encoding adapter. Three established instances motivate this primitive:

  - **Sprint 15 hub-and-spoke** (`papers/composition-doctrine/sprint15_demo/`):
    canonical fee=1 fixture with 1 hub + 2 spokes.
  - **G19 cross-metric synthetic positive control**
    (`papers/composition-doctrine/sprint_g19_synthetic_control_*`):
    designed-known persistence-barcode fixture for the persistent-H¹
    prototype.
  - **G23 A1 known-non-vanishing control**
    (`bulla/adapters/sae_controls.py`): k ∈ {1, 2, 3, 5, 10} hub-and-
    spoke for SAE feature compositions.
  - **G24 pre-A3 sanity check**
    (`bulla/adapters/pipeline_ci_controls.py`): same pattern for
    pipeline-CI compositions.

Each of these reimplemented the same hub-and-spoke + cyclic-vanishing
structure from scratch. This module promotes the pattern to a first-
class testing primitive: any future encoding adapter (G25 monitoring-
pipeline, G26 multi-agent-RL, etc.) imports these utilities directly
instead of rebuilding ad-hoc fixtures.

# API levels

Two-level API, parameterised on what the adapter needs:

**High-level (convenience wrappers)** — homogeneous tools, minimal field
specification:
  * `build_known_vanishing` — N tools in a cycle with one observable-
    field dimension; expected `fee = 0`.
  * `build_known_nonvanishing` — 1 hub + (k+1) spokes with obstruction
    field hidden on spokes; expected `fee = k`.

**Low-level (`*_from_tools`)** — for adapters with heterogeneous tool
types (e.g., `pipeline_ci_controls` has 1 script + N paper tools with
different ToolSpec shapes). Adapter pre-builds tools, function builds
cycle/obstruction edges:
  * `build_cycle_from_tools` — given a tuple of pre-built ToolSpecs
    and an edge-dimension field, build a cyclic Composition.
  * `build_hub_spoke_from_tools` — given a hub ToolSpec, spoke
    ToolSpecs, and an obstruction field, build a hub-and-spoke
    Composition.

# Audit utility

`audit_encoding_capability(comp)` inspects an arbitrary composition's
edges and returns a verdict on whether the encoding is structurally
**capable** of producing `fee > 0`. Use BEFORE running an adapter on
real data:

  - If `can_produce_obstruction == False`, the encoding is too coarse:
    every edge declares `from_field` and `to_field` from observable
    schemas, so `δ_full ≡ δ_obs` by construction. Any historical sweep
    will produce `fee = 0` regardless of input. Pause and revise the
    encoding before consuming compute.
  - If `can_produce_obstruction == True`, the encoding has at least one
    edge that contributes to `δ_full` but not `δ_obs`, so non-zero fees
    are reachable. The synthetic positive control then verifies the
    framework recovers the expected magnitude.

This audit is the lesson learned from G24 commit `6ba3f89` (pre-A3
encoding-coarseness audit): a structural inspection costs ~ms and
catches what would otherwise be a 3-day vacuous compute spend.
"""

from __future__ import annotations

from dataclasses import dataclass

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


# ── Low-level: cycle and hub-spoke from pre-built tools ──────────────


def build_cycle_from_tools(
    *,
    name: str,
    tools: tuple[ToolSpec, ...],
    edge_dimension_field: str,
    edge_name_prefix: str = "identity",
) -> Composition:
    """Build a Composition with cyclic edges between the given tools.

    Cycle: ``tools[0] → tools[1] → ... → tools[-1] → tools[0]``.
    Each edge declares one ``SemanticDimension`` on
    ``edge_dimension_field`` (identity from→to mapping).

    For ``coherence_fee = 0``, ``edge_dimension_field`` must be in EACH
    tool's ``observable_schema``. If the field is hidden on any tool,
    that edge contributes to ``δ_full`` but not ``δ_obs`` and the fee
    will be > 0. Use ``audit_encoding_capability`` to inspect.

    Args:
        name: composition name.
        tools: tuple of pre-built ToolSpecs (>= 2 for a cycle).
        edge_dimension_field: field name declared on every edge's
            single SemanticDimension.
        edge_name_prefix: prefix for SemanticDimension names; suffix
            is the edge index ``_0``, ``_1``, ....

    Returns:
        Composition with ``len(tools)`` tools and ``len(tools)`` cyclic
        edges.

    Raises:
        ValueError: if fewer than 2 tools (no cycle possible).
    """
    n = len(tools)
    if n < 2:
        raise ValueError(f"need >= 2 tools for a cycle; got {n}")

    edges = tuple(
        Edge(
            from_tool=tools[i].name,
            to_tool=tools[(i + 1) % n].name,
            dimensions=(
                SemanticDimension(
                    name=f"{edge_name_prefix}_{i}",
                    from_field=edge_dimension_field,
                    to_field=edge_dimension_field,
                ),
            ),
        )
        for i in range(n)
    )
    return Composition(name=name, tools=tools, edges=edges)


def build_hub_spoke_from_tools(
    *,
    name: str,
    hub: ToolSpec,
    spokes: tuple[ToolSpec, ...],
    obstruction_field: str,
    edge_name_prefix: str = "obstruction",
) -> Composition:
    """Build a hub-and-spoke Composition with single-dimension edges.

    Edges: ``hub → spokes[0]``, ``hub → spokes[1]``, ..., one per spoke.
    Each edge declares one ``SemanticDimension`` on
    ``obstruction_field`` (identity from→to mapping).

    For ``coherence_fee = len(spokes) - 1``:
      - ``obstruction_field`` must be in ``hub.observable_schema``
      - ``obstruction_field`` must be in each spoke's ``internal_state``
        but NOT in any spoke's ``observable_schema`` (hidden on spokes).
      - This ensures δ_obs has rank 1 (n identical rows at hub.field)
        and δ_full has rank n (n distinct rows at hub.field + spoke_i.field),
        giving fee = n - 1 = len(spokes) - 1.

    The function does NOT enforce these field-placement preconditions
    (would require inspecting hub/spoke schemas); use
    ``audit_encoding_capability`` after construction to verify the
    expected fee.

    Args:
        name: composition name.
        hub: pre-built ToolSpec exposing ``obstruction_field``.
        spokes: tuple of >= 1 pre-built ToolSpecs hiding
            ``obstruction_field`` (i.e., field is in each spoke's
            internal_state but not observable_schema).
        obstruction_field: field name declared on every edge's
            single SemanticDimension.
        edge_name_prefix: prefix for SemanticDimension names; suffix
            is the spoke index ``_0``, ``_1``, ....

    Returns:
        Composition with 1 hub + ``len(spokes)`` spoke tools and
        ``len(spokes)`` edges.

    Raises:
        ValueError: if no spokes (no obstruction possible).
    """
    if len(spokes) < 1:
        raise ValueError(f"need >= 1 spoke for obstruction; got {len(spokes)}")

    tools = (hub,) + spokes
    edges = tuple(
        Edge(
            from_tool=hub.name,
            to_tool=spokes[i].name,
            dimensions=(
                SemanticDimension(
                    name=f"{edge_name_prefix}_{i}",
                    from_field=obstruction_field,
                    to_field=obstruction_field,
                ),
            ),
        )
        for i in range(len(spokes))
    )
    return Composition(name=name, tools=tools, edges=edges)


# ── High-level: convenience wrappers for homogeneous-tool case ───────


def build_known_vanishing(
    *,
    name: str,
    n_tools: int,
    internal_state: tuple[str, ...],
    observable_schema: tuple[str, ...],
    edge_dimension_field: str,
    name_prefix: str = "synth_vanishing",
) -> Composition:
    """N homogeneous tools in a cycle with observable-field edges.

    Convenience wrapper around ``build_cycle_from_tools`` for the common
    case where all tools share the same ToolSpec shape.

    Expected ``coherence_fee = 0`` (the cyclic structure has β_1 = 1 but
    the observable-field edges do not introduce any obstruction since
    ``edge_dimension_field`` is in every tool's observable_schema).

    Args:
        name: composition name.
        n_tools: number of tools in the cycle. Must be >= 2.
        internal_state: shared ToolSpec.internal_state across all tools.
        observable_schema: shared ToolSpec.observable_schema across all
            tools. Must contain ``edge_dimension_field`` for fee=0.
        edge_dimension_field: field name declared on every edge.
        name_prefix: prefix for tool names; suffix is the index
            ``_0``, ``_1``, ....

    Returns:
        Composition with ``n_tools`` homogeneous tools + ``n_tools``
        cyclic edges. Expected ``diagnose(comp).coherence_fee == 0``.

    Raises:
        ValueError: if ``n_tools < 2`` or
            ``edge_dimension_field not in observable_schema``.
    """
    if n_tools < 2:
        raise ValueError(f"n_tools must be >= 2 for a cycle; got {n_tools}")
    if edge_dimension_field not in observable_schema:
        raise ValueError(
            f"edge_dimension_field={edge_dimension_field!r} must be in "
            f"observable_schema={observable_schema} for fee=0; "
            f"otherwise use build_cycle_from_tools directly."
        )

    tools = tuple(
        ToolSpec(
            name=f"{name_prefix}_{i}",
            internal_state=internal_state,
            observable_schema=observable_schema,
        )
        for i in range(n_tools)
    )
    return build_cycle_from_tools(
        name=name,
        tools=tools,
        edge_dimension_field=edge_dimension_field,
    )


def build_known_nonvanishing(
    *,
    name: str,
    k: int,
    obstruction_field: str,
    hub_internal: tuple[str, ...] | None = None,
    hub_observable: tuple[str, ...] | None = None,
    spoke_internal: tuple[str, ...] | None = None,
    spoke_observable: tuple[str, ...] | None = None,
    hub_name: str = "synth_hub",
    spoke_name_prefix: str = "synth_spoke",
) -> Composition:
    """Hub-and-spoke composition with designed ``coherence_fee = k``.

    Convenience wrapper around ``build_hub_spoke_from_tools`` for the
    common case where the hub and spoke ToolSpec schemas can be built
    from minimal field specifications.

    Defaults produce minimal tools where only ``obstruction_field``
    appears:
      - hub_internal = hub_observable = (obstruction_field,)
      - spoke_internal = (obstruction_field,)
      - spoke_observable = ()

    For real adapters, override the defaults to match the adapter's
    natural M2 surface (e.g., G23 SAE adapter passes
    ``hub_internal = (identifier, activation_p99, decoder_direction,
    provenance, concept)`` etc.).

    Args:
        name: composition name.
        k: target ``coherence_fee``. Must be >= 1.
        obstruction_field: the field that's observable on hub, hidden
            on spokes. Mathematically: present in hub.observable_schema
            and in spoke.internal_state but not spoke.observable_schema.
        hub_internal: hub.internal_state. Defaults to
            ``(obstruction_field,)``. Must contain ``obstruction_field``.
        hub_observable: hub.observable_schema. Defaults to
            ``(obstruction_field,)``. Must contain ``obstruction_field``.
        spoke_internal: each spoke's internal_state. Defaults to
            ``(obstruction_field,)``. Must contain ``obstruction_field``.
        spoke_observable: each spoke's observable_schema. Defaults to
            ``()``. Must NOT contain ``obstruction_field``.
        hub_name: hub ToolSpec name.
        spoke_name_prefix: spoke ToolSpec name prefix; suffix is the
            spoke index ``_0``, ``_1``, ....

    Returns:
        Composition with 1 hub + (k+1) spokes + (k+1) edges. Expected
        ``diagnose(comp).coherence_fee == k`` exactly (±0).

    Raises:
        ValueError: if ``k < 1`` or field-placement preconditions
            violated.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}")

    hub_int = hub_internal if hub_internal is not None else (obstruction_field,)
    hub_obs = hub_observable if hub_observable is not None else (obstruction_field,)
    spk_int = spoke_internal if spoke_internal is not None else (obstruction_field,)
    spk_obs = spoke_observable if spoke_observable is not None else ()

    if obstruction_field not in hub_obs:
        raise ValueError(
            f"obstruction_field={obstruction_field!r} must be in "
            f"hub_observable={hub_obs} for fee={k}"
        )
    if obstruction_field not in spk_int:
        raise ValueError(
            f"obstruction_field={obstruction_field!r} must be in "
            f"spoke_internal={spk_int} for fee={k}"
        )
    if obstruction_field in spk_obs:
        raise ValueError(
            f"obstruction_field={obstruction_field!r} must NOT be in "
            f"spoke_observable={spk_obs} for fee={k} (must be hidden on spokes)"
        )

    hub = ToolSpec(name=hub_name, internal_state=hub_int, observable_schema=hub_obs)
    spokes = tuple(
        ToolSpec(
            name=f"{spoke_name_prefix}_{i}",
            internal_state=spk_int,
            observable_schema=spk_obs,
        )
        for i in range(k + 1)
    )
    return build_hub_spoke_from_tools(
        name=name,
        hub=hub,
        spokes=spokes,
        obstruction_field=obstruction_field,
    )


# ── Audit utility ────────────────────────────────────────────────────


@dataclass(frozen=True)
class EncodingCapabilityAudit:
    """Verdict on whether a composition's encoding can produce ``fee > 0``.

    A composition can produce ``fee > 0`` iff at least one edge declares
    a SemanticDimension whose from_field is in source's hidden_schema
    OR whose to_field is in target's hidden_schema. If every edge uses
    only observable-schema fields, ``δ_full ≡ δ_obs`` by construction
    and ``fee = 0`` for all valid composition states.

    Use BEFORE running an encoding adapter on real data: an audit
    failure (``can_produce_obstruction == False``) means any historical
    sweep will produce vacuous fee=0 across all inputs.

    Attributes:
        n_edges: total edges in the composition.
        n_hidden_from_field_edges: edges whose from_field is in
            source's hidden_schema (in source.internal_state but not
            source.observable_schema).
        n_hidden_to_field_edges: edges whose to_field is in target's
            hidden_schema. (Edges may have both — counted once each.)
        can_produce_obstruction: True iff at least one edge has hidden
            from_field OR hidden to_field; False otherwise.
    """

    n_edges: int
    n_hidden_from_field_edges: int
    n_hidden_to_field_edges: int
    can_produce_obstruction: bool


def audit_encoding_capability(comp: Composition) -> EncodingCapabilityAudit:
    """Inspect ``comp.edges`` to determine if encoding can produce ``fee > 0``.

    Args:
        comp: composition to audit.

    Returns:
        EncodingCapabilityAudit verdict.
    """
    tool_lookup = {t.name: t for t in comp.tools}
    n_hidden_from = 0
    n_hidden_to = 0
    for edge in comp.edges:
        src = tool_lookup[edge.from_tool]
        tgt = tool_lookup[edge.to_tool]
        for dim in edge.dimensions:
            if dim.from_field is not None:
                if (
                    dim.from_field in src.internal_state
                    and dim.from_field not in src.observable_schema
                ):
                    n_hidden_from += 1
            if dim.to_field is not None:
                if (
                    dim.to_field in tgt.internal_state
                    and dim.to_field not in tgt.observable_schema
                ):
                    n_hidden_to += 1

    return EncodingCapabilityAudit(
        n_edges=len(comp.edges),
        n_hidden_from_field_edges=n_hidden_from,
        n_hidden_to_field_edges=n_hidden_to,
        can_produce_obstruction=(n_hidden_from > 0 or n_hidden_to > 0),
    )
