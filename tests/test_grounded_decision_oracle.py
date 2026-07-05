"""Tests for the grounded-decision oracle.

The load-bearing one is `test_oracle_import_graph_is_isolated`: the non-circularity
guarantee of holonomy_pre_registration.md §3 (preserved by the §10 amendment) requires
that the label pipeline share NO code with the predictor. We enforce it structurally
by parsing the oracle's source and asserting it imports nothing from
`bulla.adapters.holonomy` or `bulla.adapters.restriction_maps`. The remaining tests
exercise the pure, local label logic (the model-gated `read_decision` is not invoked).
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

# Oracle lives at bulla/calibration/scripts/; this test at bulla/tests/.
ORACLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "calibration"
    / "scripts"
    / "grounded_decision_oracle.py"
)


def _load_oracle():
    import sys

    spec = importlib.util.spec_from_file_location("grounded_decision_oracle", ORACLE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses with `from __future__ import annotations`
    # resolve KW_ONLY via sys.modules[cls.__module__]; absent that, exec crashes.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FORBIDDEN_SUBSTRINGS = ("holonomy", "restriction_maps")


def test_oracle_import_graph_is_isolated():
    """No import in the oracle may reference the predictor's modules (holonomy /
    restriction_maps). AST-level so it cannot be evaded by aliasing."""
    tree = ast.parse(Path(ORACLE_PATH).read_text(), filename=str(ORACLE_PATH))
    referenced: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            referenced += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            referenced.append(node.module or "")
            referenced += [f"{node.module}.{a.name}" for a in node.names]
    for name in referenced:
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in name, (
                f"non-circularity violation: oracle imports {name!r} (contains {bad!r}); "
                f"the label pipeline must share no code with the holonomy predictor"
            )


def test_provenance_is_execution_independent():
    mod = _load_oracle()
    assert mod.PROVENANCE == "EXECUTION_INDEPENDENT"


def test_verbalize_earliest_choice_wins():
    mod = _load_oracle()
    v = mod.verbalize
    assert v("I believe it is boiling, not freezing.", ("freezing", "boiling")) == "boiling"
    assert v("freezing — definitely freezing", ("freezing", "boiling")) == "freezing"
    assert v("no idea", ("freezing", "boiling")) == ""  # abstain


def test_loop_dispersion_is_set_based_and_gold_anchored():
    mod = _load_oracle()
    c = mod.ProbeConcept("x", "p", gold="yes", choices=("yes", "no"))
    D = mod.Decision
    dec = {
        ("A", "x"): D("A", "x", "yes", True),
        ("B", "x"): D("B", "x", "no", False),
        ("C", "x"): D("C", "x", "yes", True),
    }
    # 1 of 3 wrong -> dispersion 1/3, regardless of model order (order-independence).
    assert abs(mod.loop_dispersion_label(["A", "B", "C"], c, dec) - 1 / 3) < 1e-12
    assert abs(mod.loop_dispersion_label(["C", "B", "A"], c, dec) - 1 / 3) < 1e-12


def test_missing_decision_counts_as_wrong():
    mod = _load_oracle()
    c = mod.ProbeConcept("x", "p", gold="yes", choices=("yes", "no"))
    dec = {("A", "x"): mod.Decision("A", "x", "yes", True)}
    # B has no recorded decision -> abstain -> counts wrong -> 1/2.
    assert abs(mod.loop_dispersion_label(["A", "B"], c, dec) - 0.5) < 1e-12


def test_binary_label_threshold():
    mod = _load_oracle()
    assert mod.loop_label_binary(0.6, 0.5) == 1
    assert mod.loop_label_binary(0.5, 0.5) == 0


def test_probeconcept_rejects_gold_outside_choices():
    mod = _load_oracle()
    import pytest

    with pytest.raises(ValueError):
        mod.ProbeConcept("x", "p", gold="maybe", choices=("yes", "no"))
