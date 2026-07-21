"""Verified observability planning for the experimental semantic compiler.

This module turns a fixed-language non-definability witness into a bounded,
independently replayable evidence request.  It deliberately does not extend
FRSL-1: an offer reveals the truth of an already declared unary or binary
relation.  The planner enumerates every target-disagreeing model pair that is
indistinguishable on the current shared reduct, then solves the resulting
finite separating-set problem exactly for catalogs of at most sixteen offers.

Private models never enter a plan or receipt.  Only pair-set commitments,
coverage commitments, and declared burden vectors cross the protocol surface.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import (
    LANGUAGE,
    Formula,
    canonical_hash,
    canonical_json,
    evaluate,
    formula_relations,
    relation_reduct,
    structure_to_dict,
    validate_formula,
)
from bulla.experimental.invention import (
    FailureKind,
    InventionError,
    SeamProblem,
    SynthesisResult,
    SynthesisStatus,
    _admissible_models,
    verify_failure_certificate,
)
from bulla.identity import LocalEd25519Signer, verify_proof_domain


PASSPORT_SCHEMA = "0.1-experimental"
MANIFEST_SCHEMA = "0.1-experimental"
PLANNING_SCHEMA = "0.1-experimental"
# The identity kernel intentionally has a closed proof-purpose registry.  The
# experimental response is a signed content artifact, so it uses the existing
# content domain while its digest commits to this module's closed schema.
RESPONSE_PROOF_PURPOSE = "content"

BURDEN_FIELDS = (
    "disclosure_units",
    "latency_ms",
    "monetary_microunits",
    "new_authorities",
    "institutional_dependencies",
    "lifecycle_burden",
)


def _closed(
    value: Any,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    where: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventionError(f"{where} must be an object")
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise InventionError(f"{where} is missing required keys {sorted(missing)}")
    if unknown:
        raise InventionError(f"{where} has unknown keys {sorted(unknown)}")
    return value


def _digest(value: Any) -> str:
    return canonical_hash(value)


def _require_digest(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise InventionError(f"{where} must be a full lowercase sha256 digest")
    return value


@dataclass(frozen=True)
class LogicPassport:
    """Pinned finite logic and checker context for one compilation epoch."""

    extractor: Mapping[str, Any]
    checker: Mapping[str, Any]
    resource_bounds: Mapping[str, int]
    supported_guarantees: tuple[str, ...]
    unsupported_constructs: tuple[str, ...]
    finite_semantics: str = "closed-finite-structures/1"
    frsl_version: str = LANGUAGE
    schema_version: str = PASSPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PASSPORT_SCHEMA or self.frsl_version != LANGUAGE:
            raise InventionError("unsupported LogicPassport schema or language")
        if self.finite_semantics != "closed-finite-structures/1":
            raise InventionError("LogicPassport must pin the closed finite semantics")
        for name, descriptor in (("extractor", self.extractor), ("checker", self.checker)):
            if not isinstance(descriptor, Mapping) or not descriptor:
                raise InventionError(f"LogicPassport.{name} must be a non-empty object")
        required_bounds = {
            "max_ground_atoms",
            "max_models",
            "max_observable_offers",
            "max_opposing_pairs",
            "max_minimal_plans",
        }
        if set(self.resource_bounds) != required_bounds:
            raise InventionError(
                "LogicPassport.resource_bounds must be exactly "
                f"{sorted(required_bounds)}"
            )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in self.resource_bounds.values()
        ):
            raise InventionError("LogicPassport resource bounds must be positive integers")
        if self.resource_bounds["max_observable_offers"] > 16:
            raise InventionError("exact observability planning is capped at sixteen offers")
        for name, values in (
            ("supported_guarantees", self.supported_guarantees),
            ("unsupported_constructs", self.unsupported_constructs),
        ):
            values = tuple(values)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise InventionError(f"LogicPassport.{name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise InventionError(f"LogicPassport.{name} contains duplicates")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "extractor", dict(self.extractor))
        object.__setattr__(self, "checker", dict(self.checker))
        object.__setattr__(self, "resource_bounds", dict(self.resource_bounds))

    @classmethod
    def for_problem(cls, problem: SeamProblem) -> "LogicPassport":
        return cls(
            extractor={
                "id": "bulla.experimental.invention.reference",
                "version": "0.1-experimental",
            },
            checker={
                "id": "bulla.experimental.observability.reference",
                "version": "0.1-experimental",
            },
            resource_bounds={
                "max_ground_atoms": problem.synthesis_policy.reference_max_ground_atoms,
                "max_models": problem.synthesis_policy.reference_max_models,
                "max_observable_offers": 16,
                "max_opposing_pairs": 100_000,
                "max_minimal_plans": 16_384,
            },
            supported_guarantees=(
                "finite explicit-definition checking",
                "exact separating-set planning within declared bounds",
                "componentwise Pareto comparison",
                "same-reduct non-definability replay",
            ),
            unsupported_constructs=(
                "functions",
                "unbounded quantification",
                "floating point",
                "host-language regex",
                "raw-value disclosure",
            ),
        )

    @property
    def passport_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "frsl_version": self.frsl_version,
            "finite_semantics": self.finite_semantics,
            "extractor": dict(self.extractor),
            "checker": dict(self.checker),
            "resource_bounds": dict(self.resource_bounds),
            "supported_guarantees": list(self.supported_guarantees),
            "unsupported_constructs": list(self.unsupported_constructs),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "LogicPassport":
        d = _closed(
            value,
            required={
                "schema_version",
                "frsl_version",
                "finite_semantics",
                "extractor",
                "checker",
                "resource_bounds",
                "supported_guarantees",
                "unsupported_constructs",
            },
            where="logic_passport",
        )
        if not isinstance(d["extractor"], dict) or not isinstance(d["checker"], dict):
            raise InventionError("LogicPassport extractor/checker must be objects")
        if not isinstance(d["resource_bounds"], dict):
            raise InventionError("LogicPassport.resource_bounds must be an object")
        if not isinstance(d["supported_guarantees"], list) or not isinstance(
            d["unsupported_constructs"], list
        ):
            raise InventionError("LogicPassport guarantee lists must be arrays")
        return cls(
            schema_version=d["schema_version"],
            frsl_version=d["frsl_version"],
            finite_semantics=d["finite_semantics"],
            extractor=d["extractor"],
            checker=d["checker"],
            resource_bounds=d["resource_bounds"],
            supported_guarantees=tuple(d["supported_guarantees"]),
            unsupported_constructs=tuple(d["unsupported_constructs"]),
        )


@dataclass(frozen=True)
class ConservationManifest:
    """Owner-declared constraints on meaning, disclosure, and authority."""

    owner: str
    protected_relations: tuple[str, ...]
    protected_queries: tuple[Formula, ...]
    forbidden_disclosures: tuple[str, ...]
    permitted_evidence_classes: tuple[str, ...]
    authority_constraints: Mapping[str, Any]
    schema_version: str = MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA or not self.owner:
            raise InventionError("unsupported ConservationManifest schema or empty owner")
        for name in (
            "protected_relations",
            "forbidden_disclosures",
            "permitted_evidence_classes",
        ):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, str) or not value for value in values):
                raise InventionError(f"ConservationManifest.{name} contains an invalid value")
            if len(values) != len(set(values)):
                raise InventionError(f"ConservationManifest.{name} contains duplicates")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "protected_queries", tuple(self.protected_queries))
        if not isinstance(self.authority_constraints, Mapping):
            raise InventionError("ConservationManifest.authority_constraints must be an object")
        object.__setattr__(self, "authority_constraints", dict(self.authority_constraints))

    @classmethod
    def for_problem(cls, problem: SeamProblem, *, owner: str = "joint-seam") -> "ConservationManifest":
        protected = sorted(
            {name for names in problem.protected_signatures.values() for name in names}
        )
        return cls(
            owner=owner,
            protected_relations=tuple(protected),
            protected_queries=(),
            forbidden_disclosures=(problem.target_predicate,),
            permitted_evidence_classes=(
                "signed_attestation",
                "selective_disclosure",
                "threshold_proof",
            ),
            authority_constraints=dict(problem.authority),
        )

    def validate_for_problem(self, problem: SeamProblem) -> None:
        relations = set(problem.signature.relations)
        if set(self.protected_relations) - relations:
            raise InventionError("manifest protects relations outside the problem signature")
        if set(self.forbidden_disclosures) - relations:
            raise InventionError("manifest forbids relations outside the problem signature")
        declared_protected = {
            name for names in problem.protected_signatures.values() for name in names
        }
        if not declared_protected.issubset(self.protected_relations):
            raise InventionError("manifest omits a problem-declared protected relation")
        if problem.target_predicate not in self.forbidden_disclosures:
            raise InventionError("the disputed target must remain a forbidden disclosure")
        for index, query in enumerate(self.protected_queries):
            validate_formula(
                query,
                signature=problem.signature,
                where=f"conservation_manifest.protected_queries[{index}]",
            )
            if not formula_relations(query).issubset(set(self.protected_relations)):
                raise InventionError("protected query refers outside protected_relations")
        if dict(self.authority_constraints) != dict(problem.authority):
            raise InventionError("manifest authority constraints do not pin the seam authority")

    @property
    def manifest_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "owner": self.owner,
            "protected_relations": list(self.protected_relations),
            "protected_queries": list(self.protected_queries),
            "forbidden_disclosures": list(self.forbidden_disclosures),
            "permitted_evidence_classes": list(self.permitted_evidence_classes),
            "authority_constraints": dict(self.authority_constraints),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ConservationManifest":
        d = _closed(
            value,
            required={
                "schema_version",
                "owner",
                "protected_relations",
                "protected_queries",
                "forbidden_disclosures",
                "permitted_evidence_classes",
                "authority_constraints",
            },
            where="conservation_manifest",
        )
        for name in (
            "protected_relations",
            "protected_queries",
            "forbidden_disclosures",
            "permitted_evidence_classes",
        ):
            if not isinstance(d[name], list):
                raise InventionError(f"ConservationManifest.{name} must be an array")
        if not isinstance(d["authority_constraints"], dict):
            raise InventionError("ConservationManifest.authority_constraints must be an object")
        return cls(
            schema_version=d["schema_version"],
            owner=d["owner"],
            protected_relations=tuple(d["protected_relations"]),
            protected_queries=tuple(d["protected_queries"]),
            forbidden_disclosures=tuple(d["forbidden_disclosures"]),
            permitted_evidence_classes=tuple(d["permitted_evidence_classes"]),
            authority_constraints=d["authority_constraints"],
        )


@dataclass(frozen=True)
class BurdenVector:
    disclosure_units: int = 0
    latency_ms: int = 0
    monetary_microunits: int = 0
    new_authorities: int = 0
    institutional_dependencies: int = 0
    lifecycle_burden: int = 0

    def __post_init__(self) -> None:
        for name in BURDEN_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"burden.{name} must be a non-negative integer")

    def __add__(self, other: "BurdenVector") -> "BurdenVector":
        return BurdenVector(**{
            name: getattr(self, name) + getattr(other, name) for name in BURDEN_FIELDS
        })

    def dominates(self, other: "BurdenVector") -> bool:
        le = all(getattr(self, name) <= getattr(other, name) for name in BURDEN_FIELDS)
        strict = any(getattr(self, name) < getattr(other, name) for name in BURDEN_FIELDS)
        return le and strict

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in BURDEN_FIELDS}

    @classmethod
    def from_dict(cls, value: Any) -> "BurdenVector":
        d = _closed(value, required=set(BURDEN_FIELDS), where="burden")
        return cls(**d)


@dataclass(frozen=True)
class ObservableOffer:
    offer_id: str
    relation: str
    sorts: tuple[str, ...]
    meaning: Formula
    provider: str
    warrant_profile: Mapping[str, Any]
    burden: BurdenVector
    consent_subjects: tuple[str, ...]
    expiry: str | None = None

    def __post_init__(self) -> None:
        if not self.offer_id or not self.provider:
            raise InventionError("ObservableOffer id and provider must be non-empty")
        if not isinstance(self.relation, str) or not self.relation:
            raise InventionError("ObservableOffer.relation must be non-empty")
        sorts = tuple(self.sorts)
        if len(sorts) not in (1, 2) or any(not value for value in sorts):
            raise InventionError("ObservableOffer sorts must declare unary/binary FRSL facts")
        warrant = _closed(
            dict(self.warrant_profile),
            required={"kind", "evidence_class", "verifier", "reveals"},
            where="observable_offer.warrant_profile",
        )
        if warrant["kind"] not in {
            "signed_attestation",
            "selective_disclosure",
            "threshold_proof",
        }:
            raise InventionError("ObservableOffer has an unsupported warrant kind")
        if warrant["reveals"] != "boolean_fact_only":
            raise InventionError("ObservableOffer may reveal only a Boolean FRSL fact")
        if any(not isinstance(warrant[name], str) or not warrant[name] for name in warrant):
            raise InventionError("ObservableOffer warrant fields must be non-empty strings")
        subjects = tuple(self.consent_subjects)
        if self.provider not in subjects:
            raise InventionError("ObservableOffer provider must be a consent subject")
        if len(subjects) != len(set(subjects)) or any(not x for x in subjects):
            raise InventionError("ObservableOffer consent subjects must be unique and non-empty")
        if self.expiry is not None and not isinstance(self.expiry, str):
            raise InventionError("ObservableOffer.expiry must be a string or null")
        object.__setattr__(self, "warrant_profile", warrant)
        object.__setattr__(self, "consent_subjects", subjects)
        object.__setattr__(self, "sorts", sorts)

    @property
    def offer_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "relation": self.relation,
            "sorts": list(self.sorts),
            "meaning": self.meaning,
            "provider": self.provider,
            "warrant_profile": dict(self.warrant_profile),
            "burden": self.burden.to_dict(),
            "consent_subjects": list(self.consent_subjects),
            "expiry": self.expiry,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ObservableOffer":
        d = _closed(
            value,
            required={
                "offer_id",
                "relation",
                "sorts",
                "meaning",
                "provider",
                "warrant_profile",
                "burden",
                "consent_subjects",
                "expiry",
            },
            where="observable_offer",
        )
        if (
            not isinstance(d["warrant_profile"], dict)
            or not isinstance(d["consent_subjects"], list)
            or not isinstance(d["sorts"], list)
        ):
            raise InventionError("ObservableOffer warrant/consent fields have wrong type")
        return cls(
            offer_id=d["offer_id"],
            relation=d["relation"],
            sorts=tuple(d["sorts"]),
            meaning=d["meaning"],
            provider=d["provider"],
            warrant_profile=d["warrant_profile"],
            burden=BurdenVector.from_dict(d["burden"]),
            consent_subjects=tuple(d["consent_subjects"]),
            expiry=d["expiry"],
        )


@dataclass(frozen=True)
class VerifiedEnrichmentPlan:
    observable_ids: tuple[str, ...]
    opposing_pair_digest: str
    separation_digest: str
    sufficiency_certificate: Mapping[str, Any]
    minimality: str
    pareto_status: str
    indispensable_observables: tuple[str, ...]
    consent_subjects: tuple[str, ...]
    predicted_envelope_reduction: Mapping[str, int]
    burden: BurdenVector

    def __post_init__(self) -> None:
        for name in ("opposing_pair_digest", "separation_digest"):
            _require_digest(getattr(self, name), f"verified_enrichment_plan.{name}")
        ids = tuple(self.observable_ids)
        if not ids or len(ids) != len(set(ids)) or any(not value for value in ids):
            raise InventionError("VerifiedEnrichmentPlan observable ids must be unique")
        if self.minimality not in {"exact-declared-candidate-space", "unresolved"}:
            raise InventionError("VerifiedEnrichmentPlan has invalid minimality status")
        if self.pareto_status not in {"frontier", "dominated", "unresolved"}:
            raise InventionError("VerifiedEnrichmentPlan has invalid Pareto status")
        if self.minimality == "unresolved" and self.pareto_status != "unresolved":
            raise InventionError("nonminimal verified plans must leave Pareto status unresolved")
        certificate = _closed(
            dict(self.sufficiency_certificate),
            required={
                "pair_count",
                "covered_pair_count",
                "coverage_digest",
                "sufficient",
            },
            where="verified_enrichment_plan.sufficiency_certificate",
        )
        if (
            not isinstance(certificate["pair_count"], int)
            or isinstance(certificate["pair_count"], bool)
            or certificate["pair_count"] <= 0
            or certificate["covered_pair_count"] != certificate["pair_count"]
            or certificate["sufficient"] is not True
        ):
            raise InventionError("VerifiedEnrichmentPlan does not carry a sufficient cover")
        _require_digest(certificate["coverage_digest"], "coverage_digest")
        reduction = _closed(
            dict(self.predicted_envelope_reduction),
            required={"opposing_pairs_before", "opposing_pairs_after", "coverage_ppm"},
            where="predicted_envelope_reduction",
        )
        if (
            reduction["opposing_pairs_before"] != certificate["pair_count"]
            or reduction["opposing_pairs_after"] != 0
            or reduction["coverage_ppm"] != 1_000_000
        ):
            raise InventionError("VerifiedEnrichmentPlan reduction is inconsistent")
        indispensable = tuple(self.indispensable_observables)
        if not set(indispensable).issubset(ids):
            raise InventionError("indispensable observables must occur in the plan")
        subjects = tuple(self.consent_subjects)
        if not subjects or len(subjects) != len(set(subjects)):
            raise InventionError("VerifiedEnrichmentPlan consent subjects must be unique")
        object.__setattr__(self, "observable_ids", ids)
        object.__setattr__(self, "indispensable_observables", indispensable)
        object.__setattr__(self, "consent_subjects", subjects)
        object.__setattr__(self, "sufficiency_certificate", certificate)
        object.__setattr__(self, "predicted_envelope_reduction", reduction)

    @property
    def plan_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "observable_ids": list(self.observable_ids),
            "opposing_pair_digest": self.opposing_pair_digest,
            "separation_digest": self.separation_digest,
            "sufficiency_certificate": dict(self.sufficiency_certificate),
            "minimality": self.minimality,
            "pareto_status": self.pareto_status,
            "indispensable_observables": list(self.indispensable_observables),
            "consent_subjects": list(self.consent_subjects),
            "predicted_envelope_reduction": dict(self.predicted_envelope_reduction),
            "burden": self.burden.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "VerifiedEnrichmentPlan":
        d = _closed(
            value,
            required={
                "observable_ids",
                "opposing_pair_digest",
                "separation_digest",
                "sufficiency_certificate",
                "minimality",
                "pareto_status",
                "indispensable_observables",
                "consent_subjects",
                "predicted_envelope_reduction",
                "burden",
            },
            where="verified_enrichment_plan",
        )
        for name in ("observable_ids", "indispensable_observables", "consent_subjects"):
            if not isinstance(d[name], list):
                raise InventionError(f"VerifiedEnrichmentPlan.{name} must be an array")
        return cls(
            observable_ids=tuple(d["observable_ids"]),
            opposing_pair_digest=d["opposing_pair_digest"],
            separation_digest=d["separation_digest"],
            sufficiency_certificate=d["sufficiency_certificate"],
            minimality=d["minimality"],
            pareto_status=d["pareto_status"],
            indispensable_observables=tuple(d["indispensable_observables"]),
            consent_subjects=tuple(d["consent_subjects"]),
            predicted_envelope_reduction=d["predicted_envelope_reduction"],
            burden=BurdenVector.from_dict(d["burden"]),
        )


class PlanningStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    NOT_NEEDED = "NOT_NEEDED"
    NO_SUFFICIENT_PLAN = "NO_SUFFICIENT_PLAN"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class EnrichmentPlanningResult:
    status: PlanningStatus
    problem_hash: str
    passport_hash: str
    manifest_hash: str
    catalog_hash: str
    opposing_pair_digest: str | None
    opposing_pair_count: int
    plans: tuple[VerifiedEnrichmentPlan, ...] = ()
    indispensable_observables: tuple[str, ...] = ()
    reason: str = ""
    schema_version: str = PLANNING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PLANNING_SCHEMA:
            raise InventionError("unsupported enrichment planning schema")
        for name in ("problem_hash", "passport_hash", "manifest_hash", "catalog_hash"):
            _require_digest(getattr(self, name), f"enrichment_planning_result.{name}")
        if self.opposing_pair_digest is not None:
            _require_digest(self.opposing_pair_digest, "opposing_pair_digest")
        if not isinstance(self.opposing_pair_count, int) or self.opposing_pair_count < 0:
            raise InventionError("opposing_pair_count must be non-negative")
        plans = tuple(self.plans)
        if self.status is PlanningStatus.PLANNED and not plans:
            raise InventionError("PLANNED requires at least one verified plan")
        if self.status is not PlanningStatus.PLANNED and plans:
            raise InventionError("only PLANNED may carry plans")
        if self.status is not PlanningStatus.NOT_NEEDED and not self.reason:
            raise InventionError("nontrivial planning results require a reason")
        object.__setattr__(self, "plans", plans)
        object.__setattr__(self, "indispensable_observables", tuple(self.indispensable_observables))

    @property
    def result_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "problem_hash": self.problem_hash,
            "passport_hash": self.passport_hash,
            "manifest_hash": self.manifest_hash,
            "catalog_hash": self.catalog_hash,
            "opposing_pair_digest": self.opposing_pair_digest,
            "opposing_pair_count": self.opposing_pair_count,
            "plans": [plan.to_dict() for plan in self.plans],
            "indispensable_observables": list(self.indispensable_observables),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EnrichmentPlanningResult":
        d = _closed(
            value,
            required={
                "schema_version",
                "status",
                "problem_hash",
                "passport_hash",
                "manifest_hash",
                "catalog_hash",
                "opposing_pair_digest",
                "opposing_pair_count",
                "plans",
                "indispensable_observables",
                "reason",
            },
            where="enrichment_planning_result",
        )
        if not isinstance(d["plans"], list) or not isinstance(
            d["indispensable_observables"], list
        ):
            raise InventionError("planning result plan/indispensable fields must be arrays")
        return cls(
            schema_version=d["schema_version"],
            status=PlanningStatus(d["status"]),
            problem_hash=d["problem_hash"],
            passport_hash=d["passport_hash"],
            manifest_hash=d["manifest_hash"],
            catalog_hash=d["catalog_hash"],
            opposing_pair_digest=d["opposing_pair_digest"],
            opposing_pair_count=d["opposing_pair_count"],
            plans=tuple(VerifiedEnrichmentPlan.from_dict(x) for x in d["plans"]),
            indispensable_observables=tuple(d["indispensable_observables"]),
            reason=d["reason"],
        )


@dataclass(frozen=True)
class _OpposingPair:
    pair_id: str
    left: Mapping[str, tuple[tuple[str, ...], ...]]
    right: Mapping[str, tuple[tuple[str, ...], ...]]


def _validate_context(
    problem: SeamProblem,
    passport: LogicPassport,
    manifest: ConservationManifest,
) -> None:
    if passport.resource_bounds["max_ground_atoms"] != (
        problem.synthesis_policy.reference_max_ground_atoms
    ) or passport.resource_bounds["max_models"] != problem.synthesis_policy.reference_max_models:
        raise InventionError("LogicPassport enumeration bounds do not pin the seam policy")
    manifest.validate_for_problem(problem)


def _validate_offers(
    problem: SeamProblem,
    offers: Sequence[ObservableOffer],
    passport: LogicPassport,
    manifest: ConservationManifest,
) -> tuple[ObservableOffer, ...]:
    offers = tuple(offers)
    if len(offers) > passport.resource_bounds["max_observable_offers"]:
        raise InventionError("observable catalog exceeds the exact planning bound")
    if len({offer.offer_id for offer in offers}) != len(offers):
        raise InventionError("observable offer ids must be unique")
    for offer in offers:
        if offer.relation == problem.target_predicate:
            raise InventionError("the observable output cannot reuse the disputed target name")
        if any(sort not in problem.signature.sorts for sort in offer.sorts):
            raise InventionError(f"offer {offer.offer_id!r} references an unknown sort")
        existing = problem.signature.relations.get(offer.relation)
        if existing is not None and existing.sorts != offer.sorts:
            raise InventionError(f"offer {offer.offer_id!r} changes an existing relation sort")
        if offer.relation in manifest.forbidden_disclosures:
            raise InventionError(f"offer {offer.offer_id!r} violates forbidden disclosures")
        if offer.warrant_profile["evidence_class"] not in manifest.permitted_evidence_classes:
            raise InventionError(f"offer {offer.offer_id!r} uses a forbidden evidence class")
        if offer.warrant_profile["kind"] != offer.warrant_profile["evidence_class"]:
            raise InventionError("warrant kind and evidence class must agree in v0.1")
        validate_formula(
            offer.meaning,
            signature=problem.signature,
            free_variables={f"x{i}": sort for i, sort in enumerate(offer.sorts)},
            where=f"observable_offer[{offer.offer_id}].meaning",
        )
    return offers


def _offer_extension(
    problem: SeamProblem,
    offer: ObservableOffer,
    model: Mapping[str, tuple[tuple[str, ...], ...]],
) -> tuple[tuple[str, ...], ...]:
    domains = [problem.signature.sorts[sort] for sort in offer.sorts]
    extension = []
    for arguments in itertools.product(*domains):
        environment = {f"x{i}": value for i, value in enumerate(arguments)}
        if evaluate(
            offer.meaning,
            signature=problem.signature,
            structure=model,
            environment=environment,
        ):
            extension.append(tuple(arguments))
    return tuple(extension)


def _opposing_pairs(
    problem: SeamProblem,
    *,
    max_pairs: int,
    offers: Sequence[ObservableOffer] = (),
) -> tuple[list[_OpposingPair], bool]:
    models = _admissible_models(problem)
    groups: dict[str, list[Mapping[str, tuple[tuple[str, ...], ...]]]] = {}
    for model in models:
        reduct = relation_reduct(model, problem.shared_vocabulary)
        groups.setdefault(canonical_json(reduct), []).append(model)
    pairs: list[_OpposingPair] = []
    for reduct_key in sorted(groups):
        # Planning depends only on the target extension and the offered
        # observation extensions inside one shared-reduct cell.  Models that
        # agree on all of those values are behaviorally interchangeable for
        # the separating-set problem, so retain one deterministic
        # representative rather than enumerating a quadratic multiplicity of
        # certificate-identical obligations.
        representatives: dict[str, Mapping[str, tuple[tuple[str, ...], ...]]] = {}
        for model in groups[reduct_key]:
            behavior = {
                "target": [list(item) for item in model[problem.target_predicate]],
                "offers": {
                    offer.offer_id: [list(item) for item in _offer_extension(problem, offer, model)]
                    for offer in offers
                },
            }
            behavior_key = canonical_json(behavior)
            current = representatives.get(behavior_key)
            if current is None or canonical_json(structure_to_dict(dict(model))) < canonical_json(structure_to_dict(dict(current))):
                representatives[behavior_key] = model
        models_in_cell = [representatives[key] for key in sorted(representatives)]
        for left, right in itertools.combinations(models_in_cell, 2):
            if left[problem.target_predicate] == right[problem.target_predicate]:
                continue
            left_hash = _digest(structure_to_dict(dict(left)))
            right_hash = _digest(structure_to_dict(dict(right)))
            pair_id = _digest(
                {
                    "problem_hash": problem.problem_hash,
                    "shared_reduct_hash": _digest(relation_reduct(left, problem.shared_vocabulary)),
                    "model_hashes": sorted((left_hash, right_hash)),
                    "target_difference_hash": _digest(
                        sorted(
                            set(left[problem.target_predicate])
                            ^ set(right[problem.target_predicate])
                        )
                    ),
                }
            )
            pairs.append(_OpposingPair(pair_id, left, right))
            if len(pairs) > max_pairs:
                return [], True
    pairs.sort(key=lambda pair: pair.pair_id)
    return pairs, False


def _catalog_hash(offers: Sequence[ObservableOffer]) -> str:
    return _digest([offer.to_dict() for offer in sorted(offers, key=lambda x: x.offer_id)])


def _pareto_frontier(
    subsets: Sequence[tuple[str, ...]], by_id: Mapping[str, ObservableOffer]
) -> set[tuple[str, ...]]:
    burdens = {
        subset: sum((by_id[offer_id].burden for offer_id in subset), BurdenVector())
        for subset in subsets
    }
    return {
        subset
        for subset in subsets
        if not any(
            other != subset and burdens[other].dominates(burdens[subset])
            for other in subsets
        )
    }


def plan_enrichment(
    problem: SeamProblem,
    offers: Sequence[ObservableOffer],
    *,
    passport: LogicPassport,
    manifest: ConservationManifest,
) -> EnrichmentPlanningResult:
    """Compute every inclusion-minimal sufficient observable plan exactly."""

    _validate_context(problem, passport, manifest)
    try:
        offers = _validate_offers(problem, offers, passport, manifest)
    except InventionError as exc:
        # Catalog size is a computational bound, not a semantic impossibility.
        if "exact planning bound" not in str(exc):
            raise
        return EnrichmentPlanningResult(
            status=PlanningStatus.INDETERMINATE,
            problem_hash=problem.problem_hash,
            passport_hash=passport.passport_hash,
            manifest_hash=manifest.manifest_hash,
            catalog_hash=_catalog_hash(tuple(offers)),
            opposing_pair_digest=None,
            opposing_pair_count=0,
            reason=str(exc),
        )
    catalog_hash = _catalog_hash(offers)
    pairs, exhausted = _opposing_pairs(
        problem,
        max_pairs=passport.resource_bounds["max_opposing_pairs"],
        offers=offers,
    )
    if exhausted:
        return EnrichmentPlanningResult(
            status=PlanningStatus.INDETERMINATE,
            problem_hash=problem.problem_hash,
            passport_hash=passport.passport_hash,
            manifest_hash=manifest.manifest_hash,
            catalog_hash=catalog_hash,
            opposing_pair_digest=None,
            opposing_pair_count=0,
            reason="opposing-pair enumeration exceeded the pinned resource bound",
        )
    pair_ids = tuple(pair.pair_id for pair in pairs)
    pair_digest = _digest(list(pair_ids))
    if not pairs:
        return EnrichmentPlanningResult(
            status=PlanningStatus.NOT_NEEDED,
            problem_hash=problem.problem_hash,
            passport_hash=passport.passport_hash,
            manifest_hash=manifest.manifest_hash,
            catalog_hash=catalog_hash,
            opposing_pair_digest=pair_digest,
            opposing_pair_count=0,
            reason="",
        )
    coverage: dict[str, frozenset[str]] = {}
    for offer in offers:
        coverage[offer.offer_id] = frozenset(
            pair.pair_id
            for pair in pairs
            if _offer_extension(problem, offer, pair.left)
            != _offer_extension(problem, offer, pair.right)
        )
    all_pairs = frozenset(pair_ids)
    uncovered = all_pairs - frozenset().union(*coverage.values()) if coverage else all_pairs
    if uncovered:
        return EnrichmentPlanningResult(
            status=PlanningStatus.NO_SUFFICIENT_PLAN,
            problem_hash=problem.problem_hash,
            passport_hash=passport.passport_hash,
            manifest_hash=manifest.manifest_hash,
            catalog_hash=catalog_hash,
            opposing_pair_digest=pair_digest,
            opposing_pair_count=len(pairs),
            reason=(
                f"declared catalog leaves {len(uncovered)} opposing pairs unseparated; "
                "the interaction remains ESCALATE"
            ),
        )
    offer_ids = tuple(sorted(coverage))
    by_id = {offer.offer_id: offer for offer in offers}

    def materialize_plan(
        candidate: tuple[str, ...],
        *,
        minimality: str,
        pareto_status: str,
        indispensable: tuple[str, ...],
    ) -> VerifiedEnrichmentPlan:
        selected_coverage = {
            offer_id: sorted(coverage[offer_id]) for offer_id in candidate
        }
        covered = frozenset().union(*(coverage[offer_id] for offer_id in candidate))
        burden = sum((by_id[offer_id].burden for offer_id in candidate), BurdenVector())
        consent_subjects = tuple(
            sorted(
                {
                    subject
                    for offer_id in candidate
                    for subject in by_id[offer_id].consent_subjects
                }
            )
        )
        return VerifiedEnrichmentPlan(
            observable_ids=candidate,
            opposing_pair_digest=pair_digest,
            separation_digest=_digest(selected_coverage),
            sufficiency_certificate={
                "pair_count": len(pairs),
                "covered_pair_count": len(covered),
                "coverage_digest": _digest(sorted(covered)),
                "sufficient": True,
            },
            minimality=minimality,
            pareto_status=pareto_status,
            indispensable_observables=indispensable,
            consent_subjects=consent_subjects,
            predicted_envelope_reduction={
                "opposing_pairs_before": len(pairs),
                "opposing_pairs_after": 0,
                "coverage_ppm": 1_000_000,
            },
            burden=burden,
        )

    remaining = set(all_pairs)
    greedy: list[str] = []
    while remaining:
        available = [offer_id for offer_id in offer_ids if offer_id not in greedy]
        selected = min(
            available,
            key=lambda offer_id: (-len(coverage[offer_id] & remaining), offer_id),
        )
        if not coverage[selected] & remaining:
            raise AssertionError("declared sufficient catalog failed greedy coverage")
        greedy.append(selected)
        remaining -= set(coverage[selected])
    greedy_plan = materialize_plan(
        tuple(greedy),
        minimality="unresolved",
        pareto_status="unresolved",
        indispensable=(),
    )

    minimal: list[tuple[str, ...]] = []
    limit = passport.resource_bounds["max_minimal_plans"]
    for size in range(1, len(offer_ids) + 1):
        for candidate in itertools.combinations(offer_ids, size):
            candidate_set = frozenset(candidate)
            if any(frozenset(existing).issubset(candidate_set) for existing in minimal):
                continue
            covered = frozenset().union(*(coverage[offer_id] for offer_id in candidate))
            if covered == all_pairs:
                minimal.append(candidate)
                if len(minimal) > limit:
                    return EnrichmentPlanningResult(
                        status=PlanningStatus.PLANNED,
                        problem_hash=problem.problem_hash,
                        passport_hash=passport.passport_hash,
                        manifest_hash=manifest.manifest_hash,
                        catalog_hash=catalog_hash,
                        opposing_pair_digest=pair_digest,
                        opposing_pair_count=len(pairs),
                        plans=(greedy_plan,),
                        reason=(
                            "complete opposing-pair enumeration verified a sufficient "
                            "deterministic greedy plan; exact minimality search exceeded "
                            "the pinned resource bound"
                        ),
                    )
    indispensable = tuple(sorted(set.intersection(*(set(item) for item in minimal))))
    frontier = _pareto_frontier(minimal, by_id)
    plans: list[VerifiedEnrichmentPlan] = []
    for candidate in minimal:
        plans.append(
            materialize_plan(
                candidate,
                minimality="exact-declared-candidate-space",
                pareto_status="frontier" if candidate in frontier else "dominated",
                indispensable=indispensable,
            )
        )
    plans.sort(key=lambda plan: plan.plan_hash)
    return EnrichmentPlanningResult(
        status=PlanningStatus.PLANNED,
        problem_hash=problem.problem_hash,
        passport_hash=passport.passport_hash,
        manifest_hash=manifest.manifest_hash,
        catalog_hash=catalog_hash,
        opposing_pair_digest=pair_digest,
        opposing_pair_count=len(pairs),
        plans=tuple(plans),
        indispensable_observables=indispensable,
        reason="all target-disagreeing same-reduct pairs are exactly covered",
    )


def verify_enrichment_plan(
    problem: SeamProblem,
    offers: Sequence[ObservableOffer],
    plan: VerifiedEnrichmentPlan,
    *,
    passport: LogicPassport,
    manifest: ConservationManifest,
) -> bool:
    replay = plan_enrichment(
        problem,
        offers,
        passport=passport,
        manifest=manifest,
    )
    return bool(
        replay.status is PlanningStatus.PLANNED
        and plan.plan_hash in {candidate.plan_hash for candidate in replay.plans}
    )


@dataclass(frozen=True)
class EnrichmentRequest:
    problem_hash: str
    result_hash: str
    passport_hash: str
    manifest_hash: str
    insufficiency_certificate: Mapping[str, Any]
    offers: tuple[ObservableOffer, ...]
    plans: tuple[VerifiedEnrichmentPlan, ...]
    requester_authority: Mapping[str, Any]
    schema_version: str = PLANNING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PLANNING_SCHEMA:
            raise InventionError("unsupported EnrichmentRequest schema")
        for name in ("problem_hash", "result_hash", "passport_hash", "manifest_hash"):
            _require_digest(getattr(self, name), f"enrichment_request.{name}")
        if not self.offers or not self.plans:
            raise InventionError("EnrichmentRequest requires offers and verified plans")
        if not isinstance(self.insufficiency_certificate, Mapping):
            raise InventionError("EnrichmentRequest requires an insufficiency certificate")
        if self.insufficiency_certificate.get("kind") != FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY.value:
            raise InventionError("EnrichmentRequest must carry a same-reduct certificate")
        if not isinstance(self.requester_authority, Mapping) or not self.requester_authority:
            raise InventionError("EnrichmentRequest requires requester authority")
        object.__setattr__(self, "offers", tuple(self.offers))
        object.__setattr__(self, "plans", tuple(self.plans))
        object.__setattr__(self, "insufficiency_certificate", dict(self.insufficiency_certificate))
        object.__setattr__(self, "requester_authority", dict(self.requester_authority))

    @property
    def request_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "problem_hash": self.problem_hash,
            "result_hash": self.result_hash,
            "passport_hash": self.passport_hash,
            "manifest_hash": self.manifest_hash,
            "insufficiency_certificate": dict(self.insufficiency_certificate),
            "offers": [offer.to_dict() for offer in self.offers],
            "plans": [plan.to_dict() for plan in self.plans],
            "requester_authority": dict(self.requester_authority),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EnrichmentRequest":
        d = _closed(
            value,
            required={
                "schema_version",
                "problem_hash",
                "result_hash",
                "passport_hash",
                "manifest_hash",
                "insufficiency_certificate",
                "offers",
                "plans",
                "requester_authority",
            },
            where="enrichment_request",
        )
        if not isinstance(d["offers"], list) or not isinstance(d["plans"], list):
            raise InventionError("EnrichmentRequest offers/plans must be arrays")
        return cls(
            schema_version=d["schema_version"],
            problem_hash=d["problem_hash"],
            result_hash=d["result_hash"],
            passport_hash=d["passport_hash"],
            manifest_hash=d["manifest_hash"],
            insufficiency_certificate=d["insufficiency_certificate"],
            offers=tuple(ObservableOffer.from_dict(x) for x in d["offers"]),
            plans=tuple(VerifiedEnrichmentPlan.from_dict(x) for x in d["plans"]),
            requester_authority=d["requester_authority"],
        )


def build_enrichment_request(
    problem: SeamProblem,
    result: SynthesisResult,
    planning: EnrichmentPlanningResult,
    offers: Sequence[ObservableOffer],
    *,
    passport: LogicPassport,
    manifest: ConservationManifest,
    requester_authority: Mapping[str, Any],
) -> EnrichmentRequest:
    if result.problem_hash != problem.problem_hash:
        raise InventionError("synthesis result does not bind the seam problem")
    if result.status not in (SynthesisStatus.PARTIAL, SynthesisStatus.ESCALATE):
        raise InventionError("only information-limited PARTIAL/ESCALATE results may enrich")
    if result.certificate is None or result.certificate.kind is not FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY:
        raise InventionError("enrichment requires a fixed-language non-definability certificate")
    if not verify_failure_certificate(problem, result.certificate):
        raise InventionError("same-reduct insufficiency certificate did not replay")
    expected = plan_enrichment(
        problem,
        offers,
        passport=passport,
        manifest=manifest,
    )
    if (
        planning.status is not PlanningStatus.PLANNED
        or planning.result_hash != expected.result_hash
    ):
        raise InventionError("planning result does not independently replay")
    return EnrichmentRequest(
        problem_hash=problem.problem_hash,
        result_hash=result.result_hash,
        passport_hash=passport.passport_hash,
        manifest_hash=manifest.manifest_hash,
        insufficiency_certificate=result.certificate.to_dict(),
        offers=tuple(offers),
        plans=planning.plans,
        requester_authority=requester_authority,
    )


class ResponseStatus(str, enum.Enum):
    CONSENT = "CONSENT"
    REFUSE = "REFUSE"
    COUNTEROFFER = "COUNTEROFFER"
    PROVIDE = "PROVIDE"


@dataclass(frozen=True)
class ProvidedFact:
    relation: str
    arguments: tuple[str, ...]
    truth: bool
    evidence_class: str
    warrant_ref: str

    def __post_init__(self) -> None:
        if not self.relation or not self.arguments or any(not value for value in self.arguments):
            raise InventionError("ProvidedFact requires a relation and named arguments")
        if not isinstance(self.truth, bool):
            raise InventionError("ProvidedFact.truth must be Boolean")
        if not self.evidence_class:
            raise InventionError("ProvidedFact.evidence_class must be non-empty")
        _require_digest(self.warrant_ref, "provided_fact.warrant_ref")
        object.__setattr__(self, "arguments", tuple(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "arguments": list(self.arguments),
            "truth": self.truth,
            "evidence_class": self.evidence_class,
            "warrant_ref": self.warrant_ref,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProvidedFact":
        d = _closed(
            value,
            required={"relation", "arguments", "truth", "evidence_class", "warrant_ref"},
            where="provided_fact",
        )
        if not isinstance(d["arguments"], list):
            raise InventionError("ProvidedFact.arguments must be an array")
        return cls(
            relation=d["relation"],
            arguments=tuple(d["arguments"]),
            truth=d["truth"],
            evidence_class=d["evidence_class"],
            warrant_ref=d["warrant_ref"],
        )


@dataclass(frozen=True)
class EnrichmentResponse:
    request_hash: str
    responder: str
    status: ResponseStatus
    selected_plan_hash: str | None = None
    provided_facts: tuple[ProvidedFact, ...] = ()
    counteroffers: tuple[ObservableOffer, ...] = ()
    reason: str = ""
    proof: Mapping[str, Any] | None = None
    schema_version: str = PLANNING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PLANNING_SCHEMA or not self.responder:
            raise InventionError("unsupported EnrichmentResponse schema or empty responder")
        _require_digest(self.request_hash, "enrichment_response.request_hash")
        if self.selected_plan_hash is not None:
            _require_digest(self.selected_plan_hash, "enrichment_response.selected_plan_hash")
        facts = tuple(self.provided_facts)
        counteroffers = tuple(self.counteroffers)
        if self.status in (ResponseStatus.CONSENT, ResponseStatus.PROVIDE) and self.selected_plan_hash is None:
            raise InventionError("CONSENT/PROVIDE must select an offered plan")
        if self.status is ResponseStatus.PROVIDE and not facts:
            raise InventionError("PROVIDE must carry at least one warranted fact")
        if self.status is not ResponseStatus.PROVIDE and facts:
            raise InventionError("only PROVIDE may carry facts")
        if self.status is ResponseStatus.COUNTEROFFER and not counteroffers:
            raise InventionError("COUNTEROFFER must carry a replacement offer")
        if self.status is not ResponseStatus.COUNTEROFFER and counteroffers:
            raise InventionError("only COUNTEROFFER may carry counteroffers")
        if self.status is ResponseStatus.REFUSE and not self.reason:
            raise InventionError("REFUSE must carry a reason")
        if self.proof is not None and not isinstance(self.proof, Mapping):
            raise InventionError("EnrichmentResponse.proof must be an object or null")
        object.__setattr__(self, "provided_facts", facts)
        object.__setattr__(self, "counteroffers", counteroffers)
        if self.proof is not None:
            object.__setattr__(self, "proof", dict(self.proof))

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_hash": self.request_hash,
            "responder": self.responder,
            "status": self.status.value,
            "selected_plan_hash": self.selected_plan_hash,
            "provided_facts": [fact.to_dict() for fact in self.provided_facts],
            "counteroffers": [offer.to_dict() for offer in self.counteroffers],
            "reason": self.reason,
        }

    @property
    def response_hash(self) -> str:
        return _digest(self.unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "proof": dict(self.proof) if self.proof else None}

    @classmethod
    def from_dict(cls, value: Any) -> "EnrichmentResponse":
        d = _closed(
            value,
            required={
                "schema_version",
                "request_hash",
                "responder",
                "status",
                "selected_plan_hash",
                "provided_facts",
                "counteroffers",
                "reason",
                "proof",
            },
            where="enrichment_response",
        )
        if not isinstance(d["provided_facts"], list) or not isinstance(d["counteroffers"], list):
            raise InventionError("EnrichmentResponse facts/counteroffers must be arrays")
        return cls(
            schema_version=d["schema_version"],
            request_hash=d["request_hash"],
            responder=d["responder"],
            status=ResponseStatus(d["status"]),
            selected_plan_hash=d["selected_plan_hash"],
            provided_facts=tuple(ProvidedFact.from_dict(x) for x in d["provided_facts"]),
            counteroffers=tuple(ObservableOffer.from_dict(x) for x in d["counteroffers"]),
            reason=d["reason"],
            proof=d["proof"],
        )


def sign_enrichment_response(
    response: EnrichmentResponse,
    signer: LocalEd25519Signer,
) -> EnrichmentResponse:
    if response.responder != signer.issuer:
        raise InventionError("response.responder must equal the signing identity")
    unsigned = EnrichmentResponse(**{**response.__dict__, "proof": None})
    return EnrichmentResponse(
        **{
            **unsigned.__dict__,
            "proof": signer.sign_domain(RESPONSE_PROOF_PURPOSE, unsigned.response_hash),
        }
    )


def verify_enrichment_response(
    request: EnrichmentRequest,
    response: EnrichmentResponse,
) -> bool:
    if response.request_hash != request.request_hash or response.proof is None:
        return False
    if response.selected_plan_hash is not None and response.selected_plan_hash not in {
        plan.plan_hash for plan in request.plans
    }:
        return False
    verification = verify_proof_domain(
        RESPONSE_PROOF_PURPOSE,
        response.response_hash,
        dict(response.proof),
    )
    return bool(verification.authentic and verification.issuer == response.responder)


def mint_enrichment_request_receipt(
    request: EnrichmentRequest,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    from bulla.action_receipt import build_action_receipt

    return build_action_receipt(
        action={
            "type": "bulla.enrich.request",
            "subject": {
                "request_hash": request.request_hash,
                "problem_hash": request.problem_hash,
                "result_hash": request.result_hash,
                "plan_hashes": [plan.plan_hash for plan in request.plans],
            },
        },
        diagnostic_ref={"status": "reference", "ref": request.request_hash},
        envelope=envelope,
        evidence_refs=(
            {
                "name": "same_reduct_insufficiency",
                "hash": _digest(request.insufficiency_certificate),
                "grounding": "execution_verified",
            },
            {
                "name": "logic_passport",
                "hash": request.passport_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "conservation_manifest",
                "hash": request.manifest_hash,
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )


def mint_enrichment_response_receipt(
    response: EnrichmentResponse,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    from bulla.action_receipt import build_action_receipt

    if response.proof is None:
        raise InventionError("response receipt requires a signed EnrichmentResponse")
    return build_action_receipt(
        action={
            "type": "bulla.enrich.respond",
            "subject": {
                "request_hash": response.request_hash,
                "response_hash": response.response_hash,
                "status": response.status.value,
                "selected_plan_hash": response.selected_plan_hash,
            },
        },
        diagnostic_ref={"status": "reference", "ref": response.response_hash},
        envelope=envelope,
        evidence_refs=(
            {
                "name": "enrichment_response_proof",
                "hash": _digest(response.proof),
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )
