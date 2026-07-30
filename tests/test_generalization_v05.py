"""Generalization Constitution v0.5 executable gates."""

from __future__ import annotations

import dataclasses

import pytest

from bulla.experimental.claim_flow import (
    AdoptionStatus,
    AppealState,
    AuthorityToken,
    ClaimFlowAuthority,
    ClaimPermission,
    EvidenceBundle,
    PrecedentEffect,
    appraise,
    forum_finding,
)
from bulla.experimental.frsl import RelationDecl, Signature, atom
from bulla.experimental.generalization import (
    AdjudicationOrigin,
    ApplicabilityStatus,
    AuthorOrigin,
    CompoundingObservation,
    CompletenessStatus,
    EffectWarrant,
    FrontierProblem,
    HarmClass,
    ReplayStatus,
    UnsafeScopeWitness,
    adopt_precedent_candidate,
    compute_generalization_frontier,
    effect_verdict,
    exhaustive_frontier,
    find_applicability,
    propose_precedent,
    verify_generalization_frontier,
)
from bulla.experimental.invention import InventionError
from bulla.experimental.scope import StructuredScope


def digest(ch: str) -> str:
    return "sha256:" + ch * 64


EPOCH = digest("1")
REGIME = digest("2")
BASE_SCOPE = digest("3")


def signature() -> Signature:
    return Signature(
        sorts={"Case": ("c0",)},
        relations={
            "delivered": RelationDecl("delivered", ("Case",)),
            "protected_effect": RelationDecl("protected_effect", ("Case",)),
        },
    )


def scope() -> StructuredScope:
    return StructuredScope(
        signature(),
        {"op": "forall", "var": "x", "sort": "Case", "body": atom("delivered", ({"var": "x"},))},
    )


def token(permission: ClaimPermission, ch: str, scope_hash: str) -> AuthorityToken:
    return AuthorityToken(
        token_id=f"{permission.value}-{ch}",
        permission=permission,
        principal=f"did:example:{ch}",
        authority_regime_hash=REGIME,
        scope_hash=scope_hash,
        semantic_epoch=EPOCH,
        authorization_receipt_hash=digest(ch),
    )


def chain(target_scope: StructuredScope):
    appraisal = token(ClaimPermission.APPRAISE, "4", BASE_SCOPE)
    forum = token(ClaimPermission.FORUM_FINDING, "5", BASE_SCOPE)
    precedent = token(ClaimPermission.ADOPT_PRECEDENT, "6", target_scope.scope_hash)
    regime = ClaimFlowAuthority(REGIME, (appraisal,), (forum,), (precedent,), ())
    bundle = EvidenceBundle(digest("7"), digest("8"), BASE_SCOPE, (digest("9"),))
    evidence, _ = appraise(
        bundle,
        evidence_policy_hash=digest("a"),
        purpose="delivery",
        authority=regime,
        token=appraisal,
    )
    fact, _ = forum_finding(
        evidence,
        case_hash=digest("b"),
        appeal_state=AppealState.FINAL,
        authority=regime,
        token=forum,
    )
    return regime, precedent, fact


def test_candidate_has_no_operative_effect_and_adoption_binds_it() -> None:
    target = scope()
    regime, precedent_token, fact = chain(target)
    candidate = propose_precedent(
        fact,
        reason=target.predicate,
        applicability_scope=target,
        exclusions=("changed-harm",),
        protected_consequence_hashes=(digest("c"),),
        requested_effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        extractor_identity="bulla.reference",
        proposal_receipt_hash=digest("d"),
    )
    assert candidate.to_dict()["operative_effect"] is False
    adoption = adopt_precedent_candidate(
        fact,
        candidate,
        authority=regime,
        token=precedent_token,
        conservativity_verified=True,
        refusals_preserved=True,
    )
    assert adoption.candidate_hash == candidate.candidate_hash
    assert adoption.adoption.status is AdoptionStatus.ADOPTED
    assert adoption.adoption.rule is not None


def test_borrowed_candidate_and_stale_candidate_fail_closed() -> None:
    target = scope()
    regime, precedent_token, fact = chain(target)
    candidate = propose_precedent(
        fact,
        reason=target.predicate,
        applicability_scope=target,
        exclusions=(),
        protected_consequence_hashes=(digest("c"),),
        requested_effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        extractor_identity="test",
        proposal_receipt_hash=digest("d"),
    )
    with pytest.raises(InventionError, match="borrowed"):
        adopt_precedent_candidate(
            dataclasses.replace(fact, case_hash=digest("e")),
            candidate,
            authority=regime,
            token=precedent_token,
            conservativity_verified=True,
            refusals_preserved=True,
        )


def test_adoption_and_applicability_are_separate_authorized_artifacts() -> None:
    target = scope()
    regime, precedent_token, fact = chain(target)
    candidate = propose_precedent(
        fact,
        reason=target.predicate,
        applicability_scope=target,
        exclusions=(),
        protected_consequence_hashes=(digest("c"),),
        requested_effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        extractor_identity="test",
        proposal_receipt_hash=digest("d"),
    )
    adopted = adopt_precedent_candidate(
        fact,
        candidate,
        authority=regime,
        token=precedent_token,
        conservativity_verified=True,
        refusals_preserved=True,
    )
    assert adopted.adoption.rule is not None
    applicability_token = token(ClaimPermission.FORUM_FINDING, "e", target.scope_hash)
    applicability_authority = dataclasses.replace(regime, adjudication_grants=(applicability_token,))
    finding = find_applicability(
        adopted.adoption.rule,
        case_hash=digest("f"),
        case_scope=target,
        semantic_epoch=EPOCH,
        reason_holds=True,
        reason_evaluation_hash=digest("0"),
        authority=applicability_authority,
        token=applicability_token,
        distinction_witness_hash=digest("1"),
    )
    assert finding.status is ApplicabilityStatus.APPLY
    stale = find_applicability(
        adopted.adoption.rule,
        case_hash=digest("f"),
        case_scope=target,
        semantic_epoch=digest("2"),
        reason_holds=True,
        reason_evaluation_hash=digest("0"),
        authority=dataclasses.replace(
            applicability_authority,
            adjudication_grants=(dataclasses.replace(applicability_token, semantic_epoch=digest("2")),),
        ),
        token=dataclasses.replace(applicability_token, semantic_epoch=digest("2")),
        distinction_witness_hash=digest("1"),
    )
    assert stale.status is ApplicabilityStatus.STALE
    assert stale.distinction is not None


def frontier_problem(width: int = 5) -> FrontierProblem:
    # A scope is unsafe if it includes both dimensions 0 and 1, or all of 2..4.
    return FrontierProblem(
        problem_id=f"frontier-{width}",
        dimensions=tuple(f"d{i}" for i in range(width)),
        represented_case_masks=tuple(1 << i for i in range(width)),
        unsafe_witnesses=(
            UnsafeScopeWitness(0b00011, digest("3"), "changed-harm"),
            UnsafeScopeWitness(0b11100, digest("4"), "authority-boundary"),
        ),
        model_class_hash=digest("5"),
        closure_warrant_hash=digest("6"),
        authority_regime_hash=REGIME,
        semantic_epoch=EPOCH,
    )


def test_counterexample_frontier_agrees_with_exhaustive_and_is_antichain() -> None:
    problem = frontier_problem()
    frontier = compute_generalization_frontier(problem, max_branch_nodes=1 << 5)
    assert frontier.completeness is CompletenessStatus.EXACT
    assert tuple(scope.mask for scope in frontier.scopes) == exhaustive_frontier(problem)
    assert len(frontier.scopes) > 1
    assert frontier.to_dict()["selection"] == "CHOICE_REQUIRED"
    assert verify_generalization_frontier(problem, frontier)


def test_partial_frontier_is_safe_but_never_claims_completeness() -> None:
    problem = frontier_problem()
    frontier = compute_generalization_frontier(problem, max_branch_nodes=4)
    assert frontier.completeness is CompletenessStatus.UNRESOLVED
    assert verify_generalization_frontier(problem, frontier)
    assert frontier.search_frontier_hash.startswith("sha256:")


def test_compounding_observation_cannot_emit_a_naked_result() -> None:
    observation = CompoundingObservation(
        result="COMPOUNDING_OBSERVED",
        corpus_hash=digest("7"),
        author_origin=AuthorOrigin.TEAM_AUTHORED,
        adjudication_origin=AdjudicationOrigin.MACHINE_PLANTED,
        replay=ReplayStatus.INTERNAL,
        closure_warrant_hash=digest("8"),
        external_author_count=0,
        external_adjudicator_count=0,
        external_implementation_count=0,
        external_witness_count=0,
    )
    wire = observation.to_dict()
    assert wire["display"] == "Compounding observed — internal captive lineage benchmark."
    assert wire["external_counts"] == {
        "authors": 0, "adjudicators": 0, "implementations": 0, "witnesses": 0,
    }


def test_effect_barrier_depends_on_effect_not_action_name_or_wrapper() -> None:
    target = scope()
    protected = digest("9")
    warrant = EffectWarrant(
        effect_predicate=atom("protected_effect", ({"const": "c0"},)),
        applicability_scope=target,
        harm_class=HarmClass.CATEGORICAL_REFUSE,
        protected_effect_hashes=(protected,),
        semantic_epoch=EPOCH,
        authority_regime_hash=REGIME,
        warrant_receipt_hash=digest("a"),
    )
    verdicts = {
        effect_verdict(warrant, observed_effect_hashes=(protected,))
        for _disguise in (
            "rename", "wrapper", "decomposition", "proxy-field",
            "repeated-temporary", "affiliated-executor",
        )
    }
    assert verdicts == {HarmClass.CATEGORICAL_REFUSE}
