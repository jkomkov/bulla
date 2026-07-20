"""Proof-carrying semantic invention over the finite FRSL-1 fragment.

The reference backend has two exits:

* emit an independently rechecked definition or partial envelope; or
* retain a concrete same-reduct/different-target model pair.

Resource exhaustion is a third operational state, but never a mathematical
certificate.  It is reported as INDETERMINATE.

This is a research surface.  It is intentionally absent from bulla.__init__.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from bulla._canonical import CANON_VERSION, canonical_json
from bulla.experimental.frsl import (
    LANGUAGE,
    SCHEMA_VERSION,
    FRSLError,
    Formula,
    RelationDecl,
    Signature,
    Structure,
    atom,
    canonical_hash,
    conjunction,
    constant,
    disjunction,
    enumerate_structures,
    evaluate,
    falsity,
    formula_relations,
    formula_size,
    negate,
    normalize_formula,
    normalize_structure,
    relation_reduct,
    structure_to_dict,
    truth,
    validate_formula,
    variable,
)

VERIFIER_ID = "bulla.experimental.invention.reference"
VERIFIER_VERSION = "0.1-experimental"
RESULT_SCHEMA_VERSION = "0.2-experimental"
MAX_FALLBACK_AST_NODES = 4096


class InventionError(ValueError):
    """Raised when an invention document is malformed."""


class GateStatus(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class SynthesisStatus(str, enum.Enum):
    COMPILED = "COMPILED"
    PARTIAL = "PARTIAL"
    ESCALATE = "ESCALATE"
    CHOICE_REQUIRED = "CHOICE_REQUIRED"
    INDETERMINATE = "INDETERMINATE"
    INVALID_INPUT = "INVALID_INPUT"


class FailureKind(str, enum.Enum):
    TOPOLOGY_OBSTRUCTION = "topology_obstruction"
    FIXED_LANGUAGE_NON_DEFINABILITY = "fixed_language_non_definability"
    NON_CONSERVATIVITY = "non_conservativity"
    NON_UNIQUE_MINIMUM = "non_unique_minimum"
    RESOURCE_LIMIT = "resource_limit"
    INVALID_PROBLEM = "invalid_problem"


class ResultCause(str, enum.Enum):
    TOTAL_DEFINITION = "total_definition"
    PARTIAL_DEFINITION = "partial_definition"
    TOPOLOGY_OBSTRUCTION = "topology_obstruction"
    FIXED_LANGUAGE_NON_DEFINABILITY = "fixed_language_non_definability"
    NON_CONSERVATIVITY = "non_conservativity"
    NON_UNIQUE_MINIMUM = "non_unique_minimum"
    RESOURCE_LIMIT = "resource_limit"
    INVALID_PROBLEM = "invalid_problem"


class NextActionKind(str, enum.Enum):
    APPLY = "apply"
    SUPPLY_EVIDENCE = "supply_evidence"
    REPAIR_OVERLAP = "repair_overlap"
    SELECT_WITH_AUTHORITY = "select_with_authority"
    EXTEND_RESOURCE_BUDGET = "extend_resource_budget"
    CORRECT_INPUT = "correct_input"


class EnrichmentAxis(str, enum.Enum):
    EVIDENCE = "evidence"
    LANGUAGE = "language"
    POLICY = "policy"
    AUTHORITY = "authority"
    RECOURSE = "recourse"
    TOPOLOGY = "topology"
    RESOURCE = "resource"


class ChoiceKind(str, enum.Enum):
    ECONOMIC = "economic"
    NORMATIVE = "normative"


@dataclass(frozen=True)
class NextAction:
    kind: NextActionKind
    statement: str
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.statement:
            raise InventionError("next_action.statement must be non-empty")
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        if any(not isinstance(x, str) or not x for x in self.artifact_refs):
            raise InventionError("next_action.artifact_refs must be non-empty strings")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "statement": self.statement,
            "artifact_refs": list(self.artifact_refs),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "NextAction":
        d = _closed(
            value,
            required={"kind", "statement", "artifact_refs"},
            optional=set(),
            where="next_action",
        )
        if not isinstance(d["artifact_refs"], list):
            raise InventionError("next_action.artifact_refs must be a list")
        return cls(
            kind=NextActionKind(d["kind"]),
            statement=d["statement"],
            artifact_refs=tuple(d["artifact_refs"]),
        )


@dataclass(frozen=True)
class EnrichmentPlan:
    axis: EnrichmentAxis
    statement: str
    requirements: tuple[Mapping[str, Any], ...]
    cost: Mapping[str, int]
    minimality: str = "unresolved"

    def __post_init__(self) -> None:
        if not self.statement:
            raise InventionError("enrichment_plan.statement must be non-empty")
        object.__setattr__(self, "requirements", tuple(self.requirements))
        if any(not isinstance(x, Mapping) for x in self.requirements):
            raise InventionError("enrichment_plan.requirements must be objects")
        if not isinstance(self.cost, Mapping) or any(
            not isinstance(k, str)
            or not k
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 0
            for k, v in self.cost.items()
        ):
            raise InventionError("enrichment_plan.cost must map names to non-negative integers")
        if self.minimality not in ("exact-declared-candidate-space", "unresolved"):
            raise InventionError("enrichment_plan.minimality is unknown")

    @property
    def plan_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "axis": self.axis.value,
            "statement": self.statement,
            "requirements": [dict(x) for x in self.requirements],
            "cost": dict(self.cost),
            "minimality": self.minimality,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EnrichmentPlan":
        d = _closed(
            value,
            required={"axis", "statement", "requirements", "cost", "minimality"},
            optional=set(),
            where="enrichment_plan",
        )
        if not isinstance(d["requirements"], list) or not isinstance(d["cost"], dict):
            raise InventionError("enrichment_plan requirements/cost have the wrong type")
        return cls(
            axis=EnrichmentAxis(d["axis"]),
            statement=d["statement"],
            requirements=tuple(d["requirements"]),
            cost=d["cost"],
            minimality=d["minimality"],
        )


@dataclass(frozen=True)
class ChoiceClass:
    class_id: str
    package_hashes: tuple[str, ...]
    protected_behavior_hash: str
    cost_vector: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_hashes", tuple(self.package_hashes))
        if not self.class_id.startswith("sha256:"):
            raise InventionError("choice_class.class_id must be a sha256 digest")
        if not self.package_hashes or any(
            not isinstance(x, str) or not x.startswith("sha256:")
            for x in self.package_hashes
        ):
            raise InventionError("choice_class.package_hashes must contain digests")
        if not self.protected_behavior_hash.startswith("sha256:"):
            raise InventionError("choice_class.protected_behavior_hash must be a digest")
        if not isinstance(self.cost_vector, Mapping) or any(
            not isinstance(k, str)
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 0
            for k, v in self.cost_vector.items()
        ):
            raise InventionError("choice_class.cost_vector is invalid")

    def to_dict(self) -> dict:
        return {
            "class_id": self.class_id,
            "package_hashes": list(self.package_hashes),
            "protected_behavior_hash": self.protected_behavior_hash,
            "cost_vector": dict(self.cost_vector),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ChoiceClass":
        d = _closed(
            value,
            required={
                "class_id",
                "package_hashes",
                "protected_behavior_hash",
                "cost_vector",
            },
            optional=set(),
            where="choice_class",
        )
        if not isinstance(d["package_hashes"], list) or not isinstance(d["cost_vector"], dict):
            raise InventionError("choice_class package_hashes/cost_vector have the wrong type")
        return cls(
            class_id=d["class_id"],
            package_hashes=tuple(d["package_hashes"]),
            protected_behavior_hash=d["protected_behavior_hash"],
            cost_vector=d["cost_vector"],
        )


@dataclass(frozen=True)
class ChoiceAnalysis:
    kind: ChoiceKind
    classes: tuple[ChoiceClass, ...]
    cost_order: tuple[str, ...]
    selector_authority: Mapping[str, Any]
    disagreement_witness: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", tuple(self.classes))
        object.__setattr__(self, "cost_order", tuple(self.cost_order))
        if len(self.classes) < 2:
            raise InventionError("choice_analysis requires at least two classes")
        if len({x.class_id for x in self.classes}) != len(self.classes):
            raise InventionError("choice_analysis class ids must be unique")
        package_hashes = [h for item in self.classes for h in item.package_hashes]
        if len(package_hashes) != len(set(package_hashes)):
            raise InventionError("a package cannot belong to two choice classes")
        if not self.cost_order or len(self.cost_order) != len(set(self.cost_order)):
            raise InventionError("choice_analysis.cost_order must be unique and non-empty")
        if not isinstance(self.selector_authority, Mapping) or not self.selector_authority:
            raise InventionError("choice_analysis.selector_authority must be declared")
        if not isinstance(self.disagreement_witness, Mapping):
            raise InventionError("choice_analysis.disagreement_witness must be an object")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "classes": [x.to_dict() for x in self.classes],
            "cost_order": list(self.cost_order),
            "selector_authority": dict(self.selector_authority),
            "disagreement_witness": dict(self.disagreement_witness),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ChoiceAnalysis":
        d = _closed(
            value,
            required={
                "kind",
                "classes",
                "cost_order",
                "selector_authority",
                "disagreement_witness",
            },
            optional=set(),
            where="choice_analysis",
        )
        if not isinstance(d["classes"], list) or not isinstance(d["cost_order"], list):
            raise InventionError("choice_analysis classes/cost_order have the wrong type")
        if not isinstance(d["selector_authority"], dict) or not isinstance(
            d["disagreement_witness"], dict
        ):
            raise InventionError("choice_analysis authority/witness have the wrong type")
        return cls(
            kind=ChoiceKind(d["kind"]),
            classes=tuple(ChoiceClass.from_dict(x) for x in d["classes"]),
            cost_order=tuple(d["cost_order"]),
            selector_authority=d["selector_authority"],
            disagreement_witness=d["disagreement_witness"],
        )


def _closed(value: Any, *, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise InventionError(f"{where} must be an object")
    missing = required - set(value)
    if missing:
        raise InventionError(f"{where} is missing required keys {sorted(missing)}")
    unknown = set(value) - required - optional
    if unknown:
        raise InventionError(f"{where} has unknown keys {sorted(unknown)}")
    return value


@dataclass(frozen=True)
class LocalTheory:
    owner: str
    constraints: tuple[Formula, ...]

    def __post_init__(self) -> None:
        if not self.owner:
            raise InventionError("local_theory.owner must be non-empty")
        object.__setattr__(self, "constraints", tuple(self.constraints))

    def to_dict(self) -> dict:
        return {"owner": self.owner, "constraints": list(self.constraints)}

    @classmethod
    def from_dict(cls, value: Any) -> "LocalTheory":
        d = _closed(
            value,
            required={"owner", "constraints"},
            optional=set(),
            where="local_theory",
        )
        if not isinstance(d["constraints"], list):
            raise InventionError("local_theory.constraints must be a list")
        return cls(owner=d["owner"], constraints=tuple(d["constraints"]))


@dataclass(frozen=True)
class OverlapMap:
    left_owner: str
    right_owner: str
    left_relation: str
    right_relation: str
    argument_map: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.left_owner or not self.right_owner:
            raise InventionError("overlap owners must be non-empty")
        if not self.left_relation or not self.right_relation:
            raise InventionError("overlap relations must be non-empty")
        object.__setattr__(self, "argument_map", tuple(self.argument_map))

    def to_dict(self) -> dict:
        return {
            "left_owner": self.left_owner,
            "right_owner": self.right_owner,
            "left_relation": self.left_relation,
            "right_relation": self.right_relation,
            "argument_map": list(self.argument_map),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "OverlapMap":
        d = _closed(
            value,
            required={
                "left_owner",
                "right_owner",
                "left_relation",
                "right_relation",
                "argument_map",
            },
            optional=set(),
            where="overlap_map",
        )
        if not isinstance(d["argument_map"], list) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in d["argument_map"]
        ):
            raise InventionError("overlap_map.argument_map must be a list of integers")
        return cls(
            left_owner=d["left_owner"],
            right_owner=d["right_owner"],
            left_relation=d["left_relation"],
            right_relation=d["right_relation"],
            argument_map=tuple(d["argument_map"]),
        )


@dataclass(frozen=True)
class SynthesisPolicy:
    reference_max_ground_atoms: int = 16
    reference_max_models: int = 65536
    max_candidate_atoms: int = 10
    max_minimal_alternatives: int = 16
    exact_minimality: bool = True
    require_unique_minimum: bool = True

    def __post_init__(self) -> None:
        for name in (
            "reference_max_ground_atoms",
            "reference_max_models",
            "max_candidate_atoms",
            "max_minimal_alternatives",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InventionError(f"synthesis_policy.{name} must be a positive integer")
        if self.max_minimal_alternatives < 2:
            raise InventionError(
                "synthesis_policy.max_minimal_alternatives must be at least 2 "
                "so a governance fork can retain both witnesses"
            )
        if not isinstance(self.exact_minimality, bool) or not isinstance(
            self.require_unique_minimum, bool
        ):
            raise InventionError("synthesis policy flags must be boolean")
        if not self.require_unique_minimum:
            raise InventionError(
                "require_unique_minimum must be true in this schema; "
                "institutional choice cannot be silently delegated to the engine"
            )

    def to_dict(self) -> dict:
        return {
            "reference_max_ground_atoms": self.reference_max_ground_atoms,
            "reference_max_models": self.reference_max_models,
            "max_candidate_atoms": self.max_candidate_atoms,
            "max_minimal_alternatives": self.max_minimal_alternatives,
            "exact_minimality": self.exact_minimality,
            "require_unique_minimum": self.require_unique_minimum,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SynthesisPolicy":
        d = _closed(
            value,
            required=set(),
            optional={
                "reference_max_ground_atoms",
                "reference_max_models",
                "max_candidate_atoms",
                "max_minimal_alternatives",
                "exact_minimality",
                "require_unique_minimum",
            },
            where="synthesis_policy",
        )
        return cls(**d)


@dataclass(frozen=True)
class SeamProblem:
    problem_id: str
    signature: Signature
    local_theories: tuple[LocalTheory, ...]
    overlap_maps: tuple[OverlapMap, ...]
    target_predicate: str
    shared_vocabulary: tuple[str, ...]
    protected_signatures: Mapping[str, tuple[str, ...]]
    requested_judgment: str
    synthesis_policy: SynthesisPolicy = field(default_factory=SynthesisPolicy)
    authority: Mapping[str, Any] = field(default_factory=dict)
    scope: Mapping[str, Any] = field(default_factory=dict)
    expiry: str | None = None
    evidence_requirements: tuple[str, ...] = ()
    language: str = LANGUAGE
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.language != LANGUAGE:
            raise InventionError(f"language must be {LANGUAGE!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise InventionError(f"schema_version must be {SCHEMA_VERSION!r}")
        if not self.problem_id:
            raise InventionError("problem_id must be non-empty")
        if self.target_predicate not in self.signature.relations:
            raise InventionError("target_predicate must name a declared relation")
        if self.target_predicate in self.shared_vocabulary:
            raise InventionError("target_predicate cannot appear in shared_vocabulary")
        shared = tuple(self.shared_vocabulary)
        if len(set(shared)) != len(shared):
            raise InventionError("shared_vocabulary contains duplicates")
        unknown_shared = set(shared) - set(self.signature.relations)
        if unknown_shared:
            raise InventionError(f"shared_vocabulary has unknown relations {sorted(unknown_shared)}")
        theories = tuple(self.local_theories)
        if not theories:
            raise InventionError("at least one local_theory is required")
        owners = [x.owner for x in theories]
        if len(set(owners)) != len(owners):
            raise InventionError("local_theory owners must be unique")
        for theory in theories:
            for i, constraint in enumerate(theory.constraints):
                validate_formula(
                    constraint,
                    signature=self.signature,
                    where=f"local_theories[{theory.owner}].constraints[{i}]",
                )
        protected: dict[str, tuple[str, ...]] = {}
        for owner, relations in self.protected_signatures.items():
            if owner not in owners:
                raise InventionError(f"protected_signatures has unknown owner {owner!r}")
            rels = tuple(relations)
            if len(set(rels)) != len(rels):
                raise InventionError(f"protected_signatures.{owner} contains duplicates")
            unknown = set(rels) - set(self.signature.relations)
            if unknown:
                raise InventionError(
                    f"protected_signatures.{owner} has unknown relations {sorted(unknown)}"
                )
            if self.target_predicate in rels:
                raise InventionError("target_predicate cannot be protected")
            protected[owner] = rels
        for owner in owners:
            protected.setdefault(owner, ())
        for overlap in self.overlap_maps:
            if overlap.left_owner not in owners or overlap.right_owner not in owners:
                raise InventionError("overlap_map references an unknown owner")
            left = self.signature.relations.get(overlap.left_relation)
            right = self.signature.relations.get(overlap.right_relation)
            if left is None or right is None:
                raise InventionError("overlap_map references an unknown relation")
            if len(overlap.argument_map) != left.arity:
                raise InventionError("overlap_map.argument_map length must equal left arity")
            if sorted(overlap.argument_map) != list(range(right.arity)):
                raise InventionError("overlap_map.argument_map must be a permutation")
            for left_i, right_i in enumerate(overlap.argument_map):
                if left.sorts[left_i] != right.sorts[right_i]:
                    raise InventionError("overlap_map relates arguments of different sorts")
        if self.requested_judgment not in ("boolean", "rely_refuse_escalate"):
            raise InventionError(
                "requested_judgment must be 'boolean' or 'rely_refuse_escalate'"
            )
        if not isinstance(self.authority, Mapping) or not isinstance(self.scope, Mapping):
            raise InventionError("authority and scope must be objects")
        if any(not isinstance(x, str) or not x for x in self.evidence_requirements):
            raise InventionError("evidence_requirements must contain non-empty strings")
        object.__setattr__(self, "local_theories", theories)
        object.__setattr__(self, "overlap_maps", tuple(self.overlap_maps))
        object.__setattr__(self, "shared_vocabulary", shared)
        object.__setattr__(self, "protected_signatures", protected)
        object.__setattr__(self, "authority", dict(self.authority))
        object.__setattr__(self, "scope", dict(self.scope))
        object.__setattr__(self, "evidence_requirements", tuple(self.evidence_requirements))

    @property
    def target_decl(self) -> RelationDecl:
        return self.signature.relations[self.target_predicate]

    @property
    def target_variables(self) -> Mapping[str, str]:
        return {f"x{i}": sort for i, sort in enumerate(self.target_decl.sorts)}

    @property
    def problem_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "problem_id": self.problem_id,
            "signature": self.signature.to_dict(),
            "local_theories": [x.to_dict() for x in self.local_theories],
            "overlap_maps": [x.to_dict() for x in self.overlap_maps],
            "target_predicate": self.target_predicate,
            "shared_vocabulary": list(self.shared_vocabulary),
            "protected_signatures": {
                owner: list(self.protected_signatures[owner])
                for owner in sorted(self.protected_signatures)
            },
            "requested_judgment": self.requested_judgment,
            "synthesis_policy": self.synthesis_policy.to_dict(),
            "authority": dict(self.authority),
            "scope": dict(self.scope),
            "expiry": self.expiry,
            "evidence_requirements": list(self.evidence_requirements),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SeamProblem":
        d = _closed(
            value,
            required={
                "schema_version",
                "language",
                "problem_id",
                "signature",
                "local_theories",
                "overlap_maps",
                "target_predicate",
                "shared_vocabulary",
                "protected_signatures",
                "requested_judgment",
                "synthesis_policy",
                "authority",
                "scope",
                "expiry",
                "evidence_requirements",
            },
            optional=set(),
            where="seam_problem",
        )
        if not isinstance(d["local_theories"], list):
            raise InventionError("local_theories must be a list")
        if not isinstance(d["overlap_maps"], list):
            raise InventionError("overlap_maps must be a list")
        if not isinstance(d["shared_vocabulary"], list):
            raise InventionError("shared_vocabulary must be a list")
        if not isinstance(d["protected_signatures"], dict):
            raise InventionError("protected_signatures must be an object")
        if not isinstance(d["evidence_requirements"], list):
            raise InventionError("evidence_requirements must be a list")
        return cls(
            schema_version=d["schema_version"],
            language=d["language"],
            problem_id=d["problem_id"],
            signature=Signature.from_dict(d["signature"]),
            local_theories=tuple(LocalTheory.from_dict(x) for x in d["local_theories"]),
            overlap_maps=tuple(OverlapMap.from_dict(x) for x in d["overlap_maps"]),
            target_predicate=d["target_predicate"],
            shared_vocabulary=tuple(d["shared_vocabulary"]),
            protected_signatures={
                owner: tuple(relations)
                for owner, relations in d["protected_signatures"].items()
            },
            requested_judgment=d["requested_judgment"],
            synthesis_policy=SynthesisPolicy.from_dict(d["synthesis_policy"]),
            authority=d["authority"],
            scope=d["scope"],
            expiry=d["expiry"],
            evidence_requirements=tuple(d["evidence_requirements"]),
        )


@dataclass(frozen=True)
class GateReport:
    gluing: GateStatus
    conservativity: GateStatus
    definability: GateStatus
    preserved_refusals: GateStatus
    minimality: GateStatus
    receipt_binding: GateStatus
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError(
            "GateReport has no truth value; inspect each named gate explicitly"
        )

    @property
    def ok(self) -> bool:
        return all(
            gate in (GateStatus.PASS, GateStatus.NOT_APPLICABLE)
            for gate in (
                self.gluing,
                self.conservativity,
                self.definability,
                self.preserved_refusals,
                self.minimality,
                self.receipt_binding,
            )
        )

    def to_dict(self) -> dict:
        return {
            "gluing": self.gluing.value,
            "conservativity": self.conservativity.value,
            "definability": self.definability.value,
            "preserved_refusals": self.preserved_refusals.value,
            "minimality": self.minimality.value,
            "receipt_binding": self.receipt_binding.value,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "GateReport":
        d = _closed(
            value,
            required={
                "gluing",
                "conservativity",
                "definability",
                "preserved_refusals",
                "minimality",
                "receipt_binding",
                "reasons",
            },
            optional=set(),
            where="gate_report",
        )
        if not isinstance(d["reasons"], list):
            raise InventionError("gate_report.reasons must be a list")
        return cls(
            gluing=GateStatus(d["gluing"]),
            conservativity=GateStatus(d["conservativity"]),
            definability=GateStatus(d["definability"]),
            preserved_refusals=GateStatus(d["preserved_refusals"]),
            minimality=GateStatus(d["minimality"]),
            receipt_binding=GateStatus(d["receipt_binding"]),
            reasons=tuple(d["reasons"]),
        )


@dataclass(frozen=True)
class FailureCertificate:
    kind: FailureKind
    statement: str
    witness: Mapping[str, Any]
    backend: str = "exhaustive-reference"
    complete_within_bound: bool = False

    def __post_init__(self) -> None:
        if not self.statement:
            raise InventionError("failure_certificate.statement must be non-empty")
        if not isinstance(self.witness, Mapping):
            raise InventionError("failure_certificate.witness must be an object")
        if not isinstance(self.complete_within_bound, bool):
            raise InventionError(
                "failure_certificate.complete_within_bound must be boolean"
            )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "statement": self.statement,
            "witness": dict(self.witness),
            "backend": self.backend,
            "complete_within_bound": self.complete_within_bound,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FailureCertificate":
        d = _closed(
            value,
            required={
                "kind",
                "statement",
                "witness",
                "backend",
                "complete_within_bound",
            },
            optional=set(),
            where="failure_certificate",
        )
        if not isinstance(d["witness"], dict):
            raise InventionError("failure_certificate.witness must be an object")
        return cls(
            kind=FailureKind(d["kind"]),
            statement=d["statement"],
            witness=d["witness"],
            backend=d["backend"],
            complete_within_bound=d["complete_within_bound"],
        )


@dataclass(frozen=True)
class PredicatePackage:
    problem_hash: str
    mode: str
    definition: Formula | None
    rely_when: Formula | None
    refuse_when: Formula | None
    local_definitions: Mapping[str, Formula]
    bridge_constraints: tuple[Mapping[str, Any], ...]
    evidence_requirements: tuple[str, ...]
    protected_signature_pins: Mapping[str, str]
    verifier: Mapping[str, Any]
    authority: Mapping[str, Any]
    scope: Mapping[str, Any]
    expiry: str | None
    cost: Mapping[str, Any]
    proof_references: tuple[Mapping[str, Any], ...]
    language: str = LANGUAGE
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InventionError(
                f"predicate_package.schema_version must be {SCHEMA_VERSION!r}"
            )
        if self.language != LANGUAGE:
            raise InventionError(f"predicate_package.language must be {LANGUAGE!r}")
        if self.mode not in ("full", "partial"):
            raise InventionError("predicate_package.mode must be 'full' or 'partial'")
        if self.mode == "full" and self.definition is None:
            raise InventionError("full package requires definition")
        if self.mode == "partial" and (self.rely_when is None or self.refuse_when is None):
            raise InventionError("partial package requires rely_when and refuse_when")

    @property
    def package_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "problem_hash": self.problem_hash,
            "mode": self.mode,
            "definition": self.definition,
            "rely_when": self.rely_when,
            "refuse_when": self.refuse_when,
            "local_definitions": {
                owner: self.local_definitions[owner]
                for owner in sorted(self.local_definitions)
            },
            "bridge_constraints": [dict(x) for x in self.bridge_constraints],
            "evidence_requirements": list(self.evidence_requirements),
            "protected_signature_pins": {
                owner: self.protected_signature_pins[owner]
                for owner in sorted(self.protected_signature_pins)
            },
            "verifier": dict(self.verifier),
            "authority": dict(self.authority),
            "scope": dict(self.scope),
            "expiry": self.expiry,
            "cost": dict(self.cost),
            "proof_references": [dict(x) for x in self.proof_references],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PredicatePackage":
        d = _closed(
            value,
            required={
                "schema_version",
                "language",
                "problem_hash",
                "mode",
                "definition",
                "rely_when",
                "refuse_when",
                "local_definitions",
                "bridge_constraints",
                "evidence_requirements",
                "protected_signature_pins",
                "verifier",
                "authority",
                "scope",
                "expiry",
                "cost",
                "proof_references",
            },
            optional=set(),
            where="predicate_package",
        )
        for name in (
            "local_definitions",
            "protected_signature_pins",
            "verifier",
            "authority",
            "scope",
            "cost",
        ):
            if not isinstance(d[name], dict):
                raise InventionError(f"predicate_package.{name} must be an object")
        for name in ("bridge_constraints", "evidence_requirements", "proof_references"):
            if not isinstance(d[name], list):
                raise InventionError(f"predicate_package.{name} must be a list")
        return cls(
            schema_version=d["schema_version"],
            language=d["language"],
            problem_hash=d["problem_hash"],
            mode=d["mode"],
            definition=d["definition"],
            rely_when=d["rely_when"],
            refuse_when=d["refuse_when"],
            local_definitions=d["local_definitions"],
            bridge_constraints=tuple(d["bridge_constraints"]),
            evidence_requirements=tuple(d["evidence_requirements"]),
            protected_signature_pins=d["protected_signature_pins"],
            verifier=d["verifier"],
            authority=d["authority"],
            scope=d["scope"],
            expiry=d["expiry"],
            cost=d["cost"],
            proof_references=tuple(d["proof_references"]),
        )


@dataclass(frozen=True)
class SynthesisResult:
    status: SynthesisStatus
    problem_hash: str
    gate_report: GateReport
    package: PredicatePackage | None = None
    certificate: FailureCertificate | None = None
    alternatives: tuple[PredicatePackage, ...] = ()
    backend: str = "exhaustive-reference"
    verifier: Mapping[str, Any] = field(default_factory=dict)
    cause: ResultCause | None = None
    next_actions: tuple[NextAction, ...] = ()
    choice_analysis: ChoiceAnalysis | None = None
    enrichment_plans: tuple[EnrichmentPlan, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "alternatives", tuple(self.alternatives))
        object.__setattr__(self, "next_actions", tuple(self.next_actions))
        object.__setattr__(self, "enrichment_plans", tuple(self.enrichment_plans))
        if self.cause is None:
            object.__setattr__(self, "cause", _result_cause(self.status, self.certificate))
        elif self.cause is not _result_cause(self.status, self.certificate):
            raise InventionError("result cause is incompatible with status/certificate")
        if not self.next_actions:
            object.__setattr__(
                self,
                "next_actions",
                _default_next_actions(self.status, self.certificate),
            )
        if not self.enrichment_plans:
            object.__setattr__(
                self,
                "enrichment_plans",
                _default_enrichment_plans(self.status, self.certificate),
            )
        if self.status in (SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL) and self.package is None:
            raise InventionError(f"{self.status.value} result requires a package")
        if self.package is not None and self.package.problem_hash != self.problem_hash:
            raise InventionError("result package does not bind result.problem_hash")
        if any(x.problem_hash != self.problem_hash for x in self.alternatives):
            raise InventionError("result alternative does not bind result.problem_hash")
        if self.status is SynthesisStatus.COMPILED and (
            self.certificate is not None or self.alternatives
        ):
            raise InventionError("COMPILED result cannot carry a failure or choice exit")
        if self.status is SynthesisStatus.PARTIAL and self.certificate is None:
            raise InventionError("PARTIAL result requires a residual certificate")
        if self.status is SynthesisStatus.PARTIAL and (
            self.certificate.kind is not FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY
            or self.alternatives
        ):
            raise InventionError(
                "PARTIAL result requires only a fixed-language residual certificate"
            )
        if self.status == SynthesisStatus.CHOICE_REQUIRED and len(self.alternatives) < 2:
            raise InventionError("CHOICE_REQUIRED requires at least two alternatives")
        if self.status is SynthesisStatus.CHOICE_REQUIRED and (
            self.certificate is None
            or self.certificate.kind is not FailureKind.NON_UNIQUE_MINIMUM
        ):
            raise InventionError(
                "CHOICE_REQUIRED requires a non-unique-minimum certificate"
            )
        if self.status is SynthesisStatus.CHOICE_REQUIRED and self.package is not None:
            raise InventionError("CHOICE_REQUIRED cannot silently select a package")
        if self.status is SynthesisStatus.CHOICE_REQUIRED and self.choice_analysis is None:
            raise InventionError("CHOICE_REQUIRED requires a typed choice analysis")
        if self.status is not SynthesisStatus.CHOICE_REQUIRED and self.choice_analysis is not None:
            raise InventionError("choice_analysis is only valid for CHOICE_REQUIRED")
        if self.choice_analysis is not None:
            analyzed = {
                package_hash
                for choice_class in self.choice_analysis.classes
                for package_hash in choice_class.package_hashes
            }
            if analyzed != {x.package_hash for x in self.alternatives}:
                raise InventionError(
                    "choice_analysis classes must partition exactly the offered alternatives"
                )
        if self.status in (
            SynthesisStatus.ESCALATE,
            SynthesisStatus.INDETERMINATE,
            SynthesisStatus.INVALID_INPUT,
        ) and self.certificate is None:
            raise InventionError(f"{self.status.value} result requires a certificate")
        if self.status is SynthesisStatus.ESCALATE and (
            self.package is not None
            or self.alternatives
            or self.certificate.kind
            not in (
                FailureKind.TOPOLOGY_OBSTRUCTION,
                FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY,
                FailureKind.NON_CONSERVATIVITY,
            )
        ):
            raise InventionError("ESCALATE result has an incompatible exit artifact")
        if self.status is SynthesisStatus.INDETERMINATE and (
            self.package is not None
            or self.alternatives
            or self.certificate.kind is not FailureKind.RESOURCE_LIMIT
        ):
            raise InventionError("INDETERMINATE result requires only a resource limit")
        if self.status is SynthesisStatus.INVALID_INPUT and (
            self.package is not None
            or self.alternatives
            or self.certificate.kind is not FailureKind.INVALID_PROBLEM
        ):
            raise InventionError("INVALID_INPUT result requires only an invalid-problem exit")

    @property
    def result_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": self.status.value,
            "cause": self.cause.value,
            "problem_hash": self.problem_hash,
            "gate_report": self.gate_report.to_dict(),
            "package": self.package.to_dict() if self.package else None,
            "certificate": self.certificate.to_dict() if self.certificate else None,
            "alternatives": [x.to_dict() for x in self.alternatives],
            "backend": self.backend,
            "verifier": dict(self.verifier),
            "next_actions": [x.to_dict() for x in self.next_actions],
            "choice_analysis": (
                self.choice_analysis.to_dict() if self.choice_analysis is not None else None
            ),
            "enrichment_plans": [x.to_dict() for x in self.enrichment_plans],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SynthesisResult":
        d = _closed(
            value,
            required={
                "schema_version",
                "status",
                "cause",
                "problem_hash",
                "gate_report",
                "package",
                "certificate",
                "alternatives",
                "backend",
                "verifier",
                "next_actions",
                "choice_analysis",
                "enrichment_plans",
            },
            optional=set(),
            where="synthesis_result",
        )
        if d["schema_version"] != RESULT_SCHEMA_VERSION:
            raise InventionError("unknown synthesis_result schema_version")
        if (
            not isinstance(d["alternatives"], list)
            or not isinstance(d["verifier"], dict)
            or not isinstance(d["next_actions"], list)
            or not isinstance(d["enrichment_plans"], list)
        ):
            raise InventionError("invalid synthesis_result alternatives or verifier")
        return cls(
            status=SynthesisStatus(d["status"]),
            cause=ResultCause(d["cause"]),
            problem_hash=d["problem_hash"],
            gate_report=GateReport.from_dict(d["gate_report"]),
            package=PredicatePackage.from_dict(d["package"]) if d["package"] is not None else None,
            certificate=FailureCertificate.from_dict(d["certificate"]) if d["certificate"] is not None else None,
            alternatives=tuple(PredicatePackage.from_dict(x) for x in d["alternatives"]),
            backend=d["backend"],
            verifier=d["verifier"],
            next_actions=tuple(NextAction.from_dict(x) for x in d["next_actions"]),
            choice_analysis=(
                ChoiceAnalysis.from_dict(d["choice_analysis"])
                if d["choice_analysis"] is not None
                else None
            ),
            enrichment_plans=tuple(
                EnrichmentPlan.from_dict(x) for x in d["enrichment_plans"]
            ),
        )


def _result_cause(
    status: SynthesisStatus,
    certificate: FailureCertificate | None,
) -> ResultCause:
    if status is SynthesisStatus.COMPILED:
        return ResultCause.TOTAL_DEFINITION
    if status is SynthesisStatus.PARTIAL:
        return ResultCause.PARTIAL_DEFINITION
    if certificate is None:
        raise InventionError(f"{status.value} has no result cause")
    return ResultCause(certificate.kind.value)


def _default_next_actions(
    status: SynthesisStatus,
    certificate: FailureCertificate | None,
) -> tuple[NextAction, ...]:
    if status in (SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL):
        return (
            NextAction(
                kind=NextActionKind.APPLY,
                statement=(
                    "Evaluate the independently accepted predicate package; "
                    "PARTIAL residuals remain ESCALATE."
                ),
            ),
        )
    if status is SynthesisStatus.CHOICE_REQUIRED:
        return (
            NextAction(
                kind=NextActionKind.SELECT_WITH_AUTHORITY,
                statement=(
                    "A declared selector must choose one offered package under a "
                    "receipted synthesis-policy mandate."
                ),
            ),
        )
    if status is SynthesisStatus.INDETERMINATE:
        return (
            NextAction(
                kind=NextActionKind.EXTEND_RESOURCE_BUDGET,
                statement="Change the declared resource budget or backend; do not infer impossibility.",
            ),
        )
    if status is SynthesisStatus.INVALID_INPUT:
        return (
            NextAction(
                kind=NextActionKind.CORRECT_INPUT,
                statement="Correct the malformed or inconsistent seam problem before synthesis.",
            ),
        )
    if certificate is not None and certificate.kind is FailureKind.TOPOLOGY_OBSTRUCTION:
        return (
            NextAction(
                kind=NextActionKind.REPAIR_OVERLAP,
                statement="Repair or explicitly re-authorize the witnessed overlap disagreement.",
            ),
        )
    return (
        NextAction(
            kind=NextActionKind.SUPPLY_EVIDENCE,
            statement=(
                "Supply a jointly authorized observable that distinguishes the certified "
                "same-reduct expansions; a richer formula alone cannot add that information."
            ),
        ),
    )


def _default_enrichment_plans(
    status: SynthesisStatus,
    certificate: FailureCertificate | None,
) -> tuple[EnrichmentPlan, ...]:
    if certificate is None:
        return ()
    if certificate.kind is FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY:
        witness = certificate.witness
        shared_reduct = witness.get("shared_reduct", {})
        return (
            EnrichmentPlan(
                axis=EnrichmentAxis.EVIDENCE,
                statement=(
                    "Add a jointly authorized observable that distinguishes the retained "
                    "same-reduct model pair. No extension using only the current shared "
                    "information can do so."
                ),
                requirements=(
                    {
                        "kind": "distinguish_model_pair",
                        "target_arguments": witness.get("target_arguments", []),
                        "shared_reduct_hash": canonical_hash(shared_reduct),
                    },
                ),
                cost={"new_observables": 1, "authority_changes": 0},
                minimality="unresolved",
            ),
            EnrichmentPlan(
                axis=EnrichmentAxis.LANGUAGE,
                statement=(
                    "No formula or derived operator over the current reduct can separate "
                    "the witnessed expansions. A language repair must introduce a new "
                    "shared observable, not merely richer syntax over the same information."
                ),
                requirements=(
                    {
                        "kind": "add_shared_relation_separating_model_pair",
                        "target_arguments": witness.get("target_arguments", []),
                        "shared_reduct_hash": canonical_hash(shared_reduct),
                        "pure_grammar_extension_sufficient": False,
                    },
                ),
                cost={"new_shared_relations": 1, "new_operators": 0},
                minimality="unresolved",
            ),
        )
    if certificate.kind is FailureKind.TOPOLOGY_OBSTRUCTION:
        return (
            EnrichmentPlan(
                axis=EnrichmentAxis.TOPOLOGY,
                statement=(
                    "Repair the declared overlap or issue an authoritative replacement map; "
                    "additional local search cannot remove this witness."
                ),
                requirements=(
                    {
                        "kind": "replace_or_repair_overlap",
                        "overlap_map": certificate.witness.get("overlap_map"),
                    },
                ),
                cost={"overlap_changes": 1, "authority_changes": 1},
                minimality="unresolved",
            ),
        )
    if certificate.kind is FailureKind.RESOURCE_LIMIT:
        return (
            EnrichmentPlan(
                axis=EnrichmentAxis.RESOURCE,
                statement=(
                    "Use a larger explicitly declared bound or an independently checked "
                    "accelerator. This plan does not assert semantic necessity."
                ),
                requirements=(
                    {
                        "kind": "change_resource_policy",
                        "reason": certificate.witness.get("reason", certificate.statement),
                    },
                ),
                cost={"resource_policy_changes": 1},
                minimality="unresolved",
            ),
        )
    if certificate.kind is FailureKind.NON_UNIQUE_MINIMUM:
        return (
            EnrichmentPlan(
                axis=EnrichmentAxis.AUTHORITY,
                statement=(
                    "Obtain a receipted selection by the declared authority; more computation "
                    "does not resolve policy underdetermination."
                ),
                requirements=(
                    {
                        "kind": "select_offered_package",
                        "alternative_hashes": certificate.witness.get(
                            "alternative_hashes", []
                        ),
                    },
                ),
                cost={"selection_acts": 1, "authority_changes": 0},
                minimality="exact-declared-candidate-space",
            ),
        )
    return ()


def _verifier_descriptor() -> dict:
    return {
        "id": VERIFIER_ID,
        "version": VERIFIER_VERSION,
        "language": LANGUAGE,
        "canon_version": CANON_VERSION,
        "trust": "direct-finite-enumeration",
    }


def _admissible_models(problem: SeamProblem) -> list[Structure]:
    constraints = [
        constraint
        for theory in problem.local_theories
        for constraint in theory.constraints
    ]
    models: list[Structure] = []
    for structure in enumerate_structures(
        problem.signature,
        max_ground_atoms=problem.synthesis_policy.reference_max_ground_atoms,
        max_models=problem.synthesis_policy.reference_max_models,
    ):
        if all(
            evaluate(constraint, signature=problem.signature, structure=structure)
            for constraint in constraints
        ):
            models.append(structure)
    return models


def _overlap_violation(
    problem: SeamProblem, models: Sequence[Structure]
) -> tuple[OverlapMap, Structure, tuple[str, ...]] | None:
    for overlap in problem.overlap_maps:
        left = problem.signature.relations[overlap.left_relation]
        right = problem.signature.relations[overlap.right_relation]
        right_domains = [problem.signature.sorts[sort] for sort in right.sorts]
        for structure in models:
            for right_args in itertools.product(*right_domains):
                left_args = tuple(right_args[i] for i in overlap.argument_map)
                left_value = left_args in set(structure[overlap.left_relation])
                right_value = tuple(right_args) in set(structure[overlap.right_relation])
                if left_value != right_value:
                    return overlap, structure, tuple(right_args)
    return None


def _target_points(problem: SeamProblem, models: Sequence[Structure]) -> list[tuple[Structure, tuple[str, ...], bool]]:
    decl = problem.target_decl
    domains = [problem.signature.sorts[sort] for sort in decl.sorts]
    points: list[tuple[Structure, tuple[str, ...], bool]] = []
    for structure in models:
        true_tuples = set(structure[problem.target_predicate])
        for args in itertools.product(*domains):
            args_tuple = tuple(args)
            points.append((structure, args_tuple, args_tuple in true_tuples))
    return points


def _feature_atoms(problem: SeamProblem) -> tuple[Formula, ...]:
    variables = tuple(
        (f"x{i}", sort) for i, sort in enumerate(problem.target_decl.sorts)
    )
    features: dict[str, Formula] = {}
    for name, sort in variables:
        for element in problem.signature.sorts[sort]:
            candidate = {
                "op": "eq",
                "sort": sort,
                "left": variable(name),
                "right": constant(element),
            }
            features[canonical_json(candidate)] = candidate
    for relation_name in sorted(problem.shared_vocabulary):
        relation = problem.signature.relations[relation_name]
        choices: list[list[dict[str, str]]] = []
        for sort in relation.sorts:
            terms = [constant(x) for x in problem.signature.sorts[sort]]
            terms.extend(variable(name) for name, var_sort in variables if var_sort == sort)
            choices.append(terms)
        for terms in itertools.product(*choices):
            candidate = atom(relation_name, terms)
            features[canonical_json(candidate)] = candidate
    return tuple(features[key] for key in sorted(features))


def _point_environment(args: tuple[str, ...]) -> dict[str, str]:
    return {f"x{i}": value for i, value in enumerate(args)}


def _vector(
    features: Sequence[Formula],
    *,
    problem: SeamProblem,
    structure: Structure,
    target_args: tuple[str, ...],
) -> tuple[bool, ...]:
    env = _point_environment(target_args)
    return tuple(
        evaluate(
            feature,
            signature=problem.signature,
            structure=structure,
            environment=env,
        )
        for feature in features
    )


def _cube_formula(cube: tuple[bool | None, ...], features: Sequence[Formula]) -> Formula:
    terms: list[Formula] = []
    for value, feature in zip(cube, features):
        if value is True:
            terms.append(feature)
        elif value is False:
            terms.append(negate(feature))
    return conjunction(terms)


def _full_minterm_dnf(
    features: Sequence[Formula], vectors: Iterable[tuple[bool, ...]]
) -> Formula:
    """Canonical, sound truth-table fallback with no minimality claim.

    A complete Boolean vector fixes every declared feature at the target
    tuple.  Disjoining full minterms therefore defines exactly the selected
    vectors and defaults every unseen vector to false.  Unlike ``_minimal_dnf``
    this construction is linear in the number of selected vectors and does not
    enumerate the ternary cube lattice.
    """
    selected = sorted(set(vectors))
    if not selected:
        return falsity()
    return disjunction(_cube_formula(tuple(vector_), features) for vector_ in selected)


def _safe_generalized_dnf(
    features: Sequence[Formula],
    positives: Iterable[tuple[bool, ...]],
    excluded: Iterable[tuple[bool, ...]],
) -> tuple[Formula, tuple[Formula, ...]]:
    """Return a deterministic sufficient DNF and its ordered implicant terms.

    This is an anytime generator, not a minimality procedure.  It begins with
    one complete minterm per positive vector and drops a literal only when the
    resulting cube still excludes every declared opposite vector.  Every term
    is therefore independently safe.  Subsumption and greedy cover reduce the
    output without becoming part of the trust root: the ordinary finite package
    verifier checks the emitted FRSL-1 formula again.
    """

    positive_set = set(positives)
    excluded_set = set(excluded)
    if not positive_set:
        return falsity(), ()
    if positive_set & excluded_set:
        raise InventionError("positive and excluded vectors must be disjoint")

    excluded_list = sorted(excluded_set)
    excluded_masks: dict[tuple[int, bool], int] = {}
    for feature_index in range(len(features)):
        for value in (False, True):
            mask = 0
            for point_index, point in enumerate(excluded_list):
                if point[feature_index] is value:
                    mask |= 1 << point_index
            excluded_masks[(feature_index, value)] = mask
    excluded_universe = (1 << len(excluded_list)) - 1

    def excludes_opposites(cube: tuple[bool | None, ...]) -> bool:
        matches = excluded_universe
        for feature_index, value in enumerate(cube):
            if value is not None:
                matches &= excluded_masks[(feature_index, value)]
                if not matches:
                    return True
        return matches == 0

    orders: tuple[tuple[int, ...], ...] = (
        tuple(range(len(features))),
        tuple(reversed(range(len(features)))),
    )
    cubes: set[tuple[bool | None, ...]] = set()
    for vector_ in sorted(positive_set):
        candidates: list[tuple[bool | None, ...]] = []
        for order in orders:
            cube: list[bool | None] = list(vector_)
            for index in order:
                prior = cube[index]
                cube[index] = None
                candidate = tuple(cube)
                if not excludes_opposites(candidate):
                    cube[index] = prior
            candidates.append(tuple(cube))
        best = min(
            candidates,
            key=lambda item: (
                sum(value is not None for value in item),
                canonical_json(_cube_formula(item, features)),
            ),
        )
        cubes.add(best)

    # A more general safe cube makes every cube it subsumes redundant.  Sort
    # general cubes first, then use integer masks so this test does not repeat
    # Python-level literal comparisons for every pair.
    ordered_cubes = sorted(
        cubes,
        key=lambda item: (
            sum(value is not None for value in item),
            canonical_json(_cube_formula(item, features)),
        ),
    )

    def cube_masks(cube: tuple[bool | None, ...]) -> tuple[int, int]:
        fixed = truth_values = 0
        for index, value in enumerate(cube):
            if value is not None:
                fixed |= 1 << index
                if value:
                    truth_values |= 1 << index
        return fixed, truth_values

    irredundant_list: list[tuple[bool | None, ...]] = []
    irredundant_masks: list[tuple[int, int]] = []
    for cube in ordered_cubes:
        fixed, truth_values = cube_masks(cube)
        if any(
            prior_fixed & fixed == prior_fixed
            and truth_values & prior_fixed == prior_truth
            for prior_fixed, prior_truth in irredundant_masks
        ):
            continue
        irredundant_list.append(cube)
        irredundant_masks.append((fixed, truth_values))
    irredundant = tuple(irredundant_list)

    positive_list = sorted(positive_set)
    coverage_masks: dict[tuple[bool | None, ...], int] = {}
    choice_keys: dict[tuple[bool | None, ...], tuple[int, str]] = {}
    for cube in irredundant:
        mask = 0
        for point_index, point in enumerate(positive_list):
            if _cube_matches(cube, point):
                mask |= 1 << point_index
        coverage_masks[cube] = mask
        term = _cube_formula(cube, features)
        choice_keys[cube] = (formula_size(term), canonical_json(term))
    uncovered = (1 << len(positive_list)) - 1
    selected: list[tuple[bool | None, ...]] = []
    while uncovered:
        best: tuple[int, int, str, tuple[bool | None, ...]] | None = None
        for cube in irredundant:
            covered = (coverage_masks[cube] & uncovered).bit_count()
            if covered:
                size, rendered = choice_keys[cube]
                candidate = (-covered, size, rendered, cube)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            raise InventionError("generalized cube cover lost a positive vector")
        cube = best[3]
        selected.append(cube)
        uncovered &= ~coverage_masks[cube]

    terms = tuple(_cube_formula(cube, features) for cube in selected)
    return disjunction(terms), terms


def _bounded_disjunction(terms: Sequence[Formula], max_nodes: int) -> Formula:
    """Take the largest deterministic safe prefix that fits ``max_nodes``."""

    selected: list[Formula] = []
    for term in terms:
        candidate = disjunction((*selected, term))
        if formula_size(candidate) > max_nodes:
            break
        selected.append(term)
    return disjunction(selected)


def _cube_matches(cube: tuple[bool | None, ...], vector_: tuple[bool, ...]) -> bool:
    return all(want is None or want == got for want, got in zip(cube, vector_))


def _minimal_dnf(
    *,
    features: Sequence[Formula],
    positives: set[tuple[bool, ...]],
    excluded: set[tuple[bool, ...]],
    alternative_limit: int,
    equivalence_vectors: set[tuple[bool, ...]],
) -> tuple[Formula, tuple[Formula, ...], bool]:
    """Return one exact-minimal DNF, alternatives, and whether minima disagree.

    Minimality is only over conjunction cubes built from the declared finite
    feature list.  Unseen vectors are deliberate don't-cares.  Alternatives
    that disagree on a don't-care vector are governance-distinct.
    """
    if not positives:
        return falsity(), (), False
    if not excluded:
        return truth(), (), False
    n = len(features)
    positive_list = sorted(positives)
    all_cubes: list[tuple[tuple[bool | None, ...], int, Formula]] = []
    for cube in itertools.product((None, False, True), repeat=n):
        coverage = 0
        for i, point in enumerate(positive_list):
            if _cube_matches(cube, point):
                coverage |= 1 << i
        if coverage == 0:
            continue
        if any(_cube_matches(cube, point) for point in excluded):
            continue
        term = _cube_formula(cube, features)
        all_cubes.append((cube, coverage, term))
    if not all_cubes:
        raise InventionError("no DNF covers the declared positive vectors")
    all_cubes.sort(key=lambda x: (formula_size(x[2]), canonical_json(x[2])))
    by_point: dict[int, list[int]] = {i: [] for i in range(len(positive_list))}
    for cube_i, (_, coverage, _) in enumerate(all_cubes):
        for point_i in range(len(positive_list)):
            if coverage & (1 << point_i):
                by_point[point_i].append(cube_i)
    full = (1 << len(positive_list)) - 1
    best_size: int | None = None
    best_formula: Formula | None = None
    representatives: dict[tuple[bool, ...], Formula] = {}

    visited: set[tuple[int, tuple[int, ...]]] = set()

    def search(mask: int, chosen: tuple[int, ...]) -> None:
        nonlocal best_size, best_formula
        normalized_chosen = tuple(sorted(chosen))
        state = (mask, normalized_chosen)
        if state in visited:
            return
        visited.add(state)
        if mask == full:
            formula = disjunction(all_cubes[i][2] for i in normalized_chosen)
            size = formula_size(formula)
            if best_size is None or size < best_size:
                best_size = size
                best_formula = None
                representatives.clear()
            if size == best_size:
                key = canonical_json(formula)
                if best_formula is None or key < canonical_json(best_formula):
                    best_formula = formula
                table = tuple(
                    _evaluate_boolean_formula(formula, features, vector_)
                    for vector_ in sorted(equivalence_vectors)
                )
                current = representatives.get(table)
                if current is None or key < canonical_json(current):
                    if current is not None or len(representatives) < alternative_limit:
                        representatives[table] = formula
            return
        partial_terms = [all_cubes[i][2] for i in chosen]
        if partial_terms and best_size is not None:
            partial_size = formula_size(disjunction(partial_terms))
            if partial_size >= best_size:
                return
        first_missing = next(i for i in range(len(positive_list)) if not (mask & (1 << i)))
        for cube_i in by_point[first_missing]:
            if cube_i in normalized_chosen:
                continue
            _, coverage, _ = all_cubes[cube_i]
            search(mask | coverage, normalized_chosen + (cube_i,))

    search(0, ())
    if best_formula is None:
        raise InventionError("exact DNF cover search failed")
    semantic_representatives = sorted(
        representatives.values(), key=canonical_json
    )
    alternatives = [
        formula
        for formula in semantic_representatives
        if canonical_json(formula) != canonical_json(best_formula)
    ]
    return (
        best_formula,
        tuple(alternatives[: max(0, alternative_limit - 1)]),
        len(representatives) > 1,
    )


def _evaluate_boolean_formula(
    formula: Formula,
    features: Sequence[Formula],
    vector_: tuple[bool, ...],
) -> bool:
    lookup = {canonical_json(feature): value for feature, value in zip(features, vector_)}

    def visit(node: Formula) -> bool:
        key = canonical_json(node)
        if key in lookup:
            return lookup[key]
        op = node["op"]
        if op == "true":
            return True
        if op == "false":
            return False
        if op == "not":
            return not visit(node["body"])
        if op == "and":
            return all(visit(x) for x in node["args"])
        if op == "or":
            return any(visit(x) for x in node["args"])
        raise InventionError("synthesized DNF contains a non-Boolean feature")

    return visit(formula)


def _protected_pins(problem: SeamProblem) -> dict[str, str]:
    pins: dict[str, str] = {}
    for owner, relation_names in problem.protected_signatures.items():
        payload = {
            "language": LANGUAGE,
            "owner": owner,
            "relations": [
                problem.signature.relations[name].to_dict()
                for name in sorted(relation_names)
            ],
        }
        pins[owner] = canonical_hash(payload)
    return pins


def _all_shared_vectors(
    problem: SeamProblem, features: Sequence[Formula]
) -> set[tuple[bool, ...]]:
    """Feature vectors reachable in any finite shared-signature structure.

    This prevents false governance choices between formulas that are merely
    syntactically different but forced equivalent by the named finite domain
    itself (for example R(x) and R(a) on a singleton sort).
    """
    vectors: set[tuple[bool, ...]] = set()
    target_domains = [
        problem.signature.sorts[sort] for sort in problem.target_decl.sorts
    ]
    for structure in enumerate_structures(
        problem.signature,
        relation_names=problem.shared_vocabulary,
        max_ground_atoms=problem.synthesis_policy.reference_max_ground_atoms,
        max_models=problem.synthesis_policy.reference_max_models,
    ):
        for args in itertools.product(*target_domains):
            vectors.add(
                _vector(
                    features,
                    problem=problem,
                    structure=structure,
                    target_args=tuple(args),
                )
            )
    return vectors


def _find_formula_disagreement(
    problem: SeamProblem,
    first: Formula,
    second: Formula,
) -> dict | None:
    target_domains = [
        problem.signature.sorts[sort] for sort in problem.target_decl.sorts
    ]
    for structure in enumerate_structures(
        problem.signature,
        relation_names=problem.shared_vocabulary,
        max_ground_atoms=problem.synthesis_policy.reference_max_ground_atoms,
        max_models=problem.synthesis_policy.reference_max_models,
    ):
        for args_ in itertools.product(*target_domains):
            args = tuple(args_)
            env = _point_environment(args)
            left = evaluate(
                first,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            right = evaluate(
                second,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            if left != right:
                return {
                    "target_arguments": list(args),
                    "shared_structure": relation_reduct(
                        structure, problem.shared_vocabulary
                    ),
                    "first_value": left,
                    "second_value": right,
                }
    return None


_CHOICE_COST_ORDER = (
    "predicate_ast_nodes",
    "evidence_items",
    "disclosure_relations",
    "authority_changes",
)


def _package_behavior(
    problem: SeamProblem,
    package: PredicatePackage,
) -> tuple[bool, ...]:
    if package.mode != "full" or package.definition is None:
        raise InventionError("choice behavior is defined only for full packages")
    values: list[bool] = []
    target_domains = [
        problem.signature.sorts[sort] for sort in problem.target_decl.sorts
    ]
    for structure in enumerate_structures(
        problem.signature,
        relation_names=problem.shared_vocabulary,
        max_ground_atoms=problem.synthesis_policy.reference_max_ground_atoms,
        max_models=problem.synthesis_policy.reference_max_models,
    ):
        for args_ in itertools.product(*target_domains):
            values.append(
                evaluate(
                    package.definition,
                    signature=problem.signature,
                    structure=structure,
                    environment=_point_environment(tuple(args_)),
                )
            )
    return tuple(values)


def _choice_cost_vector(package: PredicatePackage) -> dict[str, int]:
    formula = package.definition
    relations = formula_relations(formula) if formula is not None else set()
    return {
        "predicate_ast_nodes": int(package.cost.get("predicate_ast_nodes", 0)),
        "evidence_items": len(package.evidence_requirements),
        "disclosure_relations": len(relations),
        "authority_changes": 0,
    }


def build_choice_analysis(
    problem: SeamProblem,
    packages: Sequence[PredicatePackage],
    disagreement_witness: Mapping[str, Any],
) -> ChoiceAnalysis:
    """Partition exact candidates by protected behavior and declared cost.

    Syntactic duplicates and behavior/cost-identical packages share a class and
    therefore do not create governance merely by having different bytes.
    """
    groups: dict[tuple[str, str], list[PredicatePackage]] = {}
    behavior_hashes: set[str] = set()
    metadata: dict[tuple[str, str], tuple[str, dict[str, int]]] = {}
    for package in packages:
        if package.problem_hash != problem.problem_hash:
            raise InventionError("choice package does not bind the problem")
        behavior_hash = canonical_hash(list(_package_behavior(problem, package)))
        cost = _choice_cost_vector(package)
        cost_hash = canonical_hash(cost)
        key = (behavior_hash, cost_hash)
        groups.setdefault(key, []).append(package)
        metadata[key] = (behavior_hash, cost)
        behavior_hashes.add(behavior_hash)
    classes: list[ChoiceClass] = []
    for key in sorted(groups):
        behavior_hash, cost = metadata[key]
        package_hashes = tuple(sorted(x.package_hash for x in groups[key]))
        class_id = canonical_hash(
            {
                "protected_behavior_hash": behavior_hash,
                "cost_vector": cost,
            }
        )
        classes.append(
            ChoiceClass(
                class_id=class_id,
                package_hashes=package_hashes,
                protected_behavior_hash=behavior_hash,
                cost_vector=cost,
            )
        )
    if len(classes) < 2:
        raise InventionError(
            "behaviorally and economically identical packages must be canonicalized, not governed"
        )
    return ChoiceAnalysis(
        kind=(
            ChoiceKind.NORMATIVE
            if len(behavior_hashes) > 1
            else ChoiceKind.ECONOMIC
        ),
        classes=tuple(classes),
        cost_order=_CHOICE_COST_ORDER,
        selector_authority=(
            dict(problem.authority)
            if problem.authority
            else {"status": "undeclared", "problem_hash": problem.problem_hash}
        ),
        disagreement_witness=dict(disagreement_witness),
    )


def canonical_choice_representative(
    problem: SeamProblem,
    packages: Sequence[PredicatePackage],
) -> PredicatePackage | None:
    """Deterministically collapse one non-normative behavior-and-cost class.

    ``None`` means the candidates span multiple classes and selection would be
    economic or normative. The ordering is over normalized predicate bytes,
    with the package hash only as a stable final tie-breaker.
    """
    if not packages:
        raise InventionError("canonical choice requires at least one package")
    keys = {
        (
            canonical_hash(list(_package_behavior(problem, package))),
            canonical_hash(_choice_cost_vector(package)),
        )
        for package in packages
    }
    if len(keys) != 1:
        return None
    return min(
        packages,
        key=lambda package: (
            canonical_json(normalize_formula(package.definition)),
            package.package_hash,
        ),
    )


def _make_package(
    problem: SeamProblem,
    *,
    mode: str,
    definition: Formula | None,
    rely_when: Formula | None,
    refuse_when: Formula | None,
    model_count: int,
    feature_count: int,
    exact_minimality: bool,
) -> PredicatePackage:
    definition = normalize_formula(definition) if definition is not None else None
    rely_when = normalize_formula(rely_when) if rely_when is not None else None
    refuse_when = normalize_formula(refuse_when) if refuse_when is not None else None
    local_formula = definition if definition is not None else {
        "op": "envelope",
        "rely_when": rely_when,
        "refuse_when": refuse_when,
        "otherwise": "ESCALATE",
    }
    size = 0
    for formula in (definition, rely_when, refuse_when):
        if formula is not None:
            size += formula_size(formula)
    return PredicatePackage(
        problem_hash=problem.problem_hash,
        mode=mode,
        definition=definition,
        rely_when=rely_when,
        refuse_when=refuse_when,
        local_definitions={
            theory.owner: local_formula for theory in problem.local_theories
        },
        bridge_constraints=tuple(x.to_dict() for x in problem.overlap_maps),
        evidence_requirements=problem.evidence_requirements,
        protected_signature_pins=_protected_pins(problem),
        verifier=_verifier_descriptor(),
        authority=problem.authority,
        scope=problem.scope,
        expiry=problem.expiry,
        cost={
            "admissible_models": model_count,
            "candidate_atoms": feature_count,
            "predicate_ast_nodes": size,
            "minimality": "exact-finite-candidate-space"
            if exact_minimality
            else "unresolved",
        },
        proof_references=(
            {
                "kind": "exhaustive-enumeration",
                "problem_hash": problem.problem_hash,
                "verifier": VERIFIER_ID,
                "verifier_version": VERIFIER_VERSION,
            },
        ),
    )


def _nondef_certificate(
    problem: SeamProblem,
    *,
    features: Sequence[Formula],
    points: Sequence[tuple[Structure, tuple[str, ...], bool]],
) -> FailureCertificate | None:
    groups: dict[
        tuple[tuple[str, ...], tuple[bool, ...]],
        list[tuple[Structure, tuple[str, ...], bool]],
    ] = {}
    for structure, args, label in points:
        vector_ = _vector(features, problem=problem, structure=structure, target_args=args)
        groups.setdefault(
            (args, vector_),
            [],
        ).append((structure, args, label))
    for (group_args, vector_), members in sorted(groups.items(), key=lambda x: x[0]):
        positives = [x for x in members if x[2]]
        negatives = [x for x in members if not x[2]]
        if positives and negatives:
            left = positives[0]
            right = negatives[0]
            shared_left = relation_reduct(left[0], problem.shared_vocabulary)
            shared_right = relation_reduct(right[0], problem.shared_vocabulary)
            if shared_left != shared_right:
                raise AssertionError("complete shared feature vector failed to pin shared reduct")
            return FailureCertificate(
                kind=FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY,
                statement=(
                    "Two admissible expansions have the same complete shared-language "
                    "reduct at the same target tuple and disagree on the target. "
                    "No FRSL-1 formula over the fixed shared vocabulary can define it."
                ),
                witness={
                    "target_arguments": list(group_args),
                    "shared_vocabulary": list(problem.shared_vocabulary),
                    "shared_reduct": shared_left,
                    "expansion_true": structure_to_dict(left[0]),
                    "expansion_false": structure_to_dict(right[0]),
                    "feature_vector": list(vector_),
                },
                complete_within_bound=True,
            )
    return None


def _invalid_result(problem_hash: str, message: str) -> SynthesisResult:
    return SynthesisResult(
        status=SynthesisStatus.INVALID_INPUT,
        problem_hash=problem_hash,
        gate_report=GateReport(
            gluing=GateStatus.UNRESOLVED,
            conservativity=GateStatus.UNRESOLVED,
            definability=GateStatus.UNRESOLVED,
            preserved_refusals=GateStatus.UNRESOLVED,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=GateStatus.NOT_APPLICABLE,
            reasons=(message,),
        ),
        certificate=FailureCertificate(
            kind=FailureKind.INVALID_PROBLEM,
            statement=message,
            witness={},
            complete_within_bound=False,
        ),
        verifier=_verifier_descriptor(),
    )


def synthesize(problem: SeamProblem) -> SynthesisResult:
    """Run the exhaustive FRSL-1 reference backend."""
    try:
        models = _admissible_models(problem)
    except (FRSLError, InventionError) as exc:
        return SynthesisResult(
            status=SynthesisStatus.INDETERMINATE,
            problem_hash=problem.problem_hash,
            gate_report=GateReport(
                gluing=GateStatus.UNRESOLVED,
                conservativity=GateStatus.UNRESOLVED,
                definability=GateStatus.UNRESOLVED,
                preserved_refusals=GateStatus.UNRESOLVED,
                minimality=GateStatus.UNRESOLVED,
                receipt_binding=GateStatus.NOT_APPLICABLE,
                reasons=(str(exc),),
            ),
            certificate=FailureCertificate(
                kind=FailureKind.RESOURCE_LIMIT,
                statement=(
                    "The exhaustive reference bound was exceeded. This is an "
                    "operational limit, not evidence of non-definability."
                ),
                witness={
                    "reason": str(exc),
                    "policy": problem.synthesis_policy.to_dict(),
                },
                complete_within_bound=False,
            ),
            verifier=_verifier_descriptor(),
        )
    if not models:
        return _invalid_result(
            problem.problem_hash,
            "local theories have no admissible model; vacuity is not a definition",
        )
    violation = _overlap_violation(problem, models)
    if violation is not None:
        overlap, structure, args = violation
        certificate = FailureCertificate(
            kind=FailureKind.TOPOLOGY_OBSTRUCTION,
            statement=(
                "An admissible structure violates a declared overlap map; local "
                "definitions cannot be treated as a glued global package."
            ),
            witness={
                "overlap_map": overlap.to_dict(),
                "right_arguments": list(args),
                "structure": structure_to_dict(structure),
            },
            complete_within_bound=True,
        )
        return SynthesisResult(
            status=SynthesisStatus.ESCALATE,
            problem_hash=problem.problem_hash,
            gate_report=GateReport(
                gluing=GateStatus.FAIL,
                conservativity=GateStatus.UNRESOLVED,
                definability=GateStatus.UNRESOLVED,
                preserved_refusals=GateStatus.UNRESOLVED,
                minimality=GateStatus.UNRESOLVED,
                receipt_binding=GateStatus.NOT_APPLICABLE,
                reasons=(certificate.statement,),
            ),
            certificate=certificate,
            verifier=_verifier_descriptor(),
        )
    features = _feature_atoms(problem)
    exact_search_allowed = bool(
        problem.synthesis_policy.exact_minimality
        and len(features) <= problem.synthesis_policy.max_candidate_atoms
    )
    points = _target_points(problem, models)
    labels_by_vector: dict[tuple[bool, ...], set[bool]] = {}
    for structure, args, label in points:
        labels_by_vector.setdefault(
            _vector(features, problem=problem, structure=structure, target_args=args),
            set(),
        ).add(label)
    stable_true = {v for v, labels in labels_by_vector.items() if labels == {True}}
    stable_false = {v for v, labels in labels_by_vector.items() if labels == {False}}
    ambiguous = {v for v, labels in labels_by_vector.items() if len(labels) > 1}
    witness = _nondef_certificate(problem, features=features, points=points)

    if not exact_search_allowed:
        rely_when, rely_terms = _safe_generalized_dnf(
            features,
            stable_true,
            stable_false | ambiguous,
        )
        refuse_when, refuse_terms = _safe_generalized_dnf(
            features,
            stable_false,
            stable_true | ambiguous,
        )
        formulas = (rely_when,) if not ambiguous else (rely_when, refuse_when)
        fallback_nodes = sum(formula_size(formula) for formula in formulas)
        if fallback_nodes > MAX_FALLBACK_AST_NODES:
            message = (
                f"generalized sufficient cover exceeds AST bound: {fallback_nodes} nodes > "
                f"{MAX_FALLBACK_AST_NODES}"
            )
            per_region_bound = MAX_FALLBACK_AST_NODES // 2
            bounded_rely = _bounded_disjunction(rely_terms, per_region_bound)
            bounded_refuse = _bounded_disjunction(refuse_terms, per_region_bound)
            if formula_size(bounded_rely) + formula_size(bounded_refuse) <= MAX_FALLBACK_AST_NODES:
                package = _make_package(
                    problem,
                    mode="partial",
                    definition=None,
                    rely_when=bounded_rely,
                    refuse_when=bounded_refuse,
                    model_count=len(models),
                    feature_count=len(features),
                    exact_minimality=False,
                )
                resource_certificate = FailureCertificate(
                    kind=FailureKind.RESOURCE_LIMIT,
                    statement=(
                        "Finite enumeration completed and a checked partial envelope fits, "
                        "but the sufficient total cover exceeds the output-size bound."
                    ),
                    witness={
                        "reason": message,
                        "candidate_atoms": len(features),
                        "sufficient_cover_ast_nodes": fallback_nodes,
                        "fallback_ast_bound": MAX_FALLBACK_AST_NODES,
                        "model_enumeration_complete": True,
                        "bounded_rely_ast_nodes": formula_size(bounded_rely),
                        "bounded_refuse_ast_nodes": formula_size(bounded_refuse),
                    },
                    complete_within_bound=False,
                )
                return SynthesisResult(
                    status=SynthesisStatus.PARTIAL,
                    problem_hash=problem.problem_hash,
                    gate_report=GateReport(
                        gluing=GateStatus.PASS,
                        conservativity=GateStatus.PASS,
                        definability=(
                            GateStatus.FAIL if ambiguous else GateStatus.UNRESOLVED
                        ),
                        preserved_refusals=GateStatus.PASS,
                        minimality=GateStatus.UNRESOLVED,
                        receipt_binding=GateStatus.NOT_APPLICABLE,
                        reasons=(resource_certificate.statement,),
                    ),
                    package=package,
                    certificate=witness or resource_certificate,
                    verifier=_verifier_descriptor(),
                )
            return SynthesisResult(
                status=SynthesisStatus.INDETERMINATE,
                problem_hash=problem.problem_hash,
                gate_report=GateReport(
                    gluing=GateStatus.PASS,
                    conservativity=GateStatus.UNRESOLVED,
                    definability=GateStatus.UNRESOLVED,
                    preserved_refusals=GateStatus.UNRESOLVED,
                    minimality=GateStatus.UNRESOLVED,
                    receipt_binding=GateStatus.NOT_APPLICABLE,
                    reasons=(message,),
                ),
                certificate=FailureCertificate(
                    kind=FailureKind.RESOURCE_LIMIT,
                    statement=(
                        "Finite model enumeration completed, but the sound nonminimal "
                        "fallback exceeded the declared verifier size bound."
                    ),
                    witness={
                        "reason": message,
                        "candidate_atoms": len(features),
                        "sufficient_cover_ast_nodes": fallback_nodes,
                        "fallback_ast_bound": MAX_FALLBACK_AST_NODES,
                        "model_enumeration_complete": True,
                    },
                    complete_within_bound=False,
                ),
                verifier=_verifier_descriptor(),
            )
        if not ambiguous:
            package = _make_package(
                problem,
                mode="full",
                definition=rely_when,
                rely_when=None,
                refuse_when=None,
                model_count=len(models),
                feature_count=len(features),
                exact_minimality=False,
            )
            return SynthesisResult(
                status=SynthesisStatus.COMPILED,
                problem_hash=problem.problem_hash,
                gate_report=GateReport(
                    gluing=GateStatus.PASS,
                    conservativity=GateStatus.PASS,
                    definability=GateStatus.PASS,
                    preserved_refusals=GateStatus.PASS,
                    minimality=GateStatus.UNRESOLVED,
                    receipt_binding=GateStatus.NOT_APPLICABLE,
                    reasons=(
                        "The generalized implicant cover is sound within the complete finite "
                        "model enumeration; minimality is unresolved.",
                    ),
                ),
                package=package,
                verifier=_verifier_descriptor(),
            )
        if witness is None:
            raise AssertionError("ambiguous complete shared vectors require a model-pair witness")
        if not stable_true and not stable_false:
            return SynthesisResult(
                status=SynthesisStatus.ESCALATE,
                problem_hash=problem.problem_hash,
                gate_report=GateReport(
                    gluing=GateStatus.PASS,
                    conservativity=GateStatus.PASS,
                    definability=GateStatus.FAIL,
                    preserved_refusals=GateStatus.PASS,
                    minimality=GateStatus.NOT_APPLICABLE,
                    receipt_binding=GateStatus.NOT_APPLICABLE,
                    reasons=(witness.statement,),
                ),
                certificate=witness,
                verifier=_verifier_descriptor(),
            )
        package = _make_package(
            problem,
            mode="partial",
            definition=None,
            rely_when=rely_when,
            refuse_when=refuse_when,
            model_count=len(models),
            feature_count=len(features),
            exact_minimality=False,
        )
        return SynthesisResult(
            status=SynthesisStatus.PARTIAL,
            problem_hash=problem.problem_hash,
            gate_report=GateReport(
                gluing=GateStatus.PASS,
                conservativity=GateStatus.PASS,
                definability=GateStatus.FAIL,
                preserved_refusals=GateStatus.PASS,
                minimality=GateStatus.UNRESOLVED,
                receipt_binding=GateStatus.NOT_APPLICABLE,
                reasons=(
                    "Generalized implicant RELY/REFUSE regions are sound within the complete "
                    "finite model enumeration; the residual remains ESCALATE.",
                ),
            ),
            package=package,
            certificate=witness,
            verifier=_verifier_descriptor(),
        )

    equivalence_vectors = _all_shared_vectors(problem, features)
    if not ambiguous:
        definition, alternatives, inequivalent = _minimal_dnf(
            features=features,
            positives=stable_true,
            excluded=stable_false,
            alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
            equivalence_vectors=equivalence_vectors,
        )
        package = _make_package(
            problem,
            mode="full",
            definition=definition,
            rely_when=None,
            refuse_when=None,
            model_count=len(models),
            feature_count=len(features),
            exact_minimality=problem.synthesis_policy.exact_minimality,
        )
        if (
            inequivalent
            and alternatives
        ):
            packages = [package]
            packages.extend(
                _make_package(
                    problem,
                    mode="full",
                    definition=alternative,
                    rely_when=None,
                    refuse_when=None,
                    model_count=len(models),
                    feature_count=len(features),
                    exact_minimality=True,
                )
                for alternative in alternatives
            )
            disagreement = None
            disagreement_pair = None
            for first_index, first_package in enumerate(packages):
                for second_package in packages[first_index + 1 :]:
                    disagreement = _find_formula_disagreement(
                        problem,
                        first_package.definition,
                        second_package.definition,
                    )
                    if disagreement is not None:
                        disagreement_pair = [
                            first_package.package_hash,
                            second_package.package_hash,
                        ]
                        break
                if disagreement is not None:
                    break
            if disagreement is None or disagreement_pair is None:
                raise AssertionError(
                    "inequivalent minima require a reachable shared-structure witness"
                )
            certificate = FailureCertificate(
                kind=FailureKind.NON_UNIQUE_MINIMUM,
                statement=(
                    "Multiple exact-minimal definitions agree on every admissible "
                    "model but disagree on unreachable shared-language valuations. "
                    "Selecting one would create policy, so authority is required."
                ),
                witness={
                    "alternative_hashes": [x.package_hash for x in packages],
                    "disagreement_pair": disagreement_pair,
                    "disagreement": disagreement,
                    "candidate_space": "FRSL-1 DNF cubes over declared feature atoms",
                },
                complete_within_bound=True,
            )
            return SynthesisResult(
                status=SynthesisStatus.CHOICE_REQUIRED,
                problem_hash=problem.problem_hash,
                gate_report=GateReport(
                    gluing=GateStatus.PASS,
                    conservativity=GateStatus.PASS,
                    definability=GateStatus.PASS,
                    preserved_refusals=GateStatus.PASS,
                    minimality=GateStatus.FAIL,
                    receipt_binding=GateStatus.NOT_APPLICABLE,
                    reasons=(certificate.statement,),
                ),
                certificate=certificate,
                alternatives=tuple(packages),
                choice_analysis=build_choice_analysis(
                    problem,
                    packages,
                    disagreement,
                ),
                verifier=_verifier_descriptor(),
            )
        return SynthesisResult(
            status=SynthesisStatus.COMPILED,
            problem_hash=problem.problem_hash,
            gate_report=GateReport(
                gluing=GateStatus.PASS,
                conservativity=GateStatus.PASS,
                definability=GateStatus.PASS,
                preserved_refusals=GateStatus.PASS,
                minimality=GateStatus.PASS
                if problem.synthesis_policy.exact_minimality
                else GateStatus.UNRESOLVED,
                receipt_binding=GateStatus.NOT_APPLICABLE,
            ),
            package=package,
            verifier=_verifier_descriptor(),
        )
    if witness is None:
        raise AssertionError("ambiguous complete shared vectors require a model-pair witness")
    if not stable_true and not stable_false:
        return SynthesisResult(
            status=SynthesisStatus.ESCALATE,
            problem_hash=problem.problem_hash,
            gate_report=GateReport(
                gluing=GateStatus.PASS,
                conservativity=GateStatus.PASS,
                definability=GateStatus.FAIL,
                preserved_refusals=GateStatus.PASS,
                minimality=GateStatus.NOT_APPLICABLE,
                receipt_binding=GateStatus.NOT_APPLICABLE,
                reasons=(witness.statement,),
            ),
            certificate=witness,
            verifier=_verifier_descriptor(),
        )
    rely_when, _, _ = _minimal_dnf(
        features=features,
        positives=stable_true,
        excluded=stable_false | ambiguous,
        alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
        equivalence_vectors=equivalence_vectors,
    )
    refuse_when, _, _ = _minimal_dnf(
        features=features,
        positives=stable_false,
        excluded=stable_true | ambiguous,
        alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
        equivalence_vectors=equivalence_vectors,
    )
    package = _make_package(
        problem,
        mode="partial",
        definition=None,
        rely_when=rely_when,
        refuse_when=refuse_when,
        model_count=len(models),
        feature_count=len(features),
        exact_minimality=problem.synthesis_policy.exact_minimality,
    )
    return SynthesisResult(
        status=SynthesisStatus.PARTIAL,
        problem_hash=problem.problem_hash,
        gate_report=GateReport(
            gluing=GateStatus.PASS,
            conservativity=GateStatus.PASS,
            definability=GateStatus.FAIL,
            preserved_refusals=GateStatus.PASS,
            minimality=GateStatus.PASS
            if problem.synthesis_policy.exact_minimality
            else GateStatus.UNRESOLVED,
            receipt_binding=GateStatus.NOT_APPLICABLE,
            reasons=(
                "The package is sound only on the emitted RELY and REFUSE "
                "surfaces; the residual remains ESCALATE.",
            ),
        ),
        package=package,
        certificate=witness,
        verifier=_verifier_descriptor(),
    )


def verify_package(problem: SeamProblem, package: PredicatePackage) -> GateReport:
    """Independently replay all semantic gates for an emitted package."""
    reasons: list[str] = []
    if package.problem_hash != problem.problem_hash:
        return GateReport(
            gluing=GateStatus.UNRESOLVED,
            conservativity=GateStatus.UNRESOLVED,
            definability=GateStatus.FAIL,
            preserved_refusals=GateStatus.UNRESOLVED,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=GateStatus.FAIL,
            reasons=("package problem_hash does not bind the supplied problem",),
        )
    if package.language != LANGUAGE or package.schema_version != SCHEMA_VERSION:
        reasons.append("package language or schema version mismatch")
    expected_pins = _protected_pins(problem)
    package_binding = GateStatus.PASS
    if dict(package.authority) != dict(problem.authority):
        package_binding = GateStatus.FAIL
        reasons.append("package authority differs from the requested authority")
    if dict(package.scope) != dict(problem.scope):
        package_binding = GateStatus.FAIL
        reasons.append("package scope differs from the requested scope")
    if package.expiry != problem.expiry:
        package_binding = GateStatus.FAIL
        reasons.append("package expiry differs from the requested expiry")
    if tuple(package.evidence_requirements) != tuple(problem.evidence_requirements):
        package_binding = GateStatus.FAIL
        reasons.append("package evidence requirements differ from the problem")
    if tuple(package.bridge_constraints) != tuple(
        x.to_dict() for x in problem.overlap_maps
    ):
        package_binding = GateStatus.FAIL
        reasons.append("package bridge constraints differ from the declared overlaps")
    if set(package.local_definitions) != {x.owner for x in problem.local_theories}:
        package_binding = GateStatus.FAIL
        reasons.append("package local definitions do not cover exactly the declared owners")
    expected_local = (
        package.definition
        if package.mode == "full"
        else {
            "op": "envelope",
            "rely_when": package.rely_when,
            "refuse_when": package.refuse_when,
            "otherwise": "ESCALATE",
        }
    )
    if any(
        package.local_definitions.get(theory.owner) != expected_local
        for theory in problem.local_theories
    ):
        package_binding = GateStatus.FAIL
        reasons.append("package local definitions differ from the emitted global surface")
    verifier_is_reference = (
        package.verifier.get("id") == VERIFIER_ID
        and package.verifier.get("version") == VERIFIER_VERSION
    )
    verifier_has_reference_checker = (
        package.verifier.get("checker") == VERIFIER_ID
    )
    if not (verifier_is_reference or verifier_has_reference_checker):
        package_binding = GateStatus.FAIL
        reasons.append("package names no supported independent reference checker")
    if dict(package.protected_signature_pins) != expected_pins:
        reasons.append("protected signature pins do not match the problem")
    try:
        models = _admissible_models(problem)
    except (FRSLError, InventionError) as exc:
        return GateReport(
            gluing=GateStatus.UNRESOLVED,
            conservativity=GateStatus.UNRESOLVED,
            definability=GateStatus.UNRESOLVED,
            preserved_refusals=GateStatus.UNRESOLVED,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=GateStatus.NOT_APPLICABLE,
            reasons=(str(exc),),
        )
    if not models:
        return GateReport(
            gluing=GateStatus.UNRESOLVED,
            conservativity=GateStatus.FAIL,
            definability=GateStatus.FAIL,
            preserved_refusals=GateStatus.FAIL,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=package_binding,
            reasons=tuple(reasons + ["problem has no admissible model"]),
        )
    violation = _overlap_violation(problem, models)
    gluing = GateStatus.FAIL if violation else GateStatus.PASS
    if violation:
        reasons.append("declared overlap map is violated")
    formulas = [x for x in (package.definition, package.rely_when, package.refuse_when) if x is not None]
    try:
        for i, formula in enumerate(formulas):
            validate_formula(
                formula,
                signature=problem.signature,
                free_variables=problem.target_variables,
                where=f"package.formula[{i}]",
            )
            leaked = formula_relations(formula) - set(problem.shared_vocabulary)
            if leaked:
                raise FRSLError(
                    f"package.formula[{i}] leaks non-shared relations {sorted(leaked)}"
                )
            if formula != normalize_formula(formula):
                raise FRSLError(
                    f"package.formula[{i}] is not in canonical FRSL-1 form"
                )
    except FRSLError as exc:
        reasons.append(str(exc))
        return GateReport(
            gluing=gluing,
            conservativity=GateStatus.FAIL if reasons else GateStatus.PASS,
            definability=GateStatus.FAIL,
            preserved_refusals=GateStatus.FAIL,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=package_binding,
            reasons=tuple(reasons),
        )
    definability = GateStatus.PASS
    preserved = GateStatus.PASS
    for structure, args, target in _target_points(problem, models):
        env = _point_environment(args)
        if package.mode == "full":
            got = evaluate(
                package.definition,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            if got != target:
                definability = GateStatus.FAIL
                if not target and got:
                    preserved = GateStatus.FAIL
        else:
            rely = evaluate(
                package.rely_when,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            refuse = evaluate(
                package.refuse_when,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            if rely and refuse:
                definability = GateStatus.FAIL
                reasons.append(f"RELY and REFUSE overlap at target tuple {args}")
            if rely and not target:
                definability = GateStatus.FAIL
                preserved = GateStatus.FAIL
                reasons.append(f"RELY includes a required refusal at target tuple {args}")
            if refuse and target:
                definability = GateStatus.FAIL
                reasons.append(f"REFUSE includes a target-positive tuple {args}")
    if package.mode == "partial":
        definability = GateStatus.FAIL
        reasons.append("partial envelope does not explicitly define the residual")
    conservativity = GateStatus.PASS
    if dict(package.protected_signature_pins) != expected_pins:
        conservativity = GateStatus.FAIL
    expected_cost_keys = {
        "admissible_models",
        "candidate_atoms",
        "predicate_ast_nodes",
        "minimality",
    }
    if set(package.cost) != expected_cost_keys:
        package_binding = GateStatus.FAIL
        reasons.append("package cost has missing or unknown fields")
    actual_nodes = sum(
        formula_size(formula)
        for formula in (package.definition, package.rely_when, package.refuse_when)
        if formula is not None
    )
    features = _feature_atoms(problem)
    feature_bound_ok = len(features) <= problem.synthesis_policy.max_candidate_atoms
    if package.cost.get("admissible_models") != len(models):
        package_binding = GateStatus.FAIL
        reasons.append("package admissible-model cost is not reproducible")
    if package.cost.get("candidate_atoms") != len(features):
        package_binding = GateStatus.FAIL
        reasons.append("package candidate-atom cost is not reproducible")
    if package.cost.get("predicate_ast_nodes") != actual_nodes:
        package_binding = GateStatus.FAIL
        reasons.append("package predicate-size cost is not reproducible")
    minimality_value = package.cost.get("minimality")
    minimality = GateStatus.UNRESOLVED
    if minimality_value == "exact-finite-candidate-space" and feature_bound_ok:
        points = _target_points(problem, models)
        labels_by_vector: dict[tuple[bool, ...], set[bool]] = {}
        for structure, args, label in points:
            labels_by_vector.setdefault(
                _vector(
                    features,
                    problem=problem,
                    structure=structure,
                    target_args=args,
                ),
                set(),
            ).add(label)
        stable_true = {
            vector_ for vector_, labels in labels_by_vector.items()
            if labels == {True}
        }
        stable_false = {
            vector_ for vector_, labels in labels_by_vector.items()
            if labels == {False}
        }
        ambiguous = {
            vector_ for vector_, labels in labels_by_vector.items()
            if len(labels) > 1
        }
        equivalence_vectors = _all_shared_vectors(problem, features)
        try:
            if package.mode == "full" and not ambiguous:
                expected, _, _ = _minimal_dnf(
                    features=features,
                    positives=stable_true,
                    excluded=stable_false,
                    alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
                    equivalence_vectors=equivalence_vectors,
                )
                minimality = (
                    GateStatus.PASS
                    if formula_size(package.definition) == formula_size(expected)
                    else GateStatus.FAIL
                )
            elif package.mode == "partial" and ambiguous:
                expected_rely, _, _ = _minimal_dnf(
                    features=features,
                    positives=stable_true,
                    excluded=stable_false | ambiguous,
                    alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
                    equivalence_vectors=equivalence_vectors,
                )
                expected_refuse, _, _ = _minimal_dnf(
                    features=features,
                    positives=stable_false,
                    excluded=stable_true | ambiguous,
                    alternative_limit=problem.synthesis_policy.max_minimal_alternatives,
                    equivalence_vectors=equivalence_vectors,
                )
                minimality = (
                    GateStatus.PASS
                    if (
                        formula_size(package.rely_when)
                        == formula_size(expected_rely)
                        and formula_size(package.refuse_when)
                        == formula_size(expected_refuse)
                    )
                    else GateStatus.FAIL
                )
            else:
                minimality = GateStatus.FAIL
        except InventionError as exc:
            minimality = GateStatus.FAIL
            reasons.append(f"minimality replay failed: {exc}")
        if minimality is GateStatus.FAIL:
            reasons.append("package exact-minimality claim did not replay")
    elif minimality_value == "exact-finite-candidate-space":
        minimality = GateStatus.UNRESOLVED
    elif minimality_value != "unresolved":
        package_binding = GateStatus.FAIL
        reasons.append("package minimality status is unknown")
    return GateReport(
        gluing=gluing,
        conservativity=conservativity,
        definability=definability,
        preserved_refusals=preserved,
        minimality=minimality,
        receipt_binding=package_binding,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def verify_failure_certificate(
    problem: SeamProblem,
    certificate: FailureCertificate,
    *,
    alternatives: Sequence[PredicatePackage] = (),
) -> bool:
    """Replay objective failure witnesses; resource limits never verify as math."""
    if certificate.kind == FailureKind.RESOURCE_LIMIT:
        return False
    if certificate.kind == FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY:
        witness = certificate.witness
        try:
            _closed(
                witness,
                required={
                    "target_arguments",
                    "shared_vocabulary",
                    "shared_reduct",
                    "expansion_true",
                    "expansion_false",
                    "feature_vector",
                },
                optional=set(),
                where="fixed_language_non_definability.witness",
            )
            if witness["shared_vocabulary"] != list(problem.shared_vocabulary):
                return False
            args = tuple(witness["target_arguments"])
            true_model = normalize_structure(
                witness["expansion_true"], problem.signature
            )
            false_model = normalize_structure(
                witness["expansion_false"], problem.signature
            )
            for value, sort in zip(args, problem.target_decl.sorts):
                if value not in problem.signature.sorts[sort]:
                    return False
            if len(args) != problem.target_decl.arity:
                return False
        except (KeyError, TypeError, InventionError, FRSLError):
            return False
        constraints = [
            constraint
            for theory in problem.local_theories
            for constraint in theory.constraints
        ]
        if not all(evaluate(x, signature=problem.signature, structure=true_model) for x in constraints):
            return False
        if not all(evaluate(x, signature=problem.signature, structure=false_model) for x in constraints):
            return False
        if relation_reduct(true_model, problem.shared_vocabulary) != relation_reduct(
            false_model, problem.shared_vocabulary
        ):
            return False
        shared_reduct = relation_reduct(true_model, problem.shared_vocabulary)
        if witness["shared_reduct"] != shared_reduct:
            return False
        features = _feature_atoms(problem)
        expected_vector = list(
            _vector(
                features,
                problem=problem,
                structure=true_model,
                target_args=args,
            )
        )
        if witness["feature_vector"] != expected_vector or expected_vector != list(
            _vector(
                features,
                problem=problem,
                structure=false_model,
                target_args=args,
            )
        ):
            return False
        target_true = args in set(true_model[problem.target_predicate])
        target_false = args in set(false_model[problem.target_predicate])
        return target_true and not target_false
    if certificate.kind == FailureKind.TOPOLOGY_OBSTRUCTION:
        try:
            _closed(
                certificate.witness,
                required={"overlap_map", "right_arguments", "structure"},
                optional=set(),
                where="topology_obstruction.witness",
            )
            overlap = OverlapMap.from_dict(certificate.witness["overlap_map"])
            if overlap not in problem.overlap_maps:
                return False
            structure = normalize_structure(
                certificate.witness["structure"], problem.signature
            )
            args = tuple(certificate.witness["right_arguments"])
            constraints = [
                constraint
                for theory in problem.local_theories
                for constraint in theory.constraints
            ]
            if not all(
                evaluate(
                    constraint,
                    signature=problem.signature,
                    structure=structure,
                )
                for constraint in constraints
            ):
                return False
        except (KeyError, TypeError, InventionError, FRSLError):
            return False
        left_args = tuple(args[i] for i in overlap.argument_map)
        return (left_args in set(structure[overlap.left_relation])) != (
            args in set(structure[overlap.right_relation])
        )
    if certificate.kind == FailureKind.NON_UNIQUE_MINIMUM:
        try:
            _closed(
                certificate.witness,
                required={
                    "alternative_hashes",
                    "disagreement_pair",
                    "disagreement",
                    "candidate_space",
                },
                optional=set(),
                where="non_unique_minimum.witness",
            )
            _closed(
                certificate.witness["disagreement"],
                required={
                    "target_arguments",
                    "shared_structure",
                    "first_value",
                    "second_value",
                },
                optional=set(),
                where="non_unique_minimum.disagreement",
            )
        except (InventionError, KeyError, TypeError):
            return False
        hashes = certificate.witness.get("alternative_hashes")
        pair = certificate.witness.get("disagreement_pair")
        witness = certificate.witness.get("disagreement")
        if not (
            isinstance(hashes, list)
            and len(hashes) >= 2
            and len(set(hashes)) == len(hashes)
            and all(isinstance(x, str) and x.startswith("sha256:") for x in hashes)
            and isinstance(pair, list)
            and len(pair) == 2
            and set(pair).issubset(set(hashes))
            and isinstance(witness, dict)
            and alternatives
            and certificate.witness.get("candidate_space")
            == "FRSL-1 DNF cubes over declared feature atoms"
        ):
            return False
        by_hash = {package.package_hash: package for package in alternatives}
        if set(by_hash) != set(hashes):
            return False
        if any(
            package.problem_hash != problem.problem_hash
            or package.mode != "full"
            or package.cost.get("minimality") != "exact-finite-candidate-space"
            for package in alternatives
        ):
            return False
        first = by_hash.get(pair[0])
        second = by_hash.get(pair[1])
        if first is None or second is None:
            return False
        try:
            structure = normalize_structure(
                witness["shared_structure"], problem.signature
            )
            args = tuple(witness["target_arguments"])
            env = _point_environment(args)
            first_value = evaluate(
                first.definition,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            second_value = evaluate(
                second.definition,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
        except (KeyError, TypeError, FRSLError):
            return False
        if (
            first_value == second_value
            or first_value != witness.get("first_value")
            or second_value != witness.get("second_value")
        ):
            return False
        recomputed = synthesize(problem)
        return (
            recomputed.status is SynthesisStatus.CHOICE_REQUIRED
            and {x.package_hash for x in recomputed.alternatives} == set(hashes)
        )
    return False


def mint_invention_receipt(
    problem: SeamProblem,
    result: SynthesisResult,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    """Bind one experimental invention outcome into ActionReceipt v0.2.

    The receipt format is unchanged.  The experimental result is an action
    subject plus evidence hashes under the open action vocabulary.
    """
    from bulla.action_receipt import build_action_receipt

    if result.problem_hash != problem.problem_hash:
        raise InventionError("result does not bind the supplied problem")
    package_hash = (
        result.package.package_hash if result.package is not None else None
    )
    certificate_hash = (
        canonical_hash(result.certificate.to_dict())
        if result.certificate is not None
        else None
    )
    artifact_hash = canonical_hash(
        {
            "result_hash": result.result_hash,
            "package_hash": package_hash,
            "certificate_hash": certificate_hash,
        }
    )
    gate_hash = canonical_hash(result.gate_report.to_dict())
    policy_hash = canonical_hash(problem.synthesis_policy.to_dict())
    verifier_hash = canonical_hash(dict(result.verifier))
    return build_action_receipt(
        action={
            "type": "bulla.invent",
            "subject": {
                "problem_hash": problem.problem_hash,
                "result_hash": result.result_hash,
                "outcome": result.status.value,
                "artifact_hash": artifact_hash,
                "package_hash": package_hash,
                "certificate_hash": certificate_hash,
                "verifier_hash": verifier_hash,
                "synthesis_policy_hash": policy_hash,
            },
        },
        diagnostic_ref={"status": "reference", "ref": gate_hash},
        envelope=envelope,
        evidence_refs=(
            {
                "name": "seam_problem",
                "hash": problem.problem_hash,
                "grounding": "self_asserted",
            },
            {
                "name": "invention_artifact",
                "hash": artifact_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "synthesis_result",
                "hash": result.result_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "gate_report",
                "hash": gate_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "verifier",
                "hash": verifier_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "synthesis_policy",
                "hash": policy_hash,
                "grounding": "self_asserted",
            },
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )
