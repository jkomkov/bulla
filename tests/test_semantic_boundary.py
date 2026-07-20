from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.experimental.invention import InventionError
from bulla.experimental.semantic_boundary import (
    ClaimChain,
    ClosureRiskAllocation,
    DerivationStatus,
    EntailmentClaim,
    EntailmentStatus,
    HarmTreatment,
    OutcomeTreatment,
    SettlementClaim,
    TraceCell,
    TraceDecision,
    SubstantiveBoundary,
    WorldClaim,
    WorldClaimStatus,
    assess_semantic_boundary,
    certify_trace_refinement,
    verify_trace_refinement,
)
from bulla.experimental.semantic_finality import (
    AmbiguityReserve,
    FinalityAssessment,
    FinalityStatus,
)


D = "sha256:" + "11" * 32
E = "sha256:" + "22" * 32
F = "sha256:" + "33" * 32
ROOT = Path(__file__).resolve().parents[1]


def base(status: FinalityStatus, reserve: AmbiguityReserve | None = None) -> FinalityAssessment:
    return FinalityAssessment(
        status=status,
        cause="TEST_BASE",
        available_alternatives=(),
        reserve=reserve,
        evidence_plan_hashes=(),
        authority_regime_hash=D,
        closure_warrant_hash=E,
        snapshot_hash=F,
        semantic_epoch=D,
        policy_hash=E,
        receipt_references=(),
    )


def chain(assessment: FinalityAssessment, world: WorldClaimStatus = WorldClaimStatus.WARRANTED_RELATIVE) -> ClaimChain:
    return ClaimChain(
        q_world=WorldClaim(
            proposition_hash=D,
            status=world,
            warrant_hashes=(F,) if world in {WorldClaimStatus.OBSERVED, WorldClaimStatus.WARRANTED_RELATIVE} else (),
            closure_warrant_hash=E,
            scope={"shipment": "17"},
        ),
        q_entailment=EntailmentClaim(
            premise_hash=D,
            conclusion_hash=E,
            status=EntailmentStatus.CERTIFIED,
            certificate_hash=F,
            model_class_hash=D,
        ),
        q_settlement=SettlementClaim(
            action_hash=F,
            status=assessment.status,
            assessment_hash=assessment.assessment_hash,
            authority_regime_hash=D,
            semantic_epoch=D,
            recourse_forum="forum://semantic-boundary",
        ),
    )


def allocation(*treatments: OutcomeTreatment, reserve: int = 100) -> ClosureRiskAllocation:
    return ClosureRiskAllocation(
        closure_warrant_hash=E,
        authority_hash=D,
        risk_bearer="did:example:risk-bearer",
        currency="USD",
        allocated_reserve_microunits=reserve,
        treatments=treatments,
        challenge_forum="forum://semantic-boundary",
        expiry="2026-08-31T00:00:00Z",
    )


def ambiguity_reserve(required: int = 100) -> AmbiguityReserve:
    return AmbiguityReserve(
        action_hash=D,
        semantic_epoch=D,
        represented_outcomes=("loss",),
        worst_case_loss_microunits=required,
        model_risk_buffer_microunits=0,
        required_reserve_microunits=required,
        currency="USD",
        external_lock_reference="lock://17",
        collectibility_evidence=({"kind": "test"},),
        expiry="2026-08-31T00:00:00Z",
        closure_warrant_hash=E,
    )


def test_claim_chain_keeps_world_entailment_and_settlement_separate() -> None:
    assessment = base(FinalityStatus.FINALIZE)
    claims = chain(assessment)
    value = claims.to_dict()
    assert value["qW"]["truth_claim"].startswith("warranted")
    assert value["qE"]["claim_boundary"].startswith("logical-consequence")
    assert value["qS"]["claim_boundary"].startswith("authorized-settlement")
    with pytest.raises(InventionError, match="certificate"):
        dataclasses.replace(claims.q_entailment, certificate_hash=None)


def test_claim_binding_mismatch_fails_stale_before_action() -> None:
    assessment = base(FinalityStatus.FINALIZE)
    claims = chain(assessment)
    claims = dataclasses.replace(
        claims,
        q_settlement=dataclasses.replace(claims.q_settlement, semantic_epoch=F),
    )
    result = assess_semantic_boundary(assessment, claims, None, active_outcomes=("safe",))
    assert result.status is FinalityStatus.TERM_STALE
    assert result.cause == "CLAIM_CHAIN_BINDING_MISMATCH"


@pytest.mark.parametrize("status", (FinalityStatus.TERM_STALE, FinalityStatus.REFUSE))
def test_boundary_policy_cannot_revive_base_stale_or_refuse(status: FinalityStatus) -> None:
    assessment = base(status)
    result = assess_semantic_boundary(
        assessment,
        chain(assessment),
        allocation(OutcomeTreatment("safe", HarmTreatment.COMPENSABLE_RESERVED, 0)),
        active_outcomes=("safe",),
    )
    assert result.status is status
    assert result.cause == "BASE:TEST_BASE"


def test_provisional_execution_requires_allocated_closure_risk() -> None:
    assessment = base(FinalityStatus.EXECUTE_PROVISIONALLY, ambiguity_reserve())
    claims = chain(assessment, WorldClaimStatus.UNKNOWN)
    missing = assess_semantic_boundary(assessment, claims, None, active_outcomes=("loss",))
    assert missing.status is FinalityStatus.ROUTE
    assert missing.cause == "UNALLOCATED_CLOSURE_RISK"

    covered = assess_semantic_boundary(
        assessment,
        claims,
        allocation(OutcomeTreatment("loss", HarmTreatment.COMPENSABLE_RESERVED, 100)),
        active_outcomes=("loss",),
    )
    assert covered.status is FinalityStatus.EXECUTE_PROVISIONALLY
    assert covered.cause.startswith("BOUNDARY_GATES_PASSED")

    short = assess_semantic_boundary(
        assessment,
        claims,
        allocation(OutcomeTreatment("loss", HarmTreatment.COMPENSABLE_RESERVED, 101)),
        active_outcomes=("loss",),
    )
    assert short.status is FinalityStatus.ROUTE
    assert short.cause == "CLOSURE_RISK_RESERVE_SHORTFALL"


@pytest.mark.parametrize(
    ("treatment", "base_status", "expected", "cause"),
    (
        (HarmTreatment.CATEGORICAL_REFUSE, FinalityStatus.FINALIZE, FinalityStatus.REFUSE, "CATEGORICAL_HARM_REFUSAL"),
        (HarmTreatment.HUMAN_REVIEW_REQUIRED, FinalityStatus.FINALIZE, FinalityStatus.ROUTE, "HUMAN_REVIEW_REQUIRED"),
        (HarmTreatment.REVERSIBLE_ONLY, FinalityStatus.FINALIZE, FinalityStatus.ROUTE, "REVERSIBILITY_BARS_FINALITY"),
    ),
)
def test_harm_treatment_controls_settlement(
    treatment: HarmTreatment,
    base_status: FinalityStatus,
    expected: FinalityStatus,
    cause: str,
) -> None:
    assessment = base(base_status)
    result = assess_semantic_boundary(
        assessment,
        chain(assessment),
        allocation(OutcomeTreatment("harm", treatment, 0)),
        active_outcomes=("harm",),
    )
    assert (result.status, result.cause) == (expected, cause)


@pytest.mark.parametrize(
    ("treatment", "expected", "cause"),
    (
        (HarmTreatment.HUMAN_REVIEW_REQUIRED, FinalityStatus.ROUTE, "HUMAN_REVIEW_REQUIRED"),
        (HarmTreatment.CATEGORICAL_REFUSE, FinalityStatus.REFUSE, "CATEGORICAL_HARM_REFUSAL"),
    ),
)
def test_noncompensable_harm_is_a_lexical_floor_at_any_reserve(
    treatment: HarmTreatment,
    expected: FinalityStatus,
    cause: str,
) -> None:
    assessment = base(FinalityStatus.FINALIZE)
    result = assess_semantic_boundary(
        assessment,
        chain(assessment),
        allocation(
            OutcomeTreatment("protected", treatment, 10**18),
            reserve=10**30,
        ),
        active_outcomes=("protected",),
    )
    assert (result.status, result.cause) == (expected, cause)


def test_resource_bounded_derivation_routes_without_becoming_a_substantive_axis() -> None:
    assessment = base(FinalityStatus.FINALIZE)
    claims = chain(assessment)
    claims = dataclasses.replace(
        claims,
        q_entailment=EntailmentClaim(
            D, E, EntailmentStatus.INDETERMINATE, None, F,
            (), DerivationStatus.RESOURCE_BOUNDED,
        ),
    )
    result = assess_semantic_boundary(assessment, claims, None, active_outcomes=("safe",))
    assert result.status is FinalityStatus.ROUTE
    assert result.cause == "RESOURCE_BOUNDED_DERIVATION"
    assert result.residual_boundaries == ()
    assert result.derivation_status is DerivationStatus.RESOURCE_BOUNDED


def test_partial_derivation_routes_without_becoming_semantic_indeterminacy() -> None:
    assessment = base(FinalityStatus.FINALIZE)
    claims = chain(assessment)
    claims = dataclasses.replace(
        claims,
        q_entailment=EntailmentClaim(
            D, E, EntailmentStatus.INDETERMINATE, None, F,
            (), DerivationStatus.PARTIAL,
        ),
    )
    result = assess_semantic_boundary(assessment, claims, None, active_outcomes=("safe",))
    assert result.cause == "PARTIAL_DERIVATION"
    assert result.residual_boundaries == ()
    assert result.derivation_status is DerivationStatus.PARTIAL


def test_semantic_indeterminacy_is_substantive_with_certified_derivation() -> None:
    assessment = base(FinalityStatus.FINALIZE)
    claims = chain(assessment)
    claims = dataclasses.replace(
        claims,
        q_entailment=EntailmentClaim(
            D, E, EntailmentStatus.INDETERMINATE, None, F,
            (SubstantiveBoundary.SEMANTIC,), DerivationStatus.CERTIFIED,
        ),
    )
    result = assess_semantic_boundary(assessment, claims, None, active_outcomes=("safe",))
    assert result.cause == "SEMANTIC_INDETERMINACY"
    assert result.residual_boundaries == (SubstantiveBoundary.SEMANTIC,)


def test_trace_refinement_certificate_preserves_both_decided_surfaces(tmp_path: Path) -> None:
    prior = (
        TraceCell("a", TraceDecision.RELY),
        TraceCell("b", TraceDecision.REFUSE),
        TraceCell("c", TraceDecision.AMBIGUOUS),
    )
    refined = (
        TraceCell("a", TraceDecision.RELY),
        TraceCell("b", TraceDecision.REFUSE),
        TraceCell("c", TraceDecision.RELY),
    )
    certificate = certify_trace_refinement(D, prior, D, refined)
    assert certificate.valid and verify_trace_refinement(certificate)
    path = tmp_path / "certificate.json"
    path.write_text(json.dumps(certificate.to_dict(), indent=2, sort_keys=True) + "\n")
    completed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/verify_semantic_boundary.py"), str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_trace_refinement_rejects_retracted_refusal(tmp_path: Path) -> None:
    certificate = certify_trace_refinement(
        D,
        (TraceCell("a", TraceDecision.REFUSE),),
        D,
        (TraceCell("a", TraceDecision.RELY),),
    )
    assert not certificate.valid
    path = tmp_path / "certificate.json"
    path.write_text(json.dumps(certificate.to_dict()))
    completed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/verify_semantic_boundary.py"), str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1


def test_trace_refinement_rejects_cross_epoch_reuse() -> None:
    certificate = certify_trace_refinement(
        D,
        (TraceCell("a", TraceDecision.RELY),),
        E,
        (TraceCell("a", TraceDecision.RELY),),
    )
    assert not certificate.same_epoch
    assert not certificate.valid
    assert not verify_trace_refinement(certificate)
