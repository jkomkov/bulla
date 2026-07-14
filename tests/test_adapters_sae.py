"""Tests for bulla/adapters/sae.py and sae_controls.py (G23 Stage A A1).

Two control families validate the SAE adapter pipeline before any
real-data spend:

    1. Known-vanishing control (``build_known_vanishing_control``):
       same-model identity composition; ``coherence_fee == 0``.

    2. Known-non-vanishing positive control
       (``build_known_nonvanishing_control``): hub-and-spoke with
       designed ``coherence_fee == k`` for k ∈ {1, 2, 3, 5}; pipeline
       must recover k EXACTLY (±0 tolerance).

Per the G23 plan
gate criteria for Stage A → Stage B PROMOTE:
    Gate 2: same-model identity → H¹ = 0 (known-vanishing) on ≥ 95% of cases
    Gate 3: known-non-vanishing toy → recovers exact H¹ = k on
            k ∈ {1, 2, 3, 5}, ±0 tolerance.

Both gates are tested here.
"""

from __future__ import annotations

import pytest

from bulla.adapters.sae import (
    INTERNAL_FIELDS,
    OBSERVABLE_FIELDS,
    SAEFeatureSpec,
)
from bulla.adapters.sae_controls import (
    build_known_nonvanishing_control,
    build_known_vanishing_control,
)
from bulla.diagnostic import diagnose


class TestSAEFeatureSpec:
    """SAE feature → ToolSpec lifter tests."""

    def test_canonical_name_format(self):
        spec = SAEFeatureSpec(model_id="gemma2-2b", layer=20, feature_id=1234)
        assert spec.name == "gemma2-2b/L20/F1234"

    def test_to_tool_spec_has_natural_m2_surface(self):
        spec = SAEFeatureSpec(model_id="gpt2-small", layer=11, feature_id=42)
        tool = spec.to_tool_spec()
        assert tool.name == "gpt2-small/L11/F42"
        assert tool.internal_state == INTERNAL_FIELDS
        assert tool.observable_schema == OBSERVABLE_FIELDS

    def test_observable_subset_of_internal_sprint9_invariant(self):
        """Sprint 9 schema-shape invariant: observable_schema ⊆ internal_state."""
        tool = SAEFeatureSpec("m", 0, 0).to_tool_spec()
        assert set(tool.observable_schema).issubset(set(tool.internal_state))

    def test_hidden_schema_is_decoder_direction_and_provenance(self):
        tool = SAEFeatureSpec("m", 0, 0).to_tool_spec()
        assert set(tool.hidden_schema) == {"decoder_direction", "provenance"}

    def test_equal_specs_lift_equal_tools(self):
        a = SAEFeatureSpec("m", 0, 0).to_tool_spec()
        b = SAEFeatureSpec("m", 0, 0).to_tool_spec()
        assert a == b

    def test_different_specs_lift_different_tools(self):
        a = SAEFeatureSpec("m", 0, 0).to_tool_spec()
        b = SAEFeatureSpec("m", 0, 1).to_tool_spec()
        assert a != b
        assert a.name != b.name


class TestKnownVanishingControl:
    """A1.1: same-model identity composition; coherence_fee == 0."""

    @pytest.mark.parametrize("n_features", [2, 3, 4, 5, 8, 16])
    def test_vanishing_fee_zero_across_sizes(self, n_features):
        comp = build_known_vanishing_control(n_features=n_features)
        diag = diagnose(comp)
        assert diag.coherence_fee == 0, (
            f"Same-model identity composition with n_features={n_features} "
            f"produced coherence_fee={diag.coherence_fee}; expected 0. "
            f"This indicates the SAE adapter produces spurious obstruction "
            f"on identity restrictions — modeling pipeline is broken."
        )

    def test_vanishing_has_no_blind_spots(self):
        diag = diagnose(build_known_vanishing_control(n_features=4))
        assert diag.blind_spots == ()

    def test_vanishing_betti_one_is_one_cyclic_structure(self):
        """The vanishing control IS cyclic (β_1 = 1) — fee=0 despite the cycle."""
        diag = diagnose(build_known_vanishing_control(n_features=4))
        assert diag.betti_1 == 1, (
            f"Expected cyclic structure (β_1 = 1); got β_1 = {diag.betti_1}. "
            f"A linear chain (β_1 = 0) would test fee=0 trivially; the "
            f"cycle structure is required to test the harder case where "
            f"a buggy lifter could produce spurious obstruction."
        )

    def test_vanishing_n_features_below_two_rejected(self):
        with pytest.raises(ValueError, match=r"n_features must be >= 2"):
            build_known_vanishing_control(n_features=1)

    def test_vanishing_95_percent_pass_rate_gate2(self):
        """Stage A → B PROMOTE gate 2: ≥ 95% pass rate.

        We test 100 different cyclic identity compositions (varying
        n_features and naming) and require ≥ 95 to yield fee=0. Since
        the construction is deterministic, the actual rate must be 100%
        if the lifter is correct.
        """
        n_pass = 0
        n_total = 100
        for trial in range(n_total):
            n_features = 2 + (trial % 15)  # n_features in [2, 16]
            comp = build_known_vanishing_control(
                n_features=n_features,
                model_id=f"trial-{trial}",
                layer=trial % 12,
            )
            if diagnose(comp).coherence_fee == 0:
                n_pass += 1
        pass_rate = n_pass / n_total
        assert pass_rate >= 0.95, (
            f"Gate 2 requires ≥ 95% pass rate on identity compositions; "
            f"got {pass_rate*100:.1f}% ({n_pass}/{n_total}). "
            f"For a correct lifter, the rate must be 100%."
        )


class TestKnownNonvanishingControl:
    """A1.2: hub-and-spoke composition; coherence_fee == k EXACTLY."""

    @pytest.mark.parametrize("k", [1, 2, 3, 5])
    def test_nonvanishing_fee_exact_match(self, k):
        """Stage A → B PROMOTE gate 3: ±0 tolerance on designed fee = k."""
        comp = build_known_nonvanishing_control(k=k)
        diag = diagnose(comp)
        assert diag.coherence_fee == k, (
            f"Hub-and-spoke control with designed k={k} produced "
            f"coherence_fee={diag.coherence_fee}; expected EXACTLY {k} (±0). "
            f"This indicates a bug in the SAE adapter's restriction-map "
            f"lifting OR in the witness-geometry rank computation. "
            f"Modeling soundness is broken; cannot proceed to A2."
        )

    def test_nonvanishing_k_one_matches_sprint15(self):
        """k=1 must reproduce Sprint 15 hub-and-spoke fee=1 exactly."""
        comp = build_known_nonvanishing_control(k=1)
        diag = diagnose(comp)
        assert diag.coherence_fee == 1
        # Sprint 15 structure: 1 hub + 2 spokes (k+1 = 2)
        assert diag.n_tools == 3
        assert diag.n_edges == 2

    def test_nonvanishing_spoke_count_is_k_plus_one(self):
        """The construction uses (k+1) spokes to produce fee = k."""
        for k in [1, 2, 3, 5]:
            comp = build_known_nonvanishing_control(k=k)
            assert comp.tools[0].name.startswith("synthetic-model-a")  # hub
            n_spokes = sum(
                1 for t in comp.tools if t.name.startswith("synthetic-model-b")
            )
            assert n_spokes == k + 1, (
                f"Expected k+1 = {k+1} spokes for fee=k={k}; got {n_spokes}"
            )

    def test_nonvanishing_k_zero_rejected(self):
        """k=0 is the vanishing case; reject to force using the right control."""
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            build_known_nonvanishing_control(k=0)

    def test_nonvanishing_negative_k_rejected(self):
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            build_known_nonvanishing_control(k=-1)

    def test_nonvanishing_blind_spots_match_spoke_count(self):
        """Each hidden spoke field that was claimed in an edge is a blind spot."""
        comp = build_known_nonvanishing_control(k=3)
        diag = diagnose(comp)
        # k+1 = 4 spokes; each hidden 'concept' field claimed via an edge
        # is a blind spot.
        assert len(diag.blind_spots) == 4
        for bs in diag.blind_spots:
            assert bs.dimension.startswith("concept_match_")
            assert bs.to_hidden  # spoke side is hidden

    def test_nonvanishing_extends_beyond_canonical_set(self):
        """k=10 still recovers exactly. Pipeline scales to larger fees."""
        comp = build_known_nonvanishing_control(k=10)
        diag = diagnose(comp)
        assert diag.coherence_fee == 10


class TestBothControlsTogether:
    """Joint A1 gate: both controls must pass for Stage A2 to proceed."""

    def test_a1_gate_both_controls_pass(self):
        """The atomic 'A1 PASS' check.

        If this test passes, the SAE adapter is sound for known-vanishing
        AND known-non-vanishing inputs at the canonical k values. Stage
        A2 (single-model multi-layer prototype) is unblocked.
        """
        # Vanishing: identity composition → fee = 0
        vanishing = build_known_vanishing_control(n_features=4)
        assert diagnose(vanishing).coherence_fee == 0, "A1.1 vanishing FAILED"

        # Non-vanishing: designed k recovered exactly across the canonical set
        for k in [1, 2, 3, 5]:
            nonvanishing = build_known_nonvanishing_control(k=k)
            assert diagnose(nonvanishing).coherence_fee == k, (
                f"A1.2 non-vanishing FAILED at k={k}: "
                f"expected fee={k}, got {diagnose(nonvanishing).coherence_fee}"
            )
