from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla.experimental.frsl import atom, truth, variable
from bulla.experimental.hybrid import (
    CandidateProvenance,
    DisclosureBudget,
    HybridStatus,
    check_candidate,
)
from bulla.experimental.invention import SeamProblem, synthesize


ROOT = Path(__file__).resolve().parents[1]


def _problem(instance_id: str) -> SeamProblem:
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    return SeamProblem.from_dict(next(x["problem"] for x in corpus["instances"] if x["id"] == instance_id))


def _provenance() -> CandidateProvenance:
    return CandidateProvenance("fixture", "1", "sha256:" + "ab" * 32, 1)


def _budget(problem: SeamProblem, *, reveal=True, countermodels=1) -> DisclosureBudget:
    return DisclosureBudget(problem.shared_vocabulary, reveal, countermodels, 32)


def test_safe_candidate_is_accepted_only_after_all_gates():
    problem = _problem("units-0")
    candidate = atom("canonical_quantity", (variable("x0"),))
    result = check_candidate(
        problem,
        candidate,
        provenance=_provenance(),
        disclosure_budget=_budget(problem),
    )
    assert result.status is HybridStatus.ACCEPTED
    assert result.package is not None
    assert result.gate_report.definability.value == "pass"


def test_failed_candidate_returns_only_budgeted_shared_countermodel():
    problem = _problem("units-0")
    result = check_candidate(
        problem,
        truth(),
        provenance=_provenance(),
        disclosure_budget=_budget(problem),
    )
    assert result.status is HybridStatus.COUNTERMODEL
    assert set(result.countermodel["shared_structure"]).issubset(problem.shared_vocabulary)
    assert problem.target_predicate not in result.countermodel["shared_structure"]
    assert "private_model_hash" in result.countermodel


def test_target_truth_and_countermodel_count_are_separate_disclosure_gates():
    problem = _problem("units-0")
    hidden = check_candidate(
        problem,
        truth(),
        provenance=_provenance(),
        disclosure_budget=_budget(problem, reveal=False),
    )
    assert hidden.status is HybridStatus.GATE_REJECTED
    assert hidden.countermodel is None
    exhausted = check_candidate(
        problem,
        truth(),
        provenance=_provenance(),
        disclosure_budget=_budget(problem, countermodels=0),
    )
    assert exhausted.status is HybridStatus.BUDGET_EXHAUSTED


def test_private_or_target_relation_candidate_is_invalid():
    problem = _problem("units-0")
    result = check_candidate(
        problem,
        atom(problem.target_predicate, (variable("x0"),)),
        provenance=_provenance(),
        disclosure_budget=_budget(problem),
    )
    assert result.status is HybridStatus.INVALID_CANDIDATE
    with pytest.raises(TypeError):
        bool(result)


def test_safe_candidate_does_not_silently_erase_choice():
    problem = _problem("enums-4")
    reference = synthesize(problem)
    candidate = reference.alternatives[0].definition
    result = check_candidate(
        problem,
        candidate,
        provenance=_provenance(),
        disclosure_budget=_budget(problem),
    )
    assert result.status is HybridStatus.CHOICE_REQUIRED
    assert result.package is None
