from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bulla.compute.cocycle_pairs import generate_pair_at_rank
from bulla.compute.semver import assess_update, classify_update_kind
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition


COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
AUTH = COMPOSITIONS_DIR / "auth_pipeline.yaml"
FINANCIAL = COMPOSITIONS_DIR / "financial_pipeline.yaml"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "bulla", *args],
        capture_output=True,
        text=True,
    )


def _build_pair() -> tuple[Composition, Composition]:
    hidden_tools = (
        ToolSpec(name="a", internal_state=("f",), observable_schema=()),
        ToolSpec(name="b", internal_state=("f",), observable_schema=()),
    )
    visible_tools = (
        ToolSpec(name="a", internal_state=("f",), observable_schema=("f",)),
        ToolSpec(name="b", internal_state=("f",), observable_schema=("f",)),
    )
    edges = (
        Edge("a", "b", (SemanticDimension("f1", "f", "f"),)),
        Edge("b", "a", (SemanticDimension("f2", "f", "f"),)),
    )
    return (
        Composition(name="hidden", tools=hidden_tools, edges=edges),
        Composition(name="visible", tools=visible_tools, edges=edges),
    )


def test_semver_assessment_delta_and_kind():
    old_comp, new_comp = _build_pair()
    assess = assess_update(old_comp, new_comp)
    # update removes obstruction => patch
    assert assess.delta_r <= 0
    assert assess.coherence_preserving is True
    assert assess.update_kind == "semantic-patch"


def test_classify_update_kind_thresholds():
    assert classify_update_kind(-2) == "semantic-patch"
    assert classify_update_kind(0) == "semantic-patch"
    assert classify_update_kind(1) == "semantic-minor"
    assert classify_update_kind(2) == "semantic-major"


def test_assess_update_minor_and_major_examples():
    pair_minor = generate_pair_at_rank(1)
    minor = assess_update(pair_minor.coherent, pair_minor.incoherent)
    assert minor.delta_r == 1
    assert minor.update_kind == "semantic-minor"
    assert minor.coherence_preserving is False
    assert minor.minimum_bridge_delta == 1

    pair_major = generate_pair_at_rank(2)
    major = assess_update(pair_major.coherent, pair_major.incoherent)
    assert major.delta_r == 2
    assert major.update_kind == "semantic-major"
    assert major.coherence_preserving is False
    assert major.minimum_bridge_delta == 2


def test_cli_certify_update_json():
    r = _run("certify-update", str(AUTH), str(FINANCIAL), "--format", "json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    old_comp = load_composition(AUTH)
    new_comp = load_composition(FINANCIAL)
    expected = assess_update(old_comp, new_comp).to_dict()

    assert data["old_fee"] == expected["old_fee"]
    assert data["new_fee"] == expected["new_fee"]
    assert data["delta_r"] == expected["delta_r"]
    assert data["update_kind"] == expected["update_kind"]
    assert data["coherence_preserving"] == expected["coherence_preserving"]
    assert data["minimum_bridge_delta"] == expected["minimum_bridge_delta"]

