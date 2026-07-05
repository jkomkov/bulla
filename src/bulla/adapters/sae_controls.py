"""Synthetic control fixtures for G23 Stage A A1 (modeling-soundness checks).

Two control families before any real-SAE measurement is named "M2":

  1. Known-vanishing control (``build_known_vanishing_control``).
     Same-model identity composition: a cyclic chain of N identical SAE
     features in the same model, linked by identity edges on observable
     fields. Expected: ``coherence_fee == 0``. This is the negative
     control — verifies the pipeline does not synthesise spurious
     obstruction.

  2. Known-non-vanishing positive control
     (``build_known_nonvanishing_control``).
     2-model hub-and-spoke composition with k+1 spokes, generalising
     the Sprint 15 hub-and-spoke fixture
     (papers/composition-doctrine/sprint15_demo/fixture.py) from k=1 to
     arbitrary k. The hub (model A) exposes an alignment field
     ``concept`` observably; the (k+1) spokes (model B) carry
     ``concept`` in internal_state but hide it from observable_schema.
     (k+1) edges from hub to each spoke declare the alignment dimension
     on ``concept``. Expected: ``coherence_fee == k`` exactly.

The positive control is the load-bearing modeling-soundness check.
The negative control alone tests for code bugs; the positive control
tests for correctness of the SAE-feature → ToolSpec lifting under
designed obstruction. If either fails, the SAE adapter is broken
before any real-data spend.

Per the G23 plan
(``/Users/jkomkov/.claude/plans/review-where-we-are-ancient-peach.md``):
    "Build the known-non-vanishing positive control before the
    known-vanishing negative control. Negative controls catch code
    bugs; positive controls catch modeling errors. Modeling errors
    are exponentially more expensive to discover late."

Both fixtures are pure combinatorial constructions: no model weights,
no activation traces, no API calls. The expected fee is a finite
integer that the witness-geometry kernel must recover exactly (±0
tolerance, not ±1).
"""

from __future__ import annotations

from bulla.adapters.sae import (
    INTERNAL_FIELDS,
    OBSERVABLE_FIELDS,
    SAEFeatureSpec,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.testing import build_hub_spoke_from_tools


def build_known_vanishing_control(
    *,
    n_features: int = 4,
    model_id: str = "synthetic-vanishing",
    layer: int = 0,
) -> Composition:
    """Same-model identity composition. Expected ``coherence_fee == 0``.

    Constructs a cyclic chain of ``n_features`` SAE features in the
    same (model, layer), each linked by identity edges on the two
    observable fields (``identifier`` and ``activation_p99``). With
    only observable-field dimensions, ``δ_obs`` and ``δ_internal`` have
    the same rank and the fee vanishes.

    The cyclic structure (as opposed to a linear chain) is deliberate:
    a linear chain has ``β_1 = 0`` and trivially yields ``fee = 0``;
    the cycle gives ``β_1 = 1`` and tests the harder case where
    rank-deficient cycles could in principle produce spurious
    obstruction if the lifter were buggy.

    Args:
        n_features: number of features in the cycle. Must be >= 2 so a
            cycle exists.
        model_id: synthetic model identifier (only used for naming).
        layer: synthetic layer index (only used for naming).

    Returns:
        Composition with ``n_features`` tools and ``n_features`` cyclic
        edges, each declaring identity dimensions on observable fields.

    Raises:
        ValueError: if ``n_features < 2``.
    """
    if n_features < 2:
        raise ValueError(
            f"n_features must be >= 2 for a cycle; got {n_features}"
        )

    features = [
        SAEFeatureSpec(model_id=model_id, layer=layer, feature_id=i)
        for i in range(n_features)
    ]
    tools = tuple(f.to_tool_spec() for f in features)

    edges = tuple(
        Edge(
            from_tool=features[i].name,
            to_tool=features[(i + 1) % n_features].name,
            dimensions=(
                SemanticDimension(
                    name=f"identifier_match_{i}",
                    from_field="identifier",
                    to_field="identifier",
                ),
                SemanticDimension(
                    name=f"activation_match_{i}",
                    from_field="activation_p99",
                    to_field="activation_p99",
                ),
            ),
        )
        for i in range(n_features)
    )

    return Composition(
        name=f"sae_vanishing_n{n_features}",
        tools=tools,
        edges=edges,
    )


def build_known_nonvanishing_control(
    *,
    k: int,
    model_a_id: str = "synthetic-model-a",
    model_b_id: str = "synthetic-model-b",
    layer_a: int = 0,
    layer_b: int = 0,
) -> Composition:
    """2-model hub-and-spoke composition with designed ``coherence_fee == k``.

    Construction (generalises Sprint 15 hub-and-spoke from k=1):

      * Hub: 1 SAE feature in model A. Its ToolSpec exposes the
        alignment field ``concept`` in observable_schema (in addition
        to the natural M2 fields ``identifier`` and ``activation_p99``).

      * Spokes: (k+1) SAE features in model B. Each spoke's ToolSpec
        carries ``concept`` in internal_state but NOT in
        observable_schema (i.e., the alignment field is hidden on the
        spoke side).

      * Edges: (k+1) edges from hub to each spoke, each declaring a
        unique semantic dimension on ``concept``
        (``concept_match_0`` through ``concept_match_k``).

    Coboundary mechanics:
        ``δ_obs`` has (k+1) rows. Each row's ``from_field=concept`` is
        in the hub's observable_schema, so the entry at
        ``hub.concept`` is -1. Each row's ``to_field=concept`` is NOT
        in the spoke's observable_schema, so no +1 entry on the spoke
        side. The (k+1) rows are identical: all are ``[-1 at hub.concept]``
        with zeros elsewhere. ``rank(δ_obs) == 1``.

        ``δ_internal`` has (k+1) rows. Each row has -1 at
        ``hub.concept`` and +1 at ``spoke_i.concept`` (distinct spoke
        column for each row). ``rank(δ_internal) == k + 1``.

        ``coherence_fee == rank_internal − rank_obs == (k + 1) − 1 == k``.

    For k=1, this reproduces the Sprint 15 fixture exactly (2 spokes,
    fee=1). For k ∈ {2, 3, 5}, the construction extends to (3, 4, 6)
    spokes producing (fee=2, 3, 5). The pipeline must recover the
    target fee EXACTLY (±0); ±1 tolerance would mask integer-arithmetic
    bugs in the witness-geometry kernel.

    Args:
        k: target ``coherence_fee``. Must be >= 1 (k=0 is the vanishing
            case; use ``build_known_vanishing_control`` instead).
        model_a_id: synthetic identifier for model A (hub side).
        model_b_id: synthetic identifier for model B (spoke side).
        layer_a: synthetic layer index for model A.
        layer_b: synthetic layer index for model B.

    Returns:
        Composition with 1 hub + (k+1) spokes + (k+1) edges. Expected
        ``diagnose(comp).coherence_fee == k``.

    Raises:
        ValueError: if ``k < 1``.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}")

    n_spokes = k + 1

    # Hub: SAE feature in model A with 'concept' exposed observably.
    # Construct ToolSpec directly because the natural M2 lifter does
    # not include 'concept'; this is a synthetic control field for
    # the obstruction.
    hub_name = f"{model_a_id}/L{layer_a}/F0"
    hub = ToolSpec(
        name=hub_name,
        internal_state=INTERNAL_FIELDS + ("concept",),
        observable_schema=OBSERVABLE_FIELDS + ("concept",),
    )

    # Spokes: SAE features in model B with 'concept' in internal_state
    # but hidden from observable_schema. The asymmetry (concept observable
    # on hub, hidden on spoke) is what produces the fee=k obstruction.
    spokes = tuple(
        ToolSpec(
            name=f"{model_b_id}/L{layer_b}/F{i}",
            internal_state=INTERNAL_FIELDS + ("concept",),
            observable_schema=OBSERVABLE_FIELDS,
        )
        for i in range(n_spokes)
    )

    # REFACTORED 2026-05-06: edge construction delegated to
    # bulla.testing.build_hub_spoke_from_tools (Refinement 2 public
    # testing utility). Edge dimension naming preserved as
    # ``concept_match_{i}`` for backward compat with existing tests.
    return build_hub_spoke_from_tools(
        name=f"sae_nonvanishing_k{k}",
        hub=hub,
        spokes=spokes,
        obstruction_field="concept",
        edge_name_prefix="concept_match",
    )
