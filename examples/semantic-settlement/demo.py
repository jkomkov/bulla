#!/usr/bin/env python3
"""Deterministic procurement-shadow demonstration for Semantic Settlement v0.1."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bulla.action_receipt import sign_action_receipt
from bulla.envelope import Authority, Bounds, RecourseEnvelope
from bulla.experimental.checkpoint import issue_checkpoint
from bulla.experimental.constitutional import (
    AuthorityPermission,
    AuthorityRegime,
    ClosureStatus,
    ModelClosureWarrant,
    WitnessInclusion,
    authorize_revision,
    mint_authorization_receipt,
    revision_authorization_subject,
)
from bulla.experimental.frsl import canonical_hash, falsity, truth
from bulla.experimental.refinement import EnvelopeRegions, EnvelopeSnapshot, authority_epoch, semantic_epoch
from bulla.experimental.semantic_finality import (
    ConsequenceClass,
    ConsequenceProfile,
    ExternalLock,
    SemanticFinalityPolicy,
    assess_finality,
    calculate_reserve,
    mint_finality_receipt,
    release_reserve,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


HERE = Path(__file__).resolve().parent
D = "sha256:" + "22" * 32
TIME = "2026-07-18T00:00:00Z"


def warrant(status=ClosureStatus.BOUNDED_EXACT, version="1"):
    return ModelClosureWarrant(
        status=status,
        model_class={"name": "procurement-delivery-worlds", "version": version},
        generation_method={"kind": "exhaustive", "source": "shadow-fixtures/1"},
        exclusions=("carrier fraud outside declared evidence model", "unobserved off-ledger custody"),
        domain_authority={"principal": "did:example:procurement-board", "policy": "closure@1"},
        adversarial_expansion_evidence=({"kind": "seller-buyer-disagreement", "hash": D},),
        scope={"invoice": "INV-17", "shipment": "SHIP-17"},
    )


def main():
    operative = LocalEd25519Signer(seed=bytes([81]) + bytes(31))
    refinement = LocalEd25519Signer(seed=bytes([82]) + bytes(31))
    supersession = LocalEd25519Signer(seed=bytes([83]) + bytes(31))
    witness_signers = (
        LocalEd25519Signer(seed=bytes([84]) + bytes(31)),
        LocalEd25519Signer(seed=bytes([85]) + bytes(31)),
    )
    regime = AuthorityRegime(
        operative=AuthorityPermission(operative.issuer, "procurement-operative@1", ("payment",)),
        refinement=AuthorityPermission(refinement.issuer, "procurement-refinement@1", ("carrier-evidence",)),
        supersession=AuthorityPermission(supersession.issuer, "procurement-supersession@1", ("delivery-term",)),
        witness_operators=tuple(item.issuer for item in witness_signers),
        forum="forum://procurement-governance",
    )
    closure = warrant()
    auth_epoch = authority_epoch({"principal": operative.issuer, "policy": "procurement-operative@1"})
    snapshot = EnvelopeSnapshot(
        base_problem_hash=D, effective_problem_hash=D, result_hash=D, package_hash=D,
        package_mode="partial", semantic_state_hash=D, passport_hash=D, manifest_hash=D,
        authority_epoch=auth_epoch, closure_warrant_hash=closure.warrant_hash,
        semantic_epoch=semantic_epoch(auth_epoch, closure.warrant_hash),
        regions=EnvelopeRegions(reachable=(D,), rely=(), refuse=(), ambiguous=(D,)),
    )
    profile = ConsequenceProfile(
        action_hash=canonical_hash({"action": "pay", "invoice": "INV-17"}),
        currency="USD", target_arguments=("SHIP-17",),
        consequence_classes=(
            ConsequenceClass("SELLER_DISPATCH", truth(), 1_000_000),
            ConsequenceClass("BUYER_CUSTODY", falsity(), 0),
        ),
        maximum_credible_loss_microunits=1_000_000,
        settlement_target={"kind": "simulated-escrow", "invoice": "INV-17"},
        external_verifier={"id": "shadow-escrow/1", "recompute": "local"},
    )
    policy = SemanticFinalityPolicy(
        permitted_closure_statuses=(ClosureStatus.BOUNDED_EXACT, ClosureStatus.FINITE_EXACT),
        maximum_reserve_microunits=1_100_000, finality_threshold=1,
        permitted_observation_classes=("carrier_attestation",),
        required_authorities=(regime.operative.principal, regime.refinement.principal, regime.supersession.principal),
        provisional_execution_allowed=True, provisional_action_types=("procurement.payment",),
    )
    initial_reserve = calculate_reserve(
        profile, ("SELLER_DISPATCH", "BUYER_CUSTODY"), semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=closure, model_risk_buffer_microunits=100_000,
        expiry="2026-07-20T00:00:00Z",
    )
    lock = ExternalLock(
        "shadow-escrow://INV-17", {"id": "shadow-escrow/1"},
        initial_reserve.required_reserve_microunits, "USD", "LOCKED",
        ({"kind": "simulated-ledger-entry", "hash": canonical_hash({"reserve": initial_reserve.required_reserve_microunits})},),
        simulated=True,
    )
    initial_reserve = initial_reserve.__class__(
        **{**initial_reserve.__dict__, "external_lock_reference": lock.lock_reference,
           "collectibility_evidence": lock.collectibility_evidence}
    )
    provisional = assess_finality(
        snapshot=snapshot, current_semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=closure, authority_regime_hash=regime.regime_hash,
        consequence_profile=profile, represented_outcomes=initial_reserve.represented_outcomes,
        policy=policy, certified_surface="AMBIGUOUS", reserve=initial_reserve,
        external_lock=lock, evidence_plan_hashes=(canonical_hash({"offer": "carrier-custody"}),),
        evidence_classes=("carrier_attestation",), route_options=("forum://procurement-governance",),
    )
    refined_reserve = calculate_reserve(
        profile, ("BUYER_CUSTODY",), semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=closure, model_risk_buffer_microunits=100_000,
        expiry="2026-07-20T00:00:00Z",
    )
    release = release_reserve(initial_reserve, refined_reserve)
    final = assess_finality(
        snapshot=snapshot, current_semantic_epoch=snapshot.semantic_epoch,
        closure_warrant=closure, authority_regime_hash=regime.regime_hash,
        consequence_profile=profile, represented_outcomes=refined_reserve.represented_outcomes,
        policy=policy, certified_surface="RELY",
        receipt_references=(release.release_hash,),
    )

    envelope = RecourseEnvelope(
        authority=Authority(operative.issuer, "procurement-operative@1"),
        bounds=Bounds(scope="payment"), retention_class="authority-permanent",
        disclosure_class="auditor",
    )
    receipts = {}
    for action, assessment, extra in (
        ("bulla.finality.assess", provisional, {}),
        ("bulla.finality.reserve", provisional, {"reserve_hash": initial_reserve.reserve_hash}),
        ("bulla.finality.release", final, {"release_hash": release.release_hash}),
        ("bulla.finality.finalize", final, {}),
    ):
        receipt = mint_finality_receipt(
            assessment, action_type=action, envelope=envelope, timestamp=TIME,
            producer={"profile": "procurement-shadow/1"}, extra_subject=extra,
        )
        receipts[action] = sign_action_receipt(receipt, operative).to_dict()

    new_closure = warrant(version="2")
    new_authority = {"principal": supersession.issuer, "policy": "procurement-supersession@2"}
    revision_subject = revision_authorization_subject(
        snapshot, new_authority=new_authority, new_closure_warrant=new_closure,
        reason="closure model expanded after carrier challenge", scope="delivery-term",
    )
    revise_auth = mint_authorization_receipt(
        action_type="bulla.semantic.revise.authorize", subject=revision_subject,
        permission=regime.supersession, signer=supersession, scope="delivery-term", timestamp=TIME,
    )
    supersede_receipt = mint_authorization_receipt(
        action_type="bulla.term.supersede", subject=revision_subject,
        permission=regime.supersession, signer=supersession, scope="delivery-term", timestamp=TIME,
    )
    claim_hash = canonical_hash({
        "authorization": revise_auth.to_dict()["hashes"]["content"],
        "supersession": supersede_receipt.to_dict()["hashes"]["content"],
    })
    witness_inclusions = []
    witness_views = []
    with tempfile.TemporaryDirectory(prefix="bulla-settlement-witness-") as temporary:
        for index, signer in enumerate(witness_signers):
            log = DeedLog(Path(temporary) / f"witness-{index + 1}.jsonl")
            attestation = canonical_hash({"claim": claim_hash, "operator": signer.issuer})
            deed = Deed(signer.issuer, claim_hash, attestation)
            position = log.append(deed)
            checkpoint = issue_checkpoint(log, signer, log_id=f"log://semantic-settlement/witness-{index + 1}", issued_at=TIME)
            inclusion = log.inclusion(position)
            witness_inclusions.append(WitnessInclusion(
                operator=signer.issuer, claim_hash=claim_hash, deed_issuer=signer.issuer,
                deed_attestation_hash=attestation, expected_leaf=inclusion["leaf"],
                checkpoint=checkpoint, inclusion_record=inclusion,
            ))
            witness_views.append({"operator": signer.issuer, "checkpoint": checkpoint.to_dict(), "inclusion": inclusion})
    revision = authorize_revision(
        snapshot, new_authority=new_authority, new_closure_warrant=new_closure,
        reason="closure model expanded after carrier challenge", regime=regime,
        authorization_receipt=revise_auth, supersession_receipt=supersede_receipt,
        witness_inclusions=witness_inclusions, scope="delivery-term",
    )
    new_semantic_epoch = semantic_epoch(authority_epoch(new_authority), new_closure.warrant_hash)
    stale = assess_finality(
        snapshot=snapshot, current_semantic_epoch=new_semantic_epoch,
        closure_warrant=new_closure, authority_regime_hash=regime.regime_hash,
        consequence_profile=profile, represented_outcomes=("BUYER_CUSTODY",),
        policy=policy, certified_surface="RELY",
    )
    governance = [
        assess_finality(
            snapshot=snapshot, current_semantic_epoch=snapshot.semantic_epoch,
            closure_warrant=closure, authority_regime_hash=regime.regime_hash,
            consequence_profile=profile, represented_outcomes=("SELLER_DISPATCH", "BUYER_CUSTODY"),
            policy=policy, certified_surface="AMBIGUOUS",
            route_options=("forum://buyer-standard", "forum://seller-standard"),
        )
        for _ in range(2)
    ]

    replay_case = {
        "snapshot": snapshot.to_dict(), "closure_warrant": closure.to_dict(),
        "authority_regime_hash": regime.regime_hash, "consequence_profile": profile.to_dict(),
        "represented_outcomes": list(initial_reserve.represented_outcomes), "policy": policy.to_dict(),
        "certified_surface": "AMBIGUOUS", "reserve": initial_reserve.to_dict(),
        "external_lock": lock.to_dict(),
        "evidence_plan_hashes": [canonical_hash({"offer": "carrier-custody"})],
        "evidence_classes": ["carrier_attestation"],
        "route_options": ["forum://procurement-governance"],
        "receipt_references": [], "action_type": "procurement.payment",
        "current_semantic_epoch": snapshot.semantic_epoch,
        "expected_assessment_hash": provisional.assessment_hash,
        "expected_assessment": provisional.to_dict(),
    }
    output = {
        "profile": "bulla.semantic-finality/0.1-experimental",
        "claim_boundary": "shadow mechanics only; no real custody or collectibility",
        "steps": [
            {"step": 1, "event": "dispatch-vs-custody residue", "status": "AMBIGUOUS"},
            {"step": 2, "event": "carrier evidence offered", "plan_hash": replay_case["evidence_plan_hashes"][0]},
            {"step": 3, "event": "reserve locked", "amount_microunits": initial_reserve.required_reserve_microunits},
            {"step": 4, "event": "payment provisional", "status": provisional.status.value},
            {"step": 5, "event": "carrier custody evidence admitted", "authority": regime.refinement.principal},
            {"step": 6, "event": "reserve decreased", "released_microunits": release.released_microunits},
            {"step": 7, "event": "reserve released", "release_hash": release.release_hash},
            {"step": 8, "event": "action finalized", "status": final.status.value},
            {"step": 9, "event": "closure and authority revised", "revision_hash": revision.revision_hash},
            {"step": 10, "event": "old term checked", "status": stale.status.value},
            {"step": 11, "event": "two witness views", "distinct_roots": len({item["checkpoint"]["root"] for item in witness_views}) == 2},
            {"step": 12, "event": "governance-limited seams", "causes": [item.cause for item in governance]},
        ],
        "authority_regime": regime.to_dict(), "closure_warrant": closure.to_dict(),
        "consequence_profile": profile.to_dict(), "initial_reserve": initial_reserve.to_dict(),
        "refined_reserve": refined_reserve.to_dict(), "release": release.to_dict(),
        "provisional_assessment": provisional.to_dict(), "final_assessment": final.to_dict(),
        "stale_assessment": stale.to_dict(), "revision": revision.to_dict(),
        "witness_views": witness_views, "receipts": receipts,
        "governance_assessments": [item.to_dict() for item in governance],
    }
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "demo-output.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    vectors = HERE.parents[1] / "bench/invention/semantic-settlement/reproduction-vectors"
    vectors.mkdir(parents=True, exist_ok=True)
    (vectors / "procurement-provisional.internal.json").write_text(json.dumps(replay_case, indent=2) + "\n", encoding="utf-8")
    blind = {key: value for key, value in replay_case.items() if key not in {"expected_assessment", "expected_assessment_hash"}}
    (vectors / "procurement-provisional.blind.json").write_text(json.dumps(blind, indent=2) + "\n", encoding="utf-8")
    answer_key = {
        "vector": "procurement-provisional.blind.json",
        "input_hash": canonical_hash(blind),
        "expected_assessment_hash": provisional.assessment_hash,
        "expected_status": provisional.status.value,
        "expected_cause": provisional.cause,
    }
    (vectors / "answer-key.internal.json").write_text(json.dumps(answer_key, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "provisional": provisional.status.value, "final": final.status.value,
        "stale": stale.status.value, "released_microunits": release.released_microunits,
        "revision_witnesses": len(witness_inclusions),
    }, indent=2))


if __name__ == "__main__":
    main()
