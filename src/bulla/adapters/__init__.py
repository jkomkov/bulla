"""Bulla adapters for external composition surfaces.

Each module in this package lifts a specific external object (e.g., an SAE
feature, a LangGraph node, a CrewAI agent) to bulla's `ToolSpec` so that
witness-geometry and coherence-fee diagnostics can be computed uniformly.

Adapters here:
  - sae: lift (model_id, layer, feature_id) SAE features to ToolSpec for
    M2-via-SAE cross-model cohomology measurement (G23 Stage A).
  - sae_controls: synthetic known-vanishing + known-non-vanishing
    fixtures for G23 A1 modeling-soundness validation.
"""
