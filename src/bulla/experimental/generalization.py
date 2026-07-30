"""Generalization Constitution and finite scope frontier (experimental v0.5).

The v0.4 claim-flow profile remains replayable as frozen evidence.  This module
adds a stricter layer in which proposing a rule, adopting it, finding it
applicable, and settling are different artifacts.  It also supplies a small
reference engine for maximal safe scopes in a finite Boolean lattice.

Nothing here is exported from stable :mod:`bulla`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.experimental.claim_flow import (
    AdoptionStatus,
    AuthorityToken,
    ClaimFlowAuthority,
    ClaimPermission,
    InstitutionalFact,
    PrecedentAdoption,
    PrecedentEffect,
    PrecedentRule,
    adopt_precedent,
)
from bulla.experimental.frsl import (
    Formula,
    canonical_hash,
    formula_relations,
    normalize_formula,
    validate_formula,
)
from bulla.experimental.invention import InventionError
from bulla.experimental.scope import ScopeOrderStatus, StructuredScope, scope_leq


PROFILE = "bulla.claim-flow/0.5-experimental"
SCHEMA_VERSION = "0.5-experimental"


def _digest(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise InventionError(f"{where} must be a full lowercase sha256 digest")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InventionError(f"{where} must be a non-empty string")
    return value


def _digests(values: Sequence[str], where: str, *, nonempty: bool = False) -> tuple[str, ...]:
    result = tuple(sorted(set(values)))
    if nonempty and not result:
        raise InventionError(f"{where} must not be empty")
    if len(result) != len(tuple(values)):
        raise InventionError(f"{where} must contain distinct values")
    for value in result:
        _digest(value, where)
    return result


class ApplicabilityStatus(str, enum.Enum):
    APPLY = "APPLY"
    DISTINGUISH = "DISTINGUISH"
    ROUTE = "ROUTE"
    STALE = "STALE"


class DistinctionKind(str, enum.Enum):
    FRESH_REASON = "FRESH_REASON"
    WRONG_AUTHORITY = "WRONG_AUTHORITY"
    STALE_EPOCH = "STALE_EPOCH"
    EVIDENCE_POLICY = "EVIDENCE_POLICY"
    CLOSURE = "CLOSURE"
    HARM = "HARM"
    OUTSIDE_SCOPE = "OUTSIDE_SCOPE"
    CASE_ONLY = "CASE_ONLY"
    PERSUASIVE_ONLY = "PERSUASIVE_ONLY"
    RESOURCE = "RESOURCE"


class CompletenessStatus(str, enum.Enum):
    EXACT = "EXACT"
    UNRESOLVED = "UNRESOLVED"


class ScopeSafety(str, enum.Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"


class AuthorOrigin(str, enum.Enum):
    TEAM_AUTHORED = "TEAM_AUTHORED"
    FOREIGN_AUTHORED = "FOREIGN_AUTHORED"
    MIXED = "MIXED"


class AdjudicationOrigin(str, enum.Enum):
    MACHINE_PLANTED = "MACHINE_PLANTED"
    INDEPENDENT_HUMAN = "INDEPENDENT_HUMAN"
    MIXED = "MIXED"


class ReplayStatus(str, enum.Enum):
    INTERNAL = "INTERNAL"
    INDEPENDENT_PARITY = "INDEPENDENT_PARITY"
    BLOCKED = "BLOCKED"


class HarmClass(str, enum.Enum):
    COMPENSABLE = "COMPENSABLE"
    REVERSIBLE_ONLY = "REVERSIBLE_ONLY"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CATEGORICAL_REFUSE = "CATEGORICAL_REFUSE"


@dataclass(frozen=True)
class PrecedentCandidate:
    source_finding_hash: str
    source_case_hash: str
    reason: Formula
    applicability_scope: StructuredScope
    exclusions: tuple[str, ...]
    protected_consequence_hashes: tuple[str, ...]
    requested_effect: PrecedentEffect
    extractor_identity: str
    semantic_epoch: str
    proposal_receipt_hash: str

    def __post_init__(self) -> None:
        for field in (
            "source_finding_hash", "source_case_hash", "semantic_epoch",
            "proposal_receipt_hash",
        ):
            _digest(getattr(self, field), f"precedent_candidate.{field}")
        _text(self.extractor_identity, "precedent_candidate.extractor_identity")
        normalized = normalize_formula(self.reason)
        validate_formula(
            normalized,
            signature=self.applicability_scope.signature,
            where="precedent_candidate.reason",
        )
        if self.reason != normalized:
            raise InventionError("precedent candidate reason must be canonical FRSL-1")
        exclusions = tuple(sorted(set(self.exclusions)))
        if len(exclusions) != len(self.exclusions) or any(not item for item in exclusions):
            raise InventionError("precedent candidate exclusions must be distinct non-empty strings")
        object.__setattr__(self, "exclusions", exclusions)
        object.__setattr__(
            self,
            "protected_consequence_hashes",
            _digests(
                self.protected_consequence_hashes,
                "precedent_candidate.protected_consequence_hashes",
            ),
        )

    @property
    def candidate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "kind": "PRECEDENT_CANDIDATE",
            "operative_effect": False,
            "source_finding_hash": self.source_finding_hash,
            "source_case_hash": self.source_case_hash,
            "reason": self.reason,
            "reason_vocabulary": sorted(formula_relations(self.reason)),
            "applicability_scope": self.applicability_scope.to_dict(),
            "exclusions": list(self.exclusions),
            "protected_consequence_hashes": list(self.protected_consequence_hashes),
            "requested_effect": self.requested_effect.value,
            "extractor_identity": self.extractor_identity,
            "semantic_epoch": self.semantic_epoch,
            "proposal_receipt_hash": self.proposal_receipt_hash,
        }


@dataclass(frozen=True)
class GeneralizationAdoption:
    candidate_hash: str
    adoption: PrecedentAdoption
    adoption_receipt_hash: str

    def __post_init__(self) -> None:
        _digest(self.candidate_hash, "generalization_adoption.candidate_hash")
        _digest(self.adoption_receipt_hash, "generalization_adoption.adoption_receipt_hash")
        if self.adoption.rule is not None:
            if self.adoption.rule.adoption_receipt_hash != self.adoption_receipt_hash:
                raise InventionError("adoption wrapper and operative rule bind different receipts")

    @property
    def adoption_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "candidate_hash": self.candidate_hash,
            "adoption": self.adoption.to_dict(),
            "adoption_receipt_hash": self.adoption_receipt_hash,
        }


@dataclass(frozen=True)
class DistinctionCertificate:
    rule_hash: str
    case_hash: str
    blockers: tuple[DistinctionKind, ...]
    witness_hashes: tuple[str, ...]
    minimality: CompletenessStatus
    checker_hash: str

    def __post_init__(self) -> None:
        for field in ("rule_hash", "case_hash", "checker_hash"):
            _digest(getattr(self, field), f"distinction.{field}")
        blockers = tuple(dict.fromkeys(self.blockers))
        if not blockers:
            raise InventionError("distinction certificate requires a blocker")
        object.__setattr__(self, "blockers", blockers)
        object.__setattr__(
            self, "witness_hashes",
            _digests(self.witness_hashes, "distinction.witness_hashes", nonempty=True),
        )

    @property
    def certificate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_hash": self.rule_hash,
            "case_hash": self.case_hash,
            "blockers": [item.value for item in self.blockers],
            "witness_hashes": list(self.witness_hashes),
            "minimality": self.minimality.value,
            "checker_hash": self.checker_hash,
        }


@dataclass(frozen=True)
class ApplicabilityFinding:
    rule_hash: str
    case_hash: str
    case_scope_hash: str
    status: ApplicabilityStatus
    semantic_epoch: str
    reason_evaluation_hash: str
    applicability_authority_token_hash: str
    finding_receipt_hash: str
    distinction: DistinctionCertificate | None = None

    def __post_init__(self) -> None:
        for field in (
            "rule_hash", "case_hash", "case_scope_hash", "semantic_epoch",
            "reason_evaluation_hash", "applicability_authority_token_hash",
            "finding_receipt_hash",
        ):
            _digest(getattr(self, field), f"applicability_finding.{field}")
        if self.status is ApplicabilityStatus.APPLY and self.distinction is not None:
            raise InventionError("an applied case cannot also carry a distinction")
        if self.status is not ApplicabilityStatus.APPLY and self.distinction is None:
            raise InventionError("a non-applied case requires a distinction certificate")

    @property
    def finding_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "kind": "APPLICABILITY_FINDING",
            "rule_hash": self.rule_hash,
            "case_hash": self.case_hash,
            "case_scope_hash": self.case_scope_hash,
            "status": self.status.value,
            "semantic_epoch": self.semantic_epoch,
            "reason_evaluation_hash": self.reason_evaluation_hash,
            "applicability_authority_token_hash": self.applicability_authority_token_hash,
            "finding_receipt_hash": self.finding_receipt_hash,
            "distinction": self.distinction.to_dict() if self.distinction else None,
        }


def propose_precedent(
    fact: InstitutionalFact,
    *,
    reason: Formula,
    applicability_scope: StructuredScope,
    exclusions: Sequence[str],
    protected_consequence_hashes: Sequence[str],
    requested_effect: PrecedentEffect,
    extractor_identity: str,
    proposal_receipt_hash: str,
) -> PrecedentCandidate:
    """Create a non-operative proposal.  No authority token is accepted here."""
    reason = normalize_formula(reason)
    return PrecedentCandidate(
        source_finding_hash=fact.claim_hash,
        source_case_hash=fact.case_hash,
        reason=reason,
        applicability_scope=applicability_scope,
        exclusions=tuple(exclusions),
        protected_consequence_hashes=tuple(protected_consequence_hashes),
        requested_effect=requested_effect,
        extractor_identity=extractor_identity,
        semantic_epoch=fact.semantic_epoch,
        proposal_receipt_hash=proposal_receipt_hash,
    )


def adopt_precedent_candidate(
    fact: InstitutionalFact,
    candidate: PrecedentCandidate,
    *,
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
    prior_rule: PrecedentRule | None = None,
    conservativity_verified: bool,
    refusals_preserved: bool,
) -> GeneralizationAdoption:
    if candidate.source_finding_hash != fact.claim_hash or candidate.source_case_hash != fact.case_hash:
        raise InventionError("precedent candidate is borrowed from another forum finding")
    if candidate.semantic_epoch != fact.semantic_epoch:
        raise InventionError("precedent candidate is stale")
    adoption, _ = adopt_precedent(
        fact,
        reason=candidate.reason,
        effect=candidate.requested_effect,
        applicability_scope=candidate.applicability_scope,
        protected_consequence_hashes=candidate.protected_consequence_hashes,
        authority=authority,
        token=token,
        prior_rule=prior_rule,
        conservativity_verified=conservativity_verified,
        refusals_preserved=refusals_preserved,
    )
    return GeneralizationAdoption(
        candidate_hash=candidate.candidate_hash,
        adoption=adoption,
        adoption_receipt_hash=token.authorization_receipt_hash,
    )


def find_applicability(
    rule: PrecedentRule,
    *,
    case_hash: str,
    case_scope: StructuredScope,
    semantic_epoch: str,
    reason_holds: bool,
    reason_evaluation_hash: str,
    authority: ClaimFlowAuthority,
    token: AuthorityToken,
    distinction_witness_hash: str,
) -> ApplicabilityFinding:
    """Make a separately authorized applicability finding.

    ``reason_holds`` is not trusted alone: its independently replayable
    evaluation hash is always bound into the finding.
    """
    case_hash = _digest(case_hash, "find_applicability.case_hash")
    reason_evaluation_hash = _digest(
        reason_evaluation_hash, "find_applicability.reason_evaluation_hash"
    )
    authority.require(
        token,
        ClaimPermission.FORUM_FINDING,
        semantic_epoch=semantic_epoch,
        scope_hash=case_scope.scope_hash,
    )
    blockers: list[DistinctionKind] = []
    status = ApplicabilityStatus.APPLY
    if rule.semantic_epoch != semantic_epoch:
        status = ApplicabilityStatus.STALE
        blockers.append(DistinctionKind.STALE_EPOCH)
    elif rule.effect is PrecedentEffect.PERSUASIVE:
        status = ApplicabilityStatus.ROUTE
        blockers.append(DistinctionKind.PERSUASIVE_ONLY)
    elif rule.effect is PrecedentEffect.CASE_ONLY and case_hash != rule.source_case_hash:
        status = ApplicabilityStatus.DISTINGUISH
        blockers.append(DistinctionKind.CASE_ONLY)
    elif rule.effect is PrecedentEffect.BINDING_WITHIN_SCOPE:
        order = scope_leq(case_scope, rule.applicability_scope)
        if order.status is ScopeOrderStatus.NOT_LEQ:
            status = ApplicabilityStatus.DISTINGUISH
            blockers.append(DistinctionKind.OUTSIDE_SCOPE)
        elif order.status is not ScopeOrderStatus.LEQ:
            status = ApplicabilityStatus.ROUTE
            blockers.append(DistinctionKind.RESOURCE)
        elif not reason_holds:
            status = ApplicabilityStatus.DISTINGUISH
            blockers.append(DistinctionKind.FRESH_REASON)
    distinction = None
    if blockers:
        distinction = DistinctionCertificate(
            rule_hash=rule.rule_hash,
            case_hash=case_hash,
            blockers=tuple(blockers),
            witness_hashes=(_digest(distinction_witness_hash, "distinction_witness_hash"),),
            minimality=CompletenessStatus.EXACT if len(blockers) == 1 else CompletenessStatus.UNRESOLVED,
            checker_hash=reason_evaluation_hash,
        )
    return ApplicabilityFinding(
        rule_hash=rule.rule_hash,
        case_hash=case_hash,
        case_scope_hash=case_scope.scope_hash,
        status=status,
        semantic_epoch=semantic_epoch,
        reason_evaluation_hash=reason_evaluation_hash,
        applicability_authority_token_hash=token.token_hash,
        finding_receipt_hash=token.authorization_receipt_hash,
        distinction=distinction,
    )


@dataclass(frozen=True)
class UnsafeScopeWitness:
    required_mask: int
    witness_hash: str
    cause: str

    def __post_init__(self) -> None:
        if not isinstance(self.required_mask, int) or isinstance(self.required_mask, bool) or self.required_mask <= 0:
            raise InventionError("unsafe witness required_mask must be a positive integer")
        _digest(self.witness_hash, "unsafe_scope_witness.witness_hash")
        _text(self.cause, "unsafe_scope_witness.cause")

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_mask": self.required_mask,
            "witness_hash": self.witness_hash,
            "cause": self.cause,
        }


@dataclass(frozen=True)
class FrontierProblem:
    problem_id: str
    dimensions: tuple[str, ...]
    represented_case_masks: tuple[int, ...]
    unsafe_witnesses: tuple[UnsafeScopeWitness, ...]
    model_class_hash: str
    closure_warrant_hash: str
    authority_regime_hash: str
    semantic_epoch: str

    def __post_init__(self) -> None:
        _text(self.problem_id, "frontier_problem.problem_id")
        dimensions = tuple(self.dimensions)
        if not dimensions or len(dimensions) > 20 or len(dimensions) != len(set(dimensions)):
            raise InventionError("frontier dimensions must contain 1..20 distinct names")
        if any(not item for item in dimensions):
            raise InventionError("frontier dimension names must be non-empty")
        full = (1 << len(dimensions)) - 1
        masks = tuple(self.represented_case_masks)
        if any(not isinstance(mask, int) or mask < 0 or mask > full for mask in masks):
            raise InventionError("represented case mask lies outside the declared lattice")
        for witness in self.unsafe_witnesses:
            if witness.required_mask > full:
                raise InventionError("unsafe witness lies outside the declared lattice")
        for field in (
            "model_class_hash", "closure_warrant_hash", "authority_regime_hash",
            "semantic_epoch",
        ):
            _digest(getattr(self, field), f"frontier_problem.{field}")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "represented_case_masks", masks)
        object.__setattr__(self, "unsafe_witnesses", tuple(self.unsafe_witnesses))

    @property
    def problem_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "dimensions": list(self.dimensions),
            "represented_case_masks": list(self.represented_case_masks),
            "unsafe_witnesses": [item.to_dict() for item in self.unsafe_witnesses],
            "model_class_hash": self.model_class_hash,
            "closure_warrant_hash": self.closure_warrant_hash,
            "authority_regime_hash": self.authority_regime_hash,
            "semantic_epoch": self.semantic_epoch,
        }


@dataclass(frozen=True)
class FrontierScope:
    mask: int
    represented_yield: int
    safety: ScopeSafety
    certificate_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.mask, int) or self.mask < 0:
            raise InventionError("frontier scope mask must be non-negative")
        if not isinstance(self.represented_yield, int) or self.represented_yield < 0:
            raise InventionError("frontier represented yield must be non-negative")
        _digest(self.certificate_hash, "frontier_scope.certificate_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mask": self.mask,
            "represented_yield": self.represented_yield,
            "safety": self.safety.value,
            "certificate_hash": self.certificate_hash,
        }


@dataclass(frozen=True)
class GeneralizationFrontier:
    problem_hash: str
    scopes: tuple[FrontierScope, ...]
    unsafe_witness_hashes: tuple[str, ...]
    evaluated_masks: tuple[int, ...]
    branch_nodes: int
    completeness: CompletenessStatus
    search_frontier_hash: str

    def __post_init__(self) -> None:
        _digest(self.problem_hash, "generalization_frontier.problem_hash")
        _digest(self.search_frontier_hash, "generalization_frontier.search_frontier_hash")
        object.__setattr__(
            self,
            "unsafe_witness_hashes",
            _digests(self.unsafe_witness_hashes, "generalization_frontier.unsafe_witness_hashes"),
        )
        masks = [scope.mask for scope in self.scopes]
        if len(masks) != len(set(masks)):
            raise InventionError("frontier contains duplicate scopes")
        for left in masks:
            for right in masks:
                if left != right and left & right == left:
                    raise InventionError("frontier scopes must be an antichain")

    @property
    def frontier_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "problem_hash": self.problem_hash,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "unsafe_witness_hashes": list(self.unsafe_witness_hashes),
            "evaluated_masks": list(self.evaluated_masks),
            "branch_nodes": self.branch_nodes,
            "completeness": self.completeness.value,
            "search_frontier_hash": self.search_frontier_hash,
            "selection": "CHOICE_REQUIRED" if len(self.scopes) > 1 else "UNSELECTED",
        }


def _unsafe_witness(problem: FrontierProblem, mask: int) -> UnsafeScopeWitness | None:
    for witness in sorted(
        problem.unsafe_witnesses,
        key=lambda item: (item.required_mask.bit_count(), item.required_mask, item.witness_hash),
    ):
        if witness.required_mask & mask == witness.required_mask:
            return witness
    return None


def _yield(problem: FrontierProblem, mask: int) -> int:
    return sum(1 for case_mask in problem.represented_case_masks if case_mask & mask == case_mask)


def exhaustive_frontier(problem: FrontierProblem) -> tuple[int, ...]:
    """Exact reference frontier.  Deliberately capped at width 12."""
    if len(problem.dimensions) > 12:
        raise InventionError("exhaustive frontier is restricted to width 12")
    safe = [mask for mask in range(1 << len(problem.dimensions)) if _unsafe_witness(problem, mask) is None]
    return tuple(
        mask for mask in safe
        if not any(mask != other and mask & other == mask for other in safe)
    )


def compute_generalization_frontier(
    problem: FrontierProblem,
    *,
    max_branch_nodes: int,
) -> GeneralizationFrontier:
    """Counterexample-pruned deterministic search over the finite lattice."""
    if not isinstance(max_branch_nodes, int) or isinstance(max_branch_nodes, bool) or max_branch_nodes <= 0:
        raise InventionError("max_branch_nodes must be a positive integer")
    width = len(problem.dimensions)
    full = (1 << width) - 1
    # Wider scopes are considered first.  A discovered counterexample rejects
    # every super-scope carrying its required mask without re-evaluation.
    order = sorted(range(full + 1), key=lambda mask: (-mask.bit_count(), mask))
    evaluated: list[int] = []
    safe: list[int] = []
    seen_witnesses: set[str] = set()
    complete = True
    for mask in order:
        if len(evaluated) >= max_branch_nodes:
            complete = False
            break
        witness = _unsafe_witness(problem, mask)
        evaluated.append(mask)
        if witness is not None:
            seen_witnesses.add(witness.witness_hash)
            continue
        if any(mask & incumbent == mask for incumbent in safe):
            continue
        safe = [incumbent for incumbent in safe if not (incumbent & mask == incumbent)]
        safe.append(mask)
    safe.sort()
    scopes = tuple(
        FrontierScope(
            mask=mask,
            represented_yield=_yield(problem, mask),
            safety=ScopeSafety.SAFE,
            certificate_hash=canonical_hash(
                {
                    "problem_hash": problem.problem_hash,
                    "mask": mask,
                    "unsafe_witnesses_excluded": sorted(
                        item.witness_hash for item in problem.unsafe_witnesses
                        if item.required_mask & mask != item.required_mask
                    ),
                }
            ),
        )
        for mask in safe
    )
    unexplored = tuple(mask for mask in order if mask not in set(evaluated))
    return GeneralizationFrontier(
        problem_hash=problem.problem_hash,
        scopes=scopes,
        unsafe_witness_hashes=tuple(sorted(seen_witnesses)),
        evaluated_masks=tuple(evaluated),
        branch_nodes=len(evaluated),
        completeness=CompletenessStatus.EXACT if complete else CompletenessStatus.UNRESOLVED,
        search_frontier_hash=canonical_hash(
            {"problem_hash": problem.problem_hash, "unexplored_masks": list(unexplored)}
        ),
    )


def verify_generalization_frontier(
    problem: FrontierProblem,
    frontier: GeneralizationFrontier,
) -> bool:
    if frontier.problem_hash != problem.problem_hash:
        return False
    masks = [scope.mask for scope in frontier.scopes]
    if any(_unsafe_witness(problem, mask) is not None for mask in masks):
        return False
    if any(scope.represented_yield != _yield(problem, scope.mask) for scope in frontier.scopes):
        return False
    if frontier.completeness is CompletenessStatus.EXACT:
        if len(problem.dimensions) > 12 or tuple(sorted(masks)) != exhaustive_frontier(problem):
            return False
        if set(frontier.evaluated_masks) != set(range(1 << len(problem.dimensions))):
            return False
    return True


@dataclass(frozen=True)
class CompoundingObservation:
    result: str
    corpus_hash: str
    author_origin: AuthorOrigin
    adjudication_origin: AdjudicationOrigin
    replay: ReplayStatus
    closure_warrant_hash: str
    external_author_count: int
    external_adjudicator_count: int
    external_implementation_count: int
    external_witness_count: int

    def __post_init__(self) -> None:
        _text(self.result, "compounding_observation.result")
        _digest(self.corpus_hash, "compounding_observation.corpus_hash")
        _digest(self.closure_warrant_hash, "compounding_observation.closure_warrant_hash")
        for field in (
            "external_author_count", "external_adjudicator_count",
            "external_implementation_count", "external_witness_count",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"{field} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "result": self.result,
            "corpus_hash": self.corpus_hash,
            "author_origin": self.author_origin.value,
            "adjudication_origin": self.adjudication_origin.value,
            "replay": self.replay.value,
            "closure_warrant_hash": self.closure_warrant_hash,
            "external_counts": {
                "authors": self.external_author_count,
                "adjudicators": self.external_adjudicator_count,
                "implementations": self.external_implementation_count,
                "witnesses": self.external_witness_count,
            },
            "display": _compounding_display(self),
        }


def _compounding_display(observation: CompoundingObservation) -> str:
    if (
        observation.author_origin is AuthorOrigin.TEAM_AUTHORED
        and observation.adjudication_origin is AdjudicationOrigin.MACHINE_PLANTED
        and observation.replay is ReplayStatus.INTERNAL
    ):
        return "Compounding observed — internal captive lineage benchmark."
    return f"Compounding observation — {observation.result}."


@dataclass(frozen=True)
class EffectWarrant:
    effect_predicate: Formula
    applicability_scope: StructuredScope
    harm_class: HarmClass
    protected_effect_hashes: tuple[str, ...]
    semantic_epoch: str
    authority_regime_hash: str
    warrant_receipt_hash: str

    def __post_init__(self) -> None:
        normalized = normalize_formula(self.effect_predicate)
        validate_formula(
            normalized,
            signature=self.applicability_scope.signature,
            where="effect_warrant.effect_predicate",
        )
        if normalized != self.effect_predicate:
            raise InventionError("effect predicate must be canonical FRSL-1")
        for field in ("semantic_epoch", "authority_regime_hash", "warrant_receipt_hash"):
            _digest(getattr(self, field), f"effect_warrant.{field}")
        object.__setattr__(
            self,
            "protected_effect_hashes",
            _digests(self.protected_effect_hashes, "effect_warrant.protected_effect_hashes", nonempty=True),
        )

    @property
    def warrant_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "effect_predicate": self.effect_predicate,
            "applicability_scope": self.applicability_scope.to_dict(),
            "harm_class": self.harm_class.value,
            "protected_effect_hashes": list(self.protected_effect_hashes),
            "semantic_epoch": self.semantic_epoch,
            "authority_regime_hash": self.authority_regime_hash,
            "warrant_receipt_hash": self.warrant_receipt_hash,
            "boundary": "action names and model classifications are advisory; the effect warrant governs",
        }


def effect_verdict(warrant: EffectWarrant, *, observed_effect_hashes: Sequence[str]) -> HarmClass | None:
    """Classify by protected effects only; action names cannot enter this API."""
    observed = set(_digests(observed_effect_hashes, "observed_effect_hashes"))
    if observed.intersection(warrant.protected_effect_hashes):
        return warrant.harm_class
    return None
