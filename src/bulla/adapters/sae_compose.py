"""Cross-model SAE composition builder for G23 Stage A A3.

Companion to ``bulla.adapters.sae.build_multi_layer_composition`` (A2,
single-model multi-layer) — this module builds the *cross-model 2-cover*
composition the A3 plan needs: pairs of SAE features from two distinct
models (Gemma-2-2B and GPT-2-Small) connected by edges that declare a
restriction-map seam on the **hidden** ``decoder_direction`` field.

# Why hidden-field cross-model edges are the load-bearing trick

The natural M2 surface for SAE features (per ``bulla.adapters.sae``)
declares ``decoder_direction`` as part of ``internal_state`` but NOT
``observable_schema`` — the natural cross-SAE comparison cannot see
decoder vectors without invoking a fitted restriction map (Procrustes,
Sparse Crosscoder, Transcoder, NeuronpediaLabelMap).

Cross-model edges declared on ``decoder_direction`` therefore contribute
to ``δ_internal`` (the "full" / hidden-side coboundary) but not
``δ_observable``. By the rank formula

    coherence_fee = rank_internal − rank_obs = h¹_obs − h¹_internal

the fee equals the magnitude of cross-model coordination structure
visible only on the hidden surface. A cross-model hub-and-spoke with
``k+1`` spokes on side B and one hub on side A, all linked on
``decoder_direction``, has expected ``coherence_fee = k`` exactly —
matching the Sprint-15 / G23 A1 known-non-vanishing topology lifted to
two model sides.

This is the M2 axiom (cross-system sheaf-restriction-map structure)
made operational. The §3b sweep's three ablated restriction maps
(Procrustes, Crosscoder, Transcoder) each propose an ALIGNMENT (which
feature on side B corresponds to which on side A) and the resulting
``dim H¹`` measures how much obstruction the alignment leaves behind.

# Lazy-import discipline

This module imports without ``torch``. ``SAEFeatureSpec`` is
identifier-only; the cross-model topology is purely structural. The
``decoder_direction`` field is referenced by name only — not by tensor.
"""

from __future__ import annotations

from dataclasses import dataclass

from bulla.adapters.sae import (
    INTERNAL_FIELDS,
    OBSERVABLE_FIELDS,
    SAEFeatureSpec,
)
from bulla.model import Composition, Edge, SemanticDimension


# Internal sanity-check: this module assumes ``decoder_direction`` is in
# the natural-M2 internal state and NOT observable. If a future refactor
# of ``bulla.adapters.sae`` moves the field, every fee calculation in
# A3 silently changes — fail loud at import time instead.
assert "decoder_direction" in INTERNAL_FIELDS, (
    "sae_compose assumes decoder_direction is in INTERNAL_FIELDS; "
    "cross-model 2-cover obstruction depends on it."
)
assert "decoder_direction" not in OBSERVABLE_FIELDS, (
    "sae_compose assumes decoder_direction is HIDDEN (not in "
    "OBSERVABLE_FIELDS); cross-model fee depends on it being hidden."
)


@dataclass(frozen=True)
class CrossModelComposition:
    """Result of `build_cross_model_composition`: composition + provenance.

    Carries the constructed ``Composition`` plus a record of which
    features came from which side so downstream code (the §3b sweep,
    falsification-branch determination) can recover the model-A /
    model-B partition without re-parsing tool names.

    Frozen + hashable so it can be embedded in receipt structures.

    Attributes:
        composition: the Composition with cross-model edges declared on
            ``decoder_direction`` (hidden).
        features_a: side-A SAE features (typically Gemma-2-2B).
        features_b: side-B SAE features (typically GPT-2-Small).
        cross_model_edges: tuple of ``(idx_a, idx_b)`` index pairs into
            ``features_a`` / ``features_b`` mirroring the composition
            edges.
    """

    composition: Composition
    features_a: tuple[SAEFeatureSpec, ...]
    features_b: tuple[SAEFeatureSpec, ...]
    cross_model_edges: tuple[tuple[int, int], ...]


def build_cross_model_composition(
    *,
    features_a: tuple[SAEFeatureSpec, ...],
    features_b: tuple[SAEFeatureSpec, ...],
    cross_model_edges: tuple[tuple[int, int], ...],
    name: str | None = None,
) -> CrossModelComposition:
    """Build a cross-model composition with hidden-field decoder-direction edges.

    Each edge in ``cross_model_edges`` is a ``(idx_a, idx_b)`` pair
    where ``idx_a`` indexes into ``features_a`` (side A; e.g. Gemma) and
    ``idx_b`` indexes into ``features_b`` (side B; e.g. GPT-2). The
    resulting ``Composition.edges[i]`` declares one SemanticDimension on
    the hidden field ``decoder_direction``:

        Edge(
            from_tool=features_a[idx_a].name,
            to_tool=features_b[idx_b].name,
            dimensions=(SemanticDimension(
                name=f"decoder_align_{i}",
                from_field="decoder_direction",
                to_field="decoder_direction",
            ),),
        )

    Because ``decoder_direction`` is in ``INTERNAL_FIELDS`` but not
    ``OBSERVABLE_FIELDS`` (asserted at module import), this edge counts
    in ``δ_internal`` but not ``δ_obs``, producing the cross-model
    obstruction the restriction-map ablation measures.

    Tools are emitted in the order ``features_a + features_b`` so a
    cross-model hub-and-spoke with hub at ``features_a[0]`` and spokes at
    ``features_b[0..k]`` can be built simply with edges
    ``[(0, 0), (0, 1), ..., (0, k)]``.

    Args:
        features_a: side-A SAE features (typically Gemma-2-2B). Must be
            non-empty.
        features_b: side-B SAE features (typically GPT-2-Small). Must be
            non-empty.
        cross_model_edges: tuple of ``(idx_a, idx_b)`` pairs. Indices
            must be valid into ``features_a`` / ``features_b``. May be
            empty (no edges; pure-tools composition with fee=0).
        name: optional composition name; defaults to a deterministic
            name encoding side counts and edge count.

    Returns:
        CrossModelComposition wrapping the constructed Composition with
        provenance preserved.

    Raises:
        ValueError: if either feature side is empty or any edge index
            is out of range.
    """
    if not features_a:
        raise ValueError("features_a must be non-empty")
    if not features_b:
        raise ValueError("features_b must be non-empty")

    n_a = len(features_a)
    n_b = len(features_b)
    for i, (idx_a, idx_b) in enumerate(cross_model_edges):
        if not (0 <= idx_a < n_a):
            raise ValueError(
                f"cross_model_edges[{i}] = ({idx_a}, {idx_b}): "
                f"idx_a={idx_a} out of range for features_a (n={n_a})"
            )
        if not (0 <= idx_b < n_b):
            raise ValueError(
                f"cross_model_edges[{i}] = ({idx_a}, {idx_b}): "
                f"idx_b={idx_b} out of range for features_b (n={n_b})"
            )

    # Per-side ToolSpec lifters preserve canonical M2 surface.
    tools = tuple(f.to_tool_spec() for f in features_a) + tuple(
        f.to_tool_spec() for f in features_b
    )

    edges = tuple(
        Edge(
            from_tool=features_a[idx_a].name,
            to_tool=features_b[idx_b].name,
            dimensions=(
                SemanticDimension(
                    name=f"decoder_align_{i}",
                    from_field="decoder_direction",
                    to_field="decoder_direction",
                ),
            ),
        )
        for i, (idx_a, idx_b) in enumerate(cross_model_edges)
    )

    composition_name = name or (
        f"sae_cross_model_a{n_a}_b{n_b}_e{len(cross_model_edges)}"
    )
    composition = Composition(
        name=composition_name,
        tools=tools,
        edges=edges,
    )
    return CrossModelComposition(
        composition=composition,
        features_a=features_a,
        features_b=features_b,
        cross_model_edges=cross_model_edges,
    )


def build_cross_model_hub_spoke(
    *,
    k: int,
    hub_model: str = "gemma2-2b",
    hub_layer: int = 20,
    hub_feature_id: int = 0,
    spoke_model: str = "gpt2-small",
    spoke_layer: int = 11,
    spoke_feature_id_start: int = 0,
    name: str | None = None,
) -> CrossModelComposition:
    """Cross-model hub-and-spoke composition with designed coherence_fee = k.

    The §3a′-tripwire workhorse: hub on side A connected to ``k`` spokes
    on side B, each edge declaring ``decoder_direction`` (hidden on
    BOTH sides per the natural SAE M2 surface). Expected
    ``diagnose(comp).coherence_fee == k`` exactly.

    # Why fee = k and not k - 1

    The standard ``build_known_nonvanishing(k)`` (single-encoding hub-
    and-spoke) needs ``k+1`` spokes for fee=k because the obstruction
    field is *observable* on the hub side, contributing rank 1 to
    ``δ_obs`` that subtracts off the boundary count. In the cross-model
    2-cover, ``decoder_direction`` is hidden on BOTH the hub side and
    the spoke side (per ``OBSERVABLE_FIELDS`` not containing
    ``decoder_direction``), so ``rank_obs = 0`` and
    ``rank_full = k`` (one independent column per cross-model edge).

    Equivalently: in the cross-model regime the hub-and-spoke and the
    star are the same shape, and the magnitude is the number of
    cross-model edges. This is the load-bearing positive control that
    makes the §3b sweep interpretable — if cross-model hub-and-spoke
    doesn't recover fee=k, the encoding is broken before any HF token
    is spent.

    Args:
        k: target ``coherence_fee``. Must be >= 1. The composition has
            ``1`` hub + ``k`` spokes + ``k`` cross-model edges.
        hub_model, hub_layer, hub_feature_id: side-A hub identity.
        spoke_model, spoke_layer, spoke_feature_id_start: side-B spoke
            identity. Spokes use feature_ids
            ``[start, start+1, ..., start+(k-1)]``.
        name: optional composition name.

    Returns:
        CrossModelComposition with expected ``coherence_fee == k``.

    Raises:
        ValueError: if ``k < 1`` or
            ``hub_model == spoke_model and hub_layer == spoke_layer``
            (would not be cross-model; use
            ``build_multi_layer_composition`` instead).
    """
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}")
    if hub_model == spoke_model and hub_layer == spoke_layer:
        raise ValueError(
            f"build_cross_model_hub_spoke requires distinct (model, layer); "
            f"got hub=({hub_model}, {hub_layer}) == "
            f"spoke=({spoke_model}, {spoke_layer}). "
            f"Use build_multi_layer_composition for single-model topology."
        )

    hub = SAEFeatureSpec(
        model_id=hub_model, layer=hub_layer, feature_id=hub_feature_id,
    )
    spokes = tuple(
        SAEFeatureSpec(
            model_id=spoke_model, layer=spoke_layer,
            feature_id=spoke_feature_id_start + i,
        )
        for i in range(k)
    )
    edges = tuple((0, i) for i in range(k))

    return build_cross_model_composition(
        features_a=(hub,),
        features_b=spokes,
        cross_model_edges=edges,
        name=name or f"sae_cross_model_hub_spoke_k{k}",
    )
