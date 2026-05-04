"""Sprint 10 Phase 4 — regression gates for the schema-shape invariant.

Two complementary gates:

  1. Positive: every bundled real/MCP-curated composition (in
     `bulla/compositions`, `bulla/audit`, and `bulla/src/bulla/compositions`)
     satisfies `has_projective_observables`. If any new bundled composition
     ever violates this, the test fails — preventing accidental regression
     of the project's well-formedness guarantee.

  2. Negative: a hand-authored fixture (`bulla/tests/fixtures/malformed_non_projective.yaml`)
     that intentionally violates the invariant produces non-empty
     `validate_regime` output AND a non-None `format_regime_warning`.
     Without this fixture the validation surface could silently no-op
     and we'd never notice.

Together: positive gate ensures nothing real ever drifts into ill-formed
territory; negative gate ensures the validation actually fires on real
violations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))

from bulla.parser import load_composition
from bulla.regime import (
    format_regime_warning,
    has_projective_observables,
    validate_regime,
)


# ---- Positive gate: bundled corpora are well-formed ----

def _bundled_yaml_paths() -> list[Path]:
    """All YAML compositions bundled with bulla (excluding negative
    fixtures intentionally crafted to violate the invariant)."""
    paths: list[Path] = []
    for d in [
        REPO / "bulla" / "compositions",
        REPO / "bulla" / "audit",
        REPO / "bulla" / "src" / "bulla" / "compositions",
    ]:
        if d.exists():
            paths.extend(sorted(d.glob("*.yaml")))
    return paths


@pytest.mark.parametrize("path", _bundled_yaml_paths(),
                         ids=lambda p: p.name)
def test_bundled_compositions_satisfy_schema_shape_invariant(path):
    """Every bundled composition must satisfy `has_projective_observables`.

    If this test fails, a new bundled YAML drifted into ill-formed territory
    — investigate and fix the YAML, do not relax the test.
    """
    comp = load_composition(path)
    violations = validate_regime(comp)
    assert not violations, (
        f"{path.name} violates the schema-shape invariant: {violations}. "
        f"Fix the YAML so observable_schema ⊆ internal_state for every tool."
    )
    assert has_projective_observables(comp)
    assert format_regime_warning(comp) is None


# ---- Negative gate (layer 1): parser rejects the malformed YAML ----

def test_parser_already_enforces_schema_shape_invariant():
    """The YAML parser (`bulla.parser`) already enforces
    observable_schema ⊆ internal_state at load time — this is the
    strongest layer of protection. The malformed fixture must raise
    `CompositionError` with a clear message mentioning the violation.

    If this test fails (the parser silently accepts the malformed YAML),
    the project's strongest regime defense has regressed.
    """
    from bulla.parser import CompositionError
    path = REPO / "bulla" / "tests" / "fixtures" / "malformed_non_projective.yaml"
    assert path.exists(), f"Negative fixture missing: {path}"
    with pytest.raises(CompositionError) as excinfo:
        load_composition(path)
    # The error message must mention both endpoint field names so the
    # user can locate the violation immediately.
    msg = str(excinfo.value).lower()
    assert "observable_schema" in msg
    assert "internal_state" in msg
    assert "secret" in msg


# ---- Negative gate (layer 2): Python-construction bypass triggers warning ----

def test_python_constructed_violation_triggers_warning():
    """The parser blocks malformed YAMLs, but direct Python construction
    via `bulla.model` primitives (e.g., random generators, programmatic
    composition building) BYPASSES the parser. The validation surface
    in `bulla.regime` must catch these cases.

    This test constructs a malformed composition directly via the model
    API — exactly the Sprint 7 random-stress failure mode — and verifies
    the validation surface fires correctly.
    """
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
    t1 = ToolSpec(
        name="t1",
        internal_state=("hidden_a",),
        observable_schema=("secret",),  # violation
    )
    t2 = ToolSpec(
        name="t2",
        internal_state=("hidden_b",),
        observable_schema=("secret",),  # violation
    )
    edge = Edge(
        from_tool="t1",
        to_tool="t2",
        dimensions=(SemanticDimension(
            name="secret_match",
            from_field="secret",
            to_field="secret",
        ),),
    )
    comp = Composition(name="malformed_via_python", tools=(t1, t2), edges=(edge,))

    # Validation surface fires
    violations = validate_regime(comp)
    assert len(violations) == 2, f"Expected 2 violations (one per tool), got {len(violations)}"
    violation_tools = {v.tool_name for v in violations}
    assert violation_tools == {"t1", "t2"}
    for v in violations:
        assert v.kind == "projective_observables"
        assert v.fields == ("secret",)
        assert "internal_state" in v.description

    # Warning formatter produces a non-None message with regime language
    warning = format_regime_warning(comp)
    assert warning is not None
    assert "schema-shape" in warning.lower() or "projective" in warning.lower()
    assert "secret" in warning


def test_python_constructed_violation_yields_negative_fee():
    """Sanity (Sprint 7 reproducer): the Python-constructed malformed
    composition produces negative `coherence_fee` — exactly the failure
    mode the validation surface and parser protect against.

    Together with `test_python_constructed_violation_triggers_warning`,
    this confirms that the regime warning fires on precisely the
    compositions that would otherwise produce silent negative fees.
    """
    from bulla.diagnostic import diagnose
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
    t1 = ToolSpec(
        name="t1",
        internal_state=("hidden_a",),
        observable_schema=("secret",),
    )
    t2 = ToolSpec(
        name="t2",
        internal_state=("hidden_b",),
        observable_schema=("secret",),
    )
    edge = Edge(
        from_tool="t1",
        to_tool="t2",
        dimensions=(SemanticDimension(
            name="secret_match",
            from_field="secret",
            to_field="secret",
        ),),
    )
    comp = Composition(name="malformed_via_python", tools=(t1, t2), edges=(edge,))
    diag = diagnose(comp)
    assert diag.coherence_fee < 0, (
        f"Expected negative fee on Python-constructed malformed comp, "
        f"got {diag.coherence_fee}. Either the construction changed or the "
        f"formula stopped going negative on observable-only seam dims."
    )
