"""The `kind` reservation on SemanticDimension must be zero-behavior: it defaults
to "field", is readable from YAML, and — the load-bearing property — is EXCLUDED
from `Composition.canonical_hash`, so reserving it leaves every existing receipt
byte-identical. The full 703 byte-identity regression is the corpus-scale version
of the last assertion; this pins it at the unit level."""

from __future__ import annotations

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def test_kind_defaults_to_field():
    d = SemanticDimension(name="urgency", from_field="a", to_field="b")
    assert d.kind == "field"


def test_kind_is_readable_and_a_predicate_value_is_accepted():
    d = SemanticDimension(name="urgency", from_field="a", to_field="b", kind="predicate")
    assert d.kind == "predicate"


def _comp(kind: str) -> Composition:
    return Composition(
        name="c",
        tools=(
            ToolSpec(name="A", internal_state=("x",), observable_schema=("x",)),
            ToolSpec(name="B", internal_state=("y",), observable_schema=("y",)),
        ),
        edges=(
            Edge(
                from_tool="A",
                to_tool="B",
                dimensions=(SemanticDimension(name="d", from_field="x", to_field="y", kind=kind),),
            ),
        ),
    )


def test_kind_is_excluded_from_canonical_hash_byte_identity():
    # the reservation is only sound if kind does not perturb the identity hash
    #
    # ⚠ ACTIVATION: this assertion pins TODAY's exclusion and becomes WRONG the day
    # `kind` is behaviour-relevant. Excluding a verdict-affecting field from the hash
    # is a receipt-integrity hole (two manifests differing only in kind would hash
    # identically, so the receipt would stop binding an input the verdict depends on).
    # At activation, FLIP this to `!=` deliberately and follow the activation checklist
    # (spec_version bump + receipt hash_schema version + dual-verification grace window).
    # Do NOT "fix" a future failure of this test by reverting kind out of behaviour.
    assert _comp("field").canonical_hash() == _comp("predicate").canonical_hash()


def test_parser_reads_kind_but_default_composition_is_hash_stable(tmp_path):
    from bulla.parser import load_composition

    base = """
name: c
tools:
  A: {internal_state: [x], observable_schema: [x]}
  B: {internal_state: [y], observable_schema: [y]}
edges:
  - from: A
    to: B
    dimensions:
      - {name: d, from_field: x, to_field: y}
"""
    with_kind = base.replace(
        "- {name: d, from_field: x, to_field: y}",
        "- {name: d, from_field: x, to_field: y, kind: predicate}",
    )
    c0 = load_composition(text=base)
    c1 = load_composition(text=with_kind)
    assert c1.edges[0].dimensions[0].kind == "predicate"
    assert c0.edges[0].dimensions[0].kind == "field"
    # kind differs, identity hash does not — receipts stay byte-identical
    assert c0.canonical_hash() == c1.canonical_hash()
