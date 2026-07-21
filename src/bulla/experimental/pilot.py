"""Transport-neutral signed operations for the external semantic pilot.

The pilot is itself a Bulla workload.  These artifacts authenticate workflow
events, but never manufacture participants, adjudications, or efficacy results.
The analysis gate is executable and remains closed until the 300-case,
six-stratum, role-separated corpus and two adjudications per seam are present.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError
from bulla.identity import verify_proof_domain


PROFILE = "bulla.semantic-pilot/0.2-experimental"
ARMS = (
    "manual_conventional_integration",
    "direct_llm_repair",
    "engine_only",
    "iterative_llm_inside_proof_checker",
)
DOMAINS = ("commercial_commitments", "identity_authorization", "scheduling_logistics_data")
STRATA = ("hidden_generative_contract", "natural_expert")


class PilotAction(str, enum.Enum):
    ROLE_ENROLL = "bulla.pilot.role.enroll"
    SEAM_SUBMIT = "bulla.pilot.seam.submit"
    DEADLINE_COMMIT = "bulla.pilot.deadline.commit"
    BLIND_CREATE = "bulla.pilot.blind.create"
    ARM_ASSIGN = "bulla.pilot.arm.assign"
    ADJUDICATE = "bulla.pilot.adjudicate"
    CORPUS_FREEZE = "bulla.pilot.corpus.freeze"
    ANALYSIS_ATTEMPT = "bulla.pilot.analysis.attempt"
    CHALLENGE = "bulla.pilot.challenge"
    CHALLENGE_DISPOSE = "bulla.pilot.challenge.dispose"


@dataclass(frozen=True)
class SignedPilotArtifact:
    action: PilotAction
    subject: Mapping[str, Any]
    issuer: str
    issued_at: str
    proof: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.subject or not self.issuer or not self.issued_at:
            raise InventionError("pilot artifact requires subject, issuer, and issued_at")
        object.__setattr__(self, "subject", dict(self.subject))
        if self.proof is not None:
            object.__setattr__(self, "proof", dict(self.proof))

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self.unsigned_dict())

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE, "action": self.action.value,
            "subject": dict(self.subject), "issuer": self.issuer, "issued_at": self.issued_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "artifact_hash": self.artifact_hash, "proof": dict(self.proof) if self.proof else None}


def sign_pilot_artifact(
    action: PilotAction, subject: Mapping[str, Any], *, signer: Any, issued_at: str,
) -> SignedPilotArtifact:
    unsigned = SignedPilotArtifact(action, subject, signer.issuer, issued_at)
    return SignedPilotArtifact(action, subject, signer.issuer, issued_at, signer.sign_domain("content", unsigned.artifact_hash))


def verify_pilot_artifact(artifact: SignedPilotArtifact) -> bool:
    if artifact.proof is None:
        return False
    result = verify_proof_domain("content", artifact.artifact_hash, dict(artifact.proof))
    return bool(result.authentic and result.issuer == artifact.issuer)


def blinded_id(seam_hash: str, *, blinding_salt: str) -> str:
    return canonical_hash({"profile": PROFILE, "kind": "blinded-id", "seam_hash": seam_hash, "salt": blinding_salt})


def assign_arm(blinded_hash: str, *, preregistered_salt: str) -> tuple[str, str]:
    assignment_hash = canonical_hash({"profile": PROFILE, "blinded_hash": blinded_hash, "salt": preregistered_salt})
    index = int(assignment_hash[7:23], 16) % len(ARMS)
    return ARMS[index], assignment_hash


@dataclass(frozen=True)
class AnalysisGate:
    status: str
    causes: tuple[str, ...]
    corpus_hash: str | None
    accepted_seams: int
    adjudication_count: int
    role_separation: bool
    domain_stratum_counts: Mapping[str, int]

    def __bool__(self) -> bool:
        raise TypeError("AnalysisGate is non-Boolean; inspect status")

    @property
    def gate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "causes": list(self.causes), "corpus_hash": self.corpus_hash,
            "accepted_seams": self.accepted_seams, "adjudication_count": self.adjudication_count,
            "role_separation": self.role_separation,
            "domain_stratum_counts": dict(self.domain_stratum_counts),
            "required": {"accepted_seams": 300, "per_cell": 50, "adjudications_per_seam": 2},
        }


def analysis_gate(
    *, accepted_submissions: Sequence[SignedPilotArtifact],
    adjudications: Sequence[SignedPilotArtifact], implementation_team_ids: Sequence[str],
) -> AnalysisGate:
    causes: list[str] = []
    submissions = tuple(accepted_submissions)
    judgments = tuple(adjudications)
    if any(item.action is not PilotAction.SEAM_SUBMIT or not verify_pilot_artifact(item) for item in submissions):
        causes.append("invalid_submission_artifact")
    if any(item.action is not PilotAction.ADJUDICATE or not verify_pilot_artifact(item) for item in judgments):
        causes.append("invalid_adjudication_artifact")
    authors = {item.issuer for item in submissions}
    adjudicators = {item.issuer for item in judgments}
    implementation = set(implementation_team_ids)
    separated = not (authors & adjudicators or authors & implementation or adjudicators & implementation)
    if len(authors) < 3 or len(adjudicators) < 3 or not separated:
        causes.append("role_separation_or_minimum_roles_not_met")
    by_cell = Counter(
        f"{item.subject.get('domain')}/{item.subject.get('stratum')}" for item in submissions
    )
    expected_cells = {f"{domain}/{stratum}" for domain in DOMAINS for stratum in STRATA}
    if len(submissions) != 300:
        causes.append("accepted_seam_count_not_300")
    if any(by_cell[cell] != 50 for cell in expected_cells):
        causes.append("six_stratum_quotas_not_closed")
    by_seam = Counter(item.subject.get("blinded_id") for item in judgments)
    seam_ids = {item.subject.get("blinded_id") for item in submissions}
    if any(by_seam[seam_id] != 2 for seam_id in seam_ids) or len(judgments) != 600:
        causes.append("two_adjudications_per_seam_not_closed")
    corpus_hash = canonical_hash(sorted(item.artifact_hash for item in submissions)) if submissions and not causes else None
    return AnalysisGate(
        status="OPEN" if not causes else "REFUSED",
        causes=tuple(causes), corpus_hash=corpus_hash,
        accepted_seams=len(submissions), adjudication_count=len(judgments),
        role_separation=separated,
        domain_stratum_counts={cell: by_cell[cell] for cell in sorted(expected_cells)},
    )


def operational_slice_gate(
    *, accepted_submissions: Sequence[SignedPilotArtifact], adjudications: Sequence[SignedPilotArtifact],
    implementation_team_ids: Sequence[str],
) -> dict[str, Any]:
    """Check the 12-seam operational target without authorizing analysis."""

    submissions = tuple(accepted_submissions)
    judgments = tuple(adjudications)
    authors = {item.issuer for item in submissions}
    judges = {item.issuer for item in judgments}
    cells = Counter(f"{item.subject.get('domain')}/{item.subject.get('stratum')}" for item in submissions)
    arms = Counter(item.subject.get("arm") for item in submissions)
    per_seam = Counter(item.subject.get("blinded_id") for item in judgments)
    expected_cells = {f"{domain}/{stratum}" for domain in DOMAINS for stratum in STRATA}
    implementation = set(implementation_team_ids)
    requirements = {
        "accepted_12": len(submissions) == 12,
        "two_per_cell": all(cells[cell] == 2 for cell in expected_cells),
        "three_authors": len(authors) >= 3,
        "three_adjudicators": len(judges) >= 3,
        "role_separated": not (authors & judges or authors & implementation or judges & implementation),
        "two_adjudications_per_seam": len(judgments) == 24 and all(per_seam[item.subject.get("blinded_id")] == 2 for item in submissions),
        "three_per_arm": all(arms[arm] == 3 for arm in ARMS),
    }
    return {
        "operational_slice_ready": all(requirements.values()),
        "requirements": requirements,
        "analysis_authorized": False,
        "reportable_scope": "operational facts only; no efficacy or arm comparison",
    }


@dataclass(frozen=True)
class ChallengeReceipt:
    challenge: SignedPilotArtifact
    disposition: SignedPilotArtifact

    def __post_init__(self) -> None:
        subject = self.disposition.subject
        if (
            self.challenge.action is not PilotAction.CHALLENGE
            or self.disposition.action is not PilotAction.CHALLENGE_DISPOSE
            or subject.get("challenge_hash") != self.challenge.artifact_hash
            or subject.get("status") not in {"ACCEPTED", "REJECTED"}
            or not subject.get("reason")
            or not verify_pilot_artifact(self.challenge)
            or not verify_pilot_artifact(self.disposition)
        ):
            raise InventionError("challenge receipt requires signed, hash-bound challenge and disposition artifacts")

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge": self.challenge.to_dict(), "disposition": self.disposition.to_dict(),
            "bounty_reference": self.disposition.subject.get("bounty_reference"),
            "stake": None, "challenge_market": "not-implemented",
        }
