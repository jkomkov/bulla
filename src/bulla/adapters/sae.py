"""SAE feature → ToolSpec lifter for M2-via-SAE composition (G23 Stage A).

A Sparse Autoencoder (SAE) feature is a triple (model_id, layer, feature_id)
identifying a single decoder direction in the SAE's dictionary, plus the
activation statistics it carries on a held-out distribution. The Coherence
Rate Theorem (G16, Lean-verified in
papers/composition-doctrine/lean/CompositionDoctrine/CoherenceRate.lean)
predicts that the bits required for two interpretability stacks to agree on
a circuit is bounded below by log_2 dim H^1 of the seam. To measure that
H^1 with bulla.diagnostic, each SAE feature must be expressed as a ToolSpec
whose observable_schema is exactly the set of fields the natural cross-SAE
restriction map can see; the hidden surface is what the restriction map
must justify with extra structure (Sparse Crosscoder, Procrustes, Universal
LLM-SAE, Transcoder).

The natural M2 surface for SAE features is:
  internal_state    = (identifier, activation_p99, decoder_direction, provenance)
  observable_schema = (identifier, activation_p99)

Justification:
  * `identifier` (observable): the canonical name of the feature
    (e.g. ``gemma2-2b/L20/F1234``). Two stacks can compare identifiers
    cheaply at the seam.
  * `activation_p99` (observable): the 99th-percentile activation
    magnitude on a held-out reference distribution. Two stacks can
    compare scalar activation magnitudes cheaply at the seam.
  * `decoder_direction` (hidden): the actual SAE-decoder direction in
    activation space. Crossing the seam with this requires a
    restriction map (Crosscoder fits an alignment; Procrustes computes
    one; Universal SAE shares a basis; Transcoder substitutes the
    activations). The natural cross-SAE comparison cannot see this.
  * `provenance` (hidden): the (model_id, layer, feature_id) origin
    record. Two stacks can compare identifiers but cannot
    cross-validate provenance without disclosure of training data,
    SAE checkpoint, etc.

Sprint 9 schema-shape invariant: ``observable_schema ⊆ internal_state``
holds by construction (identifier and activation_p99 appear in both).

This adapter does NOT load actual SAE weights. It produces the structural
ToolSpec only. Stage A2 builds on this with a ``sae_loader`` adapter that
reads HuggingFace-hosted SAE checkpoints (Gemma Scope, Sparse Crosscoders)
and populates the SAEFeatureSpec instances with real activation data.
For G23 A1 (controls), only the structural shape is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

# Canonical M2-surface field names. Exposed at module scope so synthetic
# control fixtures (sae_controls.py) can reference them by name without
# string duplication.
INTERNAL_FIELDS: tuple[str, ...] = (
    "identifier",
    "activation_p99",
    "decoder_direction",
    "provenance",
)
OBSERVABLE_FIELDS: tuple[str, ...] = (
    "identifier",
    "activation_p99",
)


@dataclass(frozen=True)
class SAEFeatureSpec:
    """A single SAE feature lifted to bulla's ToolSpec interface.

    A frozen identifier-only carrier: actual decoder vectors and
    activation traces are referenced by ``provenance`` (hidden) and not
    materialised here. The lifter ``to_tool_spec()`` produces a
    structurally-correct ToolSpec for use in compositions.

    Two SAEFeatureSpec instances with the same (model_id, layer,
    feature_id) compare equal and produce identical ToolSpec output.
    """

    model_id: str
    layer: int
    feature_id: int

    @property
    def name(self) -> str:
        """Canonical tool name: ``{model_id}/L{layer}/F{feature_id}``."""
        return f"{self.model_id}/L{self.layer}/F{self.feature_id}"

    def to_tool_spec(self) -> ToolSpec:
        """Lift to the natural M2-surface ToolSpec.

        Returns the canonical ToolSpec with
        internal_state = (identifier, activation_p99, decoder_direction,
        provenance) and observable_schema = (identifier, activation_p99).

        For synthetic control fixtures that need an asymmetric
        observable surface (e.g. spokes that hide an alignment field),
        construct ``ToolSpec`` instances directly in
        ``bulla.adapters.sae_controls`` rather than going through this
        lifter.
        """
        return ToolSpec(
            name=self.name,
            internal_state=INTERNAL_FIELDS,
            observable_schema=OBSERVABLE_FIELDS,
        )


def build_multi_layer_composition(
    *,
    features: tuple[SAEFeatureSpec, ...],
    cross_layer_edges: tuple[tuple[int, int], ...],
    name: str | None = None,
) -> Composition:
    """Build a Composition from SAE features + cross-layer edge indices.

    The G23 Stage A A2 deliverable: lift a list of SAEFeatureSpec
    instances (typically loaded via ``bulla.adapters.sae_loader``) into
    a Composition with edges declaring the natural M2 dimensions
    (identifier, activation_p99) on each cross-layer link.

    Each entry in ``cross_layer_edges`` is a (source_index, target_index)
    pair into ``features``. Each edge declares two SemanticDimensions on
    the natural M2 observable surface — ``identifier`` and
    ``activation_p99`` — which are the only fields cross-SAE restriction
    maps can compare without invoking a fitted alignment (Crosscoder,
    Procrustes, Universal-SAE, Transcoder). The hidden surface
    (``decoder_direction``, ``provenance``) is NOT declared on edges by
    this builder; A3 will add adapter-specific edge variants that
    declare hidden-field correspondence under specific restriction-map
    assumptions.

    For the natural M2 surface (this builder), a connected single-model
    multi-layer composition with all-observable-field edges has
    ``coherence_fee == 0`` because the observable rank equals the
    internal rank on this restricted edge set. The A2 smoke test
    verifies this is finite (and specifically zero) end-to-end.

    Args:
        features: SAE features to compose, typically from
            ``SAELoader.load_features()``. Must be non-empty.
        cross_layer_edges: tuple of (source_index, target_index) pairs.
            Indices must be valid indices into ``features``.
        name: optional composition name; defaults to a deterministic
            name encoding the feature and edge counts.

    Returns:
        Composition with len(features) tools and len(cross_layer_edges)
        edges, each declaring identity dimensions on observable fields.

    Raises:
        ValueError: if ``features`` is empty or any edge index is
            out of range.
    """
    if not features:
        raise ValueError("features must be non-empty")
    n = len(features)
    for i, (src, tgt) in enumerate(cross_layer_edges):
        if not (0 <= src < n) or not (0 <= tgt < n):
            raise ValueError(
                f"cross_layer_edges[{i}] = ({src}, {tgt}) out of range "
                f"for {n} features"
            )

    tools = tuple(f.to_tool_spec() for f in features)
    edges = tuple(
        Edge(
            from_tool=features[src].name,
            to_tool=features[tgt].name,
            dimensions=(
                SemanticDimension(
                    name=f"id_match_{i}",
                    from_field="identifier",
                    to_field="identifier",
                ),
                SemanticDimension(
                    name=f"act_match_{i}",
                    from_field="activation_p99",
                    to_field="activation_p99",
                ),
            ),
        )
        for i, (src, tgt) in enumerate(cross_layer_edges)
    )
    composition_name = name or (
        f"sae_multi_layer_n{len(features)}_e{len(cross_layer_edges)}"
    )
    return Composition(
        name=composition_name,
        tools=tools,
        edges=edges,
    )
