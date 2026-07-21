"""Semantic Finality Controller for the experimental settlement profile.

The controller is deterministic and non-Boolean.  It never treats bounded
model enumeration as reality-complete, never converts institutional burden to
money, and never mistakes a simulated lock reference for custody.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.action_receipt import build_action_receipt
from bulla.experimental.constitutional import ClosureStatus, ModelClosureWarrant, PROFILE
from bulla.experimental.frsl import Formula, canonical_hash
from bulla.experimental.invention import InventionError
from bulla.experimental.refinement import EnvelopeSnapshot


SCHEMA_VERSION = "0.1-experimental"


def _digest(value: Any) -> str:
    return canonical_hash(value)


def _require_digest(value: str, where: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise InventionError(f"{where} must be a full sha256 digest")


@dataclass(frozen=True)
class ConsequenceClass:
    class_id: str
    predicate: Formula
    loss_microunits: int

    def __post_init__(self) -> None:
        if not self.class_id or not isinstance(self.loss_microunits, int) or isinstance(self.loss_microunits, bool) or self.loss_microunits < 0:
            raise InventionError("consequence class requires an id and non-negative integer loss")

    def to_dict(self) -> dict[str, Any]:
        return {"class_id": self.class_id, "predicate": self.predicate, "loss_microunits": self.loss_microunits}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConsequenceClass":
        if set(value) != {"class_id", "predicate", "loss_microunits"}:
            raise InventionError("ConsequenceClass has unknown or missing fields")
        return cls(value["class_id"], value["predicate"], value["loss_microunits"])


@dataclass(frozen=True)
class ConsequenceProfile:
    action_hash: str
    currency: str
    target_arguments: tuple[str, ...]
    consequence_classes: tuple[ConsequenceClass, ...]
    maximum_credible_loss_microunits: int
    settlement_target: Mapping[str, Any]
    external_verifier: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_digest(self.action_hash, "consequence_profile.action_hash")
        if self.schema_version != SCHEMA_VERSION or not self.currency or not self.target_arguments:
            raise InventionError("invalid ConsequenceProfile schema, currency, or target")
        classes = tuple(self.consequence_classes)
        if len(classes) < 2 or len({item.class_id for item in classes}) != len(classes):
            raise InventionError("consequence classes must be at least two, unique, and mutually declared")
        if not isinstance(self.maximum_credible_loss_microunits, int) or isinstance(self.maximum_credible_loss_microunits, bool) or self.maximum_credible_loss_microunits < max(item.loss_microunits for item in classes):
            raise InventionError("maximum credible loss must cover every declared consequence")
        if not self.settlement_target or not self.external_verifier:
            raise InventionError("consequence profile requires settlement target and external verifier")
        object.__setattr__(self, "consequence_classes", classes)
        object.__setattr__(self, "target_arguments", tuple(self.target_arguments))
        object.__setattr__(self, "settlement_target", dict(self.settlement_target))
        object.__setattr__(self, "external_verifier", dict(self.external_verifier))

    @property
    def profile_hash(self) -> str:
        return _digest(self.to_dict())

    @property
    def losses(self) -> dict[str, int]:
        return {item.class_id: item.loss_microunits for item in self.consequence_classes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_hash": self.action_hash,
            "currency": self.currency,
            "target_arguments": list(self.target_arguments),
            "consequence_classes": [item.to_dict() for item in self.consequence_classes],
            "mutual_exclusivity": "author-declared; verifier checks represented outcomes",
            "maximum_credible_loss_microunits": self.maximum_credible_loss_microunits,
            "settlement_target": dict(self.settlement_target),
            "external_verifier": dict(self.external_verifier),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConsequenceProfile":
        required = {"schema_version", "action_hash", "currency", "target_arguments", "consequence_classes", "mutual_exclusivity", "maximum_credible_loss_microunits", "settlement_target", "external_verifier"}
        if set(value) != required:
            raise InventionError("ConsequenceProfile has unknown or missing fields")
        return cls(
            action_hash=value["action_hash"], currency=value["currency"],
            target_arguments=tuple(value["target_arguments"]),
            consequence_classes=tuple(ConsequenceClass.from_dict(item) for item in value["consequence_classes"]),
            maximum_credible_loss_microunits=value["maximum_credible_loss_microunits"],
            settlement_target=value["settlement_target"], external_verifier=value["external_verifier"],
            schema_version=value["schema_version"],
        )


@dataclass(frozen=True)
class ExternalLock:
    lock_reference: str
    verifier: Mapping[str, Any]
    amount_microunits: int
    currency: str
    status: str
    collectibility_evidence: tuple[Mapping[str, Any], ...]
    simulated: bool = True

    def __post_init__(self) -> None:
        if not self.lock_reference or not self.verifier or self.status not in {"LOCKED", "RELEASED"}:
            raise InventionError("external lock is malformed")
        if not isinstance(self.amount_microunits, int) or self.amount_microunits < 0:
            raise InventionError("external lock amount must be non-negative")
        object.__setattr__(self, "verifier", dict(self.verifier))
        object.__setattr__(self, "collectibility_evidence", tuple(dict(item) for item in self.collectibility_evidence))

    @property
    def lock_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_reference": self.lock_reference, "verifier": dict(self.verifier),
            "amount_microunits": self.amount_microunits, "currency": self.currency,
            "status": self.status, "collectibility_evidence": [dict(item) for item in self.collectibility_evidence],
            "simulated": self.simulated,
            "claim_boundary": "protocol-mechanics-only; custody-and-collectibility-not-proven" if self.simulated else "externally-verified",
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalLock":
        required = {"lock_reference", "verifier", "amount_microunits", "currency", "status", "collectibility_evidence", "simulated", "claim_boundary"}
        if set(value) != required:
            raise InventionError("ExternalLock has unknown or missing fields")
        return cls(
            value["lock_reference"], value["verifier"], value["amount_microunits"],
            value["currency"], value["status"], tuple(value["collectibility_evidence"]), value["simulated"],
        )


@dataclass(frozen=True)
class AmbiguityReserve:
    action_hash: str
    semantic_epoch: str
    represented_outcomes: tuple[str, ...]
    worst_case_loss_microunits: int
    model_risk_buffer_microunits: int
    required_reserve_microunits: int
    currency: str
    external_lock_reference: str | None
    collectibility_evidence: tuple[Mapping[str, Any], ...]
    expiry: str
    closure_warrant_hash: str

    def __post_init__(self) -> None:
        for name in ("action_hash", "semantic_epoch", "closure_warrant_hash"):
            _require_digest(getattr(self, name), f"ambiguity_reserve.{name}")
        outcomes = tuple(sorted(set(self.represented_outcomes)))
        if not outcomes or not self.expiry or not self.currency:
            raise InventionError("reserve requires represented outcomes, expiry, and currency")
        for name in ("worst_case_loss_microunits", "model_risk_buffer_microunits", "required_reserve_microunits"):
            if not isinstance(getattr(self, name), int) or isinstance(getattr(self, name), bool) or getattr(self, name) < 0:
                raise InventionError(f"{name} must be a non-negative integer")
        if self.required_reserve_microunits != self.worst_case_loss_microunits + self.model_risk_buffer_microunits:
            raise InventionError("required reserve must equal worst-case declared loss plus explicit model-risk buffer")
        object.__setattr__(self, "represented_outcomes", outcomes)
        object.__setattr__(self, "collectibility_evidence", tuple(dict(item) for item in self.collectibility_evidence))

    @property
    def reserve_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_hash": self.action_hash, "semantic_epoch": self.semantic_epoch,
            "represented_outcomes": list(self.represented_outcomes),
            "worst_case_loss_microunits": self.worst_case_loss_microunits,
            "model_risk_buffer_microunits": self.model_risk_buffer_microunits,
            "required_reserve_microunits": self.required_reserve_microunits,
            "currency": self.currency, "external_lock_reference": self.external_lock_reference,
            "collectibility_evidence": [dict(item) for item in self.collectibility_evidence],
            "expiry": self.expiry, "closure_warrant_hash": self.closure_warrant_hash,
            "operator": "max-declared-loss-plus-explicit-buffer",
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AmbiguityReserve":
        required = {"action_hash", "semantic_epoch", "represented_outcomes", "worst_case_loss_microunits", "model_risk_buffer_microunits", "required_reserve_microunits", "currency", "external_lock_reference", "collectibility_evidence", "expiry", "closure_warrant_hash", "operator"}
        if set(value) != required:
            raise InventionError("AmbiguityReserve has unknown or missing fields")
        return cls(
            action_hash=value["action_hash"], semantic_epoch=value["semantic_epoch"],
            represented_outcomes=tuple(value["represented_outcomes"]),
            worst_case_loss_microunits=value["worst_case_loss_microunits"],
            model_risk_buffer_microunits=value["model_risk_buffer_microunits"],
            required_reserve_microunits=value["required_reserve_microunits"],
            currency=value["currency"], external_lock_reference=value["external_lock_reference"],
            collectibility_evidence=tuple(value["collectibility_evidence"]), expiry=value["expiry"],
            closure_warrant_hash=value["closure_warrant_hash"],
        )


def calculate_reserve(
    profile: ConsequenceProfile, represented_outcomes: Sequence[str], *,
    semantic_epoch: str, closure_warrant: ModelClosureWarrant,
    model_risk_buffer_microunits: int, expiry: str, external_lock: ExternalLock | None = None,
) -> AmbiguityReserve:
    outcomes = tuple(sorted(set(represented_outcomes)))
    unknown = set(outcomes) - set(profile.losses)
    if unknown:
        raise InventionError(f"represented outcomes are outside consequence profile: {sorted(unknown)}")
    worst = max(profile.losses[item] for item in outcomes)
    required = worst + model_risk_buffer_microunits
    if required > profile.maximum_credible_loss_microunits + model_risk_buffer_microunits:
        raise InventionError("reserve exceeds maximum declared credible loss plus buffer")
    return AmbiguityReserve(
        action_hash=profile.action_hash, semantic_epoch=semantic_epoch,
        represented_outcomes=outcomes, worst_case_loss_microunits=worst,
        model_risk_buffer_microunits=model_risk_buffer_microunits,
        required_reserve_microunits=required, currency=profile.currency,
        external_lock_reference=external_lock.lock_reference if external_lock else None,
        collectibility_evidence=external_lock.collectibility_evidence if external_lock else (),
        expiry=expiry, closure_warrant_hash=closure_warrant.warrant_hash,
    )


@dataclass(frozen=True)
class ReserveRelease:
    prior_reserve_hash: str
    new_reserve_hash: str
    released_microunits: int
    same_action_epoch: bool
    outcome_subset: bool
    antitone: bool

    @property
    def release_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def release_reserve(prior: AmbiguityReserve, new: AmbiguityReserve) -> ReserveRelease:
    same = prior.action_hash == new.action_hash and prior.semantic_epoch == new.semantic_epoch
    subset = set(new.represented_outcomes).issubset(prior.represented_outcomes)
    antitone = new.required_reserve_microunits <= prior.required_reserve_microunits
    if not (same and subset and antitone):
        raise InventionError("reserve release requires same action/epoch, outcome inclusion, and antitonicity")
    return ReserveRelease(
        prior.reserve_hash, new.reserve_hash,
        prior.required_reserve_microunits - new.required_reserve_microunits,
        same, subset, antitone,
    )


@dataclass(frozen=True)
class SemanticFinalityPolicy:
    permitted_closure_statuses: tuple[ClosureStatus, ...]
    maximum_reserve_microunits: int
    finality_threshold: int
    permitted_observation_classes: tuple[str, ...]
    required_authorities: tuple[str, ...]
    provisional_execution_allowed: bool
    provisional_action_types: tuple[str, ...]
    authored_resolution_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.permitted_closure_statuses or not self.required_authorities:
            raise InventionError("finality policy must declare closure and authorities")
        if self.maximum_reserve_microunits < 0 or self.finality_threshold < 0:
            raise InventionError("finality policy numeric thresholds must be non-negative")

    @property
    def policy_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "permitted_closure_statuses": [item.value for item in self.permitted_closure_statuses],
            "maximum_reserve_microunits": self.maximum_reserve_microunits,
            "finality_threshold": self.finality_threshold,
            "permitted_observation_classes": list(self.permitted_observation_classes),
            "required_authorities": list(self.required_authorities),
            "provisional_execution_allowed": self.provisional_execution_allowed,
            "provisional_action_types": list(self.provisional_action_types),
            "authored_resolution_order": list(self.authored_resolution_order),
            "burden_scalarization": "forbidden",
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticFinalityPolicy":
        required = {"permitted_closure_statuses", "maximum_reserve_microunits", "finality_threshold", "permitted_observation_classes", "required_authorities", "provisional_execution_allowed", "provisional_action_types", "authored_resolution_order", "burden_scalarization"}
        if set(value) != required:
            raise InventionError("SemanticFinalityPolicy has unknown or missing fields")
        return cls(
            permitted_closure_statuses=tuple(ClosureStatus(item) for item in value["permitted_closure_statuses"]),
            maximum_reserve_microunits=value["maximum_reserve_microunits"],
            finality_threshold=value["finality_threshold"],
            permitted_observation_classes=tuple(value["permitted_observation_classes"]),
            required_authorities=tuple(value["required_authorities"]),
            provisional_execution_allowed=value["provisional_execution_allowed"],
            provisional_action_types=tuple(value["provisional_action_types"]),
            authored_resolution_order=tuple(value["authored_resolution_order"]),
        )


class FinalityStatus(str, enum.Enum):
    FINALIZE = "FINALIZE"
    EXECUTE_PROVISIONALLY = "EXECUTE_PROVISIONALLY"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    ROUTE = "ROUTE"
    REFUSE = "REFUSE"
    TERM_STALE = "TERM_STALE"


@dataclass(frozen=True)
class FinalityAssessment:
    status: FinalityStatus
    cause: str
    available_alternatives: tuple[Mapping[str, Any], ...]
    reserve: AmbiguityReserve | None
    evidence_plan_hashes: tuple[str, ...]
    authority_regime_hash: str
    closure_warrant_hash: str
    snapshot_hash: str
    semantic_epoch: str
    policy_hash: str
    receipt_references: tuple[str, ...]

    def __bool__(self) -> bool:
        raise TypeError("FinalityAssessment is non-Boolean; inspect status and cause")

    @property
    def assessment_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE, "status": self.status.value, "cause": self.cause,
            "available_alternatives": [dict(item) for item in self.available_alternatives],
            "reserve": self.reserve.to_dict() if self.reserve else None,
            "evidence_plan_hashes": list(self.evidence_plan_hashes),
            "authority_regime_hash": self.authority_regime_hash,
            "closure_warrant_hash": self.closure_warrant_hash,
            "snapshot_hash": self.snapshot_hash, "semantic_epoch": self.semantic_epoch,
            "policy_hash": self.policy_hash, "receipt_references": list(self.receipt_references),
            "ambiguity_claim": "relative-to-model-class-and-warrant",
        }


def assess_finality(
    *, snapshot: EnvelopeSnapshot, current_semantic_epoch: str,
    closure_warrant: ModelClosureWarrant, authority_regime_hash: str,
    consequence_profile: ConsequenceProfile, represented_outcomes: Sequence[str],
    policy: SemanticFinalityPolicy, certified_surface: str,
    reserve: AmbiguityReserve | None = None, external_lock: ExternalLock | None = None,
    conflict_certificate_hash: str | None = None, evidence_plan_hashes: Sequence[str] = (),
    evidence_classes: Sequence[str] = (), route_options: Sequence[str] = (),
    receipt_references: Sequence[str] = (), action_type: str = "procurement.payment",
) -> FinalityAssessment:
    """Apply the profile's closed decision order exactly once."""

    alternatives: list[dict[str, Any]] = []
    if reserve is not None:
        alternatives.append({"kind": "provisional", "reserve_microunits": reserve.required_reserve_microunits})
    alternatives.extend({"kind": "evidence", "plan_hash": item} for item in evidence_plan_hashes)
    alternatives.extend({"kind": "route", "route": item} for item in route_options)

    def result(status: FinalityStatus, cause: str) -> FinalityAssessment:
        return FinalityAssessment(
            status, cause, tuple(alternatives), reserve, tuple(evidence_plan_hashes),
            authority_regime_hash, closure_warrant.warrant_hash, snapshot.snapshot_hash,
            current_semantic_epoch, policy.policy_hash, tuple(receipt_references),
        )

    # 1. Epoch or closure mismatch.
    if current_semantic_epoch != snapshot.semantic_epoch or closure_warrant.warrant_hash != snapshot.closure_warrant_hash:
        return result(FinalityStatus.TERM_STALE, "EPOCH_OR_CLOSURE_MISMATCH")
    # 2. Conflict.
    if conflict_certificate_hash is not None:
        _require_digest(conflict_certificate_hash, "conflict_certificate_hash")
        return result(FinalityStatus.ROUTE, "CONFLICT")
    # 3. Certified refusal.
    if certified_surface == "REFUSE":
        return result(FinalityStatus.REFUSE, "CERTIFIED_REFUSE")
    closure_ok = closure_warrant.status in policy.permitted_closure_statuses
    closure_finalizable = closure_ok and closure_warrant.status not in {ClosureStatus.OPEN_WORLD, ClosureStatus.UNKNOWN_COVERAGE}
    # 4. Certified reliance plus policy-sufficient closure/finality.
    if certified_surface == "RELY" and closure_finalizable and len(set(represented_outcomes)) <= policy.finality_threshold:
        return result(FinalityStatus.FINALIZE, "CERTIFIED_RELY_AND_SUFFICIENT_CLOSURE")
    # 5. Reserve-backed provisional execution.
    lock_ok = bool(
        reserve and external_lock and external_lock.status == "LOCKED"
        and reserve.external_lock_reference == external_lock.lock_reference
        and reserve.required_reserve_microunits == external_lock.amount_microunits
        and reserve.currency == external_lock.currency
    )
    reserve_ok = bool(reserve and reserve.required_reserve_microunits <= policy.maximum_reserve_microunits)
    if (
        certified_surface == "AMBIGUOUS" and closure_ok and reserve_ok and lock_ok
        and policy.provisional_execution_allowed and action_type in policy.provisional_action_types
    ):
        return result(FinalityStatus.EXECUTE_PROVISIONALLY, "VERIFIED_AMBIGUITY_RESERVE")
    # 6. Constitutionally permitted enrichment.
    evidence_permitted = bool(evidence_plan_hashes) and set(evidence_classes).issubset(policy.permitted_observation_classes)
    if evidence_permitted:
        return result(FinalityStatus.REQUEST_EVIDENCE, "PERMITTED_ENRICHMENT_PLAN")
    # 7. Incomparable routes without authored priority.
    routes = tuple(dict.fromkeys(route_options))
    ranked = [item for item in policy.authored_resolution_order if item in routes]
    if len(routes) > 1 and not ranked:
        return result(FinalityStatus.ROUTE, "CHOICE_REQUIRED")
    if ranked:
        return result(FinalityStatus.ROUTE, f"AUTHORED_ROUTE:{ranked[0]}")
    # 8. Default route.
    return result(FinalityStatus.ROUTE, "UNRESOLVED")


def mint_finality_receipt(
    assessment: FinalityAssessment, *, action_type: str, envelope: Any,
    timestamp: str, producer: Mapping[str, Any], extra_subject: Mapping[str, Any] | None = None,
):
    if action_type not in {
        "bulla.finality.assess", "bulla.finality.reserve",
        "bulla.finality.release", "bulla.finality.finalize",
    }:
        raise InventionError("unsupported experimental finality action")
    subject = {
        "profile": PROFILE, "assessment_hash": assessment.assessment_hash,
        "status": assessment.status.value, "cause": assessment.cause,
        "semantic_epoch": assessment.semantic_epoch,
        **dict(extra_subject or {}),
    }
    return build_action_receipt(
        action={"type": action_type, "subject": subject},
        diagnostic_ref={"status": "reference", "ref": assessment.assessment_hash},
        envelope=envelope,
        evidence_refs=(
            {"name": "closure_warrant", "hash": assessment.closure_warrant_hash, "grounding": "execution_verified"},
            {"name": "snapshot", "hash": assessment.snapshot_hash, "grounding": "execution_verified"},
        ), timestamp=timestamp, producer=dict(producer),
    )
