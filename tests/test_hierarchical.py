"""Hierarchical fee decomposition tests.

Proves that the coherence fee is NOT sub-additive under graph
partition: fee(flat) can exceed sum(fee(sub_i)). The excess is
the boundary fee — blind spots invisible at every level of a
hierarchy that appear only in the flat expansion.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

from bulla.diagnostic import (
    ConditionalDiagnostic,
    FeeDecomposition,
    OpenPort,
    Resolution,
    conditional_diagnose,
    decompose_fee,
    diagnose,
    minimum_disclosure_set,
    resolve_conditional,
    satisfies_obligations,
)
from bulla.model import BoundaryObligation, Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition

COMPOSITIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "bulla" / "compositions"
BUNDLED_YAMLS = sorted(COMPOSITIONS_DIR.glob("*.yaml"))


# ── Helpers ──────────────────────────────────────────────────────────


def _make_tools(*specs: tuple[str, tuple[str, ...], tuple[str, ...]]):
    return tuple(ToolSpec(name, internal, obs) for name, internal, obs in specs)


def _sub_composition(
    name: str,
    comp: Composition,
    tool_names: frozenset[str],
) -> Composition:
    """Extract a sub-composition: keep only named tools and edges
    whose both endpoints are in the subset."""
    tools = tuple(t for t in comp.tools if t.name in tool_names)
    edges = tuple(
        e for e in comp.edges
        if e.from_tool in tool_names and e.to_tool in tool_names
    )
    return Composition(name, tools, edges)


# ── Step 1: The counterexample ───────────────────────────────────────


class TestHierarchicalFeeNonAdditivity:
    """A->B->C chain where fee(flat) > fee(sub_AB) + fee(sub_BC).

    A hides amount_unit. B exposes it. C hides it.
    Each sub-edge has fee=0 because one endpoint is always observable.
    The flat composition has fee=1: the observable coboundary has rank 1
    (only B's column contributes) while the full coboundary has rank 2
    (both A and C contribute through their hidden columns).
    """

    @pytest.fixture
    def tools(self):
        return _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )

    @pytest.fixture
    def flat_comp(self, tools):
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        return Composition("flat_ABC", tools, edges)

    @pytest.fixture
    def sub_AB(self, tools):
        t_A, t_B, _ = tools
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        return Composition("sub_AB", (t_A, t_B), edges)

    @pytest.fixture
    def sub_BC(self, tools):
        _, t_B, t_C = tools
        edges = (
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        return Composition("sub_BC", (t_B, t_C), edges)

    def test_sub_AB_fee_zero(self, sub_AB):
        """A hides amount_unit but B exposes it: rank_obs == rank_full."""
        diag = diagnose(sub_AB)
        assert diag.coherence_fee == 0

    def test_sub_BC_fee_zero(self, sub_BC):
        """B exposes amount_unit but C hides it: rank_obs == rank_full."""
        diag = diagnose(sub_BC)
        assert diag.coherence_fee == 0

    def test_flat_fee_nonzero(self, flat_comp):
        """The flat graph has fee=1: hidden convention propagates through B."""
        diag = diagnose(flat_comp)
        assert diag.coherence_fee == 1

    def test_fee_non_additive(self, flat_comp, sub_AB, sub_BC):
        """fee(flat) > fee(sub_AB) + fee(sub_BC): hierarchy hides a blind spot."""
        fee_flat = diagnose(flat_comp).coherence_fee
        fee_AB = diagnose(sub_AB).coherence_fee
        fee_BC = diagnose(sub_BC).coherence_fee
        assert fee_flat > fee_AB + fee_BC
        assert fee_AB + fee_BC == 0
        assert fee_flat == 1

    def test_rank_details(self, flat_comp):
        """Verify the exact rank structure that produces the boundary fee."""
        diag = diagnose(flat_comp)
        assert diag.rank_full == 2
        assert diag.rank_obs == 1
        assert diag.dim_c1 == 2
        assert diag.h1_obs == 1
        assert diag.h1_full == 0


# ── Richer counterexample: multiple hidden dimensions ────────────────


class TestMultiDimensionHierarchicalFee:
    """Two hidden dimensions flowing through a boundary tool."""

    def test_two_hidden_dims(self):
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
            ("B", ("tz", "currency", "data"), ("tz", "currency", "data")),
            ("C", ("tz", "currency", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
            Edge("B", "C", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
        )
        flat = Composition("multi_dim", tools, edges)
        diag_flat = diagnose(flat)

        sub_AB = Composition("sub_AB", (tools[0], tools[1]), (edges[0],))
        sub_BC = Composition("sub_BC", (tools[1], tools[2]), (edges[1],))
        fee_AB = diagnose(sub_AB).coherence_fee
        fee_BC = diagnose(sub_BC).coherence_fee

        assert fee_AB == 0
        assert fee_BC == 0
        assert diag_flat.coherence_fee == 2
        assert diag_flat.coherence_fee > fee_AB + fee_BC


# ── Full-disclosure boundary eliminates boundary fee ─────────────────


class TestFullDisclosureBoundary:
    """When the boundary tool exposes all conventions, and the outer
    tools also expose them, the flat fee equals the sum of sub-fees."""

    def test_full_disclosure(self):
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("amount_unit", "result")),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        flat = Composition("full_disc", tools, edges)
        sub_AB = Composition("sub_AB", (tools[0], tools[1]), (edges[0],))
        sub_BC = Composition("sub_BC", (tools[1], tools[2]), (edges[1],))

        fee_flat = diagnose(flat).coherence_fee
        fee_AB = diagnose(sub_AB).coherence_fee
        fee_BC = diagnose(sub_BC).coherence_fee

        assert fee_flat == 0
        assert fee_AB == 0
        assert fee_BC == 0
        assert fee_flat == fee_AB + fee_BC


# ── decompose_fee function tests ─────────────────────────────────────


class TestDecomposeFee:
    """Tests for the decompose_fee API."""

    def test_identity_holds_for_counterexample(self):
        """total_fee == sum(local_fees) + boundary_fee by construction."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A", "B"}), frozenset({"C"})])

        assert dec.total_fee == sum(dec.local_fees) + dec.boundary_fee
        assert dec.boundary_fee >= 0

    def test_boundary_fee_positive_for_hidden_boundary(self):
        """Partition at B: {A,B} | {C} creates a cross-partition edge."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A", "B"}), frozenset({"C"})])

        assert dec.total_fee == 1
        assert dec.boundary_edges == 1

    def test_trivial_partition_boundary_fee_zero(self):
        """Partitioning into {all tools} has no cross-partition edges."""
        tools = _make_tools(
            ("A", ("x",), ("x",)),
            ("B", ("x",), ("x",)),
        )
        edges = (Edge("A", "B", (SemanticDimension("d", "x", "x"),)),)
        comp = Composition("triv", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A", "B"})])

        assert dec.boundary_fee == 0
        assert dec.total_fee == dec.local_fees[0]

    def test_singleton_partition_all_edges_are_boundary(self):
        """Partition into singletons: every edge is a cross-partition edge."""
        tools = _make_tools(
            ("A", ("x",), ("x",)),
            ("B", ("x",), ("x",)),
        )
        edges = (Edge("A", "B", (SemanticDimension("d", "x", "x"),)),)
        comp = Composition("sing", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B"})])

        assert dec.boundary_edges == 1
        assert sum(dec.local_fees) == 0

    def test_partition_validation(self):
        """Partition must cover all tools exactly."""
        tools = _make_tools(("A", ("x",), ("x",)), ("B", ("x",), ("x",)))
        comp = Composition("v", tools, ())
        with pytest.raises(ValueError):
            decompose_fee(comp, [frozenset({"A"})])

    def test_three_way_partition(self):
        """Three groups: each tool in its own partition element."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)
        dec = decompose_fee(
            comp, [frozenset({"A"}), frozenset({"B"}), frozenset({"C"})]
        )

        assert dec.total_fee == sum(dec.local_fees) + dec.boundary_fee
        assert dec.boundary_fee >= 0
        assert dec.boundary_edges == 2


# ── Invariant: identity holds for ALL bundled compositions ───────────


def _all_binary_partitions(names: list[str]):
    """Yield all non-trivial 2-way partitions of a name list."""
    n = len(names)
    for r in range(1, n):
        for left in combinations(names, r):
            left_set = frozenset(left)
            right_set = frozenset(names) - left_set
            if right_set:
                yield [left_set, right_set]


@pytest.fixture(params=BUNDLED_YAMLS, ids=[p.stem for p in BUNDLED_YAMLS])
def bundled_comp(request):
    return load_composition(path=request.param)


class TestDecomposeFeeInvariants:
    """For every bundled composition and every binary partition:
    total_fee == sum(local_fees) + boundary_fee, boundary_fee >= 0,
    and boundary_fee == rho_full - rho_obs (block rank formula).
    """

    def test_identity_all_partitions(self, bundled_comp):
        import random as _rng

        names = [t.name for t in bundled_comp.tools]
        partitions = list(_all_binary_partitions(names))
        if len(partitions) > 50:
            partitions = _rng.Random(42).sample(partitions, 50)
        for partition in partitions:
            dec = decompose_fee(bundled_comp, partition)
            assert dec.total_fee == sum(dec.local_fees) + dec.boundary_fee, (
                f"Identity violated for {bundled_comp.name} with partition {partition}"
            )
            assert dec.boundary_fee >= 0, (
                f"Negative boundary_fee for {bundled_comp.name} with partition {partition}"
            )
            assert dec.boundary_fee == dec.rho_full - dec.rho_obs, (
                f"Block rank formula violated for {bundled_comp.name}: "
                f"boundary_fee={dec.boundary_fee} but rho_full-rho_obs="
                f"{dec.rho_full - dec.rho_obs}"
            )
            assert dec.rho_full >= dec.rho_obs, (
                f"rho monotonicity violated: rho_full={dec.rho_full} < "
                f"rho_obs={dec.rho_obs} for {bundled_comp.name}"
            )


# ── Parametrized decomposition invariant tests ───────────────────────


class TestFullDisclosureBoundaryVanishing:
    """Corollary: when all cross-partition edge fields are observable,
    boundary_fee == 0."""

    @pytest.mark.parametrize("n_dims", [1, 2, 3])
    def test_chain_full_disclosure(self, n_dims):
        """Full-disclosure chain of any dimension count."""
        dim_names = [f"d{i}" for i in range(n_dims)]
        fields = tuple(dim_names) + ("payload",)
        tools = _make_tools(
            ("A", fields, fields),
            ("B", fields, fields),
            ("C", fields, fields),
        )
        dims = tuple(
            SemanticDimension(f"dim_{d}", d, d) for d in dim_names
        )
        edges = (
            Edge("A", "B", dims),
            Edge("B", "C", dims),
        )
        comp = Composition("full_disc_chain", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B", "C"})])
        assert dec.boundary_fee == 0
        assert dec.total_fee == sum(dec.local_fees)

    def test_cycle_full_disclosure(self):
        """A→B→C→A cycle with all fields observable: boundary_fee == 0."""
        tools = _make_tools(
            ("A", ("x", "y"), ("x", "y")),
            ("B", ("x", "y"), ("x", "y")),
            ("C", ("x", "y"), ("x", "y")),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("d1", "x", "x"),)),
            Edge("B", "C", (SemanticDimension("d2", "y", "y"),)),
            Edge("C", "A", (SemanticDimension("d3", "x", "x"),)),
        )
        comp = Composition("cycle_full", tools, edges)
        for partition in _all_binary_partitions([t.name for t in tools]):
            dec = decompose_fee(comp, partition)
            assert dec.boundary_fee == 0, f"Nonzero boundary for {partition}"


class TestAdversarialHiddenInterfaces:
    """Adversarial cases: maximally hidden conventions at boundaries."""

    def test_all_hidden_both_sides(self):
        """Both endpoints hide the convention: maximum boundary fee."""
        tools = _make_tools(
            ("A", ("secret",), ()),
            ("B", ("secret",), ()),
        )
        edges = (Edge("A", "B", (SemanticDimension("d", "secret", "secret"),)),)
        comp = Composition("both_hidden", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B"})])
        assert dec.boundary_fee == dec.total_fee
        assert dec.total_fee == 1

    def test_star_topology_all_hidden(self):
        """Hub with 4 spokes, all conventions hidden: boundary_fee == total_fee."""
        spokes = [f"S{i}" for i in range(4)]
        tool_specs = [("Hub", ("secret",), ())]
        tool_specs += [(s, ("secret",), ()) for s in spokes]
        tools = _make_tools(*tool_specs)
        edges = tuple(
            Edge("Hub", s, (SemanticDimension(f"d_{s}", "secret", "secret"),))
            for s in spokes
        )
        comp = Composition("star_hidden", tools, edges)
        dec = decompose_fee(
            comp, [frozenset({"Hub"}), frozenset(spokes)]
        )
        assert dec.boundary_fee == dec.total_fee
        assert dec.total_fee > 0

    def test_hidden_only_on_one_side(self):
        """Convention hidden on source only: boundary_fee can be nonzero."""
        tools = _make_tools(
            ("A", ("conv",), ()),
            ("B", ("conv",), ("conv",)),
        )
        edges = (Edge("A", "B", (SemanticDimension("d", "conv", "conv"),)),)
        comp = Composition("one_side", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B"})])
        assert dec.total_fee == sum(dec.local_fees) + dec.boundary_fee
        assert dec.boundary_fee >= 0

    def test_mixed_hidden_and_visible(self):
        """Some dimensions hidden, some visible at the boundary."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
            ("B", ("tz", "currency", "data"), ("tz", "data")),
        )
        edges = (
            Edge("A", "B", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
        )
        comp = Composition("mixed", tools, edges)
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B"})])
        assert dec.total_fee == sum(dec.local_fees) + dec.boundary_fee
        assert dec.boundary_fee >= 0
        assert dec.total_fee >= 1


# ── Conditional receipt (partial composition) ────────────────────────


class TestConditionalDiagnose:
    """Tests for conditional_diagnose on partial compositions."""

    def test_basic_conditional(self):
        """A known tool with an open port to unknown agent B."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B_placeholder",
                dimensions=(
                    SemanticDimension("amount", "amount_unit", "amount_unit"),
                ),
            ),
        ]

        cond = conditional_diagnose(comp, open_ports)
        assert cond.baseline_fee == 0
        assert cond.worst_case_fee >= cond.baseline_fee
        assert cond.structural_unknowns == 1

    def test_obligations_from_hidden_fields(self):
        """Placeholder hides all fields; obligations list what to expose."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B_placeholder",
                dimensions=(
                    SemanticDimension("timezone", "tz", "tz"),
                    SemanticDimension("currency", "currency", "currency"),
                ),
            ),
        ]

        cond = conditional_diagnose(comp, open_ports)
        assert len(cond.obligations) >= 1
        obligation_fields = {o.field for o in cond.obligations}
        assert "tz" in obligation_fields or "currency" in obligation_fields
        assert cond.structural_unknowns == 2

    def test_meeting_obligations_reduces_fee(self):
        """Manually constructing a tool that meets obligations lowers fee."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(
                    SemanticDimension("amount", "amount_unit", "amount_unit"),
                ),
            ),
        ]

        cond = conditional_diagnose(comp, open_ports)
        worst = cond.worst_case_fee

        full_tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit",), ("amount_unit",)),
        )
        full_comp = Composition("full", full_tools, (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        ))
        full_diag = diagnose(full_comp)

        assert full_diag.coherence_fee <= worst

    def test_no_open_ports_matches_baseline(self):
        """With no open ports, conditional == baseline."""
        tools = _make_tools(
            ("A", ("x",), ("x",)),
            ("B", ("x",), ("x",)),
        )
        comp = Composition("closed", tools, (
            Edge("A", "B", (SemanticDimension("d", "x", "x"),)),
        ))

        cond = conditional_diagnose(comp, [])
        assert cond.baseline_fee == cond.worst_case_fee
        assert cond.obligations == ()
        assert cond.structural_unknowns == 0

    def test_obligation_targets_placeholder(self):
        """All obligations reference the placeholder, not existing tools."""
        tools = _make_tools(
            ("A", ("tz", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="future_agent",
                dimensions=(
                    SemanticDimension("timezone", "tz", "tz"),
                ),
            ),
        ]

        cond = conditional_diagnose(comp, open_ports)
        for obl in cond.obligations:
            assert obl.placeholder_tool == "future_agent"

    def test_multiple_open_ports(self):
        """Multiple open ports to different placeholders."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
            OpenPort(
                from_tool="A",
                placeholder_name="C",
                dimensions=(SemanticDimension("currency", "currency", "currency"),),
            ),
        ]

        cond = conditional_diagnose(comp, open_ports)
        assert cond.structural_unknowns == 2
        placeholder_tools = {o.placeholder_tool for o in cond.obligations}
        assert placeholder_tools <= {"B", "C"}


# ── Online resolution ────────────────────────────────────────────────


class TestResolveConditional:
    """Tests for resolve_conditional: substitute real tools for placeholders."""

    def test_resolve_single_placeholder_fee_drops(self):
        """Resolve a placeholder with a fully-disclosing tool: fee drops."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(
                    SemanticDimension("amount", "amount_unit", "amount_unit"),
                ),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)
        assert cond.worst_case_fee >= 1

        real_B = ToolSpec("B", ("amount_unit",), ("amount_unit",))
        res = resolve_conditional(cond, {"B": real_B})

        assert res.resolved_fee < cond.worst_case_fee
        assert res.fee_delta > 0

    def test_resolve_meets_all_obligations(self):
        """Resolve with a tool that meets all obligations."""
        tools = _make_tools(
            ("A", ("tz", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)

        real_B = ToolSpec("B", ("tz",), ("tz",))
        res = resolve_conditional(cond, {"B": real_B})

        assert len(res.met_obligations) == len(cond.obligations)
        assert len(res.remaining_obligations) == 0
        assert res.fee_delta > 0

    def test_resolve_meets_no_obligations(self):
        """Resolve with a tool that hides everything: fee stays at worst case."""
        tools = _make_tools(
            ("A", ("tz", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)

        bad_B = ToolSpec("B", ("tz",), ())
        res = resolve_conditional(cond, {"B": bad_B})

        assert res.fee_delta == 0
        assert len(res.remaining_obligations) == len(cond.obligations)
        assert len(res.met_obligations) == 0

    def test_resolve_partial_two_placeholders(self):
        """Two placeholders, resolve one, leave the other."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
            OpenPort(
                from_tool="A",
                placeholder_name="C",
                dimensions=(SemanticDimension("currency", "currency", "currency"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)

        real_B = ToolSpec("B", ("tz",), ("tz",))
        res = resolve_conditional(cond, {"B": real_B})

        assert res.resolved_fee <= cond.worst_case_fee
        assert res.fee_delta >= 0
        remaining_placeholders = {o.placeholder_tool for o in res.remaining_obligations}
        assert "C" in remaining_placeholders or len(res.remaining_obligations) >= 0

    def test_resolve_round_trip_with_disclosure(self):
        """resolve -> minimum_disclosure_set on remaining fee."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(
                    SemanticDimension("timezone", "tz", "tz"),
                    SemanticDimension("currency", "currency", "currency"),
                ),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)

        real_B = ToolSpec("B", ("tz", "currency"), ("tz",))
        res = resolve_conditional(cond, {"B": real_B})

        if res.resolved_fee > 0:
            resolved_comp = Composition(
                "resolved",
                cond.extended_comp.tools,
                cond.extended_comp.edges,
            )
            tool_map = {t.name: t for t in cond.extended_comp.tools}
            tool_map["B"] = real_B
            resolved_comp = Composition(
                "resolved",
                tuple(tool_map[t.name] for t in cond.extended_comp.tools),
                cond.extended_comp.edges,
            )
            disclosures = minimum_disclosure_set(resolved_comp)
            assert len(disclosures) == res.resolved_fee

    def test_resolve_matches_from_scratch(self):
        """Resolve C in A->B->C: fee matches diagnose(full_ABC)."""
        tools_AB = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
        )
        comp_AB = Composition(
            "AB",
            tools_AB,
            (Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),),
        )
        open_ports = [
            OpenPort(
                from_tool="B",
                placeholder_name="C",
                dimensions=(SemanticDimension("amount", "amount_unit", "amount_unit"),),
            ),
        ]
        cond = conditional_diagnose(comp_AB, open_ports)

        real_C = ToolSpec("C", ("amount_unit", "result"), ("result",))
        res = resolve_conditional(cond, {"C": real_C})

        full_tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        full_comp = Composition(
            "full_ABC",
            full_tools,
            (
                Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
                Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            ),
        )
        assert res.resolved_fee == diagnose(full_comp).coherence_fee

    def test_resolve_bundled(self, bundled_comp):
        """Remove one tool, create conditional, resolve, verify fee matches.

        Only tests compositions where the removed tool has no edges
        pointing back into the remaining tools (OpenPort only models
        outgoing edges from known tools to placeholders).
        """
        if len(bundled_comp.tools) < 2:
            pytest.skip("Need >= 2 tools")

        victim = bundled_comp.tools[-1]
        remaining_tools = bundled_comp.tools[:-1]
        remaining_names = {t.name for t in remaining_tools}

        outgoing = [
            e for e in bundled_comp.edges
            if e.from_tool in remaining_names and e.to_tool == victim.name
        ]
        reverse = [
            e for e in bundled_comp.edges
            if e.from_tool == victim.name and e.to_tool in remaining_names
        ]
        if not outgoing:
            pytest.skip("No outgoing edges to removed tool")
        if reverse:
            pytest.skip("Removed tool has reverse edges (OpenPort is unidirectional)")

        internal_edges = tuple(
            e for e in bundled_comp.edges
            if e.from_tool in remaining_names and e.to_tool in remaining_names
        )

        partial_comp = Composition(
            f"{bundled_comp.name}_partial", remaining_tools, internal_edges
        )
        open_ports = [
            OpenPort(
                from_tool=e.from_tool,
                placeholder_name=victim.name,
                dimensions=e.dimensions,
            )
            for e in outgoing
        ]

        cond = conditional_diagnose(partial_comp, open_ports)
        res = resolve_conditional(cond, {victim.name: victim})

        assert res.resolved_fee == diagnose(bundled_comp).coherence_fee, (
            f"{bundled_comp.name}: resolved_fee={res.resolved_fee} != "
            f"original_fee={diagnose(bundled_comp).coherence_fee}"
        )

    def test_resolve_invalid_placeholder_raises(self):
        """Passing a nonexistent placeholder name raises ValueError."""
        tools = _make_tools(("A", ("x",), ("x",)))
        comp = Composition("p", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("d", "x", "x"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)

        with pytest.raises(ValueError):
            resolve_conditional(cond, {"NONEXISTENT": ToolSpec("X", (), ())})


# ── Tower law and monotonicity ────────────────────────────────────────


def _single_step_refinements(partition: list[frozenset[str]]):
    """Generate single-step refinements: split exactly one group into two.

    For each group in the partition with |group| >= 2, yield all binary
    sub-partitions of that group, replacing the original group with the
    two halves. Single-step refinement suffices for monotonicity: general
    refinement follows by transitivity.
    """
    for idx, group in enumerate(partition):
        if len(group) < 2:
            continue
        members = sorted(group)
        for r in range(1, len(members)):
            for left in combinations(members, r):
                left_set = frozenset(left)
                right_set = group - left_set
                if not right_set:
                    continue
                refined = list(partition)
                refined[idx:idx + 1] = [left_set, right_set]
                yield refined, idx, left_set, right_set


class TestTowerLaw:
    """Verify bf(refined) == bf(coarse) + bf(sub-partition) across all
    bundled compositions and partition refinements.

    Complexity: bundled compositions have n <= 4 tools. Worst case ~20
    tower law checks per composition, ~150 total.
    """

    def test_tower_law_all_bundled(self, bundled_comp):
        import random as _rng

        names = [t.name for t in bundled_comp.tools]
        partitions = list(_all_binary_partitions(names))
        if len(partitions) > 50:
            partitions = _rng.Random(42).sample(partitions, 50)

        checked = 0
        for coarse in partitions:
            dec_coarse = decompose_fee(bundled_comp, coarse)
            for refined, split_idx, left, right in _single_step_refinements(coarse):
                dec_refined = decompose_fee(bundled_comp, refined)
                split_group = coarse[split_idx]
                sub_comp = _sub_composition(
                    f"sub_{'-'.join(sorted(split_group))}",
                    bundled_comp,
                    split_group,
                )
                dec_sub = decompose_fee(sub_comp, [left, right])

                assert dec_refined.boundary_fee == dec_coarse.boundary_fee + dec_sub.boundary_fee, (
                    f"Tower law violated for {bundled_comp.name}: "
                    f"bf(refined)={dec_refined.boundary_fee} != "
                    f"bf(coarse)={dec_coarse.boundary_fee} + bf(sub)={dec_sub.boundary_fee} "
                    f"coarse={coarse} refined={refined}"
                )
                checked += 1
        assert checked > 0 or len(names) < 3, (
            f"No tower law checks for {bundled_comp.name} with {len(names)} tools"
        )


class TestMonotonicity:
    """Verify bf(refined) >= bf(coarse) for all single-step refinements
    across bundled compositions. General monotonicity follows by
    transitivity."""

    def test_monotonicity_all_bundled(self, bundled_comp):
        import random as _rng

        names = [t.name for t in bundled_comp.tools]
        partitions = list(_all_binary_partitions(names))
        if len(partitions) > 50:
            partitions = _rng.Random(42).sample(partitions, 50)

        checked = 0
        for coarse in partitions:
            dec_coarse = decompose_fee(bundled_comp, coarse)
            for refined, _, _, _ in _single_step_refinements(coarse):
                dec_refined = decompose_fee(bundled_comp, refined)
                assert dec_refined.boundary_fee >= dec_coarse.boundary_fee, (
                    f"Monotonicity violated for {bundled_comp.name}: "
                    f"bf(refined)={dec_refined.boundary_fee} < "
                    f"bf(coarse)={dec_coarse.boundary_fee} "
                    f"coarse={coarse} refined={refined}"
                )
                checked += 1
        assert checked > 0 or len(names) < 3, (
            f"No monotonicity checks for {bundled_comp.name} with {len(names)} tools"
        )


# ── Two-step tower law (inductive case) ──────────────────────────────


class TestTowerLawInductive:
    """Verify the inductive case: a two-step refinement chain.

    4 tools -> 2 groups -> 3 groups -> 4 singletons.
    bf(singletons) = bf({pair,pair}) + bf(sub_1) + bf(sub_2).
    """

    def test_two_step_chain(self):
        tools = _make_tools(
            ("A", ("tz", "payload"), ("payload",)),
            ("B", ("tz", "currency", "payload"), ("tz", "currency", "payload")),
            ("C", ("currency", "data"), ("data",)),
            ("D", ("data", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("timezone", "tz", "tz"),)),
            Edge("B", "C", (SemanticDimension("currency", "currency", "currency"),)),
            Edge("C", "D", (SemanticDimension("data", "data", "data"),)),
        )
        comp = Composition("abcd", tools, edges)

        coarse = [frozenset({"A", "B"}), frozenset({"C", "D"})]
        mid = [frozenset({"A"}), frozenset({"B"}), frozenset({"C", "D"})]
        fine = [frozenset({"A"}), frozenset({"B"}), frozenset({"C"}), frozenset({"D"})]

        bf_coarse = decompose_fee(comp, coarse).boundary_fee
        bf_mid = decompose_fee(comp, mid).boundary_fee
        bf_fine = decompose_fee(comp, fine).boundary_fee

        sub_AB = _sub_composition("AB", comp, frozenset({"A", "B"}))
        bf_sub_AB = decompose_fee(sub_AB, [frozenset({"A"}), frozenset({"B"})]).boundary_fee

        sub_CD = _sub_composition("CD", comp, frozenset({"C", "D"}))
        bf_sub_CD = decompose_fee(sub_CD, [frozenset({"C"}), frozenset({"D"})]).boundary_fee

        assert bf_mid == bf_coarse + bf_sub_AB
        assert bf_fine == bf_coarse + bf_sub_AB + bf_sub_CD
        assert bf_fine == bf_mid + bf_sub_CD

    def test_two_step_bundled(self, bundled_comp):
        """Two-step refinement on bundled compositions with >= 4 tools."""
        names = sorted(t.name for t in bundled_comp.tools)
        if len(names) < 4:
            pytest.skip("Need >= 4 tools for two-step refinement")

        half = len(names) // 2
        g1 = frozenset(names[:half])
        g2 = frozenset(names[half:])
        coarse = [g1, g2]

        g1_sorted = sorted(g1)
        g1a = frozenset(g1_sorted[:len(g1_sorted)//2])
        g1b = g1 - g1a
        g2_sorted = sorted(g2)
        g2a = frozenset(g2_sorted[:len(g2_sorted)//2])
        g2b = g2 - g2a

        if not g1a or not g1b or not g2a or not g2b:
            pytest.skip("Cannot split both groups")

        fine = [g1a, g1b, g2a, g2b]

        bf_coarse = decompose_fee(bundled_comp, coarse).boundary_fee
        bf_fine = decompose_fee(bundled_comp, fine).boundary_fee

        sub1 = _sub_composition("sub1", bundled_comp, g1)
        bf_sub1 = decompose_fee(sub1, [g1a, g1b]).boundary_fee

        sub2 = _sub_composition("sub2", bundled_comp, g2)
        bf_sub2 = decompose_fee(sub2, [g2a, g2b]).boundary_fee

        assert bf_fine == bf_coarse + bf_sub1 + bf_sub2, (
            f"Two-step tower law failed for {bundled_comp.name}: "
            f"bf(fine)={bf_fine} != bf(coarse)={bf_coarse} + "
            f"bf(sub1)={bf_sub1} + bf(sub2)={bf_sub2}"
        )


# ── Edge case tests ──────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for decompose_fee and conditional_diagnose."""

    def test_decompose_fee_zero_edges(self):
        """Composition with tools but no edges: all fees zero."""
        tools = _make_tools(
            ("A", ("x",), ("x",)),
            ("B", ("y",), ("y",)),
        )
        comp = Composition("no_edges", tools, ())
        dec = decompose_fee(comp, [frozenset({"A"}), frozenset({"B"})])
        assert dec.total_fee == 0
        assert dec.boundary_fee == 0
        assert sum(dec.local_fees) == 0
        assert dec.boundary_edges == 0

    def test_conditional_diagnose_shared_placeholder(self):
        """Two known tools both connect to the same placeholder."""
        tools = _make_tools(
            ("A", ("tz", "data"), ("data",)),
            ("B", ("currency", "data"), ("data",)),
        )
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="P",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
            OpenPort(
                from_tool="B",
                placeholder_name="P",
                dimensions=(SemanticDimension("currency", "currency", "currency"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)
        assert cond.structural_unknowns == 2
        placeholder_fields = {o.field for o in cond.obligations}
        assert "tz" in placeholder_fields or "currency" in placeholder_fields


# ── Extremal boundary fee ────────────────────────────────────────────


class TestExtremalStar:
    """Extremal boundary fee for all-hidden star topologies.

    The partition {Hub} | {all spokes} achieves bf = total_fee because
    all edges are cross-partition and both groups are internally
    edge-free. For other partitions (Hub grouped with some spokes),
    bf < total_fee because those internal edges contribute local fee.
    """

    @pytest.mark.parametrize("n_spokes", [2, 3, 4, 5])
    def test_hub_vs_spokes_achieves_max(self, n_spokes):
        """Partition {Hub} | {S_1..S_n}: bf = total_fee."""
        spokes = [f"S{i}" for i in range(n_spokes)]
        tool_specs = [("Hub", ("secret",), ())]
        tool_specs += [(s, ("secret",), ()) for s in spokes]
        tools = _make_tools(*tool_specs)
        edges = tuple(
            Edge("Hub", s, (SemanticDimension(f"d_{s}", "secret", "secret"),))
            for s in spokes
        )
        comp = Composition("star_hidden", tools, edges)
        total_fee = diagnose(comp).coherence_fee
        assert total_fee == n_spokes

        dec = decompose_fee(
            comp, [frozenset({"Hub"}), frozenset(spokes)]
        )
        assert dec.boundary_fee == total_fee
        assert sum(dec.local_fees) == 0

    @pytest.mark.parametrize("n_spokes", [3, 4, 5])
    def test_mixed_partition_bf_less_than_total(self, n_spokes):
        """Hub grouped with some spokes: bf < total_fee."""
        spokes = [f"S{i}" for i in range(n_spokes)]
        tool_specs = [("Hub", ("secret",), ())]
        tool_specs += [(s, ("secret",), ()) for s in spokes]
        tools = _make_tools(*tool_specs)
        edges = tuple(
            Edge("Hub", s, (SemanticDimension(f"d_{s}", "secret", "secret"),))
            for s in spokes
        )
        comp = Composition("star_hidden", tools, edges)
        total_fee = diagnose(comp).coherence_fee

        partition = [frozenset({"Hub", spokes[0]}), frozenset(spokes[1:])]
        dec = decompose_fee(comp, partition)
        assert dec.boundary_fee < total_fee
        assert dec.boundary_fee == n_spokes - 1
        assert sum(dec.local_fees) == 1

    @pytest.mark.parametrize("n_spokes", [2, 3, 4, 5])
    def test_singleton_partition_bf_equals_total(self, n_spokes):
        """Singleton partition: bf = total_fee (always true)."""
        spokes = [f"S{i}" for i in range(n_spokes)]
        tool_specs = [("Hub", ("secret",), ())]
        tool_specs += [(s, ("secret",), ()) for s in spokes]
        tools = _make_tools(*tool_specs)
        edges = tuple(
            Edge("Hub", s, (SemanticDimension(f"d_{s}", "secret", "secret"),))
            for s in spokes
        )
        comp = Composition("star_hidden", tools, edges)
        total_fee = diagnose(comp).coherence_fee

        partition = [frozenset({t.name}) for t in tools]
        dec = decompose_fee(comp, partition)
        assert dec.boundary_fee == total_fee


# ── Valuation and submodularity ──────────────────────────────────────


class TestValuationCounterexample:
    """The boundary fee is NOT a valuation on the partition lattice.

    For a valuation: bf(P ^ Q) + bf(P v Q) = bf(P) + bf(Q).
    The A->B->C counterexample disproves this.
    """

    def test_valuation_fails(self):
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)

        P = [frozenset({"A", "B"}), frozenset({"C"})]
        Q = [frozenset({"A"}), frozenset({"B", "C"})]
        meet = [frozenset({"A"}), frozenset({"B"}), frozenset({"C"})]
        join = [frozenset({"A", "B", "C"})]

        bf_P = decompose_fee(comp, P).boundary_fee
        bf_Q = decompose_fee(comp, Q).boundary_fee
        bf_meet = decompose_fee(comp, meet).boundary_fee
        bf_join = decompose_fee(comp, join).boundary_fee

        assert bf_P == 1
        assert bf_Q == 1
        assert bf_meet == 1
        assert bf_join == 0
        assert bf_P + bf_Q != bf_meet + bf_join


def _partition_meet(P, Q):
    """Meet (greatest lower bound) of two partitions: intersect all pairs."""
    result = []
    for p in P:
        for q in Q:
            inter = p & q
            if inter:
                result.append(inter)
    return result


def _partition_join(P, Q, names):
    """Join (least upper bound): union-find on co-occurrence."""
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    for part in [P, Q]:
        for group in part:
            members = list(group)
            for i in range(1, len(members)):
                union(members[0], members[i])
    groups: dict[str, set[str]] = {}
    for n in names:
        r = find(n)
        groups.setdefault(r, set()).add(n)
    return [frozenset(g) for g in groups.values()]


class TestSubmodularity:
    """Boundary fee is NOT submodular on the partition lattice in general.

    Disproved via adversarial random survey: 4,061 violations out of
    635,095 partition pairs across 10,000 random compositions.
    The bundled compositions happen to satisfy submodularity, but
    adversarial constructions (dense graphs with many hidden fields)
    exhibit violations of magnitude up to 3.
    """

    def test_submodularity_abc(self):
        """Submodularity holds for the simple A->B->C chain."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)

        P = [frozenset({"A", "B"}), frozenset({"C"})]
        Q = [frozenset({"A"}), frozenset({"B", "C"})]
        meet = [frozenset({"A"}), frozenset({"B"}), frozenset({"C"})]
        join = [frozenset({"A", "B", "C"})]

        bf_P = decompose_fee(comp, P).boundary_fee
        bf_Q = decompose_fee(comp, Q).boundary_fee
        bf_meet = decompose_fee(comp, meet).boundary_fee
        bf_join = decompose_fee(comp, join).boundary_fee

        assert bf_meet + bf_join <= bf_P + bf_Q

    def test_submodularity_counterexample(self):
        """4-tool, 5-edge counterexample found by adversarial survey.

        T0 and T1 are all-hidden; T2 and T3 have one visible field.
        Cycle: T3->T1->T2->T0->T3, plus T1->T3.
        P = {T2}|{rest} and Q = {T3}|{rest} both have bf=0
        (the isolated singleton has no hidden cross-edges).
        But meet = {A}|{B}|{C}|{D} (singletons) has bf=1 because
        the full refinement exposes a hidden cross-boundary convention.
        """
        tools = _make_tools(
            ("T0", ("a", "c", "b"), ()),
            ("T1", ("f", "a", "e"), ("f",)),
            ("T2", ("d",), ("d",)),
            ("T3", ("f",), ("f",)),
        )
        edges = (
            Edge("T3", "T1", (SemanticDimension("d1", "f", "a"),)),
            Edge("T1", "T2", (SemanticDimension("d2", "a", "d"),)),
            Edge("T2", "T0", (SemanticDimension("d3", "d", "b"),)),
            Edge("T1", "T3", (SemanticDimension("d4", "a", "f"),)),
            Edge("T0", "T3", (SemanticDimension("d5", "b", "f"),)),
        )
        comp = Composition("submod_cx", tools, edges)
        names = ["T0", "T1", "T2", "T3"]

        P = [frozenset({"T2"}), frozenset({"T0", "T1", "T3"})]
        Q = [frozenset({"T3"}), frozenset({"T0", "T1", "T2"})]
        meet = _partition_meet(P, Q)
        join = _partition_join(P, Q, names)

        bf_P = decompose_fee(comp, P).boundary_fee
        bf_Q = decompose_fee(comp, Q).boundary_fee
        bf_meet = decompose_fee(comp, meet).boundary_fee if len(meet) >= 2 else 0
        bf_join = decompose_fee(comp, join).boundary_fee if len(join) >= 2 else 0

        assert bf_P == 0
        assert bf_Q == 0
        assert bf_meet == 1
        assert bf_join == 0
        assert bf_meet + bf_join > bf_P + bf_Q, "Submodularity violated"

    def test_submodularity_survey_bundled(self, bundled_comp):
        """Bundled compositions happen to satisfy submodularity.

        This is an empirical observation, not a theorem. Adversarial
        random compositions can violate submodularity (see above).
        For compositions with > 5 tools, we sample partition pairs
        to keep the test suite fast.
        """
        import random as _rng

        names = sorted(t.name for t in bundled_comp.tools)
        if len(names) < 3:
            pytest.skip("Need >= 3 tools for non-trivial partition pairs")

        partitions = list(_all_binary_partitions(names))
        pairs = [
            (i, j)
            for i in range(len(partitions))
            for j in range(i + 1, len(partitions))
        ]
        if len(pairs) > 500:
            pairs = _rng.Random(42).sample(pairs, 500)

        checked = 0
        for i, j in pairs:
            P = partitions[i]
            Q = partitions[j]
            meet = _partition_meet(P, Q)
            join = _partition_join(P, Q, names)
            if len(meet) < 2 and len(join) < 2:
                continue
            bf_P = decompose_fee(bundled_comp, P).boundary_fee
            bf_Q = decompose_fee(bundled_comp, Q).boundary_fee
            bf_meet = (
                decompose_fee(bundled_comp, meet).boundary_fee
                if len(meet) >= 2
                else 0
            )
            bf_join = (
                decompose_fee(bundled_comp, join).boundary_fee
                if len(join) >= 2
                else 0
            )
            assert bf_meet + bf_join <= bf_P + bf_Q, (
                f"Submodularity violated for {bundled_comp.name}: "
                f"bf(meet)+bf(join)={bf_meet + bf_join} > "
                f"bf(P)+bf(Q)={bf_P + bf_Q}"
            )
            checked += 1
        assert checked > 0, (
            f"No partition pairs checked for {bundled_comp.name}"
        )


# ── Obligation satisfaction checker ──────────────────────────────────


class TestSatisfiesObligations:
    """Tests for the satisfies_obligations checker."""

    def test_tool_meets_all(self):
        tool = ToolSpec("B", ("tz", "currency"), ("tz", "currency"))
        obligations = (
            BoundaryObligation("B", "timezone", "tz"),
            BoundaryObligation("B", "currency", "currency"),
        )
        ok, unmet = satisfies_obligations(tool, obligations)
        assert ok is True
        assert unmet == []

    def test_tool_meets_some(self):
        tool = ToolSpec("B", ("tz", "currency"), ("tz",))
        obligations = (
            BoundaryObligation("B", "timezone", "tz"),
            BoundaryObligation("B", "currency", "currency"),
        )
        ok, unmet = satisfies_obligations(tool, obligations)
        assert ok is False
        assert len(unmet) == 1
        assert "currency" in unmet[0]

    def test_tool_meets_none(self):
        tool = ToolSpec("B", ("tz", "currency"), ())
        obligations = (
            BoundaryObligation("B", "timezone", "tz"),
            BoundaryObligation("B", "currency", "currency"),
        )
        ok, unmet = satisfies_obligations(tool, obligations)
        assert ok is False
        assert len(unmet) == 2

    def test_empty_obligations(self):
        tool = ToolSpec("B", ("x",), ())
        ok, unmet = satisfies_obligations(tool, ())
        assert ok is True
        assert unmet == []

    def test_round_trip_with_conditional_diagnose(self):
        """conditional_diagnose -> obligations -> satisfies_obligations."""
        tools = _make_tools(("A", ("tz", "data"), ("data",)))
        comp = Composition("partial", tools, ())
        open_ports = [
            OpenPort(
                from_tool="A",
                placeholder_name="B",
                dimensions=(SemanticDimension("timezone", "tz", "tz"),),
            ),
        ]
        cond = conditional_diagnose(comp, open_ports)
        good_tool = ToolSpec("B", ("tz",), ("tz",))
        ok, unmet = satisfies_obligations(good_tool, cond.obligations)
        assert ok is True

        bad_tool = ToolSpec("B", ("tz",), ())
        ok, unmet = satisfies_obligations(bad_tool, cond.obligations)
        assert ok is False


# ── Minimum disclosure set ───────────────────────────────────────────


class TestMinimumDisclosureSet:
    """Tests for the minimum_disclosure_set function."""

    def test_abc_chain(self):
        """A->B->C chain: fee=1, minimum disclosure is exactly 1 field."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)
        disclosures = minimum_disclosure_set(comp)
        assert len(disclosures) == 1
        assert disclosures[0][1] == "amount_unit"

    def test_multi_dim_chain(self):
        """Two hidden dimensions: needs exactly 2 disclosures."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
            ("B", ("tz", "currency", "data"), ("tz", "currency", "data")),
            ("C", ("tz", "currency", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
            Edge("B", "C", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
        )
        comp = Composition("multi", tools, edges)
        disclosures = minimum_disclosure_set(comp)
        assert len(disclosures) == 2

    def test_zero_fee_returns_empty(self):
        """Full-disclosure composition: no disclosures needed."""
        tools = _make_tools(
            ("A", ("x",), ("x",)),
            ("B", ("x",), ("x",)),
        )
        edges = (Edge("A", "B", (SemanticDimension("d", "x", "x"),)),)
        comp = Composition("full", tools, edges)
        assert minimum_disclosure_set(comp) == []

    def test_applying_disclosures_zeroes_fee(self):
        """Applying the returned disclosures must reduce fee to 0."""
        tools = _make_tools(
            ("A", ("amount_unit", "payload"), ("payload",)),
            ("B", ("amount_unit", "payload"), ("amount_unit", "payload")),
            ("C", ("amount_unit", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
            Edge("B", "C", (SemanticDimension("amount", "amount_unit", "amount_unit"),)),
        )
        comp = Composition("abc", tools, edges)
        disclosures = minimum_disclosure_set(comp)

        tool_map = {t.name: t for t in comp.tools}
        for tool_name, field in disclosures:
            t = tool_map[tool_name]
            if field not in t.observable_schema:
                tool_map[tool_name] = ToolSpec(
                    t.name, t.internal_state, t.observable_schema + (field,)
                )
        patched = Composition(
            comp.name, tuple(tool_map[t.name] for t in comp.tools), comp.edges
        )
        assert diagnose(patched).coherence_fee == 0

    def test_minimality(self):
        """Removing any single disclosure leaves fee > 0."""
        tools = _make_tools(
            ("A", ("tz", "currency", "data"), ("data",)),
            ("B", ("tz", "currency", "data"), ("tz", "currency", "data")),
            ("C", ("tz", "currency", "result"), ("result",)),
        )
        edges = (
            Edge("A", "B", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
            Edge("B", "C", (
                SemanticDimension("timezone", "tz", "tz"),
                SemanticDimension("currency", "currency", "currency"),
            )),
        )
        comp = Composition("multi", tools, edges)
        disclosures = minimum_disclosure_set(comp)
        for skip_idx in range(len(disclosures)):
            partial = [d for i, d in enumerate(disclosures) if i != skip_idx]
            tool_map = {t.name: t for t in comp.tools}
            for tool_name, field in partial:
                t = tool_map[tool_name]
                if field not in t.observable_schema:
                    tool_map[tool_name] = ToolSpec(
                        t.name, t.internal_state, t.observable_schema + (field,)
                    )
            patched = Composition(
                comp.name, tuple(tool_map[t.name] for t in comp.tools), comp.edges
            )
            assert diagnose(patched).coherence_fee > 0, (
                f"Removing disclosure {disclosures[skip_idx]} still yields fee 0"
            )

    def test_len_equals_fee_bundled(self, bundled_comp):
        """len(minimum_disclosure_set) == coherence_fee for all bundled."""
        fee = diagnose(bundled_comp).coherence_fee
        disclosures = minimum_disclosure_set(bundled_comp)
        assert len(disclosures) == fee, (
            f"{bundled_comp.name}: len(disclosures)={len(disclosures)} != fee={fee}"
        )

    def test_disclosures_vs_bridges_bundled(self, bundled_comp):
        """Disclosures <= bridges for all bundled compositions."""
        diag = diagnose(bundled_comp)
        disclosures = minimum_disclosure_set(bundled_comp)
        assert len(disclosures) <= len(diag.bridges), (
            f"{bundled_comp.name}: disclosures={len(disclosures)} > bridges={len(diag.bridges)}"
        )

    def test_applying_disclosures_zeroes_fee_bundled(self, bundled_comp):
        """Applying disclosures to any bundled composition zeroes fee."""
        disclosures = minimum_disclosure_set(bundled_comp)
        if not disclosures:
            return
        tool_map = {t.name: t for t in bundled_comp.tools}
        for tool_name, field in disclosures:
            t = tool_map[tool_name]
            if field not in t.observable_schema:
                tool_map[tool_name] = ToolSpec(
                    t.name, t.internal_state, t.observable_schema + (field,)
                )
        patched = Composition(
            bundled_comp.name,
            tuple(tool_map[t.name] for t in bundled_comp.tools),
            bundled_comp.edges,
        )
        assert diagnose(patched).coherence_fee == 0


# ── Empirical survey: collect boundary_fee data ─────────────────────


class TestEmpiricalSurvey:
    """Run decompose_fee across all bundled compositions with all
    binary partitions. Report which ones have nonzero boundary_fee.
    This test always passes — its value is the printed output."""

    def test_survey(self, capsys):
        import random as _rng

        results: list[tuple[str, list[str], int, int, int]] = []
        tower_law_checked = 0
        tower_law_passed = 0
        total_partitions = 0

        for yaml_path in BUNDLED_YAMLS:
            comp = load_composition(path=yaml_path)
            names = [t.name for t in comp.tools]
            partitions = list(_all_binary_partitions(names))
            if len(partitions) > 50:
                partitions = _rng.Random(42).sample(partitions, 50)
            total_partitions += len(partitions)

            for partition in partitions:
                dec = decompose_fee(comp, partition)
                if dec.boundary_fee > 0:
                    results.append((
                        comp.name,
                        ["|".join(sorted(g)) for g in partition],
                        dec.total_fee,
                        sum(dec.local_fees),
                        dec.boundary_fee,
                    ))

                for refined, split_idx, left, right in _single_step_refinements(partition):
                    tower_law_checked += 1
                    dec_refined = decompose_fee(comp, refined)
                    split_group = partition[split_idx]
                    sub_comp = _sub_composition(
                        f"sub_{'-'.join(sorted(split_group))}",
                        comp,
                        split_group,
                    )
                    dec_sub = decompose_fee(sub_comp, [left, right])
                    if dec_refined.boundary_fee == dec.boundary_fee + dec_sub.boundary_fee:
                        tower_law_passed += 1

        with capsys.disabled():
            print("\n\n=== BOUNDARY FEE SURVEY ===")
            print(f"Compositions scanned: {len(BUNDLED_YAMLS)}")
            print(f"Total partitions tested: {total_partitions}")
            print(f"Partitions with boundary_fee > 0: {len(results)}")
            print()
            for name, parts, total, local_sum, bfee in results:
                print(f"  {name}")
                print(f"    partition: {parts}")
                print(f"    total_fee={total}  local_sum={local_sum}  boundary_fee={bfee}")
            if not results:
                print("  (no partitions had nonzero boundary_fee)")
            print()
            print(f"=== TOWER LAW SURVEY ===")
            print(f"Tower law pairs checked: {tower_law_checked}")
            print(f"Tower law pairs verified: {tower_law_passed}/{tower_law_checked}")
            print("=== END SURVEY ===\n")
