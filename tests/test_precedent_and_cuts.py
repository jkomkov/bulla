"""Bounded M2 compiler and reliance-cut research surfaces."""

import dataclasses

import pytest

from bulla.experimental.claim_flow import (
    AppealState,
    AuthorityToken,
    ClaimFlowAuthority,
    ClaimPermission,
    InstitutionalFact,
    PrecedentEffect,
    adopt_precedent,
)
from bulla.experimental.cuts import minimal_decision_cut, verify_decision_cut
from bulla.experimental.frsl import RelationDecl, Signature, atom, canonical_hash, variable
from bulla.experimental.invention import InventionError, LocalTheory, SeamProblem, SynthesisPolicy
from bulla.experimental.precedent import (
    ConsequenceRule,
    PrecedentStatus,
    check_reason_vocabulary,
    compile_precedent,
    verify_fresh_reason_certificate,
    verify_legislation_countermodel,
)
from bulla.experimental.refinement import authority_epoch, build_precedent_admission
from bulla.experimental.scope import StructuredScope
from bulla.reliance import (
    ESCALATE,
    REFUSE,
    RELY,
    STRICT_RELIANCE_POLICY,
    decide,
)


def _precedent_problem(*, target_constraint=True):
    x = variable("x")
    constraints = ()
    if target_constraint:
        constraints = (
            {
                "op": "forall",
                "var": "x",
                "sort": "Record",
                "body": {
                    "op": "iff",
                    "left": atom("outcome", (x,)),
                    "right": atom("record_fact", (x,)),
                },
            },
        )
    return SeamProblem(
        problem_id="precedent",
        signature=Signature(
            sorts={"Record": ("r0",)},
            relations={
                "record_fact": RelationDecl("record_fact", ("Record",)),
                "approved_effect": RelationDecl("approved_effect", ("Record",)),
                "outcome": RelationDecl("outcome", ("Record",)),
            },
        ),
        local_theories=(LocalTheory("court", constraints),),
        overlap_maps=(),
        target_predicate="outcome",
        shared_vocabulary=("record_fact",),
        protected_signatures={"court": ("record_fact", "approved_effect")},
        requested_judgment="boolean",
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
        authority={"principal": "did:example:court"},
        scope={"record_type": "synthetic"},
        evidence_requirements=("record",),
    )


def test_record_determined_outcome_compiles_to_scoped_j_tuple():
    result = compile_precedent(_precedent_problem())

    assert result.status is PrecedentStatus.COMPILED
    assert result.j_tuple is not None
    assert result.j_tuple.authority == {"principal": "did:example:court"}
    assert result.j_tuple.applicability_scope == {"record_type": "synthetic"}
    assert result.package is not None


def test_new_protected_consequence_is_labeled_legislation_with_countermodel():
    problem = _precedent_problem()
    rule = ConsequenceRule(
        label="automatic-sanction",
        when_outcome=True,
        consequence=atom("approved_effect", (variable("x0"),)),
    )
    result = compile_precedent(problem, consequence_rules=(rule,))

    assert result.status is PrecedentStatus.LEGISLATION_REQUIRED
    assert result.countermodels
    assert result.countermodels[0].rule == "automatic-sanction"
    assert result.countermodels[0].distinguishing_facts
    assert verify_legislation_countermodel(
        problem,
        result.package,
        rule,
        result.countermodels[0],
    )


def test_non_record_determined_outcome_does_not_compile():
    result = compile_precedent(_precedent_problem(target_constraint=False))

    assert result.status is PrecedentStatus.ESCALATE
    assert result.j_tuple is None


def test_fresh_reason_forces_replayable_escalation():
    result = compile_precedent(_precedent_problem())
    assert result.j_tuple is not None
    assert check_reason_vocabulary(result.j_tuple, ("record_fact",)) is None

    certificate = check_reason_vocabulary(
        result.j_tuple,
        ("record_fact", "emergency_medical_necessity"),
    )
    assert certificate is not None
    assert certificate.fresh_reasons == ("emergency_medical_necessity",)
    assert verify_fresh_reason_certificate(result.j_tuple, certificate)


def test_precedent_admission_requires_explicit_binding_adoption():
    problem = _precedent_problem()
    compiled = compile_precedent(problem)
    assert compiled.j_tuple is not None
    reason = {
        "op": "forall", "var": "x", "sort": "Record",
        "body": atom("record_fact", (variable("x"),)),
    }
    scope = StructuredScope(problem.signature, reason)
    semantic_epoch = canonical_hash({"authority": problem.authority, "closure": "test"})
    token = AuthorityToken(
        "precedent-test", ClaimPermission.ADOPT_PRECEDENT,
        "did:example:precedent-authority",
        canonical_hash(problem.authority), scope.scope_hash, semantic_epoch,
        "sha256:" + "ab" * 32,
    )
    authority = ClaimFlowAuthority(
        token.authority_regime_hash, precedential_grants=(token,),
    )
    fact = InstitutionalFact(
        case_hash="sha256:" + "11" * 32,
        proposition_hash="sha256:" + "12" * 32,
        evidence_claim_hash="sha256:" + "13" * 32,
        purpose="synthetic-precedent",
        scope_hash="sha256:" + "14" * 32,
        semantic_epoch=semantic_epoch,
        appeal_state=AppealState.FINAL,
        forum_authority_token_hash="sha256:" + "15" * 32,
        finding_receipt_hash="sha256:" + "16" * 32,
    )
    adoption, _ = adopt_precedent(
        fact, reason=reason, effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        applicability_scope=scope, protected_consequence_hashes=(),
        authority=authority, token=token,
        conservativity_verified=True, refusals_preserved=True,
    )
    admission = build_precedent_admission(
        problem, constraint=reason, j_tuple=compiled.j_tuple,
        adoption=adoption, case_reason_vocabulary=("record_fact",),
        jurisdiction="synthetic-forum", finality_ref=fact.claim_hash,
        applicability_ref=scope.scope_hash, semantic_epoch_ref=semantic_epoch,
        epoch=authority_epoch(problem.authority),
    )
    assert admission.provenance["precedent_adoption_hash"] == adoption.adoption_hash

    case_only, _ = adopt_precedent(
        dataclasses.replace(fact, scope_hash=scope.scope_hash),
        reason=reason, effect=PrecedentEffect.CASE_ONLY,
        applicability_scope=scope, protected_consequence_hashes=(),
        authority=authority, token=token,
        conservativity_verified=True, refusals_preserved=True,
    )
    with pytest.raises(InventionError, match="case-only and persuasive"):
        build_precedent_admission(
            problem, constraint=reason, j_tuple=compiled.j_tuple,
            adoption=case_only, case_reason_vocabulary=("record_fact",),
            jurisdiction="synthetic-forum", finality_ref=fact.claim_hash,
            applicability_ref=scope.scope_hash, semantic_epoch_ref=semantic_epoch,
            epoch=authority_epoch(problem.authority),
        )


def _view(**updates):
    value = {
        "ok": True,
        "verified_to": "attestation",
        "authority_authentic": "verified",
        "effective_grounding": "execution_verified",
        "conventions": {},
        "chain_integrity": "verified",
        "principal_binding": "verified",
        "policy_binding": "verified",
        "scope_binding": "verified",
        "bounds_conformance": "conforms",
        "temporal_status": "within_window",
        "revocation_status": "not_revoked",
    }
    value.update(updates)
    return value


def test_minimal_refusal_cut_is_replay_verifiable():
    decision = decide(
        _view(authority_authentic="forged", revocation_status="unresolved"),
        STRICT_RELIANCE_POLICY,
    )
    assert decision.outcome == REFUSE

    certificate = minimal_decision_cut(decision)

    assert certificate.witness_dimension == "authority_authentic"
    assert certificate.witness_routing == REFUSE
    assert "revocation_status" in certificate.repair_frontier
    assert verify_decision_cut(decision, certificate)
    assert not verify_decision_cut(
        decision,
        dataclasses.replace(certificate, witness_dimension="revocation_status"),
    )


def test_minimal_escalation_cut_never_relabels_ambiguity_as_refusal():
    decision = decide(
        _view(authority_authentic="unauthenticated"),
        STRICT_RELIANCE_POLICY,
    )
    assert decision.outcome == ESCALATE

    certificate = minimal_decision_cut(decision)

    assert certificate.witness_routing == ESCALATE
    assert verify_decision_cut(decision, certificate)


def test_rely_has_empty_cut():
    decision = decide(_view(), STRICT_RELIANCE_POLICY)
    assert decision.outcome == RELY

    certificate = minimal_decision_cut(decision)

    assert certificate.witness_dimension is None
    assert certificate.repair_frontier == ()
    assert verify_decision_cut(decision, certificate)
