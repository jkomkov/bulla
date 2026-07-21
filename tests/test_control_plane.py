from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla.action_receipt import sign_action_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.control_plane import (
    ApplicationStatus,
    CompiledTermCache,
    apply_package,
    build_control_plane_reliance_receipt,
    compilation_key,
    mint_selection_receipt,
    mint_application_receipt,
    reliance_scope,
    verify_control_plane_reliance,
    verify_application_receipt,
    verify_selection_receipt,
)
from bulla.experimental.invention import SeamProblem, SynthesisStatus, synthesize
from bulla.identity import LocalEd25519Signer
from bulla.reliance import PRAGMATIC_RELIANCE_POLICY, RelianceError


ROOT = Path(__file__).resolve().parents[1]


def _problem(instance_id: str) -> SeamProblem:
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    document = next(x["problem"] for x in corpus["instances"] if x["id"] == instance_id)
    return SeamProblem.from_dict(document)


def _shared_structure(problem: SeamProblem, truths: dict[str, list[list[str]]]):
    return {name: truths.get(name, []) for name in problem.shared_vocabulary}


def test_compile_once_apply_many_and_cache_invalidation(tmp_path):
    problem = _problem("units-0")
    result = synthesize(problem)
    assert result.status is SynthesisStatus.COMPILED and result.package is not None
    structure = _shared_structure(
        problem,
        {"canonical_quantity": [["meter"]]},
    )
    relied = apply_package(
        problem,
        result.package,
        shared_structure=structure,
        target_arguments=("meter",),
        adapter_version="units-adapter/1",
    )
    refused = apply_package(
        problem,
        result.package,
        shared_structure=structure,
        target_arguments=("foot",),
        adapter_version="units-adapter/1",
    )
    assert relied.status is ApplicationStatus.RELY
    assert refused.status is ApplicationStatus.REFUSE
    with pytest.raises(TypeError):
        bool(relied)

    cache = CompiledTermCache(tmp_path)
    key = cache.put(problem, result, adapter_version="units-adapter/1")
    cached_problem, cached_result = cache.get(key, adapter_version="units-adapter/1")
    assert cached_problem.problem_hash == problem.problem_hash
    assert cached_result.result_hash == result.result_hash
    assert key == compilation_key(
        problem,
        adapter_version="units-adapter/1",
        verifier=result.verifier,
    )
    with pytest.raises(ValueError, match="stale"):
        cache.get(key, adapter_version="units-adapter/2")


def test_partial_package_retains_escalation_residual():
    problem = _problem("null_absent-2")
    result = synthesize(problem)
    assert result.status is SynthesisStatus.PARTIAL and result.package is not None
    statuses = set()
    for element in problem.signature.sorts[problem.target_decl.sorts[0]]:
        for truth in (False, True):
            relation = problem.shared_vocabulary[0]
            structure = _shared_structure(
                problem,
                {relation: [[element]] if truth else []},
            )
            statuses.add(
                apply_package(
                    problem,
                    result.package,
                    shared_structure=structure,
                    target_arguments=(element,),
                    adapter_version="null-adapter/1",
                ).status
            )
    assert ApplicationStatus.ESCALATE in statuses
    assert result.enrichment_plans[0].axis.value == "evidence"


def test_choice_receipt_binds_offered_package_and_selector_authority():
    problem = _problem("enums-4")
    result = synthesize(problem)
    assert result.status is SynthesisStatus.CHOICE_REQUIRED
    assert result.choice_analysis is not None
    assert result.choice_analysis.kind.value == "normative"
    selected = result.alternatives[0]
    signer = LocalEd25519Signer(seed=bytes([91]) + bytes(31))
    envelope = RecourseEnvelope(
        authority=Authority(
            principal=problem.authority["principal"],
            policy=problem.authority["policy"],
        ),
        bounds=Bounds(scope="selection over the offered exact-minimal packages"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(
                log_endpoint="https://witness.invalid",
                trusted_root_ref="sha256:" + "ab" * 32,
            ),
            remedies=(
                Remedy("recompute", "bulla experimental verify-invention", result.result_hash),
            ),
        ),
    )
    receipt = mint_selection_receipt(
        problem,
        result,
        selected_package_hash=selected.package_hash,
        envelope=envelope,
        timestamp="2026-07-18T00:00:00Z",
        producer={"test": True},
    )
    signed = sign_action_receipt(receipt, signer).to_dict()
    report = verify_selection_receipt(signed, problem, result)
    assert report["ok"] is True

    tampered = json.loads(json.dumps(signed))
    tampered["action"]["subject"]["selected_package_hash"] = "sha256:" + "00" * 32
    assert verify_selection_receipt(tampered, problem, result)["ok"] is False


def test_adapter_cannot_supply_private_or_target_relations():
    problem = _problem("units-0")
    result = synthesize(problem)
    with pytest.raises(ValueError, match="exactly shared_vocabulary"):
        apply_package(
            problem,
            result.package,
            shared_structure={
                "canonical_quantity": [["meter"]],
                "target": [["meter"]],
            },
            target_arguments=("meter",),
            adapter_version="bad/1",
        )


def test_control_plane_reliance_binds_exact_policy_and_scope():
    problem = _problem("units-0")
    result = synthesize(problem)
    actor = LocalEd25519Signer(seed=bytes([71]) + bytes(31))
    relier = LocalEd25519Signer(seed=bytes([72]) + bytes(31))
    invention_envelope = RecourseEnvelope(
        authority=Authority(principal=actor.issuer, policy="policy://invent@sha256:aa"),
        bounds=Bounds(scope="finite invention act"),
    )
    from bulla.experimental.invention import mint_invention_receipt

    relied_on = sign_action_receipt(
        mint_invention_receipt(
            problem,
            result,
            envelope=invention_envelope,
            timestamp="2026-07-18T00:00:00Z",
            producer={"test": True},
        ),
        actor,
    ).to_dict()
    policy = PRAGMATIC_RELIANCE_POLICY
    policy_ref = f"{policy.name}@{policy.policy_hash}"
    envelope = RecourseEnvelope(
        authority=Authority(principal=relier.issuer, policy=policy_ref),
        bounds=Bounds(scope=reliance_scope(policy)),
        deed_schema="0.3",
    )
    reliance = build_control_plane_reliance_receipt(
        relied_on=relied_on,
        policy=policy,
        envelope=envelope,
        timestamp="2026-07-18T00:01:00Z",
    )
    signed = sign_action_receipt(reliance, relier).to_dict()
    assert verify_control_plane_reliance(signed, relied_on, policy)["ok"]

    wrong = RecourseEnvelope(
        authority=Authority(principal=relier.issuer, policy="policy://similar@sha256:bb"),
        bounds=Bounds(scope=reliance_scope(policy)),
        deed_schema="0.3",
    )
    with pytest.raises(RelianceError, match="exact policy"):
        build_control_plane_reliance_receipt(relied_on=relied_on, policy=policy, envelope=wrong)


def test_application_receipt_replays_data_plane_decision():
    problem = _problem("units-0")
    result = synthesize(problem)
    structure = _shared_structure(problem, {"canonical_quantity": [["meter"]]})
    application = apply_package(
        problem,
        result.package,
        shared_structure=structure,
        target_arguments=("meter",),
        adapter_version="units-adapter/1",
    )
    signer = LocalEd25519Signer(seed=bytes([81]) + bytes(31))
    envelope = RecourseEnvelope(
        authority=Authority(principal=signer.issuer, policy="policy://apply@sha256:aa"),
        bounds=Bounds(scope="apply one pinned package to one shared structure"),
    )
    receipt = sign_action_receipt(
        mint_application_receipt(
            application,
            envelope=envelope,
            timestamp="2026-07-18T00:00:00Z",
            producer={"test": True},
        ),
        signer,
    ).to_dict()
    assert verify_application_receipt(
        receipt,
        problem,
        result.package,
        shared_structure=structure,
        target_arguments=("meter",),
        adapter_version="units-adapter/1",
    )["ok"]
    assert not verify_application_receipt(
        receipt,
        problem,
        result.package,
        shared_structure=structure,
        target_arguments=("foot",),
        adapter_version="units-adapter/1",
    )["ok"]
