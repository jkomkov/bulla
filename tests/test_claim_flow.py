"""Typed warrant flow, no-free-precedent, and finality-obstruction gates."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.experimental.claim_flow import (
    AcceleratorStatus, AdoptionStatus, AppealState, AuthorityToken, BlockerKind,
    BudgetAuthorizationStatus, ClaimFlowAuthority, ClaimFlowTrace,
    ClaimPermission, DerivationBudgetPolicy, EvidenceBundle,
    FinalityAlternative, FinalityCondition, FinalityExplanationStatus,
    FinalityProblem, PrecedentEffect, appraise, adopt_precedent,
    explain_finality, explain_finality_with_smtinterpol, forum_finding,
    precedent_applies, settle,
    verify_claim_flow_trace, verify_derivation_run, verify_finality_explanation,
)
from bulla.experimental.frsl import RelationDecl, Signature, atom, conjunction
from bulla.experimental.invention import InventionError
from bulla.experimental.scope import StructuredScope


ROOT = Path(__file__).resolve().parents[1]


def digest(byte: str) -> str:
    return "sha256:" + byte * 64


REGIME = digest("1")
EPOCH = digest("2")
SCOPE_DIGEST = digest("3")


def token(permission: ClaimPermission, suffix: str, scope_hash: str = SCOPE_DIGEST) -> AuthorityToken:
    return AuthorityToken(
        f"{permission.value.lower()}-{suffix}", permission, f"did:example:{suffix}",
        REGIME, scope_hash, EPOCH, digest(suffix),
    )


def authority(precedent_scope: str = SCOPE_DIGEST):
    tokens = {
        ClaimPermission.APPRAISE: token(ClaimPermission.APPRAISE, "a"),
        ClaimPermission.FORUM_FINDING: token(ClaimPermission.FORUM_FINDING, "b"),
        ClaimPermission.ADOPT_PRECEDENT: token(ClaimPermission.ADOPT_PRECEDENT, "c", precedent_scope),
        ClaimPermission.SETTLE: token(ClaimPermission.SETTLE, "d"),
    }
    regime = ClaimFlowAuthority(
        REGIME, (tokens[ClaimPermission.APPRAISE],),
        (tokens[ClaimPermission.FORUM_FINDING],),
        (tokens[ClaimPermission.ADOPT_PRECEDENT],),
        (tokens[ClaimPermission.SETTLE],),
    )
    return regime, tokens


def signature() -> Signature:
    return Signature(
        sorts={"Case": ("c0",)},
        relations={
            "delivered": RelationDecl("delivered", ("Case",)),
            "authorized": RelationDecl("authorized", ("Case",)),
        },
    )


def scope(relation: str = "authorized") -> StructuredScope:
    return StructuredScope(
        signature(),
        {"op": "forall", "var": "x", "sort": "Case", "body": atom(relation, ({"var": "x"},))},
    )


def evidence_chain(regime, tokens):
    bundle = EvidenceBundle(digest("4"), digest("5"), SCOPE_DIGEST, (digest("6"),))
    evidence, edge1 = appraise(
        bundle, evidence_policy_hash=digest("7"), purpose="delivery-settlement",
        authority=regime, token=tokens[ClaimPermission.APPRAISE],
    )
    fact, edge2 = forum_finding(
        evidence, case_hash=digest("8"), appeal_state=AppealState.FINAL,
        authority=regime, token=tokens[ClaimPermission.FORUM_FINDING],
    )
    return bundle, evidence, fact, edge1, edge2


def test_explicit_authority_constructors_form_replayable_action_trace() -> None:
    regime, tokens = authority()
    bundle, _, fact, edge1, edge2 = evidence_chain(regime, tokens)
    settlement, edge3 = settle(
        fact, action_hash=digest("9"), consequence="release-payment",
        authority=regime, token=tokens[ClaimPermission.SETTLE],
    )
    trace = ClaimFlowTrace(digest("9"), EPOCH, (bundle.bundle_hash,)).append(edge1).append(edge2).append(edge3)
    assert settlement.premise_claim_hashes == (fact.claim_hash,)
    assert verify_claim_flow_trace(trace, regime)


def test_no_ambient_or_borrowed_authority() -> None:
    regime, tokens = authority()
    bundle = EvidenceBundle(digest("4"), digest("5"), SCOPE_DIGEST, (digest("6"),))
    borrowed = dataclasses.replace(tokens[ClaimPermission.APPRAISE], scope_hash=digest("e"))
    with pytest.raises(InventionError, match="missing explicit|borrowed"):
        appraise(bundle, evidence_policy_hash=digest("7"), purpose="delivery", authority=regime, token=borrowed)
    with pytest.raises(InventionError, match="contains a SETTLE token"):
        ClaimFlowAuthority(REGIME, appraisal_grants=(tokens[ClaimPermission.SETTLE],))


def test_case_only_and_persuasive_findings_never_generalize() -> None:
    precedent_scope = scope()
    regime, tokens = authority(precedent_scope.scope_hash)
    _, _, fact, _, _ = evidence_chain(regime, tokens)
    for effect in (PrecedentEffect.CASE_ONLY, PrecedentEffect.PERSUASIVE):
        source = dataclasses.replace(fact, scope_hash=precedent_scope.scope_hash) if effect is PrecedentEffect.CASE_ONLY else fact
        adoption, _ = adopt_precedent(
            source, reason=precedent_scope.predicate, effect=effect,
            applicability_scope=precedent_scope,
            protected_consequence_hashes=(digest("f"),), authority=regime,
            token=tokens[ClaimPermission.ADOPT_PRECEDENT],
            conservativity_verified=True, refusals_preserved=True,
        )
        assert adoption.status is AdoptionStatus.ADOPTED and adoption.rule is not None
        if effect is PrecedentEffect.CASE_ONLY:
            assert precedent_applies(adoption.rule, case_hash=fact.case_hash, case_scope=precedent_scope, semantic_epoch=EPOCH)
            assert not precedent_applies(adoption.rule, case_hash=digest("0"), case_scope=precedent_scope, semantic_epoch=EPOCH)
        else:
            assert not precedent_applies(adoption.rule, case_hash=fact.case_hash, case_scope=precedent_scope, semantic_epoch=EPOCH)


def test_fresh_reason_and_new_consequence_are_legislation() -> None:
    original = scope("authorized")
    regime, tokens = authority(original.scope_hash)
    _, _, fact, _, _ = evidence_chain(regime, tokens)
    initial, _ = adopt_precedent(
        fact, reason=original.predicate, effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        applicability_scope=original, protected_consequence_hashes=(digest("f"),),
        authority=regime, token=tokens[ClaimPermission.ADOPT_PRECEDENT],
        conservativity_verified=True, refusals_preserved=True,
    )
    assert initial.status is AdoptionStatus.ADOPTED and initial.rule is not None
    changed_reason = conjunction((original.predicate, scope("delivered").predicate))
    changed_scope = StructuredScope(signature(), changed_reason)
    changed_token = dataclasses.replace(tokens[ClaimPermission.ADOPT_PRECEDENT], scope_hash=changed_scope.scope_hash)
    changed_regime = dataclasses.replace(regime, precedential_grants=(changed_token,))
    changed, _ = adopt_precedent(
        fact, reason=changed_reason, effect=PrecedentEffect.BINDING_WITHIN_SCOPE,
        applicability_scope=changed_scope,
        protected_consequence_hashes=(digest("f"), digest("e")),
        authority=changed_regime, token=changed_token, prior_rule=initial.rule,
        conservativity_verified=True, refusals_preserved=True,
    )
    assert changed.status is AdoptionStatus.LEGISLATION_REQUIRED
    assert {"FRESH_REASON", "NEW_PROTECTED_CONSEQUENCE"} <= set(changed.legislation_causes)


def budget(branches: int = 8, authorization_sequence: int = 10) -> DerivationBudgetPolicy:
    return DerivationBudgetPolicy(
        "finality-reference", EPOCH, 8, 8, 64, branches, 8, (digest("a"),),
        "forum://derivation-budget", digest("b"), authorization_sequence,
    )


def finality_problem() -> FinalityProblem:
    return FinalityProblem(
        digest("1"), digest("2"), EPOCH, digest("3"), REGIME,
        (
            FinalityCondition("carrier-evidence", BlockerKind.GROUNDING, "APPRAISE", "proof://carrier", {"disclosure": 1}, 30),
            FinalityCondition("forum-review", BlockerKind.HARM, "HUMAN_REVIEW_REQUIRED", "proof://review", {"latency": 2}, None),
            FinalityCondition("settlement-authority", BlockerKind.AUTHORITY, "SETTLE", "proof://authority", {"authority": 1}, 0),
        ),
        ("settlement-authority",),
        (
            FinalityAlternative("evidence-route", ("carrier-evidence", "settlement-authority"), "FINALIZE"),
            FinalityAlternative("forum-route", ("forum-review", "settlement-authority"), "ROUTE"),
        ),
    )


def test_finality_explainer_preserves_incomparable_routes() -> None:
    problem = finality_problem()
    result = explain_finality(problem, budget=budget(), backend_hash=digest("a"), backend_version_hash=digest("c"), run_sequence=11)
    assert result.status is FinalityExplanationStatus.CHOICE_REQUIRED
    assert {route.blocker_ids for route in result.routes} == {("carrier-evidence",), ("forum-review",)}
    assert next(route for route in result.routes if route.route_id == "forum-route").reserve_delta_microunits is None
    assert result.certificate is not None
    assert verify_finality_explanation(problem, budget(), result)


def test_resource_bounded_explanation_has_precommitted_frontier() -> None:
    problem, policy = finality_problem(), budget(branches=1)
    result = explain_finality(problem, budget=policy, backend_hash=digest("a"), backend_version_hash=digest("c"), run_sequence=11)
    assert result.status is FinalityExplanationStatus.RESOURCE_BOUNDED
    assert result.certificate is not None and result.certificate.minimality_status.value == "UNRESOLVED"
    assert verify_derivation_run(policy, result.budget_receipt, replayed_frontier_hash=result.budget_receipt.search_frontier_hash) is BudgetAuthorizationStatus.AUTHORIZED


def test_budget_chosen_after_run_is_unauthorized() -> None:
    result = explain_finality(finality_problem(), budget=budget(authorization_sequence=12), backend_hash=digest("a"), backend_version_hash=digest("c"), run_sequence=11)
    assert result.status is FinalityExplanationStatus.INVALID
    assert result.cause == "UNAUTHORIZED_DERIVATION_BUDGET"


def test_cli_explain_and_replay(tmp_path: Path) -> None:
    case = {
        "problem": finality_problem().to_dict(), "budget": budget().to_dict(),
        "backend_hash": digest("a"), "backend_version_hash": digest("c"), "run_sequence": 11,
    }
    case_path, result_path = tmp_path / "case.json", tmp_path / "result.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "bulla", "experimental", "explain-finality", str(case_path), "-o", str(result_path)], cwd=ROOT, check=True)
    replay = subprocess.run([sys.executable, "-m", "bulla", "experimental", "explain-finality", str(case_path), "--verify", str(result_path)], cwd=ROOT, text=True, capture_output=True)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["ok"] is True


def test_pinned_smtinterpol_is_only_an_untrusted_route_candidate(tmp_path: Path) -> None:
    jar = tmp_path / "smtinterpol.jar"
    jar.write_bytes(b"not-a-real-jar-test-fixture")
    jar_hash = "sha256:" + hashlib.sha256(jar.read_bytes()).hexdigest()
    java = tmp_path / "fake-java"
    java.write_text(
        "#!/bin/sh\n"
        "if [ \"$3\" = \"-version\" ]; then echo 'SMTInterpol TEST'; exit 0; fi\n"
        "cat >/dev/null\n"
        "echo sat\n"
        "echo '((route_0 true) (route_1 false))'\n",
        encoding="utf-8",
    )
    java.chmod(0o755)
    policy = dataclasses.replace(budget(), permitted_backend_hashes=(jar_hash,))
    explanation, artifact = explain_finality_with_smtinterpol(
        finality_problem(), budget=policy, jar_path=jar, jar_sha256=jar_hash,
        version_contains="SMTInterpol TEST", backend_version_hash=digest("c"),
        run_sequence=11, java_command=str(java),
    )
    assert explanation.status is FinalityExplanationStatus.CHOICE_REQUIRED
    assert artifact.status is AcceleratorStatus.CANDIDATE_CHECKED
    assert artifact.candidate_route_id == "evidence-route"
    assert artifact.candidate_independently_sufficient
