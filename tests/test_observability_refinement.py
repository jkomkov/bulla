from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.action_receipt import sign_action_receipt
from bulla.envelope import Authority, Bounds, RecourseEnvelope
from bulla.experimental.control_plane import ApplicationStatus
from bulla.experimental.frsl import atom, canonical_hash, variable
from bulla.experimental.invention import SeamProblem, SynthesisStatus, synthesize
from bulla.experimental.observability import (
    BurdenVector,
    ConservationManifest,
    EnrichmentResponse,
    LogicPassport,
    ObservableOffer,
    PlanningStatus,
    ProvidedFact,
    ResponseStatus,
    build_enrichment_request,
    mint_enrichment_request_receipt,
    plan_enrichment,
    sign_enrichment_response,
    verify_enrichment_plan,
    verify_enrichment_response,
)
from bulla.experimental.refinement import (
    ApplicationCause,
    TransitionKind,
    ConstraintAdmission,
    RefinementBundle,
    apply_snapshot,
    authority_epoch,
    classify_transition,
    build_evidence_admission,
    refine_envelope,
    semantic_compilation_key,
    semantic_state,
    supersede_term,
    verify_refinement,
)
from bulla.identity import LocalEd25519Signer


ROOT = Path(__file__).resolve().parents[1]


def _problem() -> SeamProblem:
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    document = next(
        item["problem"] for item in corpus["instances"] if item["id"] == "null_absent-2"
    )
    return SeamProblem.from_dict(document)


def _offer(
    provider: str,
    *,
    offer_id: str = "source-final-disposition",
    burden: BurdenVector | None = None,
    consent_subjects: tuple[str, ...] | None = None,
) -> ObservableOffer:
    return ObservableOffer(
        offer_id=offer_id,
        relation=offer_id.replace("-", "_"),
        sorts=("Record",),
        meaning=atom("target", [variable("x0")]),
        provider=provider,
        warrant_profile={
            "kind": "signed_attestation",
            "evidence_class": "signed_attestation",
            "verifier": "source-attestation-profile/1",
            "reveals": "boolean_fact_only",
        },
        burden=burden or BurdenVector(disclosure_units=3, latency_ms=10),
        consent_subjects=consent_subjects or (provider,),
    )


def _facts(relation: str) -> tuple[ProvidedFact, ...]:
    refs = {
        "value": "sha256:" + "ab" * 32,
        "null": "sha256:" + "cd" * 32,
        "absent": "sha256:" + "ef" * 32,
    }
    return tuple(
        ProvidedFact(
            relation=relation,
            arguments=(value,),
            truth=value == "value",
            evidence_class="signed_attestation",
            warrant_ref=refs[value],
        )
        for value in ("value", "null", "absent")
    )


def _context(problem: SeamProblem):
    return LogicPassport.for_problem(problem), ConservationManifest.for_problem(problem)


def test_exact_planner_enumerates_full_pair_cover_and_pareto_frontier() -> None:
    problem = _problem()
    passport, manifest = _context(problem)
    first = LocalEd25519Signer(seed=bytes([31]) + bytes(31))
    second = LocalEd25519Signer(seed=bytes([32]) + bytes(31))
    offers = (
        _offer(
            first.issuer,
            offer_id="fast-private",
            burden=BurdenVector(disclosure_units=3, latency_ms=1),
        ),
        _offer(
            second.issuer,
            offer_id="slow-minimal",
            burden=BurdenVector(disclosure_units=1, latency_ms=100),
        ),
    )
    result = plan_enrichment(problem, offers, passport=passport, manifest=manifest)
    assert result.status is PlanningStatus.PLANNED
    assert result.opposing_pair_count > 1
    assert len(result.plans) == 2
    assert all(plan.pareto_status == "frontier" for plan in result.plans)
    assert result.indispensable_observables == ()
    assert all(
        verify_enrichment_plan(
            problem,
            offers,
            plan,
            passport=passport,
            manifest=manifest,
        )
        for plan in result.plans
    )
    assert "model_hashes" not in json.dumps(result.to_dict())


def test_catalog_bound_is_indeterminate_not_impossibility() -> None:
    problem = _problem()
    passport, manifest = _context(problem)
    signer = LocalEd25519Signer(seed=bytes([33]) + bytes(31))
    offers = tuple(
        _offer(signer.issuer, offer_id=f"observable-{index}") for index in range(17)
    )
    result = plan_enrichment(problem, offers, passport=passport, manifest=manifest)
    assert result.status is PlanningStatus.INDETERMINATE
    assert "bound" in result.reason


def test_transition_classifier_rejects_same_epoch_widening() -> None:
    problem = _problem()
    _, prior = semantic_state(problem)
    assert classify_transition(prior, prior) is TransitionKind.PRESERVE
    assert classify_transition(prior, routed=True) is TransitionKind.ROUTE
    revised = dataclasses.replace(prior, authority_epoch="sha256:" + "12" * 32)
    assert classify_transition(prior, revised) is TransitionKind.REVISE
    refined = dataclasses.replace(prior, model_hashes=prior.model_hashes[:-1])
    assert classify_transition(prior, refined) is TransitionKind.REFINE
    with pytest.raises(ValueError, match="widening"):
        classify_transition(refined, prior)


def test_frozen_refinement_artifacts_close_the_internal_gate() -> None:
    scaling = json.loads(
        (ROOT / "bench/invention/results/refinement-scaling-2026-07-18.json").read_text()
    )
    artifact_hash = scaling.pop("artifact_hash")
    assert canonical_hash(scaling) == artifact_hash
    assert scaling["freeze"]["case_count"] == 240
    assert scaling["summary"]["result_verification_failures"] == 0
    assert scaling["summary"]["plan_verification_failures"] == 0
    assert set(scaling["summary"]["synthesis_statuses"]) == {
        "COMPILED",
        "ESCALATE",
        "INDETERMINATE",
    }

    demo = json.loads(
        (ROOT / "examples/certified-semantic-refinement/demo-output.json").read_text()
    )
    assert demo["transitions"] == {
        "unchanged_context": "PRESERVE",
        "admitted_constraint": "REFINE",
        "unresolved_case": "ROUTE",
        "authority_epoch_change": "REVISE",
    }
    assert demo["decisions"]["after_epoch_change"] == "ESCALATE"
    assert all(item["ok"] for item in demo["standalone"].values())


def test_same_reduct_request_requires_complete_consent_and_warranted_extension() -> None:
    problem = _problem()
    synthesis = synthesize(problem)
    assert synthesis.status is SynthesisStatus.PARTIAL
    passport, manifest = _context(problem)
    provider = LocalEd25519Signer(seed=bytes([34]) + bytes(31))
    counterparty = LocalEd25519Signer(seed=bytes([35]) + bytes(31))
    offer = _offer(
        provider.issuer,
        consent_subjects=(provider.issuer, counterparty.issuer),
    )
    planning = plan_enrichment(problem, (offer,), passport=passport, manifest=manifest)
    request = build_enrichment_request(
        problem,
        synthesis,
        planning,
        (offer,),
        passport=passport,
        manifest=manifest,
        requester_authority=problem.authority,
    )
    plan_hash = planning.plans[0].plan_hash
    provide = sign_enrichment_response(
        EnrichmentResponse(
            request_hash=request.request_hash,
            responder=provider.issuer,
            status=ResponseStatus.PROVIDE,
            selected_plan_hash=plan_hash,
            provided_facts=_facts(offer.relation),
        ),
        provider,
    )
    assert verify_enrichment_response(request, provide)
    with pytest.raises(ValueError, match="missing operative consent"):
        build_evidence_admission(
            problem,
            request,
            selected_plan_hash=plan_hash,
            responses=(provide,),
            passport=passport,
            manifest=manifest,
            epoch=authority_epoch(problem.authority),
        )
    consent = sign_enrichment_response(
        EnrichmentResponse(
            request_hash=request.request_hash,
            responder=counterparty.issuer,
            status=ResponseStatus.CONSENT,
            selected_plan_hash=plan_hash,
        ),
        counterparty,
    )
    admission = build_evidence_admission(
        problem,
        request,
        selected_plan_hash=plan_hash,
        responses=(provide, consent),
        passport=passport,
        manifest=manifest,
        epoch=authority_epoch(problem.authority),
    )
    assert admission.request_hash == request.request_hash
    assert set(admission.response_hashes) == {provide.response_hash, consent.response_hash}

    incomplete = dataclasses.replace(provide, provided_facts=provide.provided_facts[:-1], proof=None)
    incomplete = sign_enrichment_response(incomplete, provider)
    with pytest.raises(ValueError, match="exactly one Boolean"):
        build_evidence_admission(
            problem,
            request,
            selected_plan_hash=plan_hash,
            responses=(incomplete, consent),
            passport=passport,
            manifest=manifest,
            epoch=authority_epoch(problem.authority),
        )


def test_refinement_is_monotone_replayable_and_epoch_stale_fails_closed(tmp_path) -> None:
    problem = _problem()
    prior = synthesize(problem)
    passport, manifest = _context(problem)
    provider = LocalEd25519Signer(seed=bytes([36]) + bytes(31))
    offer = _offer(provider.issuer)
    planning = plan_enrichment(problem, (offer,), passport=passport, manifest=manifest)
    request = build_enrichment_request(
        problem,
        prior,
        planning,
        (offer,),
        passport=passport,
        manifest=manifest,
        requester_authority=problem.authority,
    )
    response = sign_enrichment_response(
        EnrichmentResponse(
            request_hash=request.request_hash,
            responder=provider.issuer,
            status=ResponseStatus.PROVIDE,
            selected_plan_hash=planning.plans[0].plan_hash,
            provided_facts=_facts(offer.relation),
        ),
        provider,
    )
    epoch = authority_epoch(problem.authority)
    admission = build_evidence_admission(
        problem,
        request,
        selected_plan_hash=planning.plans[0].plan_hash,
        responses=(response,),
        passport=passport,
        manifest=manifest,
        epoch=epoch,
    )
    bundle = refine_envelope(
        problem,
        prior,
        admission,
        passport=passport,
        manifest=manifest,
    )
    assert bundle.new_result.status is SynthesisStatus.COMPILED
    assert bundle.certificate.valid
    assert verify_refinement(bundle)
    assert not bundle.new_snapshot.regions.ambiguous
    assert set(bundle.certificate.to_dict()) >= {
        "state_inclusion",
        "retained_rely",
        "retained_refuse",
        "ambiguity_narrowed",
    }

    structure = {name: [] for name in problem.shared_vocabulary}
    application = apply_snapshot(
        problem,
        bundle.new_result,
        bundle.new_snapshot,
        admissions=(admission,),
        current_authority_epoch=epoch,
        shared_structure=structure,
        target_arguments=("value",),
        adapter_version="null-adapter/1",
        passport=passport,
        manifest=manifest,
    )
    assert application.status is ApplicationStatus.RELY
    assert application.cause is ApplicationCause.DECIDED
    with pytest.raises(TypeError):
        bool(application)

    revised_authority = {"principal": "did:example:new", "policy": "policy:new:v2"}
    stale = apply_snapshot(
        problem,
        bundle.new_result,
        bundle.new_snapshot,
        admissions=(admission,),
        current_authority_epoch=authority_epoch(revised_authority),
        shared_structure=structure,
        target_arguments=("value",),
        adapter_version="null-adapter/1",
        passport=passport,
        manifest=manifest,
    )
    assert stale.status is ApplicationStatus.ESCALATE
    assert stale.cause is ApplicationCause.TERM_STALE
    supersession = supersede_term(
        bundle.new_snapshot,
        new_authority=revised_authority,
        reason="authority policy changed",
    )
    assert supersession.new_authority_epoch == authority_epoch(revised_authority)

    key = semantic_compilation_key(
        problem,
        bundle.new_result,
        bundle.new_snapshot,
        adapter_version="null-adapter/1",
    )
    assert key.startswith("sha256:")

    standalone = ROOT / "scripts/verify_invention.py"
    artifacts = {
        "problem": problem.to_dict(),
        "passport": passport.to_dict(),
        "manifest": manifest.to_dict(),
        "offers": [offer.to_dict()],
        "planning": planning.to_dict(),
        "refinement": bundle.to_dict(),
    }
    paths = {}
    for name, document in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths[name] = path
    plan_replay = subprocess.run(
        [
            sys.executable,
            "-I",
            str(standalone),
            "plan-enrichment",
            str(paths["problem"]),
            str(paths["passport"]),
            str(paths["manifest"]),
            str(paths["offers"]),
            str(paths["planning"]),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert plan_replay.returncode == 0, plan_replay.stdout + plan_replay.stderr
    assert json.loads(plan_replay.stdout)["ok"] is True
    refinement_replay = subprocess.run(
        [
            sys.executable,
            "-I",
            str(standalone),
            "verify-refinement",
            str(paths["refinement"]),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert refinement_replay.returncode == 0, (
        refinement_replay.stdout + refinement_replay.stderr
    )
    assert json.loads(refinement_replay.stdout)["ok"] is True

    tampered = bundle.to_dict()
    tampered["certificate"]["retained_rely"] = False
    parsed = RefinementBundle.from_dict(tampered)
    assert verify_refinement(parsed) is False


def test_enrichment_request_receipt_uses_existing_action_receipt_schema() -> None:
    problem = _problem()
    result = synthesize(problem)
    passport, manifest = _context(problem)
    signer = LocalEd25519Signer(seed=bytes([37]) + bytes(31))
    offer = _offer(signer.issuer)
    planning = plan_enrichment(problem, (offer,), passport=passport, manifest=manifest)
    request = build_enrichment_request(
        problem,
        result,
        planning,
        (offer,),
        passport=passport,
        manifest=manifest,
        requester_authority=problem.authority,
    )
    envelope = RecourseEnvelope(
        authority=Authority(principal=signer.issuer, policy=problem.authority["policy"]),
        bounds=Bounds(scope="request only the certified Boolean observable"),
    )
    receipt = sign_action_receipt(
        mint_enrichment_request_receipt(
            request,
            envelope=envelope,
            timestamp="2026-07-18T00:00:00Z",
            producer={"test": True},
        ),
        signer,
    ).to_dict()
    assert receipt["schema_version"] == "0.3"
    assert receipt["action"]["type"] == "bulla.enrich.request"
    assert receipt["action"]["subject"]["request_hash"] == request.request_hash
