"""Executable, receipt-native challenge and remedy lifecycle.

The module proves process events: a case was opened, acknowledged, decided,
authorized, or completed.  It does not turn a forum finding into worldly truth
or infer remedy reachability from a URL in an envelope.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, replace
from typing import Any

from bulla.action_receipt import (
    ActionReceipt,
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.envelope import RecourseEnvelope
from bulla.experimental.claim_flow import (
    AuthorityToken,
    ClaimFlowAuthority,
    ClaimPermission,
)
from bulla.experimental.generalization import EffectWarrant, HarmClass


PROFILE = "bulla.executable-recourse/0.1-experimental"


class ChallengeError(ValueError):
    pass


class ChallengeState(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EVIDENCE_OPEN = "EVIDENCE_OPEN"
    FINDING_ISSUED = "FINDING_ISSUED"
    REMEDY_PENDING = "REMEDY_PENDING"
    CLOSED = "CLOSED"
    ROUTED = "ROUTED"
    EXPIRED = "EXPIRED"


class FindingDisposition(str, enum.Enum):
    SUSTAINED = "SUSTAINED"
    REJECTED = "REJECTED"
    INDETERMINATE = "INDETERMINATE"
    CONFLICT = "CONFLICT"


class RecourseLevel(str, enum.Enum):
    DECLARED = "DECLARED"
    TRANSPORT_REACHABLE = "TRANSPORT_REACHABLE"
    CASE_ACKNOWLEDGED = "CASE_ACKNOWLEDGED"
    FINDING_ISSUED = "FINDING_ISSUED"
    REMEDY_AUTHORIZED = "REMEDY_AUTHORIZED"
    REMEDY_COMPLETED = "REMEDY_COMPLETED"


@dataclass(frozen=True)
class ChallengeCase:
    case_id: str
    target_attestation_hash: str
    scope_hash: str
    semantic_epoch: str
    deadline_checkpoint: dict[str, Any]
    permitted_remedies: tuple[str, ...]
    state: ChallengeState
    recourse_level: RecourseLevel
    events: tuple[dict[str, Any], ...]
    finding: FindingDisposition | None = None
    requested_remedy: str | None = None

    def __bool__(self) -> bool:
        raise TypeError("ChallengeCase has no truth value; inspect .state and .recourse_level")

    @property
    def latest_receipt_hash(self) -> str:
        return str(self.events[-1]["hashes"]["attestation"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "case_id": self.case_id,
            "target_attestation_hash": self.target_attestation_hash,
            "scope_hash": self.scope_hash,
            "semantic_epoch": self.semantic_epoch,
            "deadline_checkpoint": self.deadline_checkpoint,
            "permitted_remedies": list(self.permitted_remedies),
            "state": self.state.value,
            "recourse_level": self.recourse_level.value,
            "recourse_trace": [level.value for level in recourse_trace(self)],
            "finding": self.finding.value if self.finding else None,
            "requested_remedy": self.requested_remedy,
            "events": list(self.events),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChallengeCase":
        if not isinstance(value, dict):
            raise ChallengeError("challenge case must be an object")
        expected = {
            "profile", "case_id", "target_attestation_hash", "scope_hash",
            "semantic_epoch", "deadline_checkpoint", "permitted_remedies", "state",
            "recourse_level", "recourse_trace", "finding", "requested_remedy", "events",
        }
        if set(value) != expected or value.get("profile") != PROFILE:
            raise ChallengeError("challenge case has an unknown or incomplete wire shape")
        case = cls(
            case_id=value["case_id"],
            target_attestation_hash=value["target_attestation_hash"],
            scope_hash=value["scope_hash"], semantic_epoch=value["semantic_epoch"],
            deadline_checkpoint=dict(value["deadline_checkpoint"]),
            permitted_remedies=tuple(value["permitted_remedies"]),
            state=ChallengeState(value["state"]),
            recourse_level=RecourseLevel(value["recourse_level"]),
            finding=FindingDisposition(value["finding"]) if value["finding"] else None,
            requested_remedy=value["requested_remedy"],
            events=tuple(dict(event) for event in value["events"]),
        )
        if value["recourse_trace"] != [level.value for level in recourse_trace(case)]:
            raise ChallengeError("challenge recourse trace does not match its events")
        return case


def recourse_trace(case: ChallengeCase) -> tuple[RecourseLevel, ...]:
    """Derive achieved recourse levels; no single reachability Boolean exists."""
    levels = [RecourseLevel.DECLARED]
    types = [event.get("action", {}).get("type") for event in case.events]
    if "bulla.challenge.acknowledge" in types:
        levels.extend((RecourseLevel.TRANSPORT_REACHABLE, RecourseLevel.CASE_ACKNOWLEDGED))
    if "bulla.challenge.finding" in types:
        levels.append(RecourseLevel.FINDING_ISSUED)
    phases = [
        event.get("action", {}).get("subject", {}).get("phase")
        for event in case.events if event.get("action", {}).get("type") == "bulla.challenge.remedy"
    ]
    if "AUTHORIZED" in phases:
        levels.append(RecourseLevel.REMEDY_AUTHORIZED)
    if "COMPLETED" in phases:
        levels.append(RecourseLevel.REMEDY_COMPLETED)
    return tuple(levels)


def _checkpoint(value: Any, where: str) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"domain", "value"}:
        raise ChallengeError(f"{where} must contain exactly domain and value")
    domain, position = value["domain"], value["value"]
    if not isinstance(domain, str) or not domain or not isinstance(position, int) or position < 0:
        raise ChallengeError(f"{where} must be a non-empty domain and non-negative integer value")
    return domain, position


def _signed_event(
    *, action_type: str, subject: dict[str, Any], envelope: RecourseEnvelope,
    signer: Any, claimed_at: str, event_id: str | None = None,
) -> dict[str, Any]:
    receipt = sign_action_receipt_v04(
        build_action_receipt_v04(
            action={"type": action_type, "subject": subject},
            diagnostic_ref={"status": "not_applicable"}, envelope=envelope,
            event_id=event_id or str(uuid.uuid4()), claimed_at=claimed_at,
            producer={"profile": PROFILE},
        ), signer,
    )
    verification = verify_receipt(receipt.to_dict())
    if not verification.ok or verification.authority_authentic != "verified":
        raise ChallengeError("challenge event did not verify to authenticated attestation")
    return receipt.to_dict()


def open_challenge(
    *, target_receipt: ActionReceipt, scope_hash: str, semantic_epoch: str,
    deadline_checkpoint: dict[str, Any], asserted_deficiency: str,
    requested_remedy: str | None, envelope: RecourseEnvelope, signer: Any,
    claimed_at: str, case_id: str | None = None, event_id: str | None = None,
) -> ChallengeCase:
    target_verification = verify_receipt(target_receipt.to_dict())
    if not target_verification.ok:
        raise ChallengeError("challenged ActionReceipt fails integrity verification")
    _checkpoint(deadline_checkpoint, "deadline_checkpoint")
    if not scope_hash.startswith("sha256:") or not semantic_epoch.startswith("sha256:"):
        raise ChallengeError("scope_hash and semantic_epoch must be sha256 digests")
    permitted = tuple(item.rung for item in (target_receipt.envelope.recourse.remedies if target_receipt.envelope.recourse else ()))
    if requested_remedy is not None and requested_remedy not in permitted:
        raise ChallengeError("requested remedy is not permitted by the challenged envelope")
    case_id = case_id or str(uuid.uuid4())
    subject = {
        "profile": PROFILE, "case_id": case_id,
        "target_attestation_hash": target_receipt.attestation_hash,
        "scope_hash": scope_hash, "semantic_epoch": semantic_epoch,
        "deadline_checkpoint": deadline_checkpoint,
        "asserted_deficiency": asserted_deficiency,
        "requested_remedy": requested_remedy,
        "permitted_remedies": list(permitted),
    }
    event = _signed_event(
        action_type="bulla.challenge.open", subject=subject, envelope=envelope,
        signer=signer, claimed_at=claimed_at, event_id=event_id,
    )
    return ChallengeCase(
        case_id=case_id, target_attestation_hash=target_receipt.attestation_hash,
        scope_hash=scope_hash, semantic_epoch=semantic_epoch,
        deadline_checkpoint=dict(deadline_checkpoint), permitted_remedies=permitted,
        state=ChallengeState.OPEN, recourse_level=RecourseLevel.DECLARED,
        events=(event,), requested_remedy=requested_remedy,
    )


def _append(
    case: ChallengeCase, *, expected: set[ChallengeState], state: ChallengeState,
    level: RecourseLevel, action_type: str, payload: dict[str, Any],
    envelope: RecourseEnvelope, signer: Any, claimed_at: str,
) -> ChallengeCase:
    if case.state not in expected:
        raise ChallengeError(f"invalid challenge transition {case.state.value} -> {state.value}")
    subject = {
        "profile": PROFILE, "case_id": case.case_id,
        "target_attestation_hash": case.target_attestation_hash,
        "scope_hash": case.scope_hash, "semantic_epoch": case.semantic_epoch,
        "parent_attestation_hash": case.latest_receipt_hash,
        "sequence": len(case.events), **payload,
    }
    event = _signed_event(
        action_type=action_type, subject=subject, envelope=envelope,
        signer=signer, claimed_at=claimed_at,
    )
    return replace(case, state=state, recourse_level=level, events=case.events + (event,))


def acknowledge(
    case: ChallengeCase, *, transport_ref: str, envelope: RecourseEnvelope,
    signer: Any, claimed_at: str,
) -> ChallengeCase:
    if not transport_ref:
        raise ChallengeError("acknowledgement requires a transport reference")
    return _append(
        case, expected={ChallengeState.OPEN}, state=ChallengeState.ACKNOWLEDGED,
        level=RecourseLevel.CASE_ACKNOWLEDGED,
        action_type="bulla.challenge.acknowledge", payload={"transport_ref": transport_ref},
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def submit_evidence(
    case: ChallengeCase, *, evidence_hashes: tuple[str, ...], envelope: RecourseEnvelope,
    signer: Any, claimed_at: str,
) -> ChallengeCase:
    if not evidence_hashes or any(not item.startswith("sha256:") for item in evidence_hashes):
        raise ChallengeError("evidence submission requires sha256 evidence hashes")
    return _append(
        case, expected={ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN},
        state=ChallengeState.EVIDENCE_OPEN, level=RecourseLevel.CASE_ACKNOWLEDGED,
        action_type="bulla.challenge.evidence", payload={"evidence_hashes": list(evidence_hashes)},
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def issue_finding(
    case: ChallengeCase, *, disposition: FindingDisposition, reason_hash: str,
    forum_token: AuthorityToken, authority: ClaimFlowAuthority,
    envelope: RecourseEnvelope, signer: Any, claimed_at: str,
) -> ChallengeCase:
    if forum_token.principal != getattr(signer, "issuer", None):
        raise ChallengeError("forum finding signer does not match the authority token principal")
    authority.require(
        forum_token, ClaimPermission.FORUM_FINDING,
        semantic_epoch=case.semantic_epoch, scope_hash=case.scope_hash,
    )
    if not reason_hash.startswith("sha256:"):
        raise ChallengeError("finding requires a reason hash")
    if disposition is FindingDisposition.SUSTAINED:
        state = ChallengeState.REMEDY_PENDING if case.requested_remedy else ChallengeState.FINDING_ISSUED
    elif disposition is FindingDisposition.REJECTED:
        state = ChallengeState.FINDING_ISSUED
    else:
        state = ChallengeState.ROUTED
    updated = _append(
        case, expected={ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN},
        state=state, level=RecourseLevel.FINDING_ISSUED,
        action_type="bulla.challenge.finding",
        payload={
            "disposition": disposition.value, "reason_hash": reason_hash,
            "authority_token": forum_token.to_dict(),
        },
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )
    return replace(updated, finding=disposition)


def authorize_remedy(
    case: ChallengeCase, *, remedy: str, settlement_token: AuthorityToken,
    authority: ClaimFlowAuthority, effect_warrant: EffectWarrant,
    envelope: RecourseEnvelope, signer: Any, claimed_at: str,
) -> ChallengeCase:
    if case.finding is not FindingDisposition.SUSTAINED:
        raise ChallengeError("remedy requires a sustained finding")
    if remedy != case.requested_remedy or remedy not in case.permitted_remedies:
        raise ChallengeError("remedy is not the requested, envelope-permitted remedy")
    if settlement_token.principal != getattr(signer, "issuer", None):
        raise ChallengeError("remedy signer does not match the settlement authority token")
    authority.require(
        settlement_token, ClaimPermission.SETTLE,
        semantic_epoch=case.semantic_epoch, scope_hash=case.scope_hash,
    )
    if effect_warrant.semantic_epoch != case.semantic_epoch:
        raise ChallengeError("effect warrant is stale")
    if effect_warrant.harm_class is HarmClass.CATEGORICAL_REFUSE:
        raise ChallengeError("categorical effect barrier cannot be overridden by remedy")
    return _append(
        case, expected={ChallengeState.REMEDY_PENDING},
        state=ChallengeState.REMEDY_PENDING, level=RecourseLevel.REMEDY_AUTHORIZED,
        action_type="bulla.challenge.remedy",
        payload={
            "phase": "AUTHORIZED", "remedy": remedy,
            "authority_token": settlement_token.to_dict(),
            "effect_warrant_hash": effect_warrant.warrant_hash,
        },
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def complete_remedy(
    case: ChallengeCase, *, execution_evidence_hash: str,
    settlement_token: AuthorityToken, authority: ClaimFlowAuthority,
    envelope: RecourseEnvelope, signer: Any, claimed_at: str,
) -> ChallengeCase:
    if case.recourse_level is not RecourseLevel.REMEDY_AUTHORIZED:
        raise ChallengeError("remedy completion requires a prior authorized remedy")
    if settlement_token.principal != getattr(signer, "issuer", None):
        raise ChallengeError("remedy signer does not match the settlement authority token")
    authority.require(
        settlement_token, ClaimPermission.SETTLE,
        semantic_epoch=case.semantic_epoch, scope_hash=case.scope_hash,
    )
    if not execution_evidence_hash.startswith("sha256:"):
        raise ChallengeError("remedy completion requires execution evidence")
    return _append(
        case, expected={ChallengeState.REMEDY_PENDING},
        state=ChallengeState.REMEDY_PENDING, level=RecourseLevel.REMEDY_COMPLETED,
        action_type="bulla.challenge.remedy",
        payload={
            "phase": "COMPLETED", "remedy": case.requested_remedy,
            "authority_token": settlement_token.to_dict(),
            "execution_evidence_hash": execution_evidence_hash,
        },
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def close_challenge(
    case: ChallengeCase, *, closure_note_hash: str, envelope: RecourseEnvelope,
    signer: Any, claimed_at: str,
) -> ChallengeCase:
    """Append explicit closure after rejection or completed authorized remedy."""
    if not closure_note_hash.startswith("sha256:"):
        raise ChallengeError("closure requires a sha256 note hash")
    rejected = (
        case.state is ChallengeState.FINDING_ISSUED
        and case.finding is FindingDisposition.REJECTED
    )
    remedied = (
        case.state is ChallengeState.REMEDY_PENDING
        and case.recourse_level is RecourseLevel.REMEDY_COMPLETED
    )
    if not (rejected or remedied):
        raise ChallengeError("closure requires a rejected finding or completed remedy")
    return _append(
        case, expected={case.state}, state=ChallengeState.CLOSED,
        level=case.recourse_level, action_type="bulla.challenge.close",
        payload={"closure_note_hash": closure_note_hash}, envelope=envelope,
        signer=signer, claimed_at=claimed_at,
    )


def route_challenge(
    case: ChallengeCase, *, reason: str, forum_ref: str,
    envelope: RecourseEnvelope, signer: Any, claimed_at: str,
) -> ChallengeCase:
    """Route administratively without manufacturing a merits finding."""
    if not reason or not forum_ref:
        raise ChallengeError("routing requires a reason and named forum")
    return _append(
        case,
        expected={ChallengeState.OPEN, ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN},
        state=ChallengeState.ROUTED, level=case.recourse_level,
        action_type="bulla.challenge.route",
        payload={"reason": reason, "forum_ref": forum_ref},
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def expire(
    case: ChallengeCase, *, checkpoint: dict[str, Any], envelope: RecourseEnvelope,
    signer: Any, claimed_at: str,
) -> ChallengeCase:
    deadline_domain, deadline_value = _checkpoint(case.deadline_checkpoint, "deadline_checkpoint")
    domain, value = _checkpoint(checkpoint, "checkpoint")
    if domain != deadline_domain or value <= deadline_value:
        raise ChallengeError("expiry requires a comparable checkpoint strictly after the deadline")
    return _append(
        case,
        expected={ChallengeState.OPEN, ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN},
        state=ChallengeState.EXPIRED, level=case.recourse_level,
        action_type="bulla.challenge.expire", payload={"checkpoint": checkpoint},
        envelope=envelope, signer=signer, claimed_at=claimed_at,
    )


def verify_challenge_case(case: ChallengeCase) -> bool:
    if not case.events:
        return False
    previous: str | None = None
    derived_state: ChallengeState | None = None
    derived_level = RecourseLevel.DECLARED
    derived_finding: FindingDisposition | None = None
    requested_remedy: str | None = None
    permitted_remedies: tuple[str, ...] = ()
    for index, document in enumerate(case.events):
        verification = verify_receipt(document)
        if not verification.ok or verification.verified_to != "attestation":
            return False
        subject = document.get("action", {}).get("subject", {})
        if subject.get("case_id") != case.case_id:
            return False
        if index == 0:
            if document["action"]["type"] != "bulla.challenge.open":
                return False
            if subject.get("target_attestation_hash") != case.target_attestation_hash:
                return False
            if subject.get("scope_hash") != case.scope_hash or subject.get("semantic_epoch") != case.semantic_epoch:
                return False
            if subject.get("deadline_checkpoint") != case.deadline_checkpoint:
                return False
            requested_remedy = subject.get("requested_remedy")
            permitted_remedies = tuple(subject.get("permitted_remedies", ()))
            derived_state = ChallengeState.OPEN
        else:
            if subject.get("sequence") != index or subject.get("parent_attestation_hash") != previous:
                return False
            if (
                subject.get("target_attestation_hash") != case.target_attestation_hash
                or subject.get("scope_hash") != case.scope_hash
                or subject.get("semantic_epoch") != case.semantic_epoch
            ):
                return False
            action_type = document["action"]["type"]
            if action_type == "bulla.challenge.acknowledge":
                if derived_state is not ChallengeState.OPEN or not subject.get("transport_ref"):
                    return False
                derived_state = ChallengeState.ACKNOWLEDGED
                derived_level = RecourseLevel.CASE_ACKNOWLEDGED
            elif action_type == "bulla.challenge.evidence":
                if derived_state not in {ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN}:
                    return False
                derived_state = ChallengeState.EVIDENCE_OPEN
            elif action_type == "bulla.challenge.finding":
                if derived_state not in {ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN}:
                    return False
                try:
                    derived_finding = FindingDisposition(subject["disposition"])
                except (KeyError, ValueError):
                    return False
                token = subject.get("authority_token", {})
                if token.get("permission") != ClaimPermission.FORUM_FINDING.value:
                    return False
                if token.get("principal") != document.get("signature", {}).get("issuer"):
                    return False
                derived_level = RecourseLevel.FINDING_ISSUED
                if derived_finding is FindingDisposition.SUSTAINED:
                    derived_state = (
                        ChallengeState.REMEDY_PENDING if requested_remedy
                        else ChallengeState.FINDING_ISSUED
                    )
                elif derived_finding is FindingDisposition.REJECTED:
                    derived_state = ChallengeState.FINDING_ISSUED
                else:
                    derived_state = ChallengeState.ROUTED
            elif action_type == "bulla.challenge.remedy":
                token = subject.get("authority_token", {})
                if token.get("permission") != ClaimPermission.SETTLE.value:
                    return False
                if token.get("principal") != document.get("signature", {}).get("issuer"):
                    return False
                if derived_state is not ChallengeState.REMEDY_PENDING or derived_finding is not FindingDisposition.SUSTAINED:
                    return False
                if subject.get("remedy") != requested_remedy or requested_remedy not in permitted_remedies:
                    return False
                if subject.get("phase") == "AUTHORIZED":
                    if derived_level is not RecourseLevel.FINDING_ISSUED:
                        return False
                    derived_level = RecourseLevel.REMEDY_AUTHORIZED
                elif subject.get("phase") == "COMPLETED":
                    if derived_level is not RecourseLevel.REMEDY_AUTHORIZED:
                        return False
                    derived_level = RecourseLevel.REMEDY_COMPLETED
                else:
                    return False
            elif action_type == "bulla.challenge.close":
                if not (
                    (derived_state is ChallengeState.FINDING_ISSUED and derived_finding is FindingDisposition.REJECTED)
                    or (derived_state is ChallengeState.REMEDY_PENDING and derived_level is RecourseLevel.REMEDY_COMPLETED)
                ):
                    return False
                derived_state = ChallengeState.CLOSED
            elif action_type == "bulla.challenge.route":
                if derived_state not in {ChallengeState.OPEN, ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN}:
                    return False
                derived_state = ChallengeState.ROUTED
            elif action_type == "bulla.challenge.expire":
                if derived_state not in {ChallengeState.OPEN, ChallengeState.ACKNOWLEDGED, ChallengeState.EVIDENCE_OPEN}:
                    return False
                try:
                    domain, value = _checkpoint(subject["checkpoint"], "checkpoint")
                    deadline_domain, deadline_value = _checkpoint(case.deadline_checkpoint, "deadline_checkpoint")
                except (ChallengeError, KeyError):
                    return False
                if domain != deadline_domain or value <= deadline_value:
                    return False
                derived_state = ChallengeState.EXPIRED
            else:
                return False
        previous = document["hashes"]["attestation"]
    return (
        derived_state is case.state
        and derived_level is case.recourse_level
        and derived_finding is case.finding
        and requested_remedy == case.requested_remedy
        and permitted_remedies == case.permitted_remedies
        and tuple(level.value for level in recourse_trace(case))
        == tuple(case.to_dict()["recourse_trace"])
    )
