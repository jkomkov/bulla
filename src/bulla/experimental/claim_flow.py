"""Typed warrant flow and finality explanation (experimental v0.4).

This module is intentionally not a general provenance graph.  It verifies one
action-scoped derivation trace and keeps evidence appraisal, forum findings,
precedent adoption, and settlement authority distinct.  In particular, a
forum finding is an institutional fact for its bound case; only the explicit
``adopt_precedent`` constructor can create a reusable rule.

All resource limits that may affect an action are logical, precommitted, and
replayable.  Wall time and memory are retained as observations only.
"""

from __future__ import annotations

import enum
import hashlib
import itertools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import (
    Formula,
    Signature,
    canonical_hash,
    formula_relations,
    normalize_formula,
    validate_formula,
)
from bulla.experimental.invention import InventionError
from bulla.experimental.scope import ScopeOrderStatus, StructuredScope, scope_leq


PROFILE = "bulla.claim-flow/0.4-experimental"
SCHEMA_VERSION = "0.4-experimental"
REFERENCE_VERIFIER = {
    "id": "bulla.experimental.claim-flow.reference",
    "version": SCHEMA_VERSION,
    "trust": "closed-artifact-replay",
}


def _require_digest(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise InventionError(f"{where} must be a full lowercase sha256 digest")
    return value


def _require_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InventionError(f"{where} must be a non-empty string")
    return value


def _unique(values: Sequence[str], where: str, *, nonempty: bool = False) -> tuple[str, ...]:
    result = tuple(values)
    if (nonempty and not result) or any(not isinstance(x, str) or not x for x in result):
        raise InventionError(f"{where} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise InventionError(f"{where} must contain distinct values")
    return result


def _closed(value: Any, required: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InventionError(f"{where} must be an object")
    if set(value) != required:
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required)
        raise InventionError(f"{where} has missing={missing} unknown={unknown}")
    return value


class ClaimPermission(str, enum.Enum):
    APPRAISE = "APPRAISE"
    FORUM_FINDING = "FORUM_FINDING"
    ADOPT_PRECEDENT = "ADOPT_PRECEDENT"
    SETTLE = "SETTLE"


class PrecedentEffect(str, enum.Enum):
    CASE_ONLY = "CASE_ONLY"
    PERSUASIVE = "PERSUASIVE"
    BINDING_WITHIN_SCOPE = "BINDING_WITHIN_SCOPE"


class AppealState(str, enum.Enum):
    OPEN = "OPEN"
    FINAL = "FINAL"
    REVIEWED = "REVIEWED"
    REMEDIED = "REMEDIED"


class AdoptionStatus(str, enum.Enum):
    ADOPTED = "ADOPTED"
    LEGISLATION_REQUIRED = "LEGISLATION_REQUIRED"
    STALE = "STALE"
    ROUTE = "ROUTE"


class DerivationDisposition(str, enum.Enum):
    CERTIFIED = "CERTIFIED"
    PARTIAL = "PARTIAL"
    RESOURCE_BOUNDED = "RESOURCE_BOUNDED"
    INVALID = "INVALID"


class BudgetAuthorizationStatus(str, enum.Enum):
    AUTHORIZED = "AUTHORIZED"
    UNAUTHORIZED_DERIVATION_BUDGET = "UNAUTHORIZED_DERIVATION_BUDGET"
    FRONTIER_INVALID = "FRONTIER_INVALID"


class BlockerKind(str, enum.Enum):
    SEMANTIC = "SEMANTIC"
    GROUNDING = "GROUNDING"
    AUTHORITY = "AUTHORITY"
    HARM = "HARM"
    EPOCH = "EPOCH"
    RECOURSE = "RECOURSE"
    RESOURCE = "RESOURCE"


class MinimalityStatus(str, enum.Enum):
    EXACT = "EXACT"
    UNRESOLVED = "UNRESOLVED"


class FinalityExplanationStatus(str, enum.Enum):
    FINAL = "FINAL"
    ROUTE = "ROUTE"
    CHOICE_REQUIRED = "CHOICE_REQUIRED"
    RESOURCE_BOUNDED = "RESOURCE_BOUNDED"
    INVALID = "INVALID"


class AcceleratorStatus(str, enum.Enum):
    CANDIDATE_CHECKED = "CANDIDATE_CHECKED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class AuthorityToken:
    token_id: str
    permission: ClaimPermission
    principal: str
    authority_regime_hash: str
    scope_hash: str
    semantic_epoch: str
    authorization_receipt_hash: str

    def __post_init__(self) -> None:
        _require_text(self.token_id, "authority_token.token_id")
        _require_text(self.principal, "authority_token.principal")
        for name in (
            "authority_regime_hash",
            "scope_hash",
            "semantic_epoch",
            "authorization_receipt_hash",
        ):
            _require_digest(getattr(self, name), f"authority_token.{name}")

    @property
    def token_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "permission": self.permission.value,
            "principal": self.principal,
            "authority_regime_hash": self.authority_regime_hash,
            "scope_hash": self.scope_hash,
            "semantic_epoch": self.semantic_epoch,
            "authorization_receipt_hash": self.authorization_receipt_hash,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "AuthorityToken":
        d = _closed(
            value,
            {
                "token_id", "permission", "principal", "authority_regime_hash",
                "scope_hash", "semantic_epoch", "authorization_receipt_hash",
            },
            "authority_token",
        )
        return cls(
            token_id=d["token_id"], permission=ClaimPermission(d["permission"]),
            principal=d["principal"], authority_regime_hash=d["authority_regime_hash"],
            scope_hash=d["scope_hash"], semantic_epoch=d["semantic_epoch"],
            authorization_receipt_hash=d["authorization_receipt_hash"],
        )


@dataclass(frozen=True)
class ClaimFlowAuthority:
    authority_regime_hash: str
    appraisal_grants: tuple[AuthorityToken, ...] = ()
    adjudication_grants: tuple[AuthorityToken, ...] = ()
    precedential_grants: tuple[AuthorityToken, ...] = ()
    settlement_grants: tuple[AuthorityToken, ...] = ()

    def __post_init__(self) -> None:
        _require_digest(self.authority_regime_hash, "claim_flow_authority.authority_regime_hash")
        expected = (
            ("appraisal_grants", ClaimPermission.APPRAISE),
            ("adjudication_grants", ClaimPermission.FORUM_FINDING),
            ("precedential_grants", ClaimPermission.ADOPT_PRECEDENT),
            ("settlement_grants", ClaimPermission.SETTLE),
        )
        seen: set[str] = set()
        for field, permission in expected:
            tokens = tuple(getattr(self, field))
            for token in tokens:
                if token.permission is not permission:
                    raise InventionError(f"{field} contains a {token.permission.value} token")
                if token.authority_regime_hash != self.authority_regime_hash:
                    raise InventionError(f"{field} token belongs to another authority regime")
                if token.token_hash in seen:
                    raise InventionError("authority tokens must be proposition-specifically distinct")
                seen.add(token.token_hash)
            object.__setattr__(self, field, tokens)

    def require(
        self,
        token: AuthorityToken,
        permission: ClaimPermission,
        *,
        semantic_epoch: str,
        scope_hash: str,
    ) -> None:
        fields = {
            ClaimPermission.APPRAISE: self.appraisal_grants,
            ClaimPermission.FORUM_FINDING: self.adjudication_grants,
            ClaimPermission.ADOPT_PRECEDENT: self.precedential_grants,
            ClaimPermission.SETTLE: self.settlement_grants,
        }
        if token.permission is not permission or token.token_hash not in {
            item.token_hash for item in fields[permission]
        }:
            raise InventionError(f"missing explicit {permission.value} authority token")
        if token.semantic_epoch != semantic_epoch:
            raise InventionError(f"{permission.value} authority token is stale")
        if token.scope_hash != scope_hash:
            raise InventionError(f"{permission.value} authority token is borrowed from another scope")


@dataclass(frozen=True)
class EvidenceBundle:
    bundle_hash: str
    proposition_hash: str
    scope_hash: str
    warrant_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("bundle_hash", "proposition_hash", "scope_hash"):
            _require_digest(getattr(self, name), f"evidence_bundle.{name}")
        warrants = _unique(self.warrant_hashes, "evidence_bundle.warrant_hashes", nonempty=True)
        for warrant in warrants:
            _require_digest(warrant, "evidence_bundle.warrant_hash")
        object.__setattr__(self, "warrant_hashes", warrants)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_hash": self.bundle_hash,
            "proposition_hash": self.proposition_hash,
            "scope_hash": self.scope_hash,
            "warrant_hashes": list(self.warrant_hashes),
        }


@dataclass(frozen=True)
class WorldClaim:
    proposition_hash: str
    scope_hash: str
    closure_warrant_hash: str
    observation_or_domain_warrant_hash: str

    def __post_init__(self) -> None:
        for name in (
            "proposition_hash", "scope_hash", "closure_warrant_hash",
            "observation_or_domain_warrant_hash",
        ):
            _require_digest(getattr(self, name), f"world_claim.{name}")

    @property
    def claim_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "WORLD_CLAIM",
            "proposition_hash": self.proposition_hash,
            "scope_hash": self.scope_hash,
            "closure_warrant_hash": self.closure_warrant_hash,
            "observation_or_domain_warrant_hash": self.observation_or_domain_warrant_hash,
            "boundary": "warranted-relative; never derived from computation or settlement",
        }


@dataclass(frozen=True)
class EntailmentClaim:
    premise_hashes: tuple[str, ...]
    conclusion_hash: str
    model_class_hash: str
    proof_hash: str

    def __post_init__(self) -> None:
        premises = _unique(self.premise_hashes, "entailment_claim.premise_hashes", nonempty=True)
        for digest in premises:
            _require_digest(digest, "entailment_claim.premise_hash")
        for name in ("conclusion_hash", "model_class_hash", "proof_hash"):
            _require_digest(getattr(self, name), f"entailment_claim.{name}")
        object.__setattr__(self, "premise_hashes", premises)

    @property
    def claim_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "ENTAILMENT_CLAIM", "premise_hashes": list(self.premise_hashes),
            "conclusion_hash": self.conclusion_hash, "model_class_hash": self.model_class_hash,
            "proof_hash": self.proof_hash,
            "boundary": "model-relative entailment; not a WorldClaim constructor",
        }


@dataclass(frozen=True)
class EvidenceClaim:
    proposition_hash: str
    evidence_bundle_hash: str
    evidence_policy_hash: str
    purpose: str
    scope_hash: str
    semantic_epoch: str
    appraisal_authority_token_hash: str
    appraisal_receipt_hash: str

    def __post_init__(self) -> None:
        _require_text(self.purpose, "evidence_claim.purpose")
        for name in (
            "proposition_hash", "evidence_bundle_hash", "evidence_policy_hash", "scope_hash",
            "semantic_epoch", "appraisal_authority_token_hash", "appraisal_receipt_hash",
        ):
            _require_digest(getattr(self, name), f"evidence_claim.{name}")

    @property
    def claim_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "EVIDENCE_CLAIM", "proposition_hash": self.proposition_hash,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "evidence_policy_hash": self.evidence_policy_hash, "purpose": self.purpose,
            "scope_hash": self.scope_hash, "semantic_epoch": self.semantic_epoch,
            "appraisal_authority_token_hash": self.appraisal_authority_token_hash,
            "appraisal_receipt_hash": self.appraisal_receipt_hash,
        }


@dataclass(frozen=True)
class InstitutionalFact:
    case_hash: str
    proposition_hash: str
    evidence_claim_hash: str
    purpose: str
    scope_hash: str
    semantic_epoch: str
    appeal_state: AppealState
    forum_authority_token_hash: str
    finding_receipt_hash: str

    def __post_init__(self) -> None:
        _require_text(self.purpose, "institutional_fact.purpose")
        for name in (
            "case_hash", "proposition_hash", "evidence_claim_hash", "scope_hash",
            "semantic_epoch", "forum_authority_token_hash", "finding_receipt_hash",
        ):
            _require_digest(getattr(self, name), f"institutional_fact.{name}")

    @property
    def claim_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "INSTITUTIONAL_FACT", "case_hash": self.case_hash,
            "proposition_hash": self.proposition_hash,
            "evidence_claim_hash": self.evidence_claim_hash, "purpose": self.purpose,
            "scope_hash": self.scope_hash, "semantic_epoch": self.semantic_epoch,
            "appeal_state": self.appeal_state.value,
            "forum_authority_token_hash": self.forum_authority_token_hash,
            "finding_receipt_hash": self.finding_receipt_hash,
            "boundary": "institutional fact for bound case and purpose; not worldly truth",
        }


@dataclass(frozen=True)
class PrecedentRule:
    institutional_fact_hash: str
    source_case_hash: str
    reason: Formula
    reason_vocabulary: tuple[str, ...]
    effect: PrecedentEffect
    applicability_scope: StructuredScope
    protected_consequence_hashes: tuple[str, ...]
    semantic_epoch: str
    precedential_authority_token_hash: str
    adoption_receipt_hash: str

    def __post_init__(self) -> None:
        for name in (
            "institutional_fact_hash", "source_case_hash", "semantic_epoch",
            "precedential_authority_token_hash", "adoption_receipt_hash",
        ):
            _require_digest(getattr(self, name), f"precedent_rule.{name}")
        validate_formula(
            self.reason,
            signature=self.applicability_scope.signature,
            where="precedent_rule.reason",
        )
        normalized = normalize_formula(self.reason)
        if self.reason != normalized:
            raise InventionError("precedent reason must be canonical FRSL-1")
        vocabulary = tuple(sorted(set(self.reason_vocabulary)))
        if set(vocabulary) != formula_relations(self.reason):
            raise InventionError("precedent reason vocabulary must exactly name reason relations")
        consequences = _unique(
            self.protected_consequence_hashes,
            "precedent_rule.protected_consequence_hashes",
        )
        for value in consequences:
            _require_digest(value, "precedent_rule.protected_consequence_hash")
        object.__setattr__(self, "reason_vocabulary", vocabulary)
        object.__setattr__(self, "protected_consequence_hashes", tuple(sorted(consequences)))

    @property
    def rule_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "PRECEDENT_RULE", "institutional_fact_hash": self.institutional_fact_hash,
            "source_case_hash": self.source_case_hash, "reason": self.reason,
            "reason_vocabulary": list(self.reason_vocabulary), "effect": self.effect.value,
            "applicability_scope": self.applicability_scope.to_dict(),
            "protected_consequence_hashes": list(self.protected_consequence_hashes),
            "semantic_epoch": self.semantic_epoch,
            "precedential_authority_token_hash": self.precedential_authority_token_hash,
            "adoption_receipt_hash": self.adoption_receipt_hash,
        }


@dataclass(frozen=True)
class PrecedentAdoption:
    status: AdoptionStatus
    institutional_fact_hash: str
    proposed_rule_hash: str
    semantic_epoch: str
    rule: PrecedentRule | None
    prior_rule_hash: str | None
    reason_check: str
    applicability_check: str
    conservativity_check: str
    refusal_preservation_check: str
    legislation_causes: tuple[str, ...] = ()
    countermodel: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("institutional_fact_hash", "proposed_rule_hash", "semantic_epoch"):
            _require_digest(getattr(self, name), f"precedent_adoption.{name}")
        if self.prior_rule_hash is not None:
            _require_digest(self.prior_rule_hash, "precedent_adoption.prior_rule_hash")
        causes = _unique(self.legislation_causes, "precedent_adoption.legislation_causes")
        if self.status is AdoptionStatus.ADOPTED:
            if self.rule is None or causes:
                raise InventionError("adopted precedent requires a rule and no legislation causes")
            if self.rule.rule_hash != self.proposed_rule_hash:
                raise InventionError("adoption does not bind its proposed rule")
        elif self.rule is not None:
            raise InventionError("non-adopted precedent cannot expose an operative rule")
        object.__setattr__(self, "legislation_causes", causes)
        object.__setattr__(self, "countermodel", dict(self.countermodel) if self.countermodel else None)

    @property
    def adoption_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "profile": PROFILE,
            "status": self.status.value, "institutional_fact_hash": self.institutional_fact_hash,
            "proposed_rule_hash": self.proposed_rule_hash, "semantic_epoch": self.semantic_epoch,
            "rule": self.rule.to_dict() if self.rule else None,
            "prior_rule_hash": self.prior_rule_hash, "reason_check": self.reason_check,
            "applicability_check": self.applicability_check,
            "conservativity_check": self.conservativity_check,
            "refusal_preservation_check": self.refusal_preservation_check,
            "legislation_causes": list(self.legislation_causes),
            "countermodel": dict(self.countermodel) if self.countermodel else None,
        }


@dataclass(frozen=True)
class SettlementClaim:
    action_hash: str
    premise_claim_hashes: tuple[str, ...]
    consequence: str
    scope_hash: str
    semantic_epoch: str
    settlement_authority_token_hash: str
    settlement_receipt_hash: str

    def __post_init__(self) -> None:
        _require_text(self.consequence, "settlement_claim.consequence")
        premises = _unique(self.premise_claim_hashes, "settlement_claim.premise_claim_hashes", nonempty=True)
        for value in premises:
            _require_digest(value, "settlement_claim.premise_claim_hash")
        for name in (
            "action_hash", "scope_hash", "semantic_epoch",
            "settlement_authority_token_hash", "settlement_receipt_hash",
        ):
            _require_digest(getattr(self, name), f"settlement_claim.{name}")
        object.__setattr__(self, "premise_claim_hashes", premises)

    @property
    def claim_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "SETTLEMENT_CLAIM", "action_hash": self.action_hash,
            "premise_claim_hashes": list(self.premise_claim_hashes),
            "consequence": self.consequence, "scope_hash": self.scope_hash,
            "semantic_epoch": self.semantic_epoch,
            "settlement_authority_token_hash": self.settlement_authority_token_hash,
            "settlement_receipt_hash": self.settlement_receipt_hash,
            "boundary": "permission to impose consequence; not evidence or worldly truth",
        }


@dataclass(frozen=True)
class HistoricalDecision:
    decision_hash: str
    case_hash: str
    semantic_epoch: str
    status: str

    def __post_init__(self) -> None:
        _require_text(self.status, "historical_decision.status")
        for name in ("decision_hash", "case_hash", "semantic_epoch"):
            _require_digest(getattr(self, name), f"historical_decision.{name}")

    def append_review(self, *, new_epoch: str, review_status: str, receipt_hash: str) -> "DecisionReview":
        return DecisionReview(self.decision_hash, new_epoch, review_status, receipt_hash)


@dataclass(frozen=True)
class DecisionReview:
    historical_decision_hash: str
    review_epoch: str
    status: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _require_text(self.status, "decision_review.status")
        for name in ("historical_decision_hash", "review_epoch", "receipt_hash"):
            _require_digest(getattr(self, name), f"decision_review.{name}")


@dataclass(frozen=True)
class ClaimFlowEdge:
    operation: ClaimPermission
    input_hashes: tuple[str, ...]
    output_hash: str
    authority_token_hash: str
    receipt_hash: str

    def __post_init__(self) -> None:
        inputs = _unique(self.input_hashes, "claim_flow_edge.input_hashes", nonempty=True)
        for value in (*inputs, self.output_hash, self.authority_token_hash, self.receipt_hash):
            _require_digest(value, "claim_flow_edge.hash")
        object.__setattr__(self, "input_hashes", inputs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value, "input_hashes": list(self.input_hashes),
            "output_hash": self.output_hash, "authority_token_hash": self.authority_token_hash,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True)
class ClaimFlowTrace:
    action_hash: str
    semantic_epoch: str
    node_hashes: tuple[str, ...] = ()
    edges: tuple[ClaimFlowEdge, ...] = ()

    def __post_init__(self) -> None:
        _require_digest(self.action_hash, "claim_flow_trace.action_hash")
        _require_digest(self.semantic_epoch, "claim_flow_trace.semantic_epoch")
        nodes = _unique(self.node_hashes, "claim_flow_trace.node_hashes")
        outputs = [edge.output_hash for edge in self.edges]
        if len(outputs) != len(set(outputs)):
            raise InventionError("claim-flow trace contains duplicate outputs")
        known: set[str] = set(nodes) - set(outputs)
        for edge in self.edges:
            if not set(edge.input_hashes) <= known:
                raise InventionError("claim-flow edge is cyclic or precedes one of its premises")
            if edge.output_hash in known:
                raise InventionError("claim-flow edge overwrites an earlier node")
            known.add(edge.output_hash)
        if known != set(nodes):
            raise InventionError("claim-flow trace omits an edge output node")
        object.__setattr__(self, "node_hashes", nodes)
        object.__setattr__(self, "edges", tuple(self.edges))

    @property
    def trace_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def append(self, edge: ClaimFlowEdge) -> "ClaimFlowTrace":
        if not set(edge.input_hashes) <= set(self.node_hashes):
            raise InventionError("trace append requires every premise to be present")
        if edge.output_hash in self.node_hashes:
            raise InventionError("trace append cannot overwrite an existing claim")
        return ClaimFlowTrace(
            action_hash=self.action_hash,
            semantic_epoch=self.semantic_epoch,
            node_hashes=self.node_hashes + (edge.output_hash,),
            edges=self.edges + (edge,),
        )

    def concatenate(self, other: "ClaimFlowTrace") -> "ClaimFlowTrace":
        if self.action_hash != other.action_hash or self.semantic_epoch != other.semantic_epoch:
            raise InventionError("claim-flow traces concatenate only within one action and epoch")
        result = self
        for edge in other.edges:
            if edge.output_hash in result.node_hashes:
                continue
            result = result.append(edge)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "profile": PROFILE,
            "action_hash": self.action_hash, "semantic_epoch": self.semantic_epoch,
            "node_hashes": list(self.node_hashes),
            "edges": [edge.to_dict() for edge in self.edges],
            "scope": "one-action derivation DAG; not a global semantic graph",
        }


def _edge(
    operation: ClaimPermission,
    inputs: Sequence[str],
    output: str,
    token: AuthorityToken,
    receipt_hash: str,
) -> ClaimFlowEdge:
    _require_digest(receipt_hash, "claim_flow.receipt_hash")
    if token.authorization_receipt_hash != receipt_hash:
        raise InventionError("constructor receipt does not match authority-token receipt")
    return ClaimFlowEdge(operation, tuple(inputs), output, token.token_hash, receipt_hash)


def appraise(
    bundle: EvidenceBundle,
    *,
    evidence_policy_hash: str,
    purpose: str,
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
) -> tuple[EvidenceClaim, ClaimFlowEdge]:
    authority.require(
        token, ClaimPermission.APPRAISE,
        semantic_epoch=token.semantic_epoch, scope_hash=bundle.scope_hash,
    )
    claim = EvidenceClaim(
        proposition_hash=bundle.proposition_hash,
        evidence_bundle_hash=bundle.bundle_hash,
        evidence_policy_hash=_require_digest(evidence_policy_hash, "appraise.evidence_policy_hash"),
        purpose=_require_text(purpose, "appraise.purpose"), scope_hash=bundle.scope_hash,
        semantic_epoch=token.semantic_epoch,
        appraisal_authority_token_hash=token.token_hash,
        appraisal_receipt_hash=token.authorization_receipt_hash,
    )
    return claim, _edge(
        ClaimPermission.APPRAISE, (bundle.bundle_hash,), claim.claim_hash,
        token, token.authorization_receipt_hash,
    )


def forum_finding(
    evidence: EvidenceClaim,
    *,
    case_hash: str,
    appeal_state: AppealState,
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
) -> tuple[InstitutionalFact, ClaimFlowEdge]:
    authority.require(
        token, ClaimPermission.FORUM_FINDING,
        semantic_epoch=evidence.semantic_epoch, scope_hash=evidence.scope_hash,
    )
    fact = InstitutionalFact(
        case_hash=_require_digest(case_hash, "forum_finding.case_hash"),
        proposition_hash=evidence.proposition_hash, evidence_claim_hash=evidence.claim_hash,
        purpose=evidence.purpose, scope_hash=evidence.scope_hash,
        semantic_epoch=evidence.semantic_epoch, appeal_state=appeal_state,
        forum_authority_token_hash=token.token_hash,
        finding_receipt_hash=token.authorization_receipt_hash,
    )
    return fact, _edge(
        ClaimPermission.FORUM_FINDING, (evidence.claim_hash,), fact.claim_hash,
        token, token.authorization_receipt_hash,
    )


def adopt_precedent(
    fact: InstitutionalFact,
    *,
    reason: Formula,
    effect: PrecedentEffect,
    applicability_scope: StructuredScope,
    protected_consequence_hashes: Sequence[str],
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
    prior_rule: PrecedentRule | None = None,
    conservativity_verified: bool,
    refusals_preserved: bool,
) -> tuple[PrecedentAdoption, ClaimFlowEdge | None]:
    authority.require(
        token, ClaimPermission.ADOPT_PRECEDENT,
        semantic_epoch=fact.semantic_epoch, scope_hash=applicability_scope.scope_hash,
    )
    reason = normalize_formula(reason)
    validate_formula(reason, signature=applicability_scope.signature, where="adopt_precedent.reason")
    consequences = tuple(sorted(set(protected_consequence_hashes)))
    for digest in consequences:
        _require_digest(digest, "adopt_precedent.protected_consequence_hash")
    proposed = PrecedentRule(
        institutional_fact_hash=fact.claim_hash, source_case_hash=fact.case_hash,
        reason=reason, reason_vocabulary=tuple(sorted(formula_relations(reason))), effect=effect,
        applicability_scope=applicability_scope,
        protected_consequence_hashes=consequences, semantic_epoch=fact.semantic_epoch,
        precedential_authority_token_hash=token.token_hash,
        adoption_receipt_hash=token.authorization_receipt_hash,
    )
    causes: list[str] = []
    countermodel: Mapping[str, Any] | None = None
    reason_check = "PASS"
    applicability_check = "PASS"
    if fact.appeal_state is AppealState.OPEN:
        causes.append("NONFINAL_FORUM_FINDING")
    if effect is PrecedentEffect.CASE_ONLY and applicability_scope.scope_hash != fact.scope_hash:
        causes.append("CASE_ONLY_SCOPE_MISMATCH")
    if not conservativity_verified:
        causes.append("NEW_PROTECTED_CONSEQUENCE")
    if not refusals_preserved:
        causes.append("REFUSAL_ERASURE")
    if prior_rule is not None:
        if prior_rule.semantic_epoch != fact.semantic_epoch:
            causes.append("SUPERSESSION_REQUIRED")
        if prior_rule.reason != proposed.reason:
            causes.append("FRESH_REASON")
            reason_check = "LEGISLATION_REQUIRED"
        order = scope_leq(proposed.applicability_scope, prior_rule.applicability_scope)
        if order.status is ScopeOrderStatus.NOT_LEQ:
            causes.append("WIDENED_APPLICABILITY")
            applicability_check = "LEGISLATION_REQUIRED"
            countermodel = order.countermodel
        elif order.status is not ScopeOrderStatus.LEQ:
            causes.append("SCOPE_ORDER_INDETERMINATE")
            applicability_check = "ROUTE"
        if not set(proposed.protected_consequence_hashes) <= set(
            prior_rule.protected_consequence_hashes
        ):
            causes.append("NEW_PROTECTED_CONSEQUENCE")
    status = AdoptionStatus.ADOPTED
    if "SUPERSESSION_REQUIRED" in causes:
        status = AdoptionStatus.STALE
    elif any(cause in causes for cause in (
        "FRESH_REASON", "WIDENED_APPLICABILITY", "NEW_PROTECTED_CONSEQUENCE",
    )):
        status = AdoptionStatus.LEGISLATION_REQUIRED
    elif causes:
        status = AdoptionStatus.ROUTE
    adoption = PrecedentAdoption(
        status=status, institutional_fact_hash=fact.claim_hash,
        proposed_rule_hash=proposed.rule_hash, semantic_epoch=fact.semantic_epoch,
        rule=proposed if status is AdoptionStatus.ADOPTED else None,
        prior_rule_hash=prior_rule.rule_hash if prior_rule else None,
        reason_check=reason_check, applicability_check=applicability_check,
        conservativity_check="PASS" if conservativity_verified else "FAIL",
        refusal_preservation_check="PASS" if refusals_preserved else "FAIL",
        legislation_causes=tuple(dict.fromkeys(causes)), countermodel=countermodel,
    )
    edge = None
    if adoption.status is AdoptionStatus.ADOPTED:
        edge = _edge(
            ClaimPermission.ADOPT_PRECEDENT, (fact.claim_hash,), proposed.rule_hash,
            token, token.authorization_receipt_hash,
        )
    return adoption, edge


def precedent_applies(
    rule: PrecedentRule,
    *,
    case_hash: str,
    case_scope: StructuredScope,
    semantic_epoch: str,
) -> bool:
    if rule.semantic_epoch != semantic_epoch:
        return False
    if rule.effect is PrecedentEffect.PERSUASIVE:
        return False
    if rule.effect is PrecedentEffect.CASE_ONLY:
        return case_hash == rule.source_case_hash
    return scope_leq(case_scope, rule.applicability_scope).status is ScopeOrderStatus.LEQ


def settle(
    premise: EvidenceClaim | InstitutionalFact,
    *,
    action_hash: str,
    consequence: str,
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
) -> tuple[SettlementClaim, ClaimFlowEdge]:
    authority.require(
        token, ClaimPermission.SETTLE,
        semantic_epoch=premise.semantic_epoch, scope_hash=premise.scope_hash,
    )
    claim = SettlementClaim(
        action_hash=_require_digest(action_hash, "settle.action_hash"),
        premise_claim_hashes=(premise.claim_hash,), consequence=consequence,
        scope_hash=premise.scope_hash, semantic_epoch=premise.semantic_epoch,
        settlement_authority_token_hash=token.token_hash,
        settlement_receipt_hash=token.authorization_receipt_hash,
    )
    return claim, _edge(
        ClaimPermission.SETTLE, (premise.claim_hash,), claim.claim_hash,
        token, token.authorization_receipt_hash,
    )


def verify_claim_flow_trace(trace: ClaimFlowTrace, authority: ClaimFlowAuthority) -> bool:
    """Replay authority provenance and topological order for a closed trace."""
    tokens = {
        token.token_hash: token
        for group in (
            authority.appraisal_grants, authority.adjudication_grants,
            authority.precedential_grants, authority.settlement_grants,
        )
        for token in group
    }
    known = set(trace.node_hashes) - {edge.output_hash for edge in trace.edges}
    for edge in trace.edges:
        token = tokens.get(edge.authority_token_hash)
        if token is None or token.permission is not edge.operation:
            return False
        if token.semantic_epoch != trace.semantic_epoch:
            return False
        if token.authorization_receipt_hash != edge.receipt_hash:
            return False
        if not set(edge.input_hashes) <= known or edge.output_hash in known:
            return False
        known.add(edge.output_hash)
    return known == set(trace.node_hashes)


@dataclass(frozen=True)
class DerivationBudgetPolicy:
    policy_id: str
    semantic_epoch: str
    max_models: int
    max_candidate_atoms: int
    max_formula_nodes: int
    max_branch_nodes: int
    max_opposing_pairs: int
    permitted_backend_hashes: tuple[str, ...]
    challenge_escalation: str
    authorization_receipt_hash: str
    authorization_sequence: int

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "derivation_budget.policy_id")
        _require_text(self.challenge_escalation, "derivation_budget.challenge_escalation")
        _require_digest(self.semantic_epoch, "derivation_budget.semantic_epoch")
        _require_digest(self.authorization_receipt_hash, "derivation_budget.authorization_receipt_hash")
        for name in (
            "max_models", "max_candidate_atoms", "max_formula_nodes",
            "max_branch_nodes", "max_opposing_pairs",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InventionError(f"derivation_budget.{name} must be a positive integer")
        if not isinstance(self.authorization_sequence, int) or self.authorization_sequence < 0:
            raise InventionError("derivation budget authorization sequence must be non-negative")
        backends = _unique(
            self.permitted_backend_hashes,
            "derivation_budget.permitted_backend_hashes",
            nonempty=True,
        )
        for digest in backends:
            _require_digest(digest, "derivation_budget.permitted_backend_hash")
        object.__setattr__(self, "permitted_backend_hashes", tuple(sorted(backends)))

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "policy_id": self.policy_id,
            "semantic_epoch": self.semantic_epoch, "max_models": self.max_models,
            "max_candidate_atoms": self.max_candidate_atoms,
            "max_formula_nodes": self.max_formula_nodes,
            "max_branch_nodes": self.max_branch_nodes,
            "max_opposing_pairs": self.max_opposing_pairs,
            "permitted_backend_hashes": list(self.permitted_backend_hashes),
            "challenge_escalation": self.challenge_escalation,
            "authorization_receipt_hash": self.authorization_receipt_hash,
            "authorization_sequence": self.authorization_sequence,
            "boundary": "logical bounds; wall time and memory are observations only",
        }

    @classmethod
    def from_dict(cls, value: Any) -> "DerivationBudgetPolicy":
        required = {
            "schema_version", "policy_id", "semantic_epoch", "max_models",
            "max_candidate_atoms", "max_formula_nodes", "max_branch_nodes",
            "max_opposing_pairs", "permitted_backend_hashes", "challenge_escalation",
            "authorization_receipt_hash", "authorization_sequence", "boundary",
        }
        d = _closed(value, required, "derivation_budget_policy")
        if d["schema_version"] != SCHEMA_VERSION:
            raise InventionError("unsupported derivation budget schema")
        return cls(
            policy_id=d["policy_id"], semantic_epoch=d["semantic_epoch"],
            max_models=d["max_models"], max_candidate_atoms=d["max_candidate_atoms"],
            max_formula_nodes=d["max_formula_nodes"], max_branch_nodes=d["max_branch_nodes"],
            max_opposing_pairs=d["max_opposing_pairs"],
            permitted_backend_hashes=tuple(d["permitted_backend_hashes"]),
            challenge_escalation=d["challenge_escalation"],
            authorization_receipt_hash=d["authorization_receipt_hash"],
            authorization_sequence=d["authorization_sequence"],
        )


@dataclass(frozen=True)
class LogicalCounters:
    models: int = 0
    candidate_atoms: int = 0
    formula_nodes: int = 0
    branch_nodes: int = 0
    opposing_pairs: int = 0

    def __post_init__(self) -> None:
        for name in ("models", "candidate_atoms", "formula_nodes", "branch_nodes", "opposing_pairs"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"logical_counter.{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "models": self.models, "candidate_atoms": self.candidate_atoms,
            "formula_nodes": self.formula_nodes, "branch_nodes": self.branch_nodes,
            "opposing_pairs": self.opposing_pairs,
        }


@dataclass(frozen=True)
class DerivationRunReceipt:
    problem_hash: str
    policy_hash: str
    semantic_epoch: str
    backend_hash: str
    backend_version_hash: str
    run_sequence: int
    disposition: DerivationDisposition
    logical_counters: LogicalCounters
    completed_region_hashes: tuple[str, ...]
    search_frontier_hash: str
    output_hash: str
    observed_wall_millis: int
    observed_peak_memory_bytes: int

    def __post_init__(self) -> None:
        for name in (
            "problem_hash", "policy_hash", "semantic_epoch", "backend_hash",
            "backend_version_hash", "search_frontier_hash", "output_hash",
        ):
            _require_digest(getattr(self, name), f"derivation_run.{name}")
        regions = _unique(self.completed_region_hashes, "derivation_run.completed_region_hashes")
        for digest in regions:
            _require_digest(digest, "derivation_run.completed_region_hash")
        for name in ("run_sequence", "observed_wall_millis", "observed_peak_memory_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"derivation_run.{name} must be a non-negative integer")
        object.__setattr__(self, "completed_region_hashes", regions)

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "problem_hash": self.problem_hash,
            "policy_hash": self.policy_hash, "semantic_epoch": self.semantic_epoch,
            "backend_hash": self.backend_hash, "backend_version_hash": self.backend_version_hash,
            "run_sequence": self.run_sequence, "disposition": self.disposition.value,
            "logical_counters": self.logical_counters.to_dict(),
            "completed_region_hashes": list(self.completed_region_hashes),
            "search_frontier_hash": self.search_frontier_hash, "output_hash": self.output_hash,
            "observed_wall_millis": self.observed_wall_millis,
            "observed_peak_memory_bytes": self.observed_peak_memory_bytes,
            "observational_only": ["observed_wall_millis", "observed_peak_memory_bytes"],
        }


def verify_derivation_run(
    policy: DerivationBudgetPolicy,
    receipt: DerivationRunReceipt,
    *,
    replayed_frontier_hash: str,
) -> BudgetAuthorizationStatus:
    if (
        receipt.policy_hash != policy.policy_hash
        or receipt.semantic_epoch != policy.semantic_epoch
        or receipt.run_sequence <= policy.authorization_sequence
        or receipt.backend_hash not in policy.permitted_backend_hashes
    ):
        return BudgetAuthorizationStatus.UNAUTHORIZED_DERIVATION_BUDGET
    if receipt.search_frontier_hash != replayed_frontier_hash:
        return BudgetAuthorizationStatus.FRONTIER_INVALID
    limits = (
        (receipt.logical_counters.models, policy.max_models),
        (receipt.logical_counters.candidate_atoms, policy.max_candidate_atoms),
        (receipt.logical_counters.formula_nodes, policy.max_formula_nodes),
        (receipt.logical_counters.branch_nodes, policy.max_branch_nodes),
        (receipt.logical_counters.opposing_pairs, policy.max_opposing_pairs),
    )
    if any(value > limit for value, limit in limits):
        return BudgetAuthorizationStatus.FRONTIER_INVALID
    return BudgetAuthorizationStatus.AUTHORIZED


@dataclass(frozen=True)
class FinalityCondition:
    condition_id: str
    blocker_kind: BlockerKind
    required_transition: str
    proof_reference: str
    burden: Mapping[str, int]
    reserve_delta_microunits: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.condition_id, "finality_condition.condition_id")
        _require_text(self.required_transition, "finality_condition.required_transition")
        _require_text(self.proof_reference, "finality_condition.proof_reference")
        burden = dict(self.burden)
        if any(
            not isinstance(key, str) or not key or not isinstance(value, int)
            or isinstance(value, bool) or value < 0
            for key, value in burden.items()
        ):
            raise InventionError("finality condition burden must have non-negative integer coordinates")
        if self.reserve_delta_microunits is not None and (
            not isinstance(self.reserve_delta_microunits, int)
            or isinstance(self.reserve_delta_microunits, bool)
        ):
            raise InventionError("reserve delta must be an integer or NOT_APPLICABLE")
        if self.blocker_kind is BlockerKind.HARM and self.required_transition in {
            "HUMAN_REVIEW_REQUIRED", "CATEGORICAL_REFUSE", "REVERSIBLE_ONLY",
        } and self.reserve_delta_microunits is not None:
            raise InventionError("non-compensable harm route has reserve_delta=NOT_APPLICABLE")
        object.__setattr__(self, "burden", burden)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id, "blocker_kind": self.blocker_kind.value,
            "required_transition": self.required_transition,
            "proof_reference": self.proof_reference, "burden": dict(self.burden),
            "reserve_delta": (
                self.reserve_delta_microunits
                if self.reserve_delta_microunits is not None else "NOT_APPLICABLE"
            ),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FinalityCondition":
        d = _closed(
            value,
            {"condition_id", "blocker_kind", "required_transition", "proof_reference", "burden", "reserve_delta"},
            "finality_condition",
        )
        reserve = d["reserve_delta"]
        if reserve == "NOT_APPLICABLE":
            reserve = None
        return cls(
            d["condition_id"], BlockerKind(d["blocker_kind"]),
            d["required_transition"], d["proof_reference"], d["burden"], reserve,
        )


@dataclass(frozen=True)
class FinalityAlternative:
    route_id: str
    required_condition_ids: tuple[str, ...]
    expected_finality_state: str

    def __post_init__(self) -> None:
        _require_text(self.route_id, "finality_alternative.route_id")
        _require_text(self.expected_finality_state, "finality_alternative.expected_finality_state")
        object.__setattr__(self, "required_condition_ids", _unique(
            self.required_condition_ids, "finality_alternative.required_condition_ids", nonempty=True,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "required_condition_ids": list(self.required_condition_ids),
            "expected_finality_state": self.expected_finality_state,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FinalityAlternative":
        d = _closed(value, {"route_id", "required_condition_ids", "expected_finality_state"}, "finality_alternative")
        return cls(d["route_id"], tuple(d["required_condition_ids"]), d["expected_finality_state"])


@dataclass(frozen=True)
class FinalityProblem:
    current_claim_state_hash: str
    finality_policy_hash: str
    semantic_epoch: str
    closure_warrant_hash: str
    authority_regime_hash: str
    conditions: tuple[FinalityCondition, ...]
    satisfied_condition_ids: tuple[str, ...]
    alternatives: tuple[FinalityAlternative, ...]

    def __post_init__(self) -> None:
        for name in (
            "current_claim_state_hash", "finality_policy_hash", "semantic_epoch",
            "closure_warrant_hash", "authority_regime_hash",
        ):
            _require_digest(getattr(self, name), f"finality_problem.{name}")
        conditions = tuple(self.conditions)
        ids = [item.condition_id for item in conditions]
        if not conditions or len(ids) != len(set(ids)):
            raise InventionError("finality conditions must be non-empty and uniquely named")
        satisfied = _unique(self.satisfied_condition_ids, "finality_problem.satisfied_condition_ids")
        if not set(satisfied) <= set(ids):
            raise InventionError("satisfied finality condition is not declared")
        alternatives = tuple(self.alternatives)
        if not alternatives or len({item.route_id for item in alternatives}) != len(alternatives):
            raise InventionError("finality alternatives must be non-empty and uniquely named")
        if any(not set(item.required_condition_ids) <= set(ids) for item in alternatives):
            raise InventionError("finality alternative names an undeclared condition")
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "satisfied_condition_ids", satisfied)
        object.__setattr__(self, "alternatives", alternatives)

    @property
    def problem_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "current_claim_state_hash": self.current_claim_state_hash,
            "finality_policy_hash": self.finality_policy_hash,
            "semantic_epoch": self.semantic_epoch,
            "closure_warrant_hash": self.closure_warrant_hash,
            "authority_regime_hash": self.authority_regime_hash,
            "conditions": [item.to_dict() for item in self.conditions],
            "satisfied_condition_ids": list(self.satisfied_condition_ids),
            "alternatives": [item.to_dict() for item in self.alternatives],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FinalityProblem":
        d = _closed(
            value,
            {
                "schema_version", "current_claim_state_hash", "finality_policy_hash",
                "semantic_epoch", "closure_warrant_hash", "authority_regime_hash",
                "conditions", "satisfied_condition_ids", "alternatives",
            },
            "finality_problem",
        )
        if d["schema_version"] != SCHEMA_VERSION:
            raise InventionError("unsupported finality problem schema")
        return cls(
            current_claim_state_hash=d["current_claim_state_hash"],
            finality_policy_hash=d["finality_policy_hash"], semantic_epoch=d["semantic_epoch"],
            closure_warrant_hash=d["closure_warrant_hash"],
            authority_regime_hash=d["authority_regime_hash"],
            conditions=tuple(FinalityCondition.from_dict(item) for item in d["conditions"]),
            satisfied_condition_ids=tuple(d["satisfied_condition_ids"]),
            alternatives=tuple(FinalityAlternative.from_dict(item) for item in d["alternatives"]),
        )


@dataclass(frozen=True)
class FinalityObstructionCertificate:
    current_claim_state_hash: str
    finality_policy_hash: str
    minimal_blocker_ids: tuple[str, ...]
    semantic_epoch: str
    closure_warrant_hash: str
    authority_regime_hash: str
    minimality_status: MinimalityStatus
    checker: Mapping[str, str]
    examined_alternatives: int
    search_frontier_hash: str

    @property
    def certificate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_claim_state_hash": self.current_claim_state_hash,
            "finality_policy_hash": self.finality_policy_hash,
            "minimal_blocker_ids": list(self.minimal_blocker_ids),
            "semantic_epoch": self.semantic_epoch,
            "closure_warrant_hash": self.closure_warrant_hash,
            "authority_regime_hash": self.authority_regime_hash,
            "minimality_status": self.minimality_status.value,
            "checker": dict(self.checker), "examined_alternatives": self.examined_alternatives,
            "search_frontier_hash": self.search_frontier_hash,
        }


@dataclass(frozen=True)
class FinalityRoute:
    route_id: str
    required_claim_transitions: tuple[str, ...]
    expected_finality_state: str
    burden: Mapping[str, int]
    reserve_delta_microunits: int | None
    proof_references: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    minimality_status: MinimalityStatus

    @property
    def route_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "required_claim_transitions": list(self.required_claim_transitions),
            "expected_finality_state": self.expected_finality_state,
            "burden": dict(self.burden),
            "reserve_delta": (
                self.reserve_delta_microunits
                if self.reserve_delta_microunits is not None else "NOT_APPLICABLE"
            ),
            "proof_references": list(self.proof_references),
            "blocker_ids": list(self.blocker_ids),
            "minimality_status": self.minimality_status.value,
        }


@dataclass(frozen=True)
class FinalityExplanation:
    status: FinalityExplanationStatus
    problem_hash: str
    certificate: FinalityObstructionCertificate | None
    routes: tuple[FinalityRoute, ...]
    budget_receipt: DerivationRunReceipt
    cause: str

    @property
    def explanation_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "profile": PROFILE,
            "status": self.status.value, "problem_hash": self.problem_hash,
            "certificate": self.certificate.to_dict() if self.certificate else None,
            "routes": [route.to_dict() for route in self.routes],
            "budget_receipt": self.budget_receipt.to_dict(), "cause": self.cause,
        }


@dataclass(frozen=True)
class SMTFinalityArtifact:
    status: AcceleratorStatus
    input_smt2: str
    stdout: str
    stderr: str
    returncode: int | None
    solver_version: str
    jar_sha256: str
    candidate_route_id: str | None
    candidate_independently_sufficient: bool
    reason: str

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value, "input_smt2": self.input_smt2,
            "stdout": self.stdout, "stderr": self.stderr, "returncode": self.returncode,
            "solver_version": self.solver_version, "jar_sha256": self.jar_sha256,
            "candidate_route_id": self.candidate_route_id,
            "candidate_independently_sufficient": self.candidate_independently_sufficient,
            "reason": self.reason,
            "trust_boundary": "untrusted route candidate; reference explanation is authoritative",
        }


def _route(problem: FinalityProblem, alternative: FinalityAlternative, missing: tuple[str, ...], minimality: MinimalityStatus) -> FinalityRoute:
    by_id = {item.condition_id: item for item in problem.conditions}
    conditions = [by_id[item] for item in missing]
    burden: dict[str, int] = {}
    for condition in conditions:
        for coordinate, amount in condition.burden.items():
            burden[coordinate] = burden.get(coordinate, 0) + amount
    reserve_deltas = [item.reserve_delta_microunits for item in conditions]
    reserve = sum(item for item in reserve_deltas if item is not None)
    if any(item is None for item in reserve_deltas):
        reserve = None
    return FinalityRoute(
        route_id=alternative.route_id,
        required_claim_transitions=tuple(item.required_transition for item in conditions),
        expected_finality_state=alternative.expected_finality_state,
        burden=burden, reserve_delta_microunits=reserve,
        proof_references=tuple(item.proof_reference for item in conditions),
        blocker_ids=missing, minimality_status=minimality,
    )


def explain_finality(
    problem: FinalityProblem,
    *,
    budget: DerivationBudgetPolicy,
    backend_hash: str,
    backend_version_hash: str,
    run_sequence: int,
    observed_wall_millis: int = 0,
    observed_peak_memory_bytes: int = 0,
) -> FinalityExplanation:
    """Exhaustively enumerate finite policy routes within a precommitted budget."""
    _require_digest(backend_hash, "explain_finality.backend_hash")
    _require_digest(backend_version_hash, "explain_finality.backend_version_hash")
    if budget.semantic_epoch != problem.semantic_epoch:
        disposition = DerivationDisposition.INVALID
        examined = 0
        missing_sets: list[tuple[FinalityAlternative, tuple[str, ...]]] = []
        cause = "UNAUTHORIZED_DERIVATION_BUDGET"
    else:
        missing_sets = []
        examined = 0
        satisfied = set(problem.satisfied_condition_ids)
        for alternative in sorted(problem.alternatives, key=lambda item: item.route_id):
            if examined >= budget.max_branch_nodes:
                break
            examined += 1
            missing = tuple(sorted(set(alternative.required_condition_ids) - satisfied))
            missing_sets.append((alternative, missing))
        complete = examined == len(problem.alternatives)
        if any(not missing for _, missing in missing_sets):
            disposition = DerivationDisposition.CERTIFIED if complete else DerivationDisposition.PARTIAL
            cause = "FINALITY_CONDITION_SATISFIED"
        elif complete:
            disposition = DerivationDisposition.CERTIFIED
            cause = "MINIMAL_FINALITY_OBSTRUCTION"
        else:
            disposition = DerivationDisposition.RESOURCE_BOUNDED
            cause = "LOGICAL_BRANCH_BUDGET_EXHAUSTED"
    frontier = {
        "problem_hash": problem.problem_hash,
        "examined_route_ids": [item.route_id for item, _ in missing_sets],
        "unexamined_route_ids": [
            item.route_id for item in sorted(problem.alternatives, key=lambda item: item.route_id)[examined:]
        ],
        "missing": {item.route_id: list(missing) for item, missing in missing_sets},
    }
    frontier_hash = canonical_hash(frontier)
    complete = examined == len(problem.alternatives)
    if complete:
        nondominated: list[tuple[FinalityAlternative, tuple[str, ...]]] = []
        for candidate in missing_sets:
            candidate_set = set(candidate[1])
            if any(set(other[1]) < candidate_set for other in missing_sets):
                continue
            nondominated.append(candidate)
    else:
        nondominated = missing_sets[:1]
    minimality = MinimalityStatus.EXACT if complete else MinimalityStatus.UNRESOLVED
    routes = tuple(_route(problem, alt, missing, minimality) for alt, missing in nondominated)
    output_preview = {
        "status": disposition.value,
        "routes": [route.to_dict() for route in routes],
        "frontier_hash": frontier_hash,
    }
    receipt = DerivationRunReceipt(
        problem_hash=problem.problem_hash, policy_hash=budget.policy_hash,
        semantic_epoch=problem.semantic_epoch, backend_hash=backend_hash,
        backend_version_hash=backend_version_hash, run_sequence=run_sequence,
        disposition=disposition,
        logical_counters=LogicalCounters(branch_nodes=examined),
        completed_region_hashes=tuple(canonical_hash({"route": alt.route_id, "missing": missing}) for alt, missing in missing_sets),
        search_frontier_hash=frontier_hash, output_hash=canonical_hash(output_preview),
        observed_wall_millis=observed_wall_millis,
        observed_peak_memory_bytes=observed_peak_memory_bytes,
    )
    authorization = verify_derivation_run(budget, receipt, replayed_frontier_hash=frontier_hash)
    if authorization is not BudgetAuthorizationStatus.AUTHORIZED:
        status = FinalityExplanationStatus.INVALID
        cause = authorization.value
        routes = ()
        certificate = None
    elif any(not missing for _, missing in missing_sets):
        status = FinalityExplanationStatus.FINAL
        routes = tuple(route for route in routes if not route.blocker_ids)
        certificate = None
    else:
        blocker_ids = routes[0].blocker_ids if routes else ()
        certificate = FinalityObstructionCertificate(
            current_claim_state_hash=problem.current_claim_state_hash,
            finality_policy_hash=problem.finality_policy_hash,
            minimal_blocker_ids=blocker_ids, semantic_epoch=problem.semantic_epoch,
            closure_warrant_hash=problem.closure_warrant_hash,
            authority_regime_hash=problem.authority_regime_hash,
            minimality_status=minimality, checker=REFERENCE_VERIFIER,
            examined_alternatives=examined, search_frontier_hash=frontier_hash,
        )
        if not complete:
            status = FinalityExplanationStatus.RESOURCE_BOUNDED
        elif len(routes) > 1:
            status = FinalityExplanationStatus.CHOICE_REQUIRED
        else:
            status = FinalityExplanationStatus.ROUTE
    return FinalityExplanation(status, problem.problem_hash, certificate, routes, receipt, cause)


def verify_finality_explanation(
    problem: FinalityProblem,
    budget: DerivationBudgetPolicy,
    explanation: FinalityExplanation,
) -> bool:
    if explanation.problem_hash != problem.problem_hash:
        return False
    replay = explain_finality(
        problem, budget=budget,
        backend_hash=explanation.budget_receipt.backend_hash,
        backend_version_hash=explanation.budget_receipt.backend_version_hash,
        run_sequence=explanation.budget_receipt.run_sequence,
        observed_wall_millis=explanation.budget_receipt.observed_wall_millis,
        observed_peak_memory_bytes=explanation.budget_receipt.observed_peak_memory_bytes,
    )
    return replay.to_dict() == explanation.to_dict()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _finality_candidate_query(problem: FinalityProblem) -> str:
    symbols = [f"route_{index}" for index in range(len(problem.alternatives))]
    lines = ["(set-logic QF_UF)"]
    lines.extend(f"(declare-fun {symbol} () Bool)" for symbol in symbols)
    lines.append(f"(assert (or {' '.join(symbols)}))")
    for left, right in itertools.combinations(symbols, 2):
        lines.append(f"(assert (not (and {left} {right})))")
    lines.extend(["(check-sat)", f"(get-value ({' '.join(symbols)}))", "(exit)"])
    return "\n".join(lines) + "\n"


def explain_finality_with_smtinterpol(
    problem: FinalityProblem,
    *,
    budget: DerivationBudgetPolicy,
    jar_path: Path,
    jar_sha256: str,
    version_contains: str,
    backend_version_hash: str,
    run_sequence: int,
    java_command: str = "java",
    timeout_seconds: float = 10.0,
) -> tuple[FinalityExplanation, SMTFinalityArtifact]:
    """Ask pinned SMTInterpol for a route candidate, then ignore its authority.

    The solver chooses one declared alternative.  Bulla independently reruns
    the exhaustive finite algorithm and accepts the candidate only if the route
    exists and its declared conditions form a sufficient route.  Timeout,
    unknown, malformed output, or a bad candidate remains an accelerator
    failure; it never becomes a mathematical impossibility.
    """
    if _sha256_file(jar_path) != jar_sha256:
        raise InventionError("SMTInterpol jar pin mismatch")
    query = _finality_candidate_query(problem)
    try:
        probed = subprocess.run(
            [java_command, "-jar", str(jar_path), "-version"],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        version = (probed.stdout + "\n" + probed.stderr).strip()
        if probed.returncode or version_contains not in version:
            raise InventionError("SMTInterpol version probe failed or did not match pin")
        completed = subprocess.run(
            [java_command, "-jar", str(jar_path)], input=query,
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        selected = [
            int(match.group(1))
            for match in re.finditer(r"\(route_(\d+)\s+true\)", completed.stdout)
        ]
        if completed.returncode or "sat" not in completed.stdout.split() or len(selected) != 1:
            artifact = SMTFinalityArtifact(
                AcceleratorStatus.UNKNOWN, query, completed.stdout, completed.stderr,
                completed.returncode, version, jar_sha256, None, False,
                "solver did not emit one closed satisfiable route candidate",
            )
        elif selected[0] >= len(problem.alternatives):
            artifact = SMTFinalityArtifact(
                AcceleratorStatus.REJECTED, query, completed.stdout, completed.stderr,
                completed.returncode, version, jar_sha256, None, False,
                "solver named an undeclared route",
            )
        else:
            candidate = problem.alternatives[selected[0]]
            sufficient = set(candidate.required_condition_ids) <= {
                condition.condition_id for condition in problem.conditions
            }
            artifact = SMTFinalityArtifact(
                AcceleratorStatus.CANDIDATE_CHECKED if sufficient else AcceleratorStatus.REJECTED,
                query, completed.stdout, completed.stderr, completed.returncode,
                version, jar_sha256, candidate.route_id, sufficient,
                "candidate condition set independently checked against the closed policy",
            )
    except subprocess.TimeoutExpired:
        artifact = SMTFinalityArtifact(
            AcceleratorStatus.UNKNOWN, query, "", "solver timeout", None,
            version_contains, jar_sha256, None, False,
            "timeout is an accelerator failure, not impossibility",
        )
    explanation = explain_finality(
        problem, budget=budget, backend_hash=jar_sha256,
        backend_version_hash=backend_version_hash, run_sequence=run_sequence,
    )
    if artifact.status is AcceleratorStatus.CANDIDATE_CHECKED and not any(
        route.route_id == artifact.candidate_route_id for route in explanation.routes
    ):
        artifact = SMTFinalityArtifact(
            AcceleratorStatus.REJECTED, artifact.input_smt2, artifact.stdout,
            artifact.stderr, artifact.returncode, artifact.solver_version,
            artifact.jar_sha256, artifact.candidate_route_id, False,
            "candidate is sufficient but not among the independently emitted minimal routes",
        )
    return explanation, artifact


def canonical_scope_from_dict(value: Any) -> StructuredScope:
    d = _closed(
        value,
        {"schema_version", "language", "signature", "predicate", "reference_max_ground_atoms", "reference_max_models"},
        "structured_scope",
    )
    if d["language"] != "FRSL-1":
        raise InventionError("structured scope language must be FRSL-1")
    return StructuredScope(
        signature=Signature.from_dict(d["signature"]), predicate=d["predicate"],
        reference_max_ground_atoms=d["reference_max_ground_atoms"],
        reference_max_models=d["reference_max_models"],
    )
