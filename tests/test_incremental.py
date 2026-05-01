"""Correctness validation for IncrementalDiagnostic.

Every test compares the incremental Schur complement update against
full recomputation via witness_geometry.witness_gram. Exact rational
arithmetic guarantees bitwise equality — no tolerance, no epsilon.

The validation strategy:
1. Build a composition with known fee > 0
2. Disclose one field via IncrementalDiagnostic.disclose()
3. Rebuild the composition with that field moved to observable_schema
4. Compute K via full witness_gram on the modified composition
5. Assert the incremental K is EXACTLY equal to the full K

This validates the Schur complement identity:
    K_new[i,k] = K_old[i,k] - K_old[i,j]*K_old[j,k]/K_old[j,j]
"""

from fractions import Fraction

import pytest

from bulla.model import Composition, ToolSpec, Edge, SemanticDimension
from bulla.diagnostic import diagnose
from bulla.witness_geometry import witness_gram, leverage_scores, fee_from_gram
from bulla.incremental import IncrementalDiagnostic, _schur_complement_delete


# ─── Test fixtures ──────────────────────────────────────────────


def _triangle_composition() -> Composition:
    """Three tools in a triangle with hidden fields on all edges."""
    return Composition(
        name="triangle",
        tools=(
            ToolSpec("a", ("x", "h_a"), ("x",)),
            ToolSpec("b", ("x", "h_b"), ("x",)),
            ToolSpec("c", ("x", "h_c"), ("x",)),
        ),
        edges=(
            Edge("a", "b", (SemanticDimension("d1", from_field="h_a", to_field="h_b"),)),
            Edge("b", "c", (SemanticDimension("d2", from_field="h_b", to_field="h_c"),)),
            Edge("c", "a", (SemanticDimension("d3", from_field="h_c", to_field="h_a"),)),
        ),
    )


def _chain_composition() -> Composition:
    """Four tools in a chain with mixed observable/hidden fields."""
    return Composition(
        name="chain",
        tools=(
            ToolSpec("t1", ("x", "y", "h1"), ("x", "y")),
            ToolSpec("t2", ("x", "y", "h2"), ("x", "y")),
            ToolSpec("t3", ("x", "y", "h3"), ("x", "y")),
            ToolSpec("t4", ("x", "y", "h4"), ("x", "y")),
        ),
        edges=(
            Edge("t1", "t2", (
                SemanticDimension("d1", from_field="x", to_field="x"),
                SemanticDimension("d2", from_field="h1", to_field="h2"),
            )),
            Edge("t2", "t3", (
                SemanticDimension("d1", from_field="x", to_field="x"),
                SemanticDimension("d2", from_field="h2", to_field="h3"),
            )),
            Edge("t3", "t4", (
                SemanticDimension("d1", from_field="x", to_field="x"),
                SemanticDimension("d2", from_field="h3", to_field="h4"),
            )),
        ),
    )


def _diamond_composition() -> Composition:
    """Diamond shape: a -> b, a -> c, b -> d, c -> d, with hidden fields."""
    return Composition(
        name="diamond",
        tools=(
            ToolSpec("a", ("x", "ha"), ("x",)),
            ToolSpec("b", ("x", "hb"), ("x",)),
            ToolSpec("c", ("x", "hc"), ("x",)),
            ToolSpec("d", ("x", "hd"), ("x",)),
        ),
        edges=(
            Edge("a", "b", (SemanticDimension("d", from_field="ha", to_field="hb"),)),
            Edge("a", "c", (SemanticDimension("d", from_field="ha", to_field="hc"),)),
            Edge("b", "d", (SemanticDimension("d", from_field="hb", to_field="hd"),)),
            Edge("c", "d", (SemanticDimension("d", from_field="hc", to_field="hd"),)),
        ),
    )


def _modify_composition_disclose(
    comp: Composition, tool_name: str, field_name: str
) -> Composition:
    """Return a new Composition with the given field moved to observable."""
    new_tools = []
    for t in comp.tools:
        if t.name == tool_name and field_name in t.internal_state:
            new_obs = tuple(sorted(set(t.observable_schema) | {field_name}))
            new_tools.append(ToolSpec(t.name, t.internal_state, new_obs))
        else:
            new_tools.append(t)
    return Composition(comp.name + "_disclosed", tuple(new_tools), comp.edges)


# ─── Core correctness tests ────────────────────────────────────


class TestSchurComplementCorrectness:
    """Validate that incremental K matches full recomputation after disclosure."""

    def test_triangle_single_disclosure(self):
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        assert inc.fee > 0, "Triangle should have nonzero fee"

        # Pick the first hidden field with positive leverage
        lev = inc.leverage()
        field_to_disclose = None
        for (tool, field), l in lev:
            if l > 0:
                field_to_disclose = (tool, field)
                break
        assert field_to_disclose is not None

        # Incremental update
        delta = inc.disclose(*field_to_disclose)
        assert delta.fee_change == -1
        K_inc = inc._K
        basis_inc = inc._hidden_basis

        # Full recomputation
        comp_mod = _modify_composition_disclose(comp, *field_to_disclose)
        K_full, basis_full = witness_gram(list(comp_mod.tools), list(comp_mod.edges))

        # Filter full basis to match incremental basis (same hidden fields)
        assert len(K_inc) == len(K_full), (
            f"Dimension mismatch: inc={len(K_inc)}, full={len(K_full)}"
        )
        assert basis_inc == list(basis_full), (
            f"Basis mismatch: inc={basis_inc}, full={list(basis_full)}"
        )

        # Exact equality (rational arithmetic, no tolerance)
        for i in range(len(K_inc)):
            for k in range(len(K_inc)):
                assert K_inc[i][k] == K_full[i][k], (
                    f"K[{i}][{k}] mismatch: inc={K_inc[i][k]}, full={K_full[i][k]}"
                )

    def test_chain_full_repair_trajectory(self):
        """Disclose all hidden fields one at a time; verify K at each step."""
        comp = _chain_composition()
        inc = IncrementalDiagnostic(comp)
        initial_fee = inc.fee

        current_comp = comp
        for step in range(initial_fee):
            lev = inc.leverage()
            # Find a field with positive leverage
            target = None
            for (tool, field), l in lev:
                if l > 0:
                    target = (tool, field)
                    break
            if target is None:
                break

            # Incremental update
            delta = inc.disclose(*target)
            assert delta.fee_change == -1

            # Full recomputation on modified composition
            current_comp = _modify_composition_disclose(current_comp, *target)
            K_full, basis_full = witness_gram(
                list(current_comp.tools), list(current_comp.edges)
            )

            # Verify dimensions match
            assert len(inc._K) == len(K_full), (
                f"Step {step}: dim mismatch inc={len(inc._K)}, full={len(K_full)}"
            )

            # Verify exact equality
            for i in range(len(inc._K)):
                for k in range(len(inc._K)):
                    assert inc._K[i][k] == K_full[i][k], (
                        f"Step {step}: K[{i}][{k}] mismatch"
                    )

        assert inc.fee == 0, f"Fee should be 0 after full repair, got {inc.fee}"

    def test_diamond_composition(self):
        """Test on diamond topology (two independent paths)."""
        comp = _diamond_composition()
        inc = IncrementalDiagnostic(comp)

        if inc.fee == 0:
            pytest.skip("Diamond has fee=0; nothing to test incrementally")

        lev = inc.leverage()
        target = None
        for (tool, field), l in lev:
            if l > 0:
                target = (tool, field)
                break
        assert target is not None

        delta = inc.disclose(*target)
        comp_mod = _modify_composition_disclose(comp, *target)
        K_full, _ = witness_gram(list(comp_mod.tools), list(comp_mod.edges))

        for i in range(len(inc._K)):
            for k in range(len(inc._K)):
                assert inc._K[i][k] == K_full[i][k]


class TestIncrementalAPI:
    """Test the public API surface."""

    def test_preview_matches_actual(self):
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        for (tool, field), l in inc.leverage():
            preview = inc.preview_disclose(tool, field)
            expected = -1 if l > 0 else 0
            assert preview == expected, (
                f"Preview for ({tool}, {field}): got {preview}, "
                f"expected {expected} (leverage={l})"
            )

    def test_best_next_disclosure_reduces_fee(self):
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        if inc.fee == 0:
            pytest.skip("No fee to reduce")
        best = inc.best_next_disclosure()
        assert best is not None
        delta = inc.disclose(*best)
        assert delta.fee_change == -1

    def test_loop_disclosure_preserves_fee(self):
        """If a field is a loop (leverage=0), disclosing it does nothing."""
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        for (tool, field), l in inc.leverage():
            if l == 0:
                old_fee = inc.fee
                delta = inc.disclose(tool, field)
                assert delta.fee_change == 0
                assert inc.fee == old_fee
                return
        pytest.skip("No loops in this composition")

    def test_profile_matches_standalone(self):
        """IncrementalDiagnostic.profile() should match standalone computation."""
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        p = inc.profile()
        assert p.fee == inc.fee
        assert len(p.leverage) == len(inc._hidden_basis)
        assert sum(p.leverage) == Fraction(p.fee)

    def test_disclose_nonexistent_field_raises(self):
        comp = _triangle_composition()
        inc = IncrementalDiagnostic(comp)
        with pytest.raises(ValueError, match="not in hidden basis"):
            inc.disclose("nonexistent", "field")

    def test_fee_conservation(self):
        """Sum of fee deltas should equal initial fee."""
        comp = _chain_composition()
        inc = IncrementalDiagnostic(comp)
        initial_fee = inc.fee
        total_delta = 0
        while inc.fee > 0:
            best = inc.best_next_disclosure()
            if best is None:
                break
            delta = inc.disclose(*best)
            total_delta += delta.fee_change
        assert -total_delta == initial_fee, (
            f"Fee conservation: initial={initial_fee}, "
            f"total_delta={total_delta}"
        )
