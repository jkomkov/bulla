"""Proof-gated candidate checking with an explicit disclosure budget.

This module does not call an LLM. It defines the provider-neutral boundary for
one-shot or iterative candidate generation: a proposer submits an FRSL-1 term,
the reference kernel either accepts a full package or returns a bounded shared
counterexample view. Private model state is hashed, never placed in the prompt
payload, and target truth is disclosed only when explicitly budgeted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from bulla.experimental.frsl import (
    Formula,
    canonical_hash,
    evaluate,
    formula_relations,
    normalize_formula,
    relation_reduct,
    structure_to_dict,
)
from bulla.experimental.invention import (
    GateReport,
    GateStatus,
    InventionError,
    PredicatePackage,
    SeamProblem,
    SynthesisStatus,
    _admissible_models,
    _feature_atoms,
    _make_package,
    _point_environment,
    synthesize,
    verify_package,
)


PROFILE = "bulla.hybrid-invention/0.1-draft"


class HybridStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    COUNTERMODEL = "COUNTERMODEL"
    GATE_REJECTED = "GATE_REJECTED"
    CHOICE_REQUIRED = "CHOICE_REQUIRED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"


@dataclass(frozen=True)
class CandidateProvenance:
    generator: str
    generator_version: str
    prompt_hash: str
    attempt: int

    def __post_init__(self) -> None:
        if not self.generator or not self.generator_version:
            raise InventionError("candidate generator and version are required")
        if not self.prompt_hash.startswith("sha256:"):
            raise InventionError("prompt_hash must be content-addressed")
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise InventionError("attempt must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator": self.generator,
            "generator_version": self.generator_version,
            "prompt_hash": self.prompt_hash,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class DisclosureBudget:
    allowed_relations: tuple[str, ...]
    reveal_target_value: bool
    max_countermodels: int
    max_ground_facts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_relations", tuple(self.allowed_relations))
        if len(set(self.allowed_relations)) != len(self.allowed_relations):
            raise InventionError("allowed_relations contains duplicates")
        if not isinstance(self.reveal_target_value, bool):
            raise InventionError("reveal_target_value must be boolean")
        for name, value in (
            ("max_countermodels", self.max_countermodels),
            ("max_ground_facts", self.max_ground_facts),
        ):
            if isinstance(value, bool) or value < 0:
                raise InventionError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_relations": list(self.allowed_relations),
            "reveal_target_value": self.reveal_target_value,
            "max_countermodels": self.max_countermodels,
            "max_ground_facts": self.max_ground_facts,
        }


@dataclass(frozen=True)
class HybridResult:
    status: HybridStatus
    problem_hash: str
    candidate_hash: str
    provenance: CandidateProvenance
    disclosure_budget_hash: str
    gate_report: GateReport | None = None
    package: PredicatePackage | None = None
    countermodel: Mapping[str, Any] | None = None
    disclosure_cost: Mapping[str, int] | None = None
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError("HybridResult is multi-valued; inspect .status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "status": self.status.value,
            "problem_hash": self.problem_hash,
            "candidate_hash": self.candidate_hash,
            "provenance": self.provenance.to_dict(),
            "disclosure_budget_hash": self.disclosure_budget_hash,
            "gate_report": self.gate_report.to_dict() if self.gate_report is not None else None,
            "package": self.package.to_dict() if self.package is not None else None,
            "countermodel": dict(self.countermodel) if self.countermodel is not None else None,
            "disclosure_cost": dict(self.disclosure_cost or {}),
            "reasons": list(self.reasons),
        }


def _candidate_countermodel(
    problem: SeamProblem,
    candidate: Formula,
) -> tuple[dict[str, Any], int] | None:
    relations = sorted(formula_relations(candidate))
    target_domains = [problem.signature.sorts[sort] for sort in problem.target_decl.sorts]
    import itertools

    for structure in _admissible_models(problem):
        for arguments_ in itertools.product(*target_domains):
            arguments = tuple(arguments_)
            candidate_value = evaluate(
                candidate,
                signature=problem.signature,
                structure=structure,
                environment=_point_environment(arguments),
            )
            target_value = arguments in set(structure[problem.target_predicate])
            if candidate_value == target_value:
                continue
            disclosed = relation_reduct(structure, relations)
            wire = structure_to_dict(disclosed)
            fact_count = sum(len(values) for values in wire.values())
            return (
                {
                    "target_arguments": list(arguments),
                    "candidate_value": candidate_value,
                    "target_value": target_value,
                    "shared_structure": wire,
                    "private_model_hash": canonical_hash(structure_to_dict(structure)),
                    "minimality": "candidate-relation-projection; globally unresolved",
                },
                fact_count,
            )
    return None


def check_candidate(
    problem: SeamProblem,
    candidate: Formula,
    *,
    provenance: CandidateProvenance,
    disclosure_budget: DisclosureBudget,
    emitted_countermodels: int = 0,
) -> HybridResult:
    """Check one proposal and return only budget-authorized diagnostic material."""
    budget_hash = canonical_hash(disclosure_budget.to_dict())
    try:
        normalized = normalize_formula(candidate)
        candidate_hash = canonical_hash(normalized)
    except (TypeError, ValueError) as exc:
        return HybridResult(
            HybridStatus.INVALID_CANDIDATE,
            problem.problem_hash,
            canonical_hash(candidate),
            provenance,
            budget_hash,
            reasons=(str(exc),),
        )
    relations = formula_relations(normalized)
    if not relations.issubset(set(problem.shared_vocabulary)):
        return HybridResult(
            HybridStatus.INVALID_CANDIDATE,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            reasons=(
                "candidate reads target, private, or undeclared relations: "
                + repr(sorted(relations - set(problem.shared_vocabulary))),
            ),
        )
    if not relations.issubset(set(disclosure_budget.allowed_relations)):
        return HybridResult(
            HybridStatus.BUDGET_EXHAUSTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            reasons=("candidate requires relations outside the declared disclosure budget",),
        )
    models = _admissible_models(problem)
    package = _make_package(
        problem,
        mode="full",
        definition=normalized,
        rely_when=None,
        refuse_when=None,
        model_count=len(models),
        feature_count=len(_feature_atoms(problem)),
        exact_minimality=False,
    )
    report = verify_package(problem, package)
    accepted = (
        report.gluing is GateStatus.PASS
        and report.conservativity is GateStatus.PASS
        and report.definability is GateStatus.PASS
        and report.preserved_refusals is GateStatus.PASS
        and report.receipt_binding is GateStatus.PASS
    )
    if accepted:
        reference = synthesize(problem)
        if reference.status is SynthesisStatus.CHOICE_REQUIRED:
            return HybridResult(
                HybridStatus.CHOICE_REQUIRED,
                problem.problem_hash,
                candidate_hash,
                provenance,
                budget_hash,
                gate_report=report,
                reasons=("a safe candidate exists, but exhaustive governance preserves multiple classes",),
            )
        return HybridResult(
            HybridStatus.ACCEPTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            gate_report=report,
            package=package,
            disclosure_cost={"countermodels": 0, "ground_facts": 0, "target_values": 0},
        )
    if emitted_countermodels >= disclosure_budget.max_countermodels:
        return HybridResult(
            HybridStatus.BUDGET_EXHAUSTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            gate_report=report,
            reasons=("countermodel count budget exhausted",),
        )
    witness = _candidate_countermodel(problem, normalized)
    if witness is None:
        return HybridResult(
            HybridStatus.GATE_REJECTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            gate_report=report,
            reasons=tuple(report.reasons),
        )
    countermodel, fact_count = witness
    if not disclosure_budget.reveal_target_value:
        return HybridResult(
            HybridStatus.GATE_REJECTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            gate_report=report,
            reasons=("target truth disclosure is not authorized; returning gate report only",),
        )
    if fact_count > disclosure_budget.max_ground_facts:
        return HybridResult(
            HybridStatus.BUDGET_EXHAUSTED,
            problem.problem_hash,
            candidate_hash,
            provenance,
            budget_hash,
            gate_report=report,
            reasons=("countermodel ground-fact budget exhausted",),
        )
    return HybridResult(
        HybridStatus.COUNTERMODEL,
        problem.problem_hash,
        candidate_hash,
        provenance,
        budget_hash,
        gate_report=report,
        countermodel=countermodel,
        disclosure_cost={"countermodels": 1, "ground_facts": fact_count, "target_values": 1},
        reasons=tuple(report.reasons),
    )
