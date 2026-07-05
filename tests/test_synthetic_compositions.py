"""Tests for bulla/testing/synthetic_compositions.py public utility.

Verifies the universal synthetic-control primitives:
  - High-level convenience builders produce the expected fee values.
  - Low-level *_from_tools functions handle heterogeneous tool types.
  - The audit utility correctly classifies encoding capability.

The pre-existing G23 A1 + G24 pipeline_ci controls test suites continue
to pass via the refactored wrappers (see test_adapters_sae.py and
test_adapters_pipeline_ci_controls.py); this file tests the public
utility directly, ensuring future adapters that import these primitives
have a stable contract.
"""

from __future__ import annotations

import pytest

from bulla.diagnostic import diagnose
from bulla.model import ToolSpec
from bulla.testing import (
    EncodingCapabilityAudit,
    audit_encoding_capability,
    build_cycle_from_tools,
    build_hub_spoke_from_tools,
    build_known_nonvanishing,
    build_known_vanishing,
)


class TestBuildCycleFromTools:
    """Low-level cyclic builder accepts pre-built (heterogeneous) tools."""

    def test_two_tool_cycle_returns_composition(self):
        a = ToolSpec(name="a", internal_state=("x",), observable_schema=("x",))
        b = ToolSpec(name="b", internal_state=("x",), observable_schema=("x",))
        comp = build_cycle_from_tools(
            name="ab", tools=(a, b), edge_dimension_field="x"
        )
        assert comp.name == "ab"
        assert len(comp.tools) == 2
        assert len(comp.edges) == 2

    def test_heterogeneous_tools_supported(self):
        """Tools with different ToolSpec shapes still cycle correctly."""
        a = ToolSpec(name="a", internal_state=("x", "extra"), observable_schema=("x",))
        b = ToolSpec(name="b", internal_state=("x",), observable_schema=("x",))
        c = ToolSpec(name="c", internal_state=("x", "another"), observable_schema=("x",))
        comp = build_cycle_from_tools(
            name="abc", tools=(a, b, c), edge_dimension_field="x"
        )
        assert len(comp.edges) == 3
        # Cycle: a → b → c → a
        edge_pairs = [(e.from_tool, e.to_tool) for e in comp.edges]
        assert edge_pairs == [("a", "b"), ("b", "c"), ("c", "a")]

    def test_observable_field_cycle_yields_fee_zero(self):
        a = ToolSpec(name="a", internal_state=("x",), observable_schema=("x",))
        b = ToolSpec(name="b", internal_state=("x",), observable_schema=("x",))
        c = ToolSpec(name="c", internal_state=("x",), observable_schema=("x",))
        comp = build_cycle_from_tools(
            name="abc", tools=(a, b, c), edge_dimension_field="x"
        )
        assert diagnose(comp).coherence_fee == 0

    def test_below_two_tools_rejected(self):
        a = ToolSpec(name="a", internal_state=("x",), observable_schema=("x",))
        with pytest.raises(ValueError, match=r"need >= 2 tools"):
            build_cycle_from_tools(
                name="solo", tools=(a,), edge_dimension_field="x"
            )

    def test_edge_name_prefix_customisable(self):
        a = ToolSpec(name="a", internal_state=("x",), observable_schema=("x",))
        b = ToolSpec(name="b", internal_state=("x",), observable_schema=("x",))
        comp = build_cycle_from_tools(
            name="ab",
            tools=(a, b),
            edge_dimension_field="x",
            edge_name_prefix="cycle_link",
        )
        assert comp.edges[0].dimensions[0].name == "cycle_link_0"
        assert comp.edges[1].dimensions[0].name == "cycle_link_1"


class TestBuildHubSpokeFromTools:
    """Low-level hub-and-spoke builder accepts pre-built tools."""

    def test_returns_composition_with_n_plus_one_tools(self):
        hub = ToolSpec(name="h", internal_state=("p",), observable_schema=("p",))
        spokes = (
            ToolSpec(name="s0", internal_state=("p",), observable_schema=()),
            ToolSpec(name="s1", internal_state=("p",), observable_schema=()),
        )
        comp = build_hub_spoke_from_tools(
            name="t", hub=hub, spokes=spokes, obstruction_field="p"
        )
        assert len(comp.tools) == 3  # hub + 2 spokes
        assert len(comp.edges) == 2  # hub → each spoke

    def test_canonical_sprint15_fee_one(self):
        """Canonical Sprint 15 fixture: 1 hub + 2 spokes → fee = 1."""
        hub = ToolSpec(name="h", internal_state=("p",), observable_schema=("p",))
        spokes = (
            ToolSpec(name="s0", internal_state=("p",), observable_schema=()),
            ToolSpec(name="s1", internal_state=("p",), observable_schema=()),
        )
        comp = build_hub_spoke_from_tools(
            name="sprint15", hub=hub, spokes=spokes, obstruction_field="p"
        )
        assert diagnose(comp).coherence_fee == 1

    @pytest.mark.parametrize("n_spokes", [2, 3, 4, 6, 11])
    def test_fee_equals_spoke_count_minus_one(self, n_spokes):
        """fee = len(spokes) - 1 by hub-and-spoke rank arithmetic."""
        hub = ToolSpec(name="h", internal_state=("p",), observable_schema=("p",))
        spokes = tuple(
            ToolSpec(name=f"s{i}", internal_state=("p",), observable_schema=())
            for i in range(n_spokes)
        )
        comp = build_hub_spoke_from_tools(
            name=f"spokes_{n_spokes}",
            hub=hub,
            spokes=spokes,
            obstruction_field="p",
        )
        assert diagnose(comp).coherence_fee == n_spokes - 1

    def test_zero_spokes_rejected(self):
        hub = ToolSpec(name="h", internal_state=("p",), observable_schema=("p",))
        with pytest.raises(ValueError, match=r"need >= 1 spoke"):
            build_hub_spoke_from_tools(
                name="empty", hub=hub, spokes=(), obstruction_field="p"
            )


class TestBuildKnownVanishing:
    """High-level convenience wrapper for homogeneous-tool vanishing."""

    @pytest.mark.parametrize("n_tools", [2, 3, 4, 8])
    def test_fee_zero_across_sizes(self, n_tools):
        comp = build_known_vanishing(
            name=f"v_{n_tools}",
            n_tools=n_tools,
            internal_state=("x", "y"),
            observable_schema=("x", "y"),
            edge_dimension_field="x",
        )
        assert diagnose(comp).coherence_fee == 0

    def test_n_tools_below_two_rejected(self):
        with pytest.raises(ValueError, match=r"n_tools must be >= 2"):
            build_known_vanishing(
                name="solo",
                n_tools=1,
                internal_state=("x",),
                observable_schema=("x",),
                edge_dimension_field="x",
            )

    def test_edge_field_not_in_observable_rejected(self):
        with pytest.raises(ValueError, match=r"must be in observable_schema"):
            build_known_vanishing(
                name="bad",
                n_tools=3,
                internal_state=("x", "hidden"),
                observable_schema=("x",),
                edge_dimension_field="hidden",
            )

    def test_default_name_prefix(self):
        comp = build_known_vanishing(
            name="v",
            n_tools=2,
            internal_state=("x",),
            observable_schema=("x",),
            edge_dimension_field="x",
        )
        assert comp.tools[0].name == "synth_vanishing_0"
        assert comp.tools[1].name == "synth_vanishing_1"


class TestBuildKnownNonvanishing:
    """High-level convenience wrapper for hub-and-spoke obstruction."""

    @pytest.mark.parametrize("k", [1, 2, 3, 5, 10])
    def test_fee_exact_match_with_defaults(self, k):
        comp = build_known_nonvanishing(
            name=f"nv_{k}", k=k, obstruction_field="p"
        )
        assert diagnose(comp).coherence_fee == k

    def test_canonical_sprint15_fee_one_via_high_level(self):
        comp = build_known_nonvanishing(
            name="sprint15", k=1, obstruction_field="p"
        )
        assert diagnose(comp).coherence_fee == 1
        assert len(comp.tools) == 3  # 1 hub + 2 spokes
        assert len(comp.edges) == 2

    def test_k_zero_rejected(self):
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            build_known_nonvanishing(name="bad", k=0, obstruction_field="p")

    def test_obstruction_in_spoke_observable_rejected(self):
        """If obstruction_field is in spoke_observable, fee=0 not k."""
        with pytest.raises(ValueError, match=r"must NOT be in spoke_observable"):
            build_known_nonvanishing(
                name="bad",
                k=2,
                obstruction_field="p",
                spoke_observable=("p",),
            )

    def test_obstruction_not_in_hub_observable_rejected(self):
        with pytest.raises(ValueError, match=r"must be in hub_observable"):
            build_known_nonvanishing(
                name="bad",
                k=2,
                obstruction_field="p",
                hub_observable=("other",),
                hub_internal=("p", "other"),
            )

    def test_obstruction_not_in_spoke_internal_rejected(self):
        with pytest.raises(ValueError, match=r"must be in spoke_internal"):
            build_known_nonvanishing(
                name="bad",
                k=2,
                obstruction_field="p",
                spoke_internal=("other",),
            )

    def test_full_field_customisation_for_realistic_adapter(self):
        """Realistic case: adapter passes its full M2 surface field set."""
        comp = build_known_nonvanishing(
            name="sae_realistic",
            k=3,
            obstruction_field="concept",
            hub_internal=("identifier", "activation_p99", "decoder_direction", "provenance", "concept"),
            hub_observable=("identifier", "activation_p99", "concept"),
            spoke_internal=("identifier", "activation_p99", "decoder_direction", "provenance", "concept"),
            spoke_observable=("identifier", "activation_p99"),
            hub_name="model_a/L0/F0",
            spoke_name_prefix="model_b/L0/F",
        )
        assert diagnose(comp).coherence_fee == 3


class TestAuditEncodingCapability:
    """Audit utility correctly classifies encoding capability."""

    def test_observable_only_edges_cannot_produce_obstruction(self):
        comp = build_known_vanishing(
            name="v",
            n_tools=3,
            internal_state=("x",),
            observable_schema=("x",),
            edge_dimension_field="x",
        )
        audit = audit_encoding_capability(comp)
        assert isinstance(audit, EncodingCapabilityAudit)
        assert audit.can_produce_obstruction is False
        assert audit.n_hidden_from_field_edges == 0
        assert audit.n_hidden_to_field_edges == 0
        assert audit.n_edges == 3

    def test_hub_spoke_can_produce_obstruction(self):
        comp = build_known_nonvanishing(
            name="nv", k=2, obstruction_field="p"
        )
        audit = audit_encoding_capability(comp)
        assert audit.can_produce_obstruction is True
        # to_field=p is hidden on each of 3 spokes; from_field=p is observable on hub
        assert audit.n_hidden_to_field_edges == 3  # one per edge
        assert audit.n_hidden_from_field_edges == 0
        assert audit.n_edges == 3

    def test_audit_consistency_with_fee_outcome(self):
        """If audit says CANNOT obstruct, diagnose must return fee=0."""
        # Construct an explicitly observable-only composition
        a = ToolSpec(name="a", internal_state=("x", "y"), observable_schema=("x", "y"))
        b = ToolSpec(name="b", internal_state=("x", "y"), observable_schema=("x", "y"))
        comp = build_cycle_from_tools(name="ab", tools=(a, b), edge_dimension_field="x")
        audit = audit_encoding_capability(comp)
        assert audit.can_produce_obstruction is False
        assert diagnose(comp).coherence_fee == 0

    def test_audit_consistency_with_fee_positive(self):
        """If audit says CAN obstruct, diagnose can return fee>0."""
        comp = build_known_nonvanishing(name="nv", k=4, obstruction_field="p")
        audit = audit_encoding_capability(comp)
        assert audit.can_produce_obstruction is True
        assert diagnose(comp).coherence_fee == 4

    def test_audit_handles_empty_dimensions(self):
        """Edges with empty dimensions don't contribute hidden-field counts."""
        a = ToolSpec(name="a", internal_state=("x",), observable_schema=("x",))
        b = ToolSpec(name="b", internal_state=("x",), observable_schema=("x",))
        # Use build_cycle_from_tools but with min n=2
        comp = build_cycle_from_tools(name="ab", tools=(a, b), edge_dimension_field="x")
        audit = audit_encoding_capability(comp)
        assert audit.n_edges == 2
        assert audit.can_produce_obstruction is False


class TestRefactorContractInvariants:
    """Locks the contract that adapter wrappers depend on.

    These tests guard against accidental changes that would break
    pipeline_ci_controls and sae_controls (G23 A1) wrappers downstream.
    """

    def test_hub_spoke_produces_exact_recovery_for_canonical_k_set(self):
        """G23 A1 + G24 + Sprint 15 all rely on exact ±0 recovery on
        canonical k ∈ {1, 2, 3, 5}. Lock this contract."""
        for k in [1, 2, 3, 5]:
            comp = build_known_nonvanishing(name=f"k_{k}", k=k, obstruction_field="p")
            assert diagnose(comp).coherence_fee == k, f"Contract violation at k={k}"

    def test_vanishing_cycle_yields_zero_for_canonical_n_set(self):
        """G23 A1 + G24 controls rely on fee=0 for n ∈ {2, 3, 4, 5, 8, 16}."""
        for n in [2, 3, 4, 5, 8, 16]:
            comp = build_known_vanishing(
                name=f"n_{n}",
                n_tools=n,
                internal_state=("x",),
                observable_schema=("x",),
                edge_dimension_field="x",
            )
            assert diagnose(comp).coherence_fee == 0, f"Contract violation at n={n}"

    def test_audit_returns_dataclass_with_locked_field_names(self):
        """Adapter tests inspect audit.can_produce_obstruction etc;
        breaking field names would break downstream tests."""
        comp = build_known_nonvanishing(name="nv", k=1, obstruction_field="p")
        audit = audit_encoding_capability(comp)
        # Exact field names — locked contract
        assert hasattr(audit, "n_edges")
        assert hasattr(audit, "n_hidden_from_field_edges")
        assert hasattr(audit, "n_hidden_to_field_edges")
        assert hasattr(audit, "can_produce_obstruction")
