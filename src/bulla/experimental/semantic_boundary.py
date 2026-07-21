"""Claim-separated semantic settlement boundary (experimental v0.3).

The module prevents three different propositions from collapsing into one:

* qW -- what is warranted about the world;
* qE -- what follows inside the declared model;
* qS -- what an authority permits the system to settle.

It is deliberately a wrapper around Semantic Finality v0.1.  It changes no
stable result or receipt schema and cannot manufacture an external warrant.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError
from bulla.experimental.semantic_finality import FinalityAssessment, FinalityStatus


PROFILE = "bulla.semantic-boundary/0.3-experimental"
SCHEMA_VERSION = "0.3-experimental"


def _require_digest(value: str, where: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise InventionError(f"{where} must be sha256:<64 hex>")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise InventionError(f"{where} must be sha256:<64 hex>") from exc


def _unique(values: Sequence[str], where: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not value for value in result) or len(set(result)) != len(result):
        raise InventionError(f"{where} must contain distinct non-empty values")
    return result


class SubstantiveBoundary(str, enum.Enum):
    SEMANTIC = "SEMANTIC"
    GROUNDING = "GROUNDING"
    AUTHORITY = "AUTHORITY"


class DerivationStatus(str, enum.Enum):
    """Meta-level status of the procedure examining substantive claims."""

    CERTIFIED = "CERTIFIED"
    PARTIAL = "PARTIAL"
    RESOURCE_BOUNDED = "RESOURCE_BOUNDED"
    INVALID = "INVALID"


class WorldClaimStatus(str, enum.Enum):
    OBSERVED = "OBSERVED"
    WARRANTED_RELATIVE = "WARRANTED_RELATIVE"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


class EntailmentStatus(str, enum.Enum):
    CERTIFIED = "CERTIFIED"
    REFUTED = "REFUTED"
    INDETERMINATE = "INDETERMINATE"


class HarmTreatment(str, enum.Enum):
    COMPENSABLE_RESERVED = "COMPENSABLE_RESERVED"
    REVERSIBLE_ONLY = "REVERSIBLE_ONLY"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CATEGORICAL_REFUSE = "CATEGORICAL_REFUSE"


@dataclass(frozen=True)
class WorldClaim:
    proposition_hash: str
    status: WorldClaimStatus
    warrant_hashes: tuple[str, ...]
    closure_warrant_hash: str
    scope: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_digest(self.proposition_hash, "qW.proposition_hash")
        _require_digest(self.closure_warrant_hash, "qW.closure_warrant_hash")
        for value in self.warrant_hashes:
            _require_digest(value, "qW.warrant_hash")
        if self.status in {WorldClaimStatus.OBSERVED, WorldClaimStatus.WARRANTED_RELATIVE} and not self.warrant_hashes:
            raise InventionError("supported qW requires at least one warrant")
        if not self.scope:
            raise InventionError("qW requires an explicit scope")
        object.__setattr__(self, "warrant_hashes", tuple(self.warrant_hashes))
        object.__setattr__(self, "scope", dict(self.scope))

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposition_hash": self.proposition_hash,
            "status": self.status.value,
            "warrant_hashes": list(self.warrant_hashes),
            "closure_warrant_hash": self.closure_warrant_hash,
            "scope": dict(self.scope),
            "truth_claim": "warranted-and-scope-relative; never theorem-derived",
        }


@dataclass(frozen=True)
class EntailmentClaim:
    premise_hash: str
    conclusion_hash: str
    status: EntailmentStatus
    certificate_hash: str | None
    model_class_hash: str
    residual_boundaries: tuple[SubstantiveBoundary, ...] = ()
    derivation_status: DerivationStatus = DerivationStatus.CERTIFIED

    def __post_init__(self) -> None:
        for name in ("premise_hash", "conclusion_hash", "model_class_hash"):
            _require_digest(getattr(self, name), f"qE.{name}")
        if self.certificate_hash is not None:
            _require_digest(self.certificate_hash, "qE.certificate_hash")
        if self.status is EntailmentStatus.CERTIFIED and self.certificate_hash is None:
            raise InventionError("certified qE requires a certificate")
        if len(set(self.residual_boundaries)) != len(self.residual_boundaries):
            raise InventionError("qE residual substantive boundaries must be distinct")
        if self.status is EntailmentStatus.CERTIFIED and self.derivation_status is not DerivationStatus.CERTIFIED:
            raise InventionError("certified qE requires a certified derivation")
        if self.status is EntailmentStatus.REFUTED and self.derivation_status is not DerivationStatus.CERTIFIED:
            raise InventionError("refuted qE requires a certified derivation")
        if self.derivation_status is DerivationStatus.RESOURCE_BOUNDED and self.status is not EntailmentStatus.INDETERMINATE:
            raise InventionError("resource-bounded derivation cannot claim certified or refuted entailment")
        object.__setattr__(self, "residual_boundaries", tuple(self.residual_boundaries))

    def to_dict(self) -> dict[str, Any]:
        return {
            "premise_hash": self.premise_hash,
            "conclusion_hash": self.conclusion_hash,
            "status": self.status.value,
            "certificate_hash": self.certificate_hash,
            "model_class_hash": self.model_class_hash,
            "residual_boundaries": [boundary.value for boundary in self.residual_boundaries],
            "derivation_status": self.derivation_status.value,
            "claim_boundary": "logical-consequence-only; not a world observation",
        }


@dataclass(frozen=True)
class SettlementClaim:
    action_hash: str
    status: FinalityStatus
    assessment_hash: str
    authority_regime_hash: str
    semantic_epoch: str
    recourse_forum: str

    def __post_init__(self) -> None:
        for name in ("action_hash", "assessment_hash", "authority_regime_hash", "semantic_epoch"):
            _require_digest(getattr(self, name), f"qS.{name}")
        if not self.recourse_forum:
            raise InventionError("qS requires a recourse forum")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_hash": self.action_hash,
            "status": self.status.value,
            "assessment_hash": self.assessment_hash,
            "authority_regime_hash": self.authority_regime_hash,
            "semantic_epoch": self.semantic_epoch,
            "recourse_forum": self.recourse_forum,
            "claim_boundary": "authorized-settlement-only; not semantic or empirical truth",
        }


@dataclass(frozen=True)
class ClaimChain:
    q_world: WorldClaim
    q_entailment: EntailmentClaim
    q_settlement: SettlementClaim

    @property
    def chain_hash(self) -> str:
        return canonical_hash(self.to_dict())

    @property
    def residual_boundaries(self) -> tuple[SubstantiveBoundary, ...]:
        boundaries = list(self.q_entailment.residual_boundaries)
        if self.q_world.status in {WorldClaimStatus.DISPUTED, WorldClaimStatus.UNKNOWN}:
            boundaries.append(SubstantiveBoundary.GROUNDING)
        return tuple(dict.fromkeys(boundaries))

    @property
    def derivation_status(self) -> DerivationStatus:
        return self.q_entailment.derivation_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "qW": self.q_world.to_dict(),
            "qE": self.q_entailment.to_dict(),
            "qS": self.q_settlement.to_dict(),
            "separation_invariant": "qE never upgrades qW; qS never upgrades qE or qW",
            "boundary_model": "semantic-grounding-authority substantive; derivation status meta-level",
        }


@dataclass(frozen=True)
class OutcomeTreatment:
    outcome_id: str
    treatment: HarmTreatment
    maximum_harm_microunits: int

    def __post_init__(self) -> None:
        if not self.outcome_id:
            raise InventionError("harm outcome requires an id")
        if not isinstance(self.maximum_harm_microunits, int) or isinstance(self.maximum_harm_microunits, bool) or self.maximum_harm_microunits < 0:
            raise InventionError("maximum harm must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "treatment": self.treatment.value,
            "maximum_harm_microunits": self.maximum_harm_microunits,
        }


@dataclass(frozen=True)
class ClosureRiskAllocation:
    closure_warrant_hash: str
    authority_hash: str
    risk_bearer: str
    currency: str
    allocated_reserve_microunits: int
    treatments: tuple[OutcomeTreatment, ...]
    challenge_forum: str
    expiry: str

    def __post_init__(self) -> None:
        _require_digest(self.closure_warrant_hash, "risk_allocation.closure_warrant_hash")
        _require_digest(self.authority_hash, "risk_allocation.authority_hash")
        if not self.risk_bearer or not self.currency or not self.challenge_forum or not self.expiry:
            raise InventionError("risk allocation requires bearer, currency, forum, and expiry")
        if not isinstance(self.allocated_reserve_microunits, int) or isinstance(self.allocated_reserve_microunits, bool) or self.allocated_reserve_microunits < 0:
            raise InventionError("allocated reserve must be a non-negative integer")
        treatments = tuple(self.treatments)
        _unique(tuple(item.outcome_id for item in treatments), "risk allocation outcomes")
        if not treatments:
            raise InventionError("risk allocation requires outcome treatments")
        object.__setattr__(self, "treatments", treatments)

    @property
    def allocation_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "closure_warrant_hash": self.closure_warrant_hash,
            "authority_hash": self.authority_hash,
            "risk_bearer": self.risk_bearer,
            "currency": self.currency,
            "allocated_reserve_microunits": self.allocated_reserve_microunits,
            "treatments": [item.to_dict() for item in self.treatments],
            "challenge_forum": self.challenge_forum,
            "expiry": self.expiry,
            "claim_boundary": "allocates declared closure risk; does not prove model completeness or collectibility",
        }


@dataclass(frozen=True)
class BoundaryAssessment:
    status: FinalityStatus
    cause: str
    base_assessment_hash: str
    claim_chain_hash: str
    allocation_hash: str | None
    active_outcomes: tuple[str, ...]
    residual_boundaries: tuple[SubstantiveBoundary, ...]
    derivation_status: DerivationStatus
    recourse_forum: str

    def __bool__(self) -> bool:
        raise TypeError("BoundaryAssessment is non-Boolean; inspect status and cause")

    @property
    def assessment_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "status": self.status.value,
            "cause": self.cause,
            "base_assessment_hash": self.base_assessment_hash,
            "claim_chain_hash": self.claim_chain_hash,
            "allocation_hash": self.allocation_hash,
            "active_outcomes": list(self.active_outcomes),
            "residual_boundaries": [boundary.value for boundary in self.residual_boundaries],
            "derivation_status": self.derivation_status.value,
            "recourse_forum": self.recourse_forum,
        }


def assess_semantic_boundary(
    base: FinalityAssessment,
    chain: ClaimChain,
    allocation: ClosureRiskAllocation | None,
    *,
    active_outcomes: Sequence[str],
    rollback_reference: str | None = None,
) -> BoundaryAssessment:
    """Apply constitutional harm and claim-separation gates to a v0.1 decision."""

    outcomes = _unique(active_outcomes, "active outcomes")
    if not outcomes:
        raise InventionError("boundary assessment requires active outcomes")

    def result(status: FinalityStatus, cause: str) -> BoundaryAssessment:
        return BoundaryAssessment(
            status=status,
            cause=cause,
            base_assessment_hash=base.assessment_hash,
            claim_chain_hash=chain.chain_hash,
            allocation_hash=allocation.allocation_hash if allocation else None,
            active_outcomes=outcomes,
            residual_boundaries=chain.residual_boundaries,
            derivation_status=chain.derivation_status,
            recourse_forum=chain.q_settlement.recourse_forum,
        )

    settlement = chain.q_settlement
    if (
        settlement.assessment_hash != base.assessment_hash
        or settlement.status is not base.status
        or settlement.semantic_epoch != base.semantic_epoch
        or settlement.authority_regime_hash != base.authority_regime_hash
    ):
        return result(FinalityStatus.TERM_STALE, "CLAIM_CHAIN_BINDING_MISMATCH")
    if chain.q_world.closure_warrant_hash != base.closure_warrant_hash:
        return result(FinalityStatus.TERM_STALE, "QW_CLOSURE_MISMATCH")
    # Preserve the v0.1 controller's first and third precedence gates.  A
    # boundary policy may narrow an action; it may not revive a stale term or
    # erase a certified refusal.
    if base.status is FinalityStatus.TERM_STALE:
        return result(FinalityStatus.TERM_STALE, f"BASE:{base.cause}")
    if base.status is FinalityStatus.REFUSE:
        return result(FinalityStatus.REFUSE, f"BASE:{base.cause}")
    if chain.q_entailment.status is EntailmentStatus.REFUTED:
        return result(FinalityStatus.REFUSE, "REFUTED_ENTAILMENT")
    if chain.derivation_status is DerivationStatus.INVALID:
        return result(FinalityStatus.ROUTE, "INVALID_DERIVATION")
    if chain.derivation_status is DerivationStatus.PARTIAL and base.status in {
        FinalityStatus.FINALIZE,
        FinalityStatus.EXECUTE_PROVISIONALLY,
    }:
        return result(FinalityStatus.ROUTE, "PARTIAL_DERIVATION")
    if chain.derivation_status is DerivationStatus.RESOURCE_BOUNDED and base.status in {
        FinalityStatus.FINALIZE,
        FinalityStatus.EXECUTE_PROVISIONALLY,
    }:
        return result(FinalityStatus.ROUTE, "RESOURCE_BOUNDED_DERIVATION")
    if chain.q_entailment.status is EntailmentStatus.INDETERMINATE and base.status in {
        FinalityStatus.FINALIZE,
        FinalityStatus.EXECUTE_PROVISIONALLY,
    }:
        return result(FinalityStatus.ROUTE, "SEMANTIC_INDETERMINACY")

    if allocation is None:
        if base.status is FinalityStatus.EXECUTE_PROVISIONALLY:
            return result(FinalityStatus.ROUTE, "UNALLOCATED_CLOSURE_RISK")
        if chain.q_world.status in {WorldClaimStatus.DISPUTED, WorldClaimStatus.UNKNOWN} and base.status is FinalityStatus.FINALIZE:
            return result(FinalityStatus.ROUTE, "UNRESOLVED_GROUNDING")
        return result(base.status, f"BASE:{base.cause}")

    if allocation.closure_warrant_hash != base.closure_warrant_hash:
        return result(FinalityStatus.TERM_STALE, "RISK_ALLOCATION_CLOSURE_MISMATCH")
    by_outcome = {item.outcome_id: item for item in allocation.treatments}
    missing = set(outcomes) - set(by_outcome)
    if missing:
        return result(FinalityStatus.ROUTE, "UNALLOCATED_OUTCOME")
    active = tuple(by_outcome[item] for item in outcomes)
    treatments = {item.treatment for item in active}
    if HarmTreatment.CATEGORICAL_REFUSE in treatments:
        return result(FinalityStatus.REFUSE, "CATEGORICAL_HARM_REFUSAL")
    if HarmTreatment.HUMAN_REVIEW_REQUIRED in treatments:
        return result(FinalityStatus.ROUTE, "HUMAN_REVIEW_REQUIRED")
    if HarmTreatment.REVERSIBLE_ONLY in treatments:
        if base.status is FinalityStatus.FINALIZE:
            return result(FinalityStatus.ROUTE, "REVERSIBILITY_BARS_FINALITY")
        if base.status is FinalityStatus.EXECUTE_PROVISIONALLY and not rollback_reference:
            return result(FinalityStatus.ROUTE, "MISSING_ROLLBACK_BINDING")
    compensable_harm = max(
        (item.maximum_harm_microunits for item in active if item.treatment is HarmTreatment.COMPENSABLE_RESERVED),
        default=0,
    )
    if compensable_harm > allocation.allocated_reserve_microunits:
        return result(FinalityStatus.ROUTE, "CLOSURE_RISK_RESERVE_SHORTFALL")
    if base.status is FinalityStatus.EXECUTE_PROVISIONALLY and base.reserve is not None:
        if allocation.currency != base.reserve.currency or allocation.allocated_reserve_microunits < base.reserve.required_reserve_microunits:
            return result(FinalityStatus.ROUTE, "SEMANTIC_RESERVE_NOT_COVERED")
    return result(base.status, f"BOUNDARY_GATES_PASSED:{base.cause}")


class TraceDecision(str, enum.Enum):
    RELY = "RELY"
    REFUSE = "REFUSE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class TraceCell:
    cell_id: str
    decision: TraceDecision

    def __post_init__(self) -> None:
        if not self.cell_id:
            raise InventionError("trace cell requires an id")

    def to_dict(self) -> dict[str, str]:
        return {"cell_id": self.cell_id, "decision": self.decision.value}


@dataclass(frozen=True)
class TraceRefinementCertificate:
    prior_semantic_epoch: str
    refined_semantic_epoch: str
    prior_trace_hash: str
    refined_trace_hash: str
    prior_cells: tuple[TraceCell, ...]
    refined_cells: tuple[TraceCell, ...]
    same_epoch: bool
    same_domain: bool
    prior_rely_preserved: bool
    prior_refuse_preserved: bool
    ambiguous_antitone: bool

    def __post_init__(self) -> None:
        for name in ("prior_semantic_epoch", "refined_semantic_epoch", "prior_trace_hash", "refined_trace_hash"):
            _require_digest(getattr(self, name), f"trace_certificate.{name}")
        for label, cells in (("prior", self.prior_cells), ("refined", self.refined_cells)):
            _unique(tuple(cell.cell_id for cell in cells), f"{label} trace cells")
        object.__setattr__(self, "prior_cells", tuple(self.prior_cells))
        object.__setattr__(self, "refined_cells", tuple(self.refined_cells))

    @property
    def valid(self) -> bool:
        return self.same_epoch and self.same_domain and self.prior_rely_preserved and self.prior_refuse_preserved and self.ambiguous_antitone

    @property
    def certificate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "prior_semantic_epoch": self.prior_semantic_epoch,
            "refined_semantic_epoch": self.refined_semantic_epoch,
            "prior_trace_hash": self.prior_trace_hash,
            "refined_trace_hash": self.refined_trace_hash,
            "prior_cells": [cell.to_dict() for cell in self.prior_cells],
            "refined_cells": [cell.to_dict() for cell in self.refined_cells],
            "same_epoch": self.same_epoch,
            "same_domain": self.same_domain,
            "prior_rely_preserved": self.prior_rely_preserved,
            "prior_refuse_preserved": self.prior_refuse_preserved,
            "ambiguous_antitone": self.ambiguous_antitone,
            "valid": self.valid,
        }


def certify_trace_refinement(
    prior_semantic_epoch: str,
    prior_cells: Sequence[TraceCell],
    refined_semantic_epoch: str,
    refined_cells: Sequence[TraceCell],
) -> TraceRefinementCertificate:
    _require_digest(prior_semantic_epoch, "prior_semantic_epoch")
    _require_digest(refined_semantic_epoch, "refined_semantic_epoch")
    prior = tuple(sorted(prior_cells, key=lambda cell: cell.cell_id))
    refined = tuple(sorted(refined_cells, key=lambda cell: cell.cell_id))
    prior_map = {cell.cell_id: cell.decision for cell in prior}
    refined_map = {cell.cell_id: cell.decision for cell in refined}
    same_domain = set(prior_map) == set(refined_map)
    return TraceRefinementCertificate(
        prior_semantic_epoch=prior_semantic_epoch,
        refined_semantic_epoch=refined_semantic_epoch,
        prior_trace_hash=canonical_hash({"semantic_epoch": prior_semantic_epoch, "cells": [cell.to_dict() for cell in prior]}),
        refined_trace_hash=canonical_hash({"semantic_epoch": refined_semantic_epoch, "cells": [cell.to_dict() for cell in refined]}),
        prior_cells=prior,
        refined_cells=refined,
        same_epoch=prior_semantic_epoch == refined_semantic_epoch,
        same_domain=same_domain,
        prior_rely_preserved=all(refined_map.get(cell_id) is TraceDecision.RELY for cell_id, decision in prior_map.items() if decision is TraceDecision.RELY),
        prior_refuse_preserved=all(refined_map.get(cell_id) is TraceDecision.REFUSE for cell_id, decision in prior_map.items() if decision is TraceDecision.REFUSE),
        ambiguous_antitone=all(prior_map.get(cell_id) is TraceDecision.AMBIGUOUS for cell_id, decision in refined_map.items() if decision is TraceDecision.AMBIGUOUS),
    )


def verify_trace_refinement(certificate: TraceRefinementCertificate) -> bool:
    expected = certify_trace_refinement(
        certificate.prior_semantic_epoch,
        certificate.prior_cells,
        certificate.refined_semantic_epoch,
        certificate.refined_cells,
    )
    return certificate.to_dict() == expected.to_dict() and certificate.valid
