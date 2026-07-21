"""Finite semantic gates for the experimental Interpolant Envelope."""

from __future__ import annotations

import dataclasses
import json
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.experimental.frsl import RelationDecl, Signature, atom, constant, variable
from bulla.experimental.invention import (
    FailureKind,
    GateStatus,
    LocalTheory,
    OverlapMap,
    SeamProblem,
    SynthesisPolicy,
    SynthesisStatus,
    mint_invention_receipt,
    synthesize,
    verify_failure_certificate,
    verify_package,
)
from bulla.action_receipt import verify_receipt
from bulla.envelope import (
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)


def _forall_iff(left: dict, right: dict) -> dict:
    return {
        "op": "forall",
        "var": "x",
        "sort": "Item",
        "body": {"op": "iff", "left": left, "right": right},
    }


def _forall_implies(left: dict, right: dict) -> dict:
    return {
        "op": "forall",
        "var": "x",
        "sort": "Item",
        "body": {"op": "implies", "left": left, "right": right},
    }


def _base_problem(*, constraints: tuple[dict, ...], shared: tuple[str, ...] = ("signal",), **kwargs) -> SeamProblem:
    signature = Signature(
        sorts={"Item": ("a", "b")},
        relations={
            "signal": RelationDecl("signal", ("Item",)),
            "target": RelationDecl("target", ("Item",)),
        },
    )
    return SeamProblem(
        problem_id=kwargs.pop("problem_id", "test"),
        signature=signature,
        local_theories=(LocalTheory("left", constraints),),
        overlap_maps=(),
        target_predicate="target",
        shared_vocabulary=shared,
        protected_signatures={"left": shared},
        requested_judgment="rely_refuse_escalate",
        synthesis_policy=kwargs.pop(
            "synthesis_policy",
            SynthesisPolicy(max_candidate_atoms=8),
        ),
        authority={"principal": "did:example:owner"},
        scope={"seam": "test"},
        evidence_requirements=("source-record",),
        **kwargs,
    )


def _partial_problem() -> SeamProblem:
    signature = Signature(
        sorts={"Item": ("a",)},
        relations={
            "positive": RelationDecl("positive", ("Item",)),
            "negative": RelationDecl("negative", ("Item",)),
            "target": RelationDecl("target", ("Item",)),
        },
    )
    x = variable("x")
    not_target = {"op": "not", "body": atom("target", (x,))}
    no_conflict = {
        "op": "forall",
        "var": "x",
        "sort": "Item",
        "body": {
            "op": "not",
            "body": {
                "op": "and",
                "args": [atom("positive", (x,)), atom("negative", (x,))],
            },
        },
    }
    return SeamProblem(
        problem_id="partial",
        signature=signature,
        local_theories=(
            LocalTheory(
                "owner",
                (
                    _forall_implies(atom("positive", (x,)), atom("target", (x,))),
                    _forall_implies(atom("negative", (x,)), not_target),
                    no_conflict,
                ),
            ),
        ),
        overlap_maps=(),
        target_predicate="target",
        shared_vocabulary=("positive", "negative"),
        protected_signatures={"owner": ("positive", "negative")},
        requested_judgment="rely_refuse_escalate",
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )


def test_compiles_uniform_definition_and_replays_gates():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )

    result = synthesize(problem)

    assert result.status is SynthesisStatus.COMPILED
    assert result.package is not None
    assert result.package.definition == atom("signal", (variable("x0"),))
    replay = verify_package(problem, result.package)
    assert replay.gluing is GateStatus.PASS
    assert replay.conservativity is GateStatus.PASS
    assert replay.definability is GateStatus.PASS
    assert replay.preserved_refusals is GateStatus.PASS


def test_gate_report_rejects_boolean_coercion():
    x = variable("x")
    result = synthesize(
        _base_problem(
            constraints=(
                _forall_iff(atom("target", (x,)), atom("signal", (x,))),
            )
        )
    )
    with pytest.raises(TypeError, match="no truth value"):
        bool(result.gate_report)


def test_policy_cannot_delegate_nonunique_choice_to_engine():
    with pytest.raises(ValueError, match="cannot be silently delegated"):
        SynthesisPolicy(require_unique_minimum=False)


def test_unconstrained_target_emits_checked_same_reduct_countermodel():
    problem = _base_problem(constraints=())

    result = synthesize(problem)

    assert result.status is SynthesisStatus.ESCALATE
    assert result.certificate is not None
    assert result.certificate.kind is FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY
    assert verify_failure_certificate(
        problem,
        result.certificate,
        alternatives=result.alternatives,
    )
    witness = result.certificate.witness
    assert witness["expansion_true"]["signal"] == witness["expansion_false"]["signal"]
    assert witness["expansion_true"]["target"] != witness["expansion_false"]["target"]


def test_countermodel_metadata_is_part_of_the_checked_certificate():
    problem = _base_problem(constraints=())
    result = synthesize(problem)
    witness = dict(result.certificate.witness)
    witness["shared_reduct"] = {"signal": [["forged"]]}
    tampered = dataclasses.replace(result.certificate, witness=witness)

    assert not verify_failure_certificate(problem, tampered)


def test_partial_envelope_preserves_rely_refuse_and_escalate_residual():
    problem = _partial_problem()

    result = synthesize(problem)

    assert result.status is SynthesisStatus.PARTIAL
    assert result.package is not None
    assert result.certificate is not None
    replay = verify_package(problem, result.package)
    assert replay.preserved_refusals is GateStatus.PASS
    assert replay.definability is GateStatus.FAIL
    assert verify_failure_certificate(problem, result.certificate)


def test_tampered_definition_fails_closed():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem)
    assert result.package is not None
    tampered = dataclasses.replace(
        result.package,
        definition={"op": "true"},
    )

    replay = verify_package(problem, tampered)

    assert replay.definability is GateStatus.FAIL
    assert replay.preserved_refusals is GateStatus.FAIL


def test_definition_cannot_read_target_or_private_state():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem)
    assert result.package is not None
    leaked = dataclasses.replace(
        result.package,
        definition=atom("target", (variable("x0"),)),
    )

    replay = verify_package(problem, leaked)

    assert replay.definability is GateStatus.FAIL
    assert any("leaks non-shared" in reason for reason in replay.reasons)


def test_authority_expansion_breaks_package_binding():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem)
    assert result.package is not None
    expanded = dataclasses.replace(
        result.package,
        authority={"principal": "did:example:attacker", "power": "unbounded"},
    )

    replay = verify_package(problem, expanded)

    assert replay.receipt_binding is GateStatus.FAIL
    assert any("authority differs" in reason for reason in replay.reasons)


def test_noncanonical_equivalent_formula_is_rejected_before_hashing():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem)
    assert result.package is not None
    duplicated = {
        "op": "and",
        "args": [
            atom("signal", (variable("x0"),)),
            atom("signal", (variable("x0"),)),
        ],
    }
    noncanonical = dataclasses.replace(result.package, definition=duplicated)

    replay = verify_package(problem, noncanonical)

    assert replay.definability is GateStatus.FAIL
    assert any("not in canonical" in reason for reason in replay.reasons)


def test_unknown_or_timeout_state_is_not_a_math_certificate():
    problem = _base_problem(
        constraints=(),
        synthesis_policy=SynthesisPolicy(
            reference_max_ground_atoms=1,
            reference_max_models=2,
            max_candidate_atoms=8,
        ),
    )

    result = synthesize(problem)

    assert result.status is SynthesisStatus.INDETERMINATE
    assert result.certificate is not None
    assert result.certificate.kind is FailureKind.RESOURCE_LIMIT
    assert not verify_failure_certificate(problem, result.certificate)


def test_overlap_violation_emits_topology_certificate():
    signature = Signature(
        sorts={"Item": ("a",)},
        relations={
            "left_seen": RelationDecl("left_seen", ("Item",)),
            "right_seen": RelationDecl("right_seen", ("Item",)),
            "target": RelationDecl("target", ("Item",)),
        },
    )
    problem = SeamProblem(
        problem_id="overlap",
        signature=signature,
        local_theories=(LocalTheory("left", ()), LocalTheory("right", ())),
        overlap_maps=(
            OverlapMap(
                left_owner="left",
                right_owner="right",
                left_relation="left_seen",
                right_relation="right_seen",
                argument_map=(0,),
            ),
        ),
        target_predicate="target",
        shared_vocabulary=("left_seen", "right_seen"),
        protected_signatures={
            "left": ("left_seen",),
            "right": ("right_seen",),
        },
        requested_judgment="boolean",
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )

    result = synthesize(problem)

    assert result.status is SynthesisStatus.ESCALATE
    assert result.certificate is not None
    assert result.certificate.kind is FailureKind.TOPOLOGY_OBSTRUCTION
    assert verify_failure_certificate(problem, result.certificate)


def test_constant_specific_definition_is_available_in_finite_language():
    x = variable("x")
    is_a = {
        "op": "eq",
        "sort": "Item",
        "left": x,
        "right": constant("a"),
    }
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), is_a),
        )
    )

    result = synthesize(problem)

    assert result.status is SynthesisStatus.COMPILED
    assert result.package is not None
    assert result.package.definition == {
        **is_a,
        "left": variable("x0"),
    }


def test_nonunique_exact_minimum_routes_to_choice_required():
    x = variable("x")
    signal_a = atom("signal", (constant("a"),))
    signal_b = atom("signal", (constant("b"),))
    problem = _base_problem(
        constraints=(
            {"op": "iff", "left": signal_a, "right": signal_b},
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )

    result = synthesize(problem)

    assert result.status is SynthesisStatus.CHOICE_REQUIRED
    assert result.certificate is not None
    assert result.certificate.kind is FailureKind.NON_UNIQUE_MINIMUM
    assert len(result.alternatives) >= 2
    assert verify_failure_certificate(
        problem,
        result.certificate,
        alternatives=result.alternatives,
    )


def test_nonnormative_choice_has_stable_class_and_canonical_term():
    from bulla.experimental.invention import (
        build_choice_analysis,
        canonical_choice_representative,
    )

    x = variable("x")
    problem = _base_problem(
        constraints=(_forall_iff(atom("target", (x,)), atom("signal", (x,))),)
    )
    package = synthesize(problem).package
    duplicate = dataclasses.replace(
        package,
        proof_references=package.proof_references + ({"kind": "independent-replay"},),
    )
    assert duplicate.package_hash != package.package_hash
    representative = canonical_choice_representative(problem, (duplicate, package))
    assert representative.definition == package.definition
    # One class is canonicalized, not escalated into governance.
    with pytest.raises(ValueError, match="canonicalized"):
        build_choice_analysis(problem, (duplicate, package), {})


def test_zero_import_checker_recomputes_choice_quotient(tmp_path):
    x = variable("x")
    problem = _base_problem(
        constraints=(
            {
                "op": "iff",
                "left": atom("signal", (constant("a"),)),
                "right": atom("signal", (constant("b"),)),
            },
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem).to_dict()
    problem_path = tmp_path / "problem.json"
    result_path = tmp_path / "result.json"
    problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"
    valid = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert valid.returncode == 0
    assert json.loads(valid.stdout)["choice_analysis_valid"] is True

    result["choice_analysis"]["classes"][0]["protected_behavior_hash"] = "sha256:" + "00" * 32
    result_path.write_text(json.dumps(result), encoding="utf-8")
    invalid = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert invalid.returncode != 0
    assert json.loads(invalid.stdout)["choice_analysis_valid"] is False


def test_zero_import_checker_matches_library_verdict(tmp_path):
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        ),
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )
    result = synthesize(problem)
    problem_path = tmp_path / "problem.json"
    result_path = tmp_path / "result.json"
    problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
    result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    standalone = json.loads(completed.stdout)
    library = verify_package(problem, result.package)
    assert standalone["ok"] is True
    assert standalone["result_hash"] == result.result_hash
    assert standalone["package_gates"] == library.to_dict()


def test_zero_import_checker_rejects_nested_non_frsl_field(tmp_path):
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        ),
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )
    result = synthesize(problem).to_dict()
    result["package"]["definition"]["host_regex"] = ".*"
    problem_path = tmp_path / "problem.json"
    result_path = tmp_path / "result.json"
    problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert json.loads(completed.stdout)["ok"] is False


def test_zero_import_checker_rejects_authority_expansion(tmp_path):
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        ),
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )
    result = synthesize(problem).to_dict()
    result["package"]["authority"] = {
        "principal": "did:example:attacker",
        "power": "unbounded",
    }
    problem_path = tmp_path / "problem.json"
    result_path = tmp_path / "result.json"
    problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["package_gates"]["receipt_binding"] == "fail"


def test_zero_import_checker_replays_choice_certificate(tmp_path):
    x = variable("x")
    signal_a = atom("signal", (constant("a"),))
    signal_b = atom("signal", (constant("b"),))
    problem = _base_problem(
        constraints=(
            {"op": "iff", "left": signal_a, "right": signal_b},
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        )
    )
    result = synthesize(problem)
    problem_path = tmp_path / "problem.json"
    result_path = tmp_path / "result.json"
    problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
    result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(problem_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["certificate_valid"] is True


def test_zero_import_checker_replays_partial_and_escalate_exits(tmp_path):
    cases = (
        (_partial_problem(), SynthesisStatus.PARTIAL),
        (_base_problem(constraints=()), SynthesisStatus.ESCALATE),
    )
    script = Path(__file__).parent.parent / "scripts" / "verify_invention.py"
    for index, (problem, expected_status) in enumerate(cases):
        result = synthesize(problem)
        assert result.status is expected_status
        problem_path = tmp_path / f"problem-{index}.json"
        result_path = tmp_path / f"result-{index}.json"
        problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
        result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(script), str(problem_path), str(result_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["ok"] is True
        assert payload["status"] == expected_status.value
        assert payload["certificate_valid"] is True


def test_invention_uses_ordinary_action_receipt_without_wire_change():
    x = variable("x")
    problem = _base_problem(
        constraints=(
            _forall_iff(atom("target", (x,)), atom("signal", (x,))),
        ),
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )
    result = synthesize(problem)
    envelope = RecourseEnvelope(
        authority=Authority(
            principal="did:example:owner",
            policy="policy:sha256:test",
        ),
        bounds=Bounds(scope="test seam"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://log.example.test",
                trusted_root_ref="sha256:root",
            ),
            remedies=(
                Remedy(
                    rung="recompute",
                    verifier="verify_invention.py",
                    anchor=result.result_hash,
                ),
            ),
        ),
    )

    receipt = mint_invention_receipt(
        problem,
        result,
        envelope=envelope,
        timestamp="2026-07-17T00:00:00Z",
        producer={"bulla_version": "test"},
    )

    assert receipt.schema_version == "0.2"
    assert receipt.action["type"] == "bulla.invent"
    assert receipt.action["subject"]["problem_hash"] == problem.problem_hash
    assert verify_receipt(receipt.to_dict()).ok


def test_failed_invention_receipt_binds_certificate_not_impossibility_by_timeout():
    problem = _base_problem(constraints=())
    result = synthesize(problem)
    assert result.status is SynthesisStatus.ESCALATE
    envelope = RecourseEnvelope(
        authority=Authority(
            principal="did:example:owner",
            policy="policy:sha256:test",
        ),
        bounds=Bounds(scope="test seam"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://log.example.test",
                trusted_root_ref="sha256:root",
            ),
            remedies=(
                Remedy(
                    rung="recompute",
                    verifier="verify_invention.py",
                    anchor=result.result_hash,
                ),
            ),
        ),
    )

    receipt = mint_invention_receipt(
        problem,
        result,
        envelope=envelope,
        timestamp="2026-07-17T00:00:00Z",
        producer={"bulla_version": "test"},
    )

    subject = receipt.action["subject"]
    assert subject["package_hash"] is None
    assert subject["certificate_hash"].startswith("sha256:")
    assert verify_receipt(receipt.to_dict()).ok
