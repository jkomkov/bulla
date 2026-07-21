"""Finite J-tuple compilation and legislation detection.

The theorem-sized claim implemented here is deliberately narrow: when an
outcome is explicitly defined by a verified FRSL-1 package, that definition can
be carried as a scoped precedent without changing protected relations.  Any
additional required consequence is checked separately against every admissible
finite model.  A failing consequence yields a concrete countermodel and is
labeled legislation, not precedent.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass
from typing import Any, Mapping

from bulla.experimental.frsl import (
    Formula,
    canonical_hash,
    evaluate,
    formula_relations,
    normalize_structure,
    structure_to_dict,
    validate_formula,
)
from bulla.experimental.invention import (
    GateStatus,
    PredicatePackage,
    SeamProblem,
    SynthesisResult,
    SynthesisStatus,
    _admissible_models,
    _point_environment,
    _target_points,
    synthesize,
    verify_package,
)


class PrecedentStatus(str, enum.Enum):
    COMPILED = "COMPILED"
    LEGISLATION_REQUIRED = "LEGISLATION_REQUIRED"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class ConsequenceRule:
    label: str
    when_outcome: bool
    consequence: Formula

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("consequence rule label must be non-empty")

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "when_outcome": self.when_outcome,
            "consequence": self.consequence,
        }


@dataclass(frozen=True)
class JTuple:
    record_definition: Formula
    evidence_requirements: tuple[str, ...]
    authority: Mapping[str, Any]
    applicability_scope: Mapping[str, Any]
    outcome: str
    package_hash: str
    reason_vocabulary: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "record_definition": self.record_definition,
            "evidence_requirements": list(self.evidence_requirements),
            "authority": dict(self.authority),
            "applicability_scope": dict(self.applicability_scope),
            "outcome": self.outcome,
            "package_hash": self.package_hash,
            "reason_vocabulary": list(self.reason_vocabulary),
        }


@dataclass(frozen=True)
class FreshReasonCertificate:
    package_hash: str
    committed_vocabulary: tuple[str, ...]
    case_vocabulary: tuple[str, ...]
    fresh_reasons: tuple[str, ...]
    statement: str

    def to_dict(self) -> dict:
        return {
            "package_hash": self.package_hash,
            "committed_vocabulary": list(self.committed_vocabulary),
            "case_vocabulary": list(self.case_vocabulary),
            "fresh_reasons": list(self.fresh_reasons),
            "statement": self.statement,
        }


@dataclass(frozen=True)
class LegislationCountermodel:
    rule: str
    target_arguments: tuple[str, ...]
    structure: Mapping[str, Any]
    distinguishing_facts: tuple[Mapping[str, Any], ...]
    statement: str

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "target_arguments": list(self.target_arguments),
            "structure": dict(self.structure),
            "distinguishing_facts": [dict(x) for x in self.distinguishing_facts],
            "statement": self.statement,
        }


@dataclass(frozen=True)
class PrecedentCompilation:
    status: PrecedentStatus
    invention_result: SynthesisResult
    j_tuple: JTuple | None = None
    package: PredicatePackage | None = None
    countermodels: tuple[LegislationCountermodel, ...] = ()

    @property
    def compilation_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": "0.1-experimental",
            "status": self.status.value,
            "invention_result_hash": self.invention_result.result_hash,
            "j_tuple": self.j_tuple.to_dict() if self.j_tuple else None,
            "package": self.package.to_dict() if self.package else None,
            "countermodels": [x.to_dict() for x in self.countermodels],
        }


def compile_precedent(
    problem: SeamProblem,
    *,
    consequence_rules: tuple[ConsequenceRule, ...] = (),
) -> PrecedentCompilation:
    """Compile a record-determined outcome or retain its non-compilation exit."""
    invention = synthesize(problem)
    if invention.status is not SynthesisStatus.COMPILED or invention.package is None:
        return PrecedentCompilation(
            status=PrecedentStatus.ESCALATE,
            invention_result=invention,
        )
    package = invention.package
    protected = {
        relation
        for relations in problem.protected_signatures.values()
        for relation in relations
    }
    countermodels: list[LegislationCountermodel] = []
    models = _admissible_models(problem)
    for rule in consequence_rules:
        validate_formula(
            rule.consequence,
            signature=problem.signature,
            free_variables=problem.target_variables,
            where=f"consequence_rule[{rule.label}]",
        )
        leaked = formula_relations(rule.consequence) - protected
        if leaked:
            raise ValueError(
                f"consequence rule {rule.label!r} is not in the protected signature: "
                f"{sorted(leaked)}"
            )
        for structure, args, _ in _target_points(problem, models):
            env = _point_environment(args)
            outcome = evaluate(
                package.definition,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            if outcome != rule.when_outcome:
                continue
            consequence = evaluate(
                rule.consequence,
                signature=problem.signature,
                structure=structure,
                environment=env,
            )
            if not consequence:
                countermodels.append(
                    LegislationCountermodel(
                        rule=rule.label,
                        target_arguments=args,
                        structure=structure_to_dict(structure),
                        distinguishing_facts=_minimal_distinguishing_facts(
                            problem, package, rule, models, structure, args
                        ),
                        statement=(
                            "The proposed precedent adds a protected consequence "
                            "not entailed by the record-determined outcome."
                        ),
                    )
                )
                break
    if countermodels:
        return PrecedentCompilation(
            status=PrecedentStatus.LEGISLATION_REQUIRED,
            invention_result=invention,
            package=package,
            countermodels=tuple(countermodels),
        )
    j_tuple = JTuple(
        record_definition=package.definition,
        evidence_requirements=package.evidence_requirements,
        authority=package.authority,
        applicability_scope=package.scope,
        outcome=problem.target_predicate,
        package_hash=package.package_hash,
        reason_vocabulary=tuple(sorted(problem.shared_vocabulary)),
    )
    return PrecedentCompilation(
        status=PrecedentStatus.COMPILED,
        invention_result=invention,
        j_tuple=j_tuple,
        package=package,
    )


def _minimal_distinguishing_facts(
    problem: SeamProblem,
    package: PredicatePackage,
    rule: ConsequenceRule,
    models,
    witness,
    args: tuple[str, ...],
) -> tuple[Mapping[str, Any], ...]:
    """Smallest finite protected fact set forcing the legislation witness."""
    protected = sorted(
        {
            relation
            for relations in problem.protected_signatures.values()
            for relation in relations
        }
        | set(problem.shared_vocabulary)
    )
    facts = []
    for relation in protected:
        declaration = problem.signature.relations[relation]
        domains = [problem.signature.sorts[sort] for sort in declaration.sorts]
        for ground_ in itertools.product(*domains):
            ground = tuple(ground_)
            facts.append(
                {
                    "relation": relation,
                    "arguments": list(ground),
                    "value": ground in set(witness[relation]),
                }
            )

    def agrees(model, selected) -> bool:
        return all(
            (tuple(fact["arguments"]) in set(model[fact["relation"]])) == fact["value"]
            for fact in selected
        )

    def property_holds(model) -> bool:
        env = _point_environment(args)
        outcome = evaluate(
            package.definition,
            signature=problem.signature,
            structure=model,
            environment=env,
        )
        consequence = evaluate(
            rule.consequence,
            signature=problem.signature,
            structure=model,
            environment=env,
        )
        return outcome == rule.when_outcome and not consequence

    for size in range(len(facts) + 1):
        for selected in itertools.combinations(facts, size):
            if all(property_holds(model) for model in models if agrees(model, selected)):
                return tuple(selected)
    raise AssertionError("the full finite protected valuation must distinguish its own witness")


def verify_legislation_countermodel(
    problem: SeamProblem,
    package: PredicatePackage,
    rule: ConsequenceRule,
    countermodel: LegislationCountermodel,
) -> bool:
    """Replay that a proposed protected consequence is not already entailed."""
    if countermodel.rule != rule.label or package.problem_hash != problem.problem_hash:
        return False
    gates = verify_package(problem, package)
    if not (
        gates.gluing is GateStatus.PASS
        and gates.conservativity is GateStatus.PASS
        and gates.definability is GateStatus.PASS
        and gates.preserved_refusals is GateStatus.PASS
        and gates.receipt_binding is GateStatus.PASS
    ):
        return False
    protected = {
        relation
        for relations in problem.protected_signatures.values()
        for relation in relations
    }
    try:
        validate_formula(
            rule.consequence,
            signature=problem.signature,
            free_variables=problem.target_variables,
            where=f"consequence_rule[{rule.label}]",
        )
        if formula_relations(rule.consequence) - protected:
            return False
        structure = normalize_structure(countermodel.structure, problem.signature)
        args = tuple(countermodel.target_arguments)
        if len(args) != problem.target_decl.arity or any(
            value not in problem.signature.sorts[sort]
            for value, sort in zip(args, problem.target_decl.sorts)
        ):
            return False
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
        env = _point_environment(args)
        outcome = evaluate(
            package.definition,
            signature=problem.signature,
            structure=structure,
            environment=env,
        )
        consequence = evaluate(
            rule.consequence,
            signature=problem.signature,
            structure=structure,
            environment=env,
        )
    except (KeyError, TypeError, ValueError):
        return False
    if not (outcome == rule.when_outcome and not consequence):
        return False
    models = _admissible_models(problem)
    expected_facts = _minimal_distinguishing_facts(
        problem,
        package,
        rule,
        models,
        structure,
        args,
    )
    return tuple(countermodel.distinguishing_facts) == expected_facts


def check_reason_vocabulary(
    j_tuple: JTuple,
    case_vocabulary: tuple[str, ...],
) -> FreshReasonCertificate | None:
    """Return a replayable escalation certificate when a case adds a fresh reason."""
    if any(not isinstance(x, str) or not x for x in case_vocabulary):
        raise ValueError("case reason vocabulary must contain non-empty strings")
    case = tuple(sorted(set(case_vocabulary)))
    committed = tuple(sorted(set(j_tuple.reason_vocabulary)))
    fresh = tuple(sorted(set(case) - set(committed)))
    if not fresh:
        return None
    return FreshReasonCertificate(
        package_hash=j_tuple.package_hash,
        committed_vocabulary=committed,
        case_vocabulary=case,
        fresh_reasons=fresh,
        statement=(
            "The case introduces reasons outside the compiled precedent vocabulary; "
            "no priority involving them was established, so applicability escalates."
        ),
    )


def verify_fresh_reason_certificate(
    j_tuple: JTuple,
    certificate: FreshReasonCertificate,
) -> bool:
    committed = tuple(sorted(set(j_tuple.reason_vocabulary)))
    case = tuple(sorted(set(certificate.case_vocabulary)))
    fresh = tuple(sorted(set(case) - set(committed)))
    return bool(
        certificate.package_hash == j_tuple.package_hash
        and certificate.committed_vocabulary == committed
        and certificate.case_vocabulary == case
        and certificate.fresh_reasons == fresh
        and fresh
    )
