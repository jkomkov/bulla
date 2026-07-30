from __future__ import annotations

import copy

import pytest

pytest.importorskip("nacl")

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.challenge import (
    ChallengeCase,
    ChallengeError,
    ChallengeState,
    FindingDisposition,
    RecourseLevel,
    acknowledge,
    authorize_remedy,
    close_challenge,
    complete_remedy,
    expire,
    issue_finding,
    open_challenge,
    submit_evidence,
    verify_challenge_case,
)
from bulla.experimental.claim_flow import AuthorityToken, ClaimFlowAuthority, ClaimPermission
from bulla.experimental.frsl import RelationDecl, Signature, atom
from bulla.experimental.generalization import EffectWarrant, HarmClass
from bulla.experimental.invention import InventionError
from bulla.experimental.scope import StructuredScope
from bulla.identity import LocalEd25519Signer


def digest(ch: str) -> str:
    return "sha256:" + ch * 64


EPOCH = digest("1")
REGIME = digest("2")
SCOPE = digest("3")


def envelope(signer, *, remedies=("challenge", "revert")):
    return RecourseEnvelope(
        authority=Authority(principal=signer.issuer, policy="policy://challenge"),
        bounds=Bounds(scope="case:payment"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum("https://forum.example", digest("4")),
            remedies=tuple(Remedy(rung, f"verify:{rung}", f"anchor:{rung}") for rung in remedies),
        ),
        retention_class="authority-permanent",
    )


def target(signer):
    return sign_action_receipt_v04(
        build_action_receipt_v04(
            action={"type": "payments.charge", "subject": {"amount_micros": 1000000}},
            diagnostic_ref={"status": "not_applicable"}, envelope=envelope(signer),
            event_id="4f9142b7-87a5-4d91-8711-8ad6a0e7d19e",
            claimed_at="2026-07-21T22:00:00Z",
        ), signer,
    )


def token(signer, permission, ch):
    return AuthorityToken(
        token_id=f"{permission.value}-{ch}", permission=permission,
        principal=signer.issuer, authority_regime_hash=REGIME,
        scope_hash=SCOPE, semantic_epoch=EPOCH,
        authorization_receipt_hash=digest(ch),
    )


def authorities(forum_signer, settlement_signer):
    forum_token = token(forum_signer, ClaimPermission.FORUM_FINDING, "5")
    settlement_token = token(settlement_signer, ClaimPermission.SETTLE, "6")
    return (
        ClaimFlowAuthority(
            REGIME, adjudication_grants=(forum_token,), settlement_grants=(settlement_token,),
        ),
        forum_token,
        settlement_token,
    )


def effect_warrant(harm=HarmClass.REVERSIBLE_ONLY):
    sig = Signature(
        sorts={"Action": ("a0",)}, relations={"effect": RelationDecl("effect", ("Action",))},
    )
    scope = StructuredScope(
        sig, {"op": "forall", "var": "x", "sort": "Action", "body": atom("effect", ({"var": "x"},))},
    )
    return EffectWarrant(
        effect_predicate=atom("effect", ({"const": "a0"},)), applicability_scope=scope,
        harm_class=harm, protected_effect_hashes=(digest("7"),), semantic_epoch=EPOCH,
        authority_regime_hash=REGIME, warrant_receipt_hash=digest("8"),
    )


def opened(opener):
    return open_challenge(
        target_receipt=target(opener), scope_hash=SCOPE, semantic_epoch=EPOCH,
        deadline_checkpoint={"domain": "witness", "value": 10},
        asserted_deficiency="amount disputed", requested_remedy="revert",
        envelope=envelope(opener), signer=opener, claimed_at="2026-07-21T22:01:00Z",
        case_id="4dd11c36-52b6-487f-a30e-9c040305bd33",
        event_id="c7886142-7da2-46d7-9054-081696712136",
    )


def test_full_challenge_finding_remedy_trace_is_replayable():
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    settlement = LocalEd25519Signer.generate()
    authority, forum_token, settlement_token = authorities(forum, settlement)

    case = opened(opener)
    assert case.state is ChallengeState.OPEN and case.recourse_level is RecourseLevel.DECLARED
    case = acknowledge(
        case, transport_ref="forum-case:1", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    case = submit_evidence(
        case, evidence_hashes=(digest("9"),), envelope=envelope(opener), signer=opener,
        claimed_at="2026-07-21T22:03:00Z",
    )
    case = issue_finding(
        case, disposition=FindingDisposition.SUSTAINED, reason_hash=digest("a"),
        forum_token=forum_token, authority=authority, envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:04:00Z",
    )
    assert case.state is ChallengeState.REMEDY_PENDING
    case = authorize_remedy(
        case, remedy="revert", settlement_token=settlement_token, authority=authority,
        effect_warrant=effect_warrant(), envelope=envelope(settlement), signer=settlement,
        claimed_at="2026-07-21T22:05:00Z",
    )
    assert case.recourse_level is RecourseLevel.REMEDY_AUTHORIZED
    case = complete_remedy(
        case, execution_evidence_hash=digest("b"), settlement_token=settlement_token,
        authority=authority, envelope=envelope(settlement), signer=settlement,
        claimed_at="2026-07-21T22:06:00Z",
    )
    assert case.state is ChallengeState.REMEDY_PENDING
    assert case.recourse_level is RecourseLevel.REMEDY_COMPLETED
    case = close_challenge(
        case, closure_note_hash=digest("c"), envelope=envelope(settlement),
        signer=settlement, claimed_at="2026-07-21T22:07:00Z",
    )
    assert case.state is ChallengeState.CLOSED
    assert verify_challenge_case(case)
    assert ChallengeCase.from_dict(case.to_dict()) == case


def test_opening_challenge_does_not_confer_finding_authority():
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    settlement = LocalEd25519Signer.generate()
    authority, forum_token, _ = authorities(forum, settlement)
    case = acknowledge(
        opened(opener), transport_ref="forum-case:2", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    with pytest.raises(ChallengeError, match="signer"):
        issue_finding(
            case, disposition=FindingDisposition.SUSTAINED, reason_hash=digest("a"),
            forum_token=forum_token, authority=authority, envelope=envelope(opener), signer=opener,
            claimed_at="2026-07-21T22:03:00Z",
        )


def test_borrowed_scope_or_stale_forum_token_is_rejected():
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    settlement = LocalEd25519Signer.generate()
    authority, forum_token, _ = authorities(forum, settlement)
    case = acknowledge(
        opened(opener), transport_ref="forum-case:3", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    borrowed = AuthorityToken(
        token_id=forum_token.token_id, permission=forum_token.permission,
        principal=forum_token.principal, authority_regime_hash=REGIME,
        scope_hash=digest("c"), semantic_epoch=EPOCH,
        authorization_receipt_hash=forum_token.authorization_receipt_hash,
    )
    with pytest.raises(InventionError, match="missing explicit"):
        issue_finding(
            case, disposition=FindingDisposition.REJECTED, reason_hash=digest("a"),
            forum_token=borrowed, authority=authority, envelope=envelope(forum), signer=forum,
            claimed_at="2026-07-21T22:03:00Z",
        )


@pytest.mark.parametrize("disposition", [FindingDisposition.INDETERMINATE, FindingDisposition.CONFLICT])
def test_indeterminate_and_conflict_route_without_remedy(disposition):
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    settlement = LocalEd25519Signer.generate()
    authority, forum_token, _ = authorities(forum, settlement)
    case = acknowledge(
        opened(opener), transport_ref="forum-case:4", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    case = issue_finding(
        case, disposition=disposition, reason_hash=digest("a"), forum_token=forum_token,
        authority=authority, envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:03:00Z",
    )
    assert case.state is ChallengeState.ROUTED
    assert case.recourse_level is RecourseLevel.FINDING_ISSUED
    assert verify_challenge_case(case)


def test_categorical_effect_barrier_survives_sustained_finding():
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    settlement = LocalEd25519Signer.generate()
    authority, forum_token, settlement_token = authorities(forum, settlement)
    case = acknowledge(
        opened(opener), transport_ref="forum-case:5", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    case = issue_finding(
        case, disposition=FindingDisposition.SUSTAINED, reason_hash=digest("a"),
        forum_token=forum_token, authority=authority, envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:03:00Z",
    )
    with pytest.raises(ChallengeError, match="categorical"):
        authorize_remedy(
            case, remedy="revert", settlement_token=settlement_token, authority=authority,
            effect_warrant=effect_warrant(HarmClass.CATEGORICAL_REFUSE),
            envelope=envelope(settlement), signer=settlement,
            claimed_at="2026-07-21T22:04:00Z",
        )


def test_expiry_requires_comparable_checkpoint_after_deadline():
    opener = LocalEd25519Signer.generate()
    case = opened(opener)
    with pytest.raises(ChallengeError, match="strictly after"):
        expire(
            case, checkpoint={"domain": "witness", "value": 10},
            envelope=envelope(opener), signer=opener, claimed_at="2026-07-21T22:02:00Z",
        )
    expired = expire(
        case, checkpoint={"domain": "witness", "value": 11},
        envelope=envelope(opener), signer=opener, claimed_at="2026-07-21T22:02:00Z",
    )
    assert expired.state is ChallengeState.EXPIRED and verify_challenge_case(expired)


def test_target_or_parent_mutation_breaks_replay():
    opener = LocalEd25519Signer.generate()
    forum = LocalEd25519Signer.generate()
    case = acknowledge(
        opened(opener), transport_ref="forum-case:6", envelope=envelope(forum), signer=forum,
        claimed_at="2026-07-21T22:02:00Z",
    )
    tampered = copy.deepcopy(case.to_dict())
    tampered["events"][1]["action"]["subject"]["parent_attestation_hash"] = digest("d")
    assert not verify_challenge_case(ChallengeCase.from_dict(tampered))
