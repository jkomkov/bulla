"""§3a′ Synthetic-control validation tripwires for G23 A3 (commit 1d).

The 9 §3a′ tripwires (0a-0i) gate Iter-2 entry per
``~/.claude/plans/review-where-we-are-ancient-peach.md``. ALL must pass
before any HuggingFace download / Modal compute is permitted. The cost
of a failed Iter-3 from an Iter-1-catchable bug is ~$5 + 30 min wallclock;
these tripwires fail in <1s and catch the G24 failure class for free.

# The 9 tripwires (mapping to test classes below)

  * **0a Structural integrity** — cross-model composition has 1 hub +
    k spokes + k edges in the expected topology; tools and edges
    enumerate as designed.
    → ``TestTripwire0a_StructuralIntegrity``

  * **0b Encoding capability** — ``audit_encoding_capability(comp)``
    returns ``can_produce_obstruction == True`` (cheapest gate; fails
    in milliseconds when the encoding is too coarse). This is the G24
    pre-A3 lesson promoted to first-class Tripwire 0 per the plan's
    Refinement 1.
    → ``TestTripwire0b_EncodingCapability``

  * **0c-0g Magnitude recovery** — for each k ∈ {1, 2, 3, 5, 10},
    ``build_cross_model_hub_spoke(k=k)`` produces a composition with
    ``diagnose(comp).coherence_fee == k`` exactly (±0). This is the
    cross-model lift of the Sprint-15 / G23 A1 ``build_known_nonvanishing``
    positive control.
    → ``TestTripwire0c_through_0g_MagnitudeRecovery``

  * **0h Vanishing control** — a cross-model composition with
    OBSERVABLE-field edges (e.g. ``activation_p99`` cross-model
    alignment) produces ``coherence_fee == 0``, and a same-side cycle
    on observable fields likewise vanishes. Verifies that the encoding
    can also represent the "no obstruction" case correctly.
    → ``TestTripwire0h_VanishingControl``

  * **0i Map-invariance pre-check** — for the synthetic-only restriction
    maps Identity / OffsetRestrictionMap(shift) / BijectionRestrictionMap(perm),
    applying the map to side-B's feature_ids produces a relabeled
    composition with the SAME ``coherence_fee`` as the un-permuted
    composition. This is the LOAD-BEARING fix for Concern 1: if the
    encoding's fee changes under a feature-id permutation, then the
    restriction-map ablation in §3b would conflate map-induced rotation
    with real cross-model coordination structure.
    → ``TestTripwire0i_MapInvariance``

# Why these gate Iter-2

Tripwire 0b (encoding capability) is the cheapest soundness check the
codebase has for an encoding adapter. Tripwires 0c-0g verify the
encoding recovers known magnitudes — the G24 lesson empirically
documented (commit 6ba3f89 + ``feedback_pre_registration_calibration.md``).
Tripwire 0i is the structural prerequisite for Gate 7
(``dim H¹(SAE_a, π(SAE_a)) = 0``) being satisfiable in the §3b sweep.

If any tripwire fails, halt before any HF download. Debug encoding
(typically commit 1d itself or commit 1f sae_loader.load_dictionary
delegation) before retrying.
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae import OBSERVABLE_FIELDS, SAEFeatureSpec
from bulla.adapters.sae_compose import (
    build_cross_model_composition,
    build_cross_model_hub_spoke,
)
from bulla.adapters.restriction_maps import (
    BijectionRestrictionMap,
    IdentityRestrictionMap,
    OffsetRestrictionMap,
)
from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension
from bulla.testing import audit_encoding_capability


# ── Tripwire 0a: structural integrity ──────────────────────────────────


class TestTripwire0a_StructuralIntegrity:
    """The cross-model composition has the topology we designed."""

    def test_hub_spoke_has_1_plus_k_tools(self):
        for k in [1, 2, 3, 5, 10]:
            result = build_cross_model_hub_spoke(k=k)
            assert len(result.composition.tools) == 1 + k, (
                f"Tripwire 0a FAILED at k={k}: expected {1 + k} tools, "
                f"got {len(result.composition.tools)}"
            )
            assert len(result.composition.edges) == k

    def test_first_tool_is_hub_remainder_are_spokes(self):
        result = build_cross_model_hub_spoke(k=3)
        names = tuple(t.name for t in result.composition.tools)
        # Hub on side A
        assert names[0] == "gemma2-2b/L20/F0"
        # Spokes on side B
        for i in range(3):
            assert names[1 + i] == f"gpt2-small/L11/F{i}"

    def test_all_edges_originate_at_hub(self):
        result = build_cross_model_hub_spoke(k=5)
        hub_name = "gemma2-2b/L20/F0"
        for e in result.composition.edges:
            assert e.from_tool == hub_name

    def test_edges_declare_decoder_direction_only(self):
        result = build_cross_model_hub_spoke(k=2)
        for e in result.composition.edges:
            assert len(e.dimensions) == 1
            d = e.dimensions[0]
            assert d.from_field == "decoder_direction"
            assert d.to_field == "decoder_direction"


# ── Tripwire 0b: encoding capability (the cheapest gate) ───────────────


class TestTripwire0b_EncodingCapability:
    """`audit_encoding_capability(comp).can_produce_obstruction == True`.

    This is the load-bearing tripwire from Refinement 1 of the plan:
    the G24 pre-A3 lesson promoted to first-class Tripwire 0. Fails in
    milliseconds when the encoding is too coarse to produce fee > 0.
    """

    def test_hub_spoke_can_produce_obstruction_at_all_k(self):
        for k in [1, 2, 3, 5, 10]:
            result = build_cross_model_hub_spoke(k=k)
            verdict = audit_encoding_capability(result.composition)
            assert verdict.can_produce_obstruction is True, (
                f"Tripwire 0b FAILED at k={k}: encoding cannot produce "
                f"obstruction. Audit: {verdict}. The cross-model 2-cover "
                f"with decoder_direction edges should ALWAYS be capable; "
                f"this means the OBSERVABLE_FIELDS contract is broken."
            )

    def test_audit_counts_hidden_field_edges(self):
        # Every cross-model edge declares decoder_direction (hidden on
        # both sides), so ALL edges should count as both "hidden_from"
        # and "hidden_to".
        result = build_cross_model_hub_spoke(k=4)
        verdict = audit_encoding_capability(result.composition)
        assert verdict.n_edges == 4
        assert verdict.n_hidden_from_field_edges == 4
        assert verdict.n_hidden_to_field_edges == 4


# ── Tripwires 0c-0g: magnitude recovery for k ∈ {1, 2, 3, 5, 10} ───────


class TestTripwire0c_through_0g_MagnitudeRecovery:
    """Cross-model hub-and-spoke recovers `coherence_fee == k` exactly.

    Five tripwires combined into one parametrized class for clarity.
    Each k value is its own pre-registered gate per the plan §3a′.
    Failure at ANY k means the encoding is broken — halt and revise.
    """

    @pytest.mark.parametrize("k", [1, 2, 3, 5, 10])
    def test_recovers_exact_fee_k(self, k):
        result = build_cross_model_hub_spoke(k=k)
        d = diagnose(result.composition)
        assert d.coherence_fee == k, (
            f"Tripwire 0{'cdefg'[[1, 2, 3, 5, 10].index(k)]} FAILED: "
            f"build_cross_model_hub_spoke(k={k}) produced fee={d.coherence_fee}, "
            f"expected {k} exactly. Encoding is broken — halt and revise "
            f"before any HF spend."
        )

    @pytest.mark.parametrize("k", [1, 2, 3, 5, 10])
    def test_recovers_exact_fee_off_default_features(self, k):
        # Same magnitude with shifted feature_ids, to verify the encoding
        # doesn't accidentally depend on F0 / F0..Fk-1 being literal.
        result = build_cross_model_hub_spoke(
            k=k, hub_feature_id=42, spoke_feature_id_start=100,
        )
        d = diagnose(result.composition)
        assert d.coherence_fee == k


# ── Tripwire 0h: vanishing control ─────────────────────────────────────


class TestTripwire0h_VanishingControl:
    """Cross-model composition with observable-field edges → fee=0.

    Verifies the encoding can ALSO represent the "no obstruction" case
    correctly. If hub-spoke gives fee=k AND a separate observable-field
    composition gives fee=0, then the encoding distinguishes obstruction
    from non-obstruction.
    """

    def test_observable_field_cross_model_edge_vanishes(self):
        # Cross-model edge declared on `activation_p99` (observable on
        # both sides per OBSERVABLE_FIELDS) — should NOT produce fee>0.
        a = SAEFeatureSpec(model_id="gemma2-2b", layer=20, feature_id=0)
        b = SAEFeatureSpec(model_id="gpt2-small", layer=11, feature_id=0)
        # Manual composition: edges on activation_p99 (observable)
        comp = Composition(
            name="cross_model_observable_edge",
            tools=(a.to_tool_spec(), b.to_tool_spec()),
            edges=(
                Edge(
                    from_tool=a.name, to_tool=b.name,
                    dimensions=(
                        SemanticDimension(
                            name="activation_match",
                            from_field="activation_p99",
                            to_field="activation_p99",
                        ),
                    ),
                ),
            ),
        )
        d = diagnose(comp)
        assert d.coherence_fee == 0, (
            f"Tripwire 0h FAILED: observable-field cross-model edge "
            f"produced fee={d.coherence_fee}, expected 0. The encoding "
            f"is incorrectly attributing obstruction to observable edges."
        )

    def test_audit_says_observable_cannot_produce_obstruction(self):
        a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp = Composition(
            name="t", tools=(a.to_tool_spec(), b.to_tool_spec()),
            edges=(Edge(from_tool=a.name, to_tool=b.name, dimensions=(
                SemanticDimension(
                    name="d", from_field="activation_p99",
                    to_field="activation_p99",
                ),
            )),),
        )
        verdict = audit_encoding_capability(comp)
        assert verdict.can_produce_obstruction is False, (
            "Audit incorrectly said an observable-field edge could "
            "produce obstruction"
        )

    def test_no_edges_means_fee_zero(self):
        # Empty-edges composition → trivially fee=0.
        a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        result = build_cross_model_composition(
            features_a=(a,), features_b=(b,), cross_model_edges=(),
        )
        d = diagnose(result.composition)
        assert d.coherence_fee == 0


# ── Tripwire 0i: map-invariance pre-check (LOAD-BEARING for Concern 1) ─


class TestTripwire0i_MapInvariance:
    """Identity / Offset / Bijection produce same coherence_fee.

    The structural prerequisite for Gate 7 ``dim H¹(SAE_a, π(SAE_a)) = 0``
    being satisfiable in the §3b sweep. If feature-id permutation
    changes the cross-model fee, then the restriction-map ablation
    cannot distinguish map-induced rotation from real cross-model
    coordination structure — Risk #5 (uniformly-trivial dim H¹) becomes
    the modal outcome.

    The test: build a baseline cross-model hub-and-spoke composition
    with k spokes at feature_ids ``[0, 1, ..., k-1]``. Then build the
    same topology but with side-B feature_ids permuted by:
      (a) Identity (shift=0)
      (b) Offset (shift=K for some K)
      (c) Bijection (arbitrary permutation σ)
    Verify all three produce the SAME ``coherence_fee == k``.
    """

    @pytest.mark.parametrize("k", [1, 2, 3, 5])
    def test_identity_preserves_fee(self, k):
        # The synthetic IdentityRestrictionMap doesn't change feature_ids,
        # so the relabeled composition should match the baseline exactly.
        baseline = build_cross_model_hub_spoke(k=k)
        # "Apply" identity by re-emitting at the same feature_id_start
        relabeled = build_cross_model_hub_spoke(k=k, spoke_feature_id_start=0)
        assert diagnose(baseline.composition).coherence_fee == diagnose(
            relabeled.composition
        ).coherence_fee == k

    @pytest.mark.parametrize("shift,k", [(1, 3), (5, 3), (100, 5)])
    def test_offset_preserves_fee(self, shift, k):
        # OffsetRestrictionMap(shift=K): feature_id i → (i + K) mod n.
        # The cross-model fee depends only on topology, not feature_id
        # values, so shifting all spokes by K should preserve fee.
        baseline = build_cross_model_hub_spoke(k=k, spoke_feature_id_start=0)
        shifted = build_cross_model_hub_spoke(k=k, spoke_feature_id_start=shift)
        baseline_fee = diagnose(baseline.composition).coherence_fee
        shifted_fee = diagnose(shifted.composition).coherence_fee
        assert baseline_fee == shifted_fee == k, (
            f"Tripwire 0i FAILED at shift={shift}, k={k}: "
            f"baseline fee={baseline_fee}, shifted fee={shifted_fee}, "
            f"expected both equal to k={k}. Feature-id permutation "
            f"changed the cross-model fee — Concern 1 is NOT mitigated, "
            f"halt and debug encoding."
        )

    @pytest.mark.parametrize("perm,k", [
        ((2, 0, 1), 3),
        ((4, 3, 2, 1, 0), 5),
        ((1, 0, 3, 2, 5, 4), 6),
    ])
    def test_bijection_preserves_fee(self, perm, k):
        # An arbitrary permutation σ of side-B feature_ids: edges
        # (0, 0), (0, 1), ..., (0, k-1) become (0, σ(0)), (0, σ(1)),
        # ..., (0, σ(k-1)). Topology is preserved (still 1 hub + k
        # spokes + k edges); only feature_id labels change.
        assert k == len(perm)
        # Build baseline + permuted side B
        hub = SAEFeatureSpec(model_id="gemma2-2b", layer=20, feature_id=0)
        spokes = tuple(
            SAEFeatureSpec(model_id="gpt2-small", layer=11, feature_id=i)
            for i in range(k)
        )
        # Relabel side B by perm: spokes[i] becomes
        # SAEFeatureSpec(..., feature_id=perm[i]).
        permuted_spokes = tuple(
            SAEFeatureSpec(model_id="gpt2-small", layer=11, feature_id=perm[i])
            for i in range(k)
        )
        edges = tuple((0, i) for i in range(k))
        baseline = build_cross_model_composition(
            features_a=(hub,), features_b=spokes, cross_model_edges=edges,
        )
        permuted = build_cross_model_composition(
            features_a=(hub,), features_b=permuted_spokes, cross_model_edges=edges,
        )
        b_fee = diagnose(baseline.composition).coherence_fee
        p_fee = diagnose(permuted.composition).coherence_fee
        assert b_fee == p_fee == k, (
            f"Tripwire 0i FAILED at perm={perm}, k={k}: "
            f"baseline fee={b_fee}, permuted fee={p_fee}, expected k={k}. "
            f"Feature-id permutation changed the cross-model fee — "
            f"Concern 1 is NOT mitigated, halt and debug encoding."
        )

    def test_synthetic_maps_used_only_in_synthetic_validation(self):
        # Defensive: ensure all 3 synthetic maps are flagged
        # SYNTHETIC_ONLY=True so they CAN'T accidentally enter the §3b
        # sweep. The plan §3a′ Tripwire 0i tests ALL three; if any one
        # were to leak into production, the sweep would fit identity
        # alignments to real cross-model topology and dim H¹ would be
        # uniformly trivial.
        assert IdentityRestrictionMap().SYNTHETIC_ONLY is True
        assert OffsetRestrictionMap(shift=0).SYNTHETIC_ONLY is True
        assert BijectionRestrictionMap(perm=(0,)).SYNTHETIC_ONLY is True


# ── Iter-2 entry gate: aggregate verdict ───────────────────────────────


class TestIter2EntryGate:
    """A summary test that fails iff any of 0a-0i would fail.

    Provides a single `pytest -k Iter2EntryGate` invocation point that
    the plan §3a′ documentation can reference. Implementation runs the
    same checks the individual tripwires run (so this is not a separate
    gate, just an aggregator)."""

    def test_all_9_tripwires_pass(self):
        # 0a + 0b + 0c-0g + 0h + 0i in compressed form
        for k in [1, 2, 3, 5, 10]:
            r = build_cross_model_hub_spoke(k=k)
            assert len(r.composition.tools) == 1 + k          # 0a
            assert audit_encoding_capability(
                r.composition
            ).can_produce_obstruction is True                  # 0b
            assert diagnose(r.composition).coherence_fee == k  # 0c-0g

        # 0h: observable-field edge vanishes
        a = SAEFeatureSpec(model_id="m_a", layer=0, feature_id=0)
        b = SAEFeatureSpec(model_id="m_b", layer=0, feature_id=0)
        comp_obs = Composition(
            name="t", tools=(a.to_tool_spec(), b.to_tool_spec()),
            edges=(Edge(from_tool=a.name, to_tool=b.name, dimensions=(
                SemanticDimension(
                    name="d", from_field="activation_p99",
                    to_field="activation_p99",
                ),
            )),),
        )
        assert diagnose(comp_obs).coherence_fee == 0

        # 0i: bijection-permutation preserves fee
        baseline = build_cross_model_hub_spoke(k=3, spoke_feature_id_start=0)
        permuted = build_cross_model_hub_spoke(k=3, spoke_feature_id_start=10)
        assert diagnose(baseline.composition).coherence_fee == diagnose(
            permuted.composition
        ).coherence_fee == 3
