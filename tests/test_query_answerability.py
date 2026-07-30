from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from bulla.experimental.answerability import (
    AssessmentStatus,
    CATALOG_PROFILE,
    MAX_CANDIDATES,
    AnswerabilityProblem,
    assess,
    assess_document,
    plan_document,
    verify_assessment_document,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "papers" / "compositional-accountability" / "fixtures"
STANDALONE = (
    ROOT / "papers" / "compositional-accountability" / "verify_answerability.py"
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "status", "margin"),
    (
        ("bypassed-action.json", AssessmentStatus.NOT_ANSWERABLE, 0),
        ("mobile-erasure.json", AssessmentStatus.NOT_ANSWERABLE, 1),
        ("independent-commitment.json", AssessmentStatus.ANSWERABLE, 2),
        ("correlated-services.json", AssessmentStatus.NOT_ANSWERABLE, 1),
        (
            "answerable-remedy-unassessed.json",
            AssessmentStatus.ANSWERABLE,
            2,
        ),
    ),
)
def test_canonical_fixtures_have_exact_expected_disposition(
    name: str,
    status: AssessmentStatus,
    margin: int,
) -> None:
    assessment = assess_document(_load(name))
    assert assessment.status is status
    assert assessment.robustness_margin == margin
    assert assessment.errors == ()


def test_bypass_is_unanswerable_without_any_adversarial_corruption() -> None:
    assessment = assess_document(_load("bypassed-action.json")).to_dict()
    assert assessment["status"] == "NOT_ANSWERABLE"
    assert assessment["robustness_margin"] == 0
    assert assessment["counterexample"]["distinguishing_atoms"] == []
    assert assessment["counterexample"]["execution_pair"] == ["clean", "harm"]


def test_mobile_corruption_breaks_a_time_respecting_custody_path() -> None:
    assessment = assess_document(_load("mobile-erasure.json")).to_dict()
    schedule = assessment["counterexample"]["corruption_schedule"]
    assert max(len(epoch["mobile_groups"]) for epoch in schedule) == 1
    assert assessment["counterexample"]["failed_custody_paths"]
    assert all(
        not item["challenger_reached"]
        for item in assessment["counterexample"]["failed_custody_paths"]
    )


def test_missing_retention_does_not_allow_evidence_to_jump_epochs() -> None:
    problem = _load("independent-commitment.json")
    problem["challenge_epoch"] = 2
    for edge in problem["custody_edges"]:
        edge["source_epoch"] = 1
        edge["target_epoch"] = 2
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.NOT_ANSWERABLE
    assert assessment.robustness_margin == 0


def test_later_honesty_does_not_resurrect_erased_origin_evidence() -> None:
    problem = _load("mobile-erasure.json")
    problem["adversary"] = {
        "peak_budget": 0,
        "fixed_corruptions": [{"epoch": 0, "group_id": "witness-a"}],
    }
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.NOT_ANSWERABLE
    assert assessment.robustness_margin == 0
    schedule = assessment.counterexample["corruption_schedule"]
    assert schedule[0]["fixed_groups"] == ["witness-a"]
    assert all("witness-a" not in epoch["fixed_groups"] for epoch in schedule[1:])


def test_administrative_control_not_service_count_determines_margin() -> None:
    independent = assess_document(_load("independent-commitment.json"))
    correlated = assess_document(_load("correlated-services.json"))
    assert len(_load("independent-commitment.json")["evidence_atoms"]) == 2
    assert len(_load("correlated-services.json")["evidence_atoms"]) == 2
    assert independent.status is AssessmentStatus.ANSWERABLE
    assert correlated.status is AssessmentStatus.NOT_ANSWERABLE
    assert independent.robustness_margin == 2
    assert correlated.robustness_margin == 1


def test_other_accountability_games_are_never_inferred() -> None:
    assessment = assess_document(
        _load("answerable-remedy-unassessed.json")
    ).to_dict()
    assert assessment["status"] == "ANSWERABLE"
    assert assessment["property_scope"] == {
        "occurrence_query": "assessed",
        "non_equivocation": "not_assessed",
        "authority_binding": "not_assessed",
        "remedy_reachability": "not_assessed",
        "external_validation": "none",
    }


def test_forum_declaration_with_another_occurrence_is_not_credited() -> None:
    problem = _load("bypassed-action.json")
    declaration = copy.deepcopy(problem["evidence_atoms"][0])
    declaration["atom_id"] = "forum-declaration"
    declaration["occurrence_binding"] = (
        "sha256:9999999999999999999999999999999999999999999999999999999999999999"
    )
    declaration["values"]["harm"] = (
        "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    )
    problem["evidence_atoms"].append(declaration)
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.MODEL_INVALID
    assert "occurrence binding" in assessment.errors[0]


def test_problem_and_result_are_permutation_canonical() -> None:
    original = _load("independent-commitment.json")
    permuted = copy.deepcopy(original)
    for key in ("control_groups", "executions", "evidence_atoms", "custody_edges"):
        permuted[key].reverse()
    left = assess_document(original).to_dict()
    right = assess_document(permuted).to_dict()
    assert left == right


def test_tampered_assessment_fails_full_replay() -> None:
    problem = _load("independent-commitment.json")
    assessment = assess_document(problem).to_dict()
    assessment["robustness_margin"] = 1
    ok, reasons = verify_assessment_document(problem, assessment)
    assert not ok
    assert any("robustness_margin" in reason for reason in reasons)


def test_changed_query_value_invalidates_prior_assessment() -> None:
    problem = _load("independent-commitment.json")
    assessment = assess_document(problem).to_dict()
    mutated = copy.deepcopy(problem)
    mutated["executions"][1]["query_value"] = mutated["executions"][0]["query_value"]
    ok, reasons = verify_assessment_document(mutated, assessment)
    assert not ok
    assert reasons


@pytest.mark.parametrize(
    "mutator",
    (
        lambda doc: doc["evidence_atoms"][0].__setitem__(
            "occurrence_binding", "sha256:wrong"
        ),
        lambda doc: doc["control_groups"][1]["holders"].append("holder-a"),
        lambda doc: doc["custody_edges"][0].__setitem__("target_epoch", 0),
        lambda doc: doc["custody_edges"][0].__setitem__("kind", "forum"),
        lambda doc: doc["adversary"].__setitem__("peak_budget", 3),
    ),
)
def test_model_mutants_fail_closed(mutator) -> None:
    problem = _load("independent-commitment.json")
    mutator(problem)
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.MODEL_INVALID
    assert assessment.errors


def test_skipped_epoch_edge_fails_closed() -> None:
    problem = _load("mobile-erasure.json")
    problem["custody_edges"][0]["target_epoch"] = 2
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.MODEL_INVALID
    assert "exactly one epoch" in assessment.errors[0]


def test_corruption_at_challenge_epoch_fails_closed() -> None:
    problem = _load("independent-commitment.json")
    problem["adversary"]["fixed_corruptions"] = [
        {"epoch": problem["challenge_epoch"], "group_id": "owner-a"}
    ]
    assessment = assess_document(problem)
    assert assessment.status is AssessmentStatus.MODEL_INVALID
    assert "before the challenge" in assessment.errors[0]


def test_search_limit_fails_closed_without_partial_verdict() -> None:
    problem = AnswerabilityProblem.from_dict(_load("independent-commitment.json"))
    assessment = assess(problem, state_limit=0)
    assert assessment.status is AssessmentStatus.LIMIT_EXCEEDED
    assert assessment.robustness_margin is None
    assert assessment.errors


def test_exact_planner_accounts_for_sequential_mobile_erasure() -> None:
    plan = plan_document(
        _load("bypassed-action.json"),
        _load("bypassed-action-catalog.json"),
    )
    assert plan.status.value == "FEASIBLE"
    assert plan.selected_candidates == ("non-corruptible-anchor",)
    assert plan.total_cost == 10
    assert plan.assessment["status"] == "ANSWERABLE"
    assert plan.assessment["robustness_margin"] is None
    assert plan.assessment["unbounded_within_model"] is True


def test_equal_cost_plans_use_candidate_identifier_order() -> None:
    catalog = _load("bypassed-action-catalog.json")
    anchor = copy.deepcopy(catalog["candidates"][-1])
    lexical_first = copy.deepcopy(anchor)
    lexical_first["candidate_id"] = "a-non-corruptible-anchor"
    lexical_first["control_groups"][0]["group_id"] = "a-anchor"
    lexical_first["control_groups"][0]["holders"] = ["a-anchor-log"]
    lexical_first["evidence_atoms"][0]["atom_id"] = "a-anchor-occurrence"
    lexical_first["evidence_atoms"][0]["holder"] = "a-anchor-log"
    for edge in lexical_first["custody_edges"]:
        edge["atom_id"] = "a-anchor-occurrence"
        edge["source_holder"] = "a-anchor-log"
        if edge["target_holder"] != "@challenger":
            edge["target_holder"] = "a-anchor-log"
    catalog["candidates"] = [anchor, lexical_first]
    plan = plan_document(_load("bypassed-action.json"), catalog)
    assert plan.status.value == "FEASIBLE"
    assert plan.total_cost == anchor["cost"]
    assert plan.selected_candidates == ("a-non-corruptible-anchor",)


def test_infeasible_plan_returns_its_strongest_attempt() -> None:
    catalog = _load("bypassed-action-catalog.json")
    catalog["candidates"] = catalog["candidates"][:1]
    plan = plan_document(_load("bypassed-action.json"), catalog)
    assert plan.status.value == "INFEASIBLE"
    assert plan.selected_candidates == ("independent-witness-a",)
    assert plan.total_cost == 2
    assert plan.assessment["status"] == "NOT_ANSWERABLE"
    assert plan.assessment["robustness_margin"] == 1


def test_candidate_limit_fails_closed() -> None:
    template = _load("bypassed-action-catalog.json")["candidates"][0]
    candidates = []
    for index in range(MAX_CANDIDATES + 1):
        candidate = copy.deepcopy(template)
        candidate["candidate_id"] = f"candidate-{index:02d}"
        candidate["control_groups"][0]["group_id"] = f"group-{index:02d}"
        candidate["control_groups"][0]["holders"] = [f"holder-{index:02d}"]
        candidate["evidence_atoms"][0]["atom_id"] = f"atom-{index:02d}"
        candidate["evidence_atoms"][0]["holder"] = f"holder-{index:02d}"
        for edge in candidate["custody_edges"]:
            edge["atom_id"] = f"atom-{index:02d}"
            edge["source_holder"] = f"holder-{index:02d}"
            if edge["target_holder"] != "@challenger":
                edge["target_holder"] = f"holder-{index:02d}"
        candidates.append(candidate)
    plan = plan_document(
        _load("bypassed-action.json"),
        {"profile": CATALOG_PROFILE, "candidates": candidates},
    )
    assert plan.status.value == "MODEL_INVALID"
    assert "limit" in plan.errors[0]


def test_standalone_checker_recomputes_without_bulla_install(
    tmp_path: Path,
) -> None:
    problem_path = FIXTURES / "independent-commitment.json"
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(assess_document(_load(problem_path.name)).to_dict(), indent=2)
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(STANDALONE), str(problem_path), str(assessment_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"ok": True, "reasons": []}


def test_standalone_checker_and_bulla_agree_on_every_fixture() -> None:
    spec = importlib.util.spec_from_file_location(
        "independent_answerability_verifier", STANDALONE
    )
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    for path in sorted(FIXTURES.glob("*.json")):
        if "catalog" in path.name:
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        assert verifier.recompute(document) == assess_document(document).to_dict()


def test_cli_assess_verify_and_plan(tmp_path: Path) -> None:
    assessment_path = tmp_path / "assessment.json"
    plan_path = tmp_path / "plan.json"
    entry = "from bulla.cli import main; main()"
    env = {"PYTHONPATH": str(ROOT / "bulla" / "src")}

    assessed = subprocess.run(
        [
            sys.executable,
            "-c",
            entry,
            "experimental",
            "answerability",
            "assess",
            str(FIXTURES / "independent-commitment.json"),
            "-o",
            str(assessment_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert assessed.returncode == 0, assessed.stdout + assessed.stderr

    verified = subprocess.run(
        [
            sys.executable,
            "-c",
            entry,
            "experimental",
            "answerability",
            "verify",
            str(FIXTURES / "independent-commitment.json"),
            str(assessment_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr

    planned = subprocess.run(
        [
            sys.executable,
            "-c",
            entry,
            "experimental",
            "answerability",
            "plan",
            str(FIXTURES / "bypassed-action.json"),
            str(FIXTURES / "bypassed-action-catalog.json"),
            "-o",
            str(plan_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert json.loads(plan_path.read_text())["selected_candidates"] == [
        "non-corruptible-anchor"
    ]
