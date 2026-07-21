from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from bulla.experimental.constitutional import (
    AuthorityPermission,
    AuthorityRegime,
    ClosureStatus,
    ModelClosureWarrant,
    ObservationAuthorization,
    ObservationAuthorizationBasis,
    ObservationConstitution,
    constitutional_admission,
    filter_observation_offers,
    mint_authorization_receipt,
    verify_authorization_receipt,
    sign_observation_authorization,
)
from bulla.experimental.frsl import atom, canonical_hash, falsity, truth, variable
from bulla.experimental.invention import SeamProblem
from bulla.experimental.pilot import (
    ARMS,
    ChallengeReceipt,
    DOMAINS,
    STRATA,
    PilotAction,
    analysis_gate,
    assign_arm,
    operational_slice_gate,
    sign_pilot_artifact,
)
from bulla.experimental.refinement import (
    AdmissionKind,
    ConstraintAdmission,
    EnvelopeRegions,
    EnvelopeSnapshot,
    authority_epoch,
    semantic_epoch,
)
from bulla.experimental.semantic_finality import (
    ConsequenceClass,
    ConsequenceProfile,
    ExternalLock,
    FinalityStatus,
    SemanticFinalityPolicy,
    assess_finality,
    calculate_reserve,
    release_reserve,
)
from bulla.identity import LocalEd25519Signer
from bulla.experimental.observability import BurdenVector, ObservableOffer


ROOT = Path(__file__).resolve().parents[1]
D = "sha256:" + "11" * 32


def _problem() -> SeamProblem:
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    return SeamProblem.from_dict(corpus["instances"][0]["problem"])


def _warrant(status: ClosureStatus = ClosureStatus.BOUNDED_EXACT) -> ModelClosureWarrant:
    return ModelClosureWarrant(
        status=status,
        model_class={"name": "declared finite FRSL structures", "version": "1"},
        generation_method={"kind": "exhaustive", "checker": "reference/1"},
        exclusions=("undeclared external state",),
        domain_authority={"principal": "did:example:domain", "policy": "closure-policy@1"},
        adversarial_expansion_evidence=({"kind": "boundary-fixtures", "hash": D},),
        scope={"domain": "procurement-shadow", "term": "delivery"},
    )


def _snapshot(warrant: ModelClosureWarrant) -> EnvelopeSnapshot:
    auth_epoch = canonical_hash({"authority": "epoch-1"})
    return EnvelopeSnapshot(
        base_problem_hash=D, effective_problem_hash=D, result_hash=D, package_hash=D,
        package_mode="partial", semantic_state_hash=D, passport_hash=D, manifest_hash=D,
        authority_epoch=auth_epoch, closure_warrant_hash=warrant.warrant_hash,
        semantic_epoch=semantic_epoch(auth_epoch, warrant.warrant_hash),
        regions=EnvelopeRegions(reachable=(D,), rely=(), refuse=(), ambiguous=(D,)),
    )


def _profile() -> ConsequenceProfile:
    return ConsequenceProfile(
        action_hash=canonical_hash({"action": "pay-invoice-17"}), currency="USD",
        target_arguments=("shipment-17",),
        consequence_classes=(
            ConsequenceClass("DISPATCH_ONLY", truth(), 1_000_000),
            ConsequenceClass("CUSTODY_TRANSFER", falsity(), 0),
        ),
        maximum_credible_loss_microunits=1_000_000,
        settlement_target={"kind": "simulated-escrow", "invoice": "17"},
        external_verifier={"id": "shadow-escrow/1", "independent_recompute": True},
    )


def _policy() -> SemanticFinalityPolicy:
    return SemanticFinalityPolicy(
        permitted_closure_statuses=(ClosureStatus.BOUNDED_EXACT, ClosureStatus.FINITE_EXACT),
        maximum_reserve_microunits=1_100_000, finality_threshold=1,
        permitted_observation_classes=("carrier_attestation",),
        required_authorities=("operative", "refinement", "supersession"),
        provisional_execution_allowed=True,
        provisional_action_types=("procurement.payment",),
    )


def test_differential_authority_receipt_binds_exact_role_scope_and_subject() -> None:
    signer = LocalEd25519Signer(seed=bytes([51]) + bytes(31))
    permission = AuthorityPermission(
        signer.issuer, "policy:refinement@1", ("delivery-evidence",),
    )
    subject = {"admission_hash": D, "prior_snapshot_hash": D, "scope": "delivery-evidence", "authority_epoch": D}
    receipt = mint_authorization_receipt(
        action_type="bulla.semantic.refine.authorize", subject=subject,
        permission=permission, signer=signer, scope="delivery-evidence",
        timestamp="2026-07-18T00:00:00Z",
    )
    assert verify_authorization_receipt(
        receipt, action_type="bulla.semantic.refine.authorize",
        expected_subject=subject, permission=permission, scope="delivery-evidence",
    )
    assert not verify_authorization_receipt(
        receipt, action_type="bulla.semantic.revise.authorize",
        expected_subject=subject, permission=permission, scope="delivery-evidence",
    )
    assert receipt.envelope.retention_class == "authority-permanent"


def test_conflict_quarantines_claims_and_does_not_mutate_state() -> None:
    problem = _problem()
    admission = ConstraintAdmission(
        kind=AdmissionKind.PRECEDENT, constraint=falsity(),
        provenance={"warrant_hash": D, "authority": "did:example:buyer"},
        authority_epoch=authority_epoch(problem.authority),
    )
    result = constitutional_admission(
        problem, (), admission,
        authority_claims=("did:example:buyer", "did:example:seller"),
        forum="forum://procurement-shadow",
    )
    assert result.outcome.value == "CONFLICT"
    assert result.next_state_hash is None
    assert result.conflict is not None
    assert result.conflict.operative_state_unchanged
    assert result.conflict.to_dict()["transition"] == "ROUTE"
    with pytest.raises(TypeError):
        bool(result)


def test_observation_constitution_filters_signed_basis_before_optimization() -> None:
    provider = LocalEd25519Signer(seed=bytes([52]) + bytes(31))
    offer = ObservableOffer(
        offer_id="carrier-custody", relation="carrier_custody", sorts=("Record",),
        meaning=atom("target", [variable("x0")]), provider=provider.issuer,
        warrant_profile={
            "kind": "signed_attestation", "evidence_class": "signed_attestation",
            "verifier": "carrier-warrant/1", "reveals": "boolean_fact_only",
        },
        burden=BurdenVector(disclosure_units=1, latency_ms=50),
        consent_subjects=(provider.issuer,),
    )
    unsigned = ObservationAuthorization(
        offer_hash=offer.offer_hash, subject=provider.issuer,
        basis=ObservationAuthorizationBasis.CONTRACT,
        authorization_ref="contract://shipment-17", purpose="delivery-settlement",
        scope={"shipment": "17"}, issuer=provider.issuer,
    )
    authorization = sign_observation_authorization(unsigned, provider)
    constitution = ObservationConstitution(
        permitted_observables=(offer.offer_id,), prohibited_observables=(),
        purposes=("delivery-settlement",), prohibited_reuse=("marketing",),
        maximum_burden=BurdenVector(disclosure_units=2, latency_ms=100),
        permitted_providers=(provider.issuer,),
        permitted_warrant_classes=("signed_attestation",),
        permitted_authorization_bases=(ObservationAuthorizationBasis.CONTRACT,),
        retention_policy="delete raw carrier payload after Boolean fact",
        challenge_policy="challenge://carrier/30d",
    )
    assert filter_observation_offers(
        (offer,), (authorization,), constitution, purpose="delivery-settlement"
    ) == (offer,)
    assert filter_observation_offers(
        (offer,), (unsigned,), constitution, purpose="delivery-settlement"
    ) == ()


def test_reserve_is_exact_worst_case_plus_buffer_and_antitone_under_refinement() -> None:
    warrant = _warrant()
    snapshot = _snapshot(warrant)
    profile = _profile()
    prior = calculate_reserve(
        profile, ("DISPATCH_ONLY", "CUSTODY_TRANSFER"),
        semantic_epoch=snapshot.semantic_epoch, closure_warrant=warrant,
        model_risk_buffer_microunits=100_000, expiry="2026-07-20T00:00:00Z",
    )
    refined = calculate_reserve(
        profile, ("CUSTODY_TRANSFER",), semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=warrant, model_risk_buffer_microunits=100_000,
        expiry="2026-07-20T00:00:00Z",
    )
    release = release_reserve(prior, refined)
    assert prior.required_reserve_microunits == 1_100_000
    assert refined.required_reserve_microunits == 100_000
    assert release.released_microunits == 1_000_000
    assert release.antitone and release.outcome_subset
    with pytest.raises(ValueError, match="antitonicity"):
        release_reserve(refined, prior)


def test_finality_precedence_provisional_finalize_refuse_stale_and_open_world() -> None:
    warrant = _warrant()
    snapshot = _snapshot(warrant)
    profile = _profile()
    reserve = calculate_reserve(
        profile, ("DISPATCH_ONLY", "CUSTODY_TRANSFER"),
        semantic_epoch=snapshot.semantic_epoch, closure_warrant=warrant,
        model_risk_buffer_microunits=100_000, expiry="2026-07-20T00:00:00Z",
    )
    lock = ExternalLock(
        "escrow://shadow/17", {"id": "shadow-escrow/1"},
        reserve.required_reserve_microunits, "USD", "LOCKED",
        ({"kind": "simulated-balance", "hash": D},), simulated=True,
    )
    reserve = dataclasses.replace(reserve, external_lock_reference=lock.lock_reference, collectibility_evidence=lock.collectibility_evidence)
    common = dict(
        snapshot=snapshot, current_semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=warrant, authority_regime_hash=D,
        consequence_profile=profile, policy=_policy(),
    )
    provisional = assess_finality(
        **common, represented_outcomes=reserve.represented_outcomes,
        certified_surface="AMBIGUOUS", reserve=reserve, external_lock=lock,
    )
    assert provisional.status is FinalityStatus.EXECUTE_PROVISIONALLY
    finalized = assess_finality(
        **common, represented_outcomes=("CUSTODY_TRANSFER",), certified_surface="RELY",
    )
    assert finalized.status is FinalityStatus.FINALIZE
    refused = assess_finality(
        **common, represented_outcomes=("DISPATCH_ONLY",), certified_surface="REFUSE",
    )
    assert refused.status is FinalityStatus.REFUSE
    stale = assess_finality(
        **{**common, "current_semantic_epoch": D},
        represented_outcomes=("CUSTODY_TRANSFER",), certified_surface="RELY",
    )
    assert stale.status is FinalityStatus.TERM_STALE
    open_warrant = _warrant(ClosureStatus.OPEN_WORLD)
    open_snapshot = _snapshot(open_warrant)
    open_policy = dataclasses.replace(_policy(), permitted_closure_statuses=(ClosureStatus.OPEN_WORLD,))
    open_result = assess_finality(
        snapshot=open_snapshot, current_semantic_epoch=open_snapshot.semantic_epoch,
        closure_warrant=open_warrant, authority_regime_hash=D,
        consequence_profile=profile, represented_outcomes=("CUSTODY_TRANSFER",),
        policy=open_policy, certified_surface="RELY",
    )
    assert open_result.status is FinalityStatus.ROUTE
    with pytest.raises(TypeError):
        bool(finalized)


def test_pilot_operations_are_signed_but_analysis_remains_fail_closed() -> None:
    signers = [LocalEd25519Signer(seed=bytes([60 + i]) + bytes(31)) for i in range(6)]
    submissions = []
    judgments = []
    index = 0
    for domain in DOMAINS:
        for stratum in STRATA:
            for _ in range(2):
                blind = canonical_hash({"seam": index})
                subject = {
                    "blinded_id": blind, "domain": domain, "stratum": stratum,
                    "arm": ARMS[index % 4], "deadline": "2026-08-01T00:00:00Z",
                }
                submissions.append(sign_pilot_artifact(PilotAction.SEAM_SUBMIT, subject, signer=signers[index % 3], issued_at="2026-07-18T00:00:00Z"))
                for judge in signers[3:5]:
                    judgments.append(sign_pilot_artifact(PilotAction.ADJUDICATE, {"blinded_id": blind, "verdict": "operational-only"}, signer=judge, issued_at="2026-07-18T00:00:00Z"))
                index += 1
    # Add a third independent adjudicator without changing two-per-seam by
    # assigning the second judgment of one-third of cases to signer 6.
    for position in range(1, len(judgments), 6):
        original = judgments[position]
        judgments[position] = sign_pilot_artifact(PilotAction.ADJUDICATE, original.subject, signer=signers[5], issued_at=original.issued_at)
    operational = operational_slice_gate(
        accepted_submissions=submissions, adjudications=judgments,
        implementation_team_ids=("did:example:implementation",),
    )
    assert operational["operational_slice_ready"]
    assert operational["analysis_authorized"] is False
    closed = analysis_gate(
        accepted_submissions=submissions, adjudications=judgments,
        implementation_team_ids=("did:example:implementation",),
    )
    assert closed.status == "REFUSED"
    assert "accepted_seam_count_not_300" in closed.causes
    assert assign_arm(D, preregistered_salt="frozen-salt") == assign_arm(D, preregistered_salt="frozen-salt")


def test_hostile_challenge_and_disposition_are_both_signed_and_hash_bound() -> None:
    challenger = LocalEd25519Signer(seed=bytes([70]) + bytes(31))
    reviewer = LocalEd25519Signer(seed=bytes([71]) + bytes(31))
    challenge = sign_pilot_artifact(
        PilotAction.CHALLENGE, {"finding_id": "closure-laundering", "claim": "open world finalizes"},
        signer=challenger, issued_at="2026-07-18T00:00:00Z",
    )
    disposition = sign_pilot_artifact(
        PilotAction.CHALLENGE_DISPOSE,
        {"challenge_hash": challenge.artifact_hash, "status": "ACCEPTED", "reason": "guard routed", "bounty_reference": "bounty://internal/1"},
        signer=reviewer, issued_at="2026-07-18T00:00:00Z",
    )
    receipt = ChallengeReceipt(challenge, disposition)
    assert receipt.receipt_hash.startswith("sha256:")
    with pytest.raises(ValueError, match="hash-bound"):
        ChallengeReceipt(challenge, dataclasses.replace(disposition, subject={**disposition.subject, "challenge_hash": D}))
