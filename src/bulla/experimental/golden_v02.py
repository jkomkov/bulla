"""Golden Gate v0.2 experimental evidence interfaces.

This module describes evidence about an evaluator; it is not part of Bulla's
stable API.  In particular, an empty external record remains blocked and no
locally generated identity can satisfy reviewer-originated blindness.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError


PROFILE = "bulla.golden-suite/0.2-experimental"
SCHEMA_VERSION = "0.2-experimental"


def _digest(value: Any) -> str:
    return canonical_hash(value)


def _require_digest(value: str, where: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise InventionError(f"{where} must be sha256:<64 hex>")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise InventionError(f"{where} must be sha256:<64 hex>") from exc


def _closed(value: Mapping[str, Any], required: set[str], where: str) -> None:
    if set(value) != required:
        raise InventionError(f"{where} has unknown or missing fields")


class BlindnessMode(str, enum.Enum):
    AUTHOR_KNOWN_PARTICIPANT_BLIND = "AUTHOR_KNOWN_PARTICIPANT_BLIND"
    REVIEWER_ORIGINATED_BLIND = "REVIEWER_ORIGINATED_BLIND"


class ExternalGateStatus(str, enum.Enum):
    BLOCKED = "BLOCKED"
    COMMITTED = "COMMITTED"
    SUBMITTED = "SUBMITTED"
    REVEALED = "REVEALED"


class MetamorphicKind(str, enum.Enum):
    INVARIANT = "INVARIANT"
    FORCING = "FORCING"


@dataclass(frozen=True)
class CustodyCeremony:
    candidate_commit: str
    specification_hash: str
    scoring_hash: str
    mode: BlindnessMode
    curator_ids: tuple[str, ...]
    cleanroom_implementer_id: str | None
    adjudicator_ids: tuple[str, ...]
    implementation_team_ids: tuple[str, ...]
    sops_key_group_count: int
    shamir_threshold: int
    implementation_team_key_access: bool
    hidden_case_count: int
    machine_property_count: int
    adjudication_count: int
    ciphertext_hash: str | None = None
    commitment_root: str | None = None
    status: ExternalGateStatus = ExternalGateStatus.BLOCKED

    def __post_init__(self) -> None:
        if len(self.candidate_commit) != 40:
            raise InventionError("custody candidate_commit must be a full git commit")
        try:
            int(self.candidate_commit, 16)
        except ValueError as exc:
            raise InventionError("custody candidate_commit must be hexadecimal") from exc
        _require_digest(self.specification_hash, "custody.specification_hash")
        _require_digest(self.scoring_hash, "custody.scoring_hash")
        curators = tuple(self.curator_ids)
        adjudicators = tuple(self.adjudicator_ids)
        implementers = tuple(self.implementation_team_ids)
        if len(curators) != 3 or len(set(curators)) != 3:
            raise InventionError("reviewer-originated custody requires three distinct curators")
        if len(adjudicators) < 6 or len(set(adjudicators)) != len(adjudicators):
            raise InventionError("custody requires at least six distinct adjudicators")
        roles = set(curators) | set(adjudicators) | set(implementers)
        expected = len(curators) + len(adjudicators) + len(implementers)
        if self.cleanroom_implementer_id is not None:
            expected += 1
            roles.add(self.cleanroom_implementer_id)
        if len(roles) != expected:
            raise InventionError("custody roles must be disjoint")
        if (self.sops_key_group_count, self.shamir_threshold) != (3, 2):
            raise InventionError("custody requires three SOPS key groups with threshold two")
        if self.implementation_team_key_access:
            raise InventionError("implementation team may not hold a custody key")
        if (self.hidden_case_count, self.machine_property_count, self.adjudication_count) != (36, 24, 12):
            raise InventionError("custody case strata must be exactly 36 = 24 + 12")
        if self.status is not ExternalGateStatus.BLOCKED:
            if self.mode is not BlindnessMode.REVIEWER_ORIGINATED_BLIND:
                raise InventionError("external custody cannot promote author-known blindness")
            if self.cleanroom_implementer_id is None:
                raise InventionError("external custody requires a clean-room implementer")
            if self.ciphertext_hash is None or self.commitment_root is None:
                raise InventionError("promoted custody requires ciphertext and commitment hashes")
        for name in ("ciphertext_hash", "commitment_root"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, f"custody.{name}")
        object.__setattr__(self, "curator_ids", curators)
        object.__setattr__(self, "adjudicator_ids", adjudicators)
        object.__setattr__(self, "implementation_team_ids", implementers)

    @property
    def ceremony_hash(self) -> str:
        return _digest(self.to_dict())

    @property
    def reviewer_originated_ready(self) -> bool:
        return (
            self.status in {
                ExternalGateStatus.COMMITTED,
                ExternalGateStatus.SUBMITTED,
                ExternalGateStatus.REVEALED,
            }
            and self.mode is BlindnessMode.REVIEWER_ORIGINATED_BLIND
            and not self.implementation_team_key_access
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "candidate_commit": self.candidate_commit,
            "specification_hash": self.specification_hash,
            "scoring_hash": self.scoring_hash,
            "mode": self.mode.value,
            "curator_ids": list(self.curator_ids),
            "cleanroom_implementer_id": self.cleanroom_implementer_id,
            "adjudicator_ids": list(self.adjudicator_ids),
            "implementation_team_ids": list(self.implementation_team_ids),
            "sops": {
                "key_group_count": self.sops_key_group_count,
                "shamir_threshold": self.shamir_threshold,
                "implementation_team_key_access": self.implementation_team_key_access,
            },
            "hidden_cases": {
                "total": self.hidden_case_count,
                "machine_or_property": self.machine_property_count,
                "adjudication_or_open_world": self.adjudication_count,
            },
            "ciphertext_hash": self.ciphertext_hash,
            "commitment_root": self.commitment_root,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ExternalSubmission:
    submitter_id: str
    role: str
    candidate_commit: str
    case_manifest_hash: str
    result_hash: str
    submitted_at: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.role not in {"bulla", "cleanroom"} or not self.submitter_id or not self.submitted_at:
            raise InventionError("invalid external submission identity, role, or timestamp")
        if len(self.candidate_commit) != 40:
            raise InventionError("submission candidate_commit must be full length")
        try:
            int(self.candidate_commit, 16)
        except ValueError as exc:
            raise InventionError("submission candidate_commit must be hexadecimal") from exc
        for name in ("case_manifest_hash", "result_hash", "receipt_hash"):
            _require_digest(getattr(self, name), f"submission.{name}")

    @property
    def submission_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "submitter_id": self.submitter_id,
            "role": self.role,
            "candidate_commit": self.candidate_commit,
            "case_manifest_hash": self.case_manifest_hash,
            "result_hash": self.result_hash,
            "submitted_at": self.submitted_at,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True)
class ExternalReveal:
    custody_hash: str
    bulla_submission_hash: str
    cleanroom_submission_hash: str
    revealing_curator_ids: tuple[str, ...]
    plaintext_hash: str
    revealed_at: str
    after_both_submissions: bool

    def __post_init__(self) -> None:
        for name in (
            "custody_hash",
            "bulla_submission_hash",
            "cleanroom_submission_hash",
            "plaintext_hash",
        ):
            _require_digest(getattr(self, name), f"reveal.{name}")
        if len(set(self.revealing_curator_ids)) < 2:
            raise InventionError("reveal requires two distinct custodians")
        if not self.after_both_submissions or not self.revealed_at:
            raise InventionError("reveal must occur after both receipted submissions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "custody_hash": self.custody_hash,
            "bulla_submission_hash": self.bulla_submission_hash,
            "cleanroom_submission_hash": self.cleanroom_submission_hash,
            "revealing_curator_ids": list(self.revealing_curator_ids),
            "plaintext_hash": self.plaintext_hash,
            "revealed_at": self.revealed_at,
            "after_both_submissions": self.after_both_submissions,
        }


@dataclass(frozen=True)
class ProvenanceCard:
    case_id: str
    source_ids: tuple[str, ...]
    origin: str
    retrieval_timestamps: tuple[str, ...]
    content_hashes: tuple[str, ...]
    license_status: str
    redistribution_status: str
    transformation_history: tuple[Mapping[str, Any], ...]
    semantic_question: str
    oracle_class: str
    adjudication_status: str

    def __post_init__(self) -> None:
        if not self.case_id or not self.source_ids or not self.origin or not self.semantic_question:
            raise InventionError("provenance card is incomplete")
        if len(self.content_hashes) != len(self.source_ids):
            raise InventionError("provenance source and content-hash counts differ")
        for value in self.content_hashes:
            _require_digest(value, "provenance.content_hash")
        if self.oracle_class not in {"MACHINE", "PROPERTY", "ADJUDICATION"}:
            raise InventionError("invalid provenance oracle class")
        object.__setattr__(self, "transformation_history", tuple(dict(x) for x in self.transformation_history))

    @property
    def card_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "source_ids": list(self.source_ids),
            "origin": self.origin,
            "retrieval_timestamps": list(self.retrieval_timestamps),
            "content_hashes": list(self.content_hashes),
            "license_status": self.license_status,
            "redistribution_status": self.redistribution_status,
            "transformation_history": [dict(x) for x in self.transformation_history],
            "semantic_question": self.semantic_question,
            "oracle_class": self.oracle_class,
            "adjudication_status": self.adjudication_status,
        }


@dataclass(frozen=True)
class MetamorphicRelation:
    relation_id: str
    kind: MetamorphicKind
    description: str
    preserved_fields: tuple[str, ...]
    permitted_changes: tuple[str, ...]
    forced_exit: str | None = None

    def __post_init__(self) -> None:
        if not self.relation_id or not self.description or not self.preserved_fields:
            raise InventionError("metamorphic relation is incomplete")
        if self.kind is MetamorphicKind.INVARIANT and self.forced_exit is not None:
            raise InventionError("invariant relation cannot force an exit")
        if self.kind is MetamorphicKind.FORCING and self.forced_exit is None:
            raise InventionError("forcing relation must name the expected exit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "kind": self.kind.value,
            "description": self.description,
            "preserved_fields": list(self.preserved_fields),
            "permitted_changes": list(self.permitted_changes),
            "forced_exit": self.forced_exit,
        }


@dataclass(frozen=True)
class MetamorphicObservation:
    base_case_id: str
    relation_id: str
    base_input_hash: str
    transformed_input_hash: str
    base_exit: str
    transformed_exit: str
    checked_fields: tuple[str, ...]
    passed: bool
    cause: str

    def __post_init__(self) -> None:
        for name in ("base_input_hash", "transformed_input_hash"):
            _require_digest(getattr(self, name), f"metamorphic.{name}")
        if not self.base_case_id or not self.relation_id or not self.checked_fields or not self.cause:
            raise InventionError("metamorphic observation is incomplete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_case_id": self.base_case_id,
            "relation_id": self.relation_id,
            "base_input_hash": self.base_input_hash,
            "transformed_input_hash": self.transformed_input_hash,
            "base_exit": self.base_exit,
            "transformed_exit": self.transformed_exit,
            "checked_fields": list(self.checked_fields),
            "passed": self.passed,
            "cause": self.cause,
        }


@dataclass(frozen=True)
class ComplexityFingerprint:
    case_id: str
    hypothesis_count: int
    opposing_pair_count: int
    candidate_observable_count: int
    vocabulary_width: int
    authority_branching: int
    proof_nodes: int
    peak_memory_bytes: int
    elapsed_ns: int
    best_certified_partial_state: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.case_id:
            raise InventionError("complexity fingerprint requires case_id")
        for name in (
            "hypothesis_count",
            "opposing_pair_count",
            "candidate_observable_count",
            "vocabulary_width",
            "authority_branching",
            "proof_nodes",
            "peak_memory_bytes",
            "elapsed_ns",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"complexity fingerprint {name} must be non-negative integer")
        object.__setattr__(self, "best_certified_partial_state", dict(self.best_certified_partial_state))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "hypothesis_count": self.hypothesis_count,
            "opposing_pair_count": self.opposing_pair_count,
            "candidate_observable_count": self.candidate_observable_count,
            "vocabulary_width": self.vocabulary_width,
            "authority_branching": self.authority_branching,
            "proof_nodes": self.proof_nodes,
            "peak_memory_bytes": self.peak_memory_bytes,
            "elapsed_ns": self.elapsed_ns,
            "best_certified_partial_state": dict(self.best_certified_partial_state),
        }


@dataclass(frozen=True)
class CoverageReport:
    abstract_state_count: int
    transition_count: int
    accepted_transition_count: int
    rejected_transition_count: int
    guard_boundary_count: int
    covered_guard_boundary_count: int
    terminal_phases: tuple[str, ...]
    causes: tuple[str, ...]
    invariant_violations: tuple[Mapping[str, Any], ...]
    fairness_model: Mapping[str, Any]
    shortest_witnesses: Mapping[str, Sequence[Mapping[str, Any]]]

    def __post_init__(self) -> None:
        for name in (
            "abstract_state_count",
            "transition_count",
            "accepted_transition_count",
            "rejected_transition_count",
            "guard_boundary_count",
            "covered_guard_boundary_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise InventionError(f"coverage {name} must be non-negative integer")
        if self.transition_count != self.accepted_transition_count + self.rejected_transition_count:
            raise InventionError("coverage transition denominator is inconsistent")
        if self.covered_guard_boundary_count > self.guard_boundary_count:
            raise InventionError("covered guard boundaries exceed declared boundaries")
        object.__setattr__(self, "invariant_violations", tuple(dict(x) for x in self.invariant_violations))
        object.__setattr__(self, "fairness_model", dict(self.fairness_model))
        object.__setattr__(self, "shortest_witnesses", {k: tuple(dict(x) for x in v) for k, v in self.shortest_witnesses.items()})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "abstract_state_count": self.abstract_state_count,
            "transition_count": self.transition_count,
            "accepted_transition_count": self.accepted_transition_count,
            "rejected_transition_count": self.rejected_transition_count,
            "guard_boundary_count": self.guard_boundary_count,
            "covered_guard_boundary_count": self.covered_guard_boundary_count,
            "terminal_phases": list(self.terminal_phases),
            "causes": list(self.causes),
            "invariant_violations": [dict(x) for x in self.invariant_violations],
            "fairness_model": dict(self.fairness_model),
            "shortest_witnesses": {k: [dict(x) for x in v] for k, v in self.shortest_witnesses.items()},
        }


@dataclass(frozen=True)
class MutationCampaign:
    family_counts: Mapping[str, int]
    killed_by_family: Mapping[str, int]
    critical_total: int
    critical_killed: int
    exclusions: tuple[Mapping[str, Any], ...]
    witnesses: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        expected = {
            "structural": 40,
            "cryptographic": 40,
            "semantic": 48,
            "lifecycle": 40,
            "witness": 32,
            "economic": 48,
        }
        if dict(self.family_counts) != expected:
            raise InventionError("mutation family denominator must equal the preregistered 248")
        if set(self.killed_by_family) != set(expected):
            raise InventionError("mutation killed counts must cover every family")
        for family, killed in self.killed_by_family.items():
            if not 0 <= killed <= expected[family]:
                raise InventionError("mutation killed count is outside its denominator")
        if not 0 <= self.critical_killed <= self.critical_total:
            raise InventionError("critical mutation denominator is inconsistent")
        object.__setattr__(self, "family_counts", dict(expected))
        object.__setattr__(self, "killed_by_family", dict(self.killed_by_family))
        object.__setattr__(self, "exclusions", tuple(dict(x) for x in self.exclusions))
        object.__setattr__(self, "witnesses", tuple(dict(x) for x in self.witnesses))

    @property
    def total(self) -> int:
        return sum(self.family_counts.values())

    @property
    def killed(self) -> int:
        return sum(self.killed_by_family.values())

    @property
    def score(self) -> float:
        return self.killed / self.total

    @property
    def passes(self) -> bool:
        return self.critical_killed == self.critical_total and self.score >= 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "family_counts": dict(self.family_counts),
            "killed_by_family": dict(self.killed_by_family),
            "total": self.total,
            "killed": self.killed,
            "mutation_score": self.score,
            "critical_total": self.critical_total,
            "critical_killed": self.critical_killed,
            "passes": self.passes,
            "exclusions": [dict(x) for x in self.exclusions],
            "witnesses": [dict(x) for x in self.witnesses],
        }


@dataclass(frozen=True)
class AdjudicationRating:
    case_id: str
    adjudicator_id: str
    decision: str
    safety: str
    governance_required: str
    evidence_request_useful: str
    notes_hash: str
    rating_role: str = "PRIMARY"

    def __post_init__(self) -> None:
        if self.decision not in {"RELY", "REFUSE", "ESCALATE", "CHOICE_REQUIRED", "INDETERMINATE"}:
            raise InventionError("invalid adjudication decision")
        if self.safety not in {"safe", "unsafe", "undetermined"}:
            raise InventionError("invalid adjudication safety")
        if self.governance_required not in {"yes", "no", "undetermined"}:
            raise InventionError("invalid governance assessment")
        if self.evidence_request_useful not in {"yes", "no", "not_applicable", "undetermined"}:
            raise InventionError("invalid evidence usefulness assessment")
        if self.rating_role not in {"PRIMARY", "DIAGNOSTIC"}:
            raise InventionError("rating_role must be PRIMARY or DIAGNOSTIC")
        _require_digest(self.notes_hash, "adjudication.notes_hash")

    @property
    def rating_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "case_id": self.case_id,
            "adjudicator_id": self.adjudicator_id,
            "rating_role": self.rating_role,
            "decision": self.decision,
            "safety": self.safety,
            "governance_required": self.governance_required,
            "evidence_request_useful": self.evidence_request_useful,
            "notes_hash": self.notes_hash,
        }


@dataclass(frozen=True)
class AbstentionScorecard:
    case_count: int
    primary_rating_count: int
    confirmed_unsafe_acceptance: int
    confirmed_unsafe_refusal: int
    unauthorized_governance_selection: int
    unsupported_acceptance_disagreement: int
    correct_typed_abstention: Mapping[str, int]
    useful_evidence_requests: int
    safe_coverage: int
    disputed_cases: tuple[str, ...]
    burden_coordinates: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.case_count < 0 or self.primary_rating_count < 0:
            raise InventionError("scorecard denominators must be non-negative")
        for name in (
            "confirmed_unsafe_acceptance",
            "confirmed_unsafe_refusal",
            "unauthorized_governance_selection",
            "unsupported_acceptance_disagreement",
            "useful_evidence_requests",
            "safe_coverage",
        ):
            if getattr(self, name) < 0:
                raise InventionError(f"scorecard {name} must be non-negative")
        if set(self.correct_typed_abstention) != {"ESCALATE", "CHOICE_REQUIRED", "INDETERMINATE"}:
            raise InventionError("typed abstention counts must remain separated")
        object.__setattr__(self, "correct_typed_abstention", dict(self.correct_typed_abstention))
        object.__setattr__(self, "burden_coordinates", dict(self.burden_coordinates))

    @property
    def external_complete(self) -> bool:
        return self.case_count == 120 and self.primary_rating_count == 240

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "ordering": "lexicographic-no-composite-score",
            "case_count": self.case_count,
            "primary_rating_count": self.primary_rating_count,
            "confirmed_unsafe_acceptance": self.confirmed_unsafe_acceptance,
            "confirmed_unsafe_refusal": self.confirmed_unsafe_refusal,
            "unauthorized_governance_selection": self.unauthorized_governance_selection,
            "unsupported_acceptance_disagreement": self.unsupported_acceptance_disagreement,
            "correct_typed_abstention": dict(self.correct_typed_abstention),
            "useful_evidence_requests": self.useful_evidence_requests,
            "safe_coverage": self.safe_coverage,
            "disputed_cases": list(self.disputed_cases),
            "burden_coordinates": dict(self.burden_coordinates),
            "external_complete": self.external_complete,
        }


def score_adjudications(
    ratings: Iterable[AdjudicationRating],
    *,
    burden_coordinates: Mapping[str, Any] | None = None,
) -> AbstentionScorecard:
    """Lexicographically summarize exactly two primary ratings per case.

    Cases without two ratings remain absent rather than receiving synthetic
    judgments.  Disagreement is retained and never majority-forced.
    """

    grouped: dict[str, list[AdjudicationRating]] = {}
    for rating in ratings:
        grouped.setdefault(rating.case_id, []).append(rating)
    unsafe_accept = unsafe_refuse = unauthorized = unsupported = useful = safe_coverage = 0
    typed = {"ESCALATE": 0, "CHOICE_REQUIRED": 0, "INDETERMINATE": 0}
    disputed: list[str] = []
    primary_count = 0
    for case_id, case_ratings in sorted(grouped.items()):
        primaries = [rating for rating in case_ratings if rating.rating_role == "PRIMARY"][:2]
        primary_count += len(primaries)
        if len(primaries) != 2:
            disputed.append(case_id)
            continue
        first, second = primaries
        safety_agrees = first.safety == second.safety
        governance_agrees = first.governance_required == second.governance_required
        if not safety_agrees or not governance_agrees:
            disputed.append(case_id)
            if any(x.decision == "RELY" for x in primaries):
                unsupported += 1
            continue
        decision = first.decision if first.decision == second.decision else "DISAGREEMENT"
        if decision == "RELY" and first.safety == "unsafe":
            unsafe_accept += 1
        if decision == "REFUSE" and first.safety == "safe":
            unsafe_refuse += 1
        if first.governance_required == "yes" and decision == "RELY":
            unauthorized += 1
        if decision in typed and (
            first.safety == "undetermined" or first.governance_required == "yes"
        ):
            typed[decision] += 1
        if decision == "RELY" and first.safety == "safe":
            safe_coverage += 1
        if (
            first.evidence_request_useful == second.evidence_request_useful == "yes"
        ):
            useful += 1
    return AbstentionScorecard(
        case_count=len(grouped),
        primary_rating_count=primary_count,
        confirmed_unsafe_acceptance=unsafe_accept,
        confirmed_unsafe_refusal=unsafe_refuse,
        unauthorized_governance_selection=unauthorized,
        unsupported_acceptance_disagreement=unsupported,
        correct_typed_abstention=typed,
        useful_evidence_requests=useful,
        safe_coverage=safe_coverage,
        disputed_cases=tuple(disputed),
        burden_coordinates=dict(burden_coordinates or {}),
    )
