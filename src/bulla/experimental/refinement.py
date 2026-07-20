"""Certified monotone refinement for experimental semantic invention.

The governing object is the finite set of admissible FRSL-1 structures under a
pinned authority epoch.  Evidence and precedent both intersect that set with a
checked constraint.  Authority changes never pretend to be intersections: they
create a new epoch and stale every dependent term.

All public artifacts contain commitments and FRSL constraints, never private
model bodies or raw evidence values.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.experimental.control_plane import ApplicationStatus, apply_package
from bulla.experimental.frsl import (
    Formula,
    atom,
    canonical_hash,
    conjunction,
    constant,
    evaluate,
    negate,
    normalize_formula,
    relation_reduct,
    structure_to_dict,
    validate_formula,
    variable,
)
from bulla.experimental.invention import (
    GateStatus,
    InventionError,
    LocalTheory,
    PredicatePackage,
    SeamProblem,
    SynthesisResult,
    SynthesisStatus,
    _admissible_models,
    synthesize,
    verify_package,
)
from bulla.experimental.observability import (
    ConservationManifest,
    EnrichmentRequest,
    EnrichmentResponse,
    LogicPassport,
    ObservableOffer,
    ProvidedFact,
    ResponseStatus,
    verify_enrichment_response,
)
from bulla.experimental.claim_flow import (
    AdoptionStatus,
    PrecedentAdoption,
    PrecedentEffect,
)
from bulla.experimental.precedent import JTuple, check_reason_vocabulary


SCHEMA_VERSION = "0.1-experimental"
REFINEMENT_VERIFIER = {
    "id": "bulla.experimental.refinement.reference",
    "version": "0.1-experimental",
    "trust": "direct-finite-enumeration",
}

# Legacy callers of the reusable finite kernel do not assert closure.  Their
# snapshots therefore bind an explicit UNKNOWN_COVERAGE warrant rather than
# inheriting an unstated closed-world assumption.  The Semantic Settlement
# profile always supplies a concrete ModelClosureWarrant hash.
UNKNOWN_CLOSURE_WARRANT_HASH = canonical_hash(
    {
        "profile": "bulla.model-closure-warrant/0.1-experimental",
        "status": "UNKNOWN_COVERAGE",
        "scope": "legacy-finite-kernel",
    }
)


def semantic_epoch(authority_epoch_hash: str, closure_warrant_hash: str) -> str:
    """Bind authority and model closure into the term-staleness epoch."""

    _require_digest(authority_epoch_hash, "semantic_epoch.authority_epoch")
    _require_digest(closure_warrant_hash, "semantic_epoch.closure_warrant_hash")
    return canonical_hash(
        {
            "profile": "bulla.semantic-epoch/0.1-experimental",
            "authority_epoch": authority_epoch_hash,
            "closure_warrant_hash": closure_warrant_hash,
        }
    )


def semantic_compilation_key(
    problem: SeamProblem,
    result: SynthesisResult,
    snapshot: "EnvelopeSnapshot",
    *,
    adapter_version: str,
) -> str:
    """Cache key for a term under its full semantic and lifecycle context."""

    if not adapter_version:
        raise InventionError("adapter_version must be non-empty")
    return canonical_hash(
        {
            "profile": "bulla.certified-semantic-refinement/0.1-experimental",
            "problem_hash": problem.problem_hash,
            "result_hash": result.result_hash,
            "package_hash": result.package.package_hash if result.package else None,
            "protected_signatures": {
                owner: list(relations)
                for owner, relations in sorted(problem.protected_signatures.items())
            },
            "synthesis_policy": problem.synthesis_policy.to_dict(),
            "adapter_version": adapter_version,
            "verifier": dict(result.verifier),
            "logic_passport_hash": snapshot.passport_hash,
            "conservation_manifest_hash": snapshot.manifest_hash,
            "semantic_state_hash": snapshot.semantic_state_hash,
            "authority_epoch": snapshot.authority_epoch,
            "closure_warrant_hash": snapshot.closure_warrant_hash,
            "semantic_epoch": snapshot.semantic_epoch,
        }
    )


def _closed(value: Any, *, required: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventionError(f"{where} must be an object")
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise InventionError(f"{where} is missing required keys {sorted(missing)}")
    if unknown:
        raise InventionError(f"{where} has unknown keys {sorted(unknown)}")
    return value


def _require_digest(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise InventionError(f"{where} must be a full lowercase sha256 digest")
    return value


def authority_epoch(authority: Mapping[str, Any]) -> str:
    if not isinstance(authority, Mapping) or not authority:
        raise InventionError("authority epoch requires a non-empty authority object")
    return canonical_hash({"kind": "bulla.semantic-authority-epoch/0.1", "authority": dict(authority)})


class AdmissionKind(str, enum.Enum):
    EVIDENCE = "EVIDENCE"
    PRECEDENT = "PRECEDENT"


class TransitionKind(str, enum.Enum):
    """The four honest movements of a compiled semantic interaction."""

    PRESERVE = "PRESERVE"
    REFINE = "REFINE"
    REVISE = "REVISE"
    ROUTE = "ROUTE"


@dataclass(frozen=True)
class ConstraintAdmission:
    kind: AdmissionKind
    constraint: Formula
    provenance: Mapping[str, Any]
    authority_epoch: str
    response_hashes: tuple[str, ...] = ()
    request_hash: str | None = None
    plan_hash: str | None = None
    evidence_classes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InventionError("unsupported ConstraintAdmission schema")
        _require_digest(self.authority_epoch, "constraint_admission.authority_epoch")
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise InventionError("ConstraintAdmission.provenance must be non-empty")
        responses = tuple(self.response_hashes)
        for value in responses:
            _require_digest(value, "constraint_admission.response_hashes[]")
        if len(responses) != len(set(responses)):
            raise InventionError("ConstraintAdmission response hashes must be unique")
        classes = tuple(self.evidence_classes)
        if len(classes) != len(set(classes)) or any(not value for value in classes):
            raise InventionError("ConstraintAdmission evidence classes must be unique")
        if self.kind is AdmissionKind.EVIDENCE:
            if self.request_hash is None or self.plan_hash is None or not responses or not classes:
                raise InventionError(
                    "evidence admission requires request, plan, response, and evidence-class bindings"
                )
            _require_digest(self.request_hash, "constraint_admission.request_hash")
            _require_digest(self.plan_hash, "constraint_admission.plan_hash")
        else:
            if self.request_hash is not None or self.plan_hash is not None or responses or classes:
                raise InventionError("precedent admission cannot impersonate an evidence handshake")
        object.__setattr__(self, "provenance", dict(self.provenance))
        object.__setattr__(self, "response_hashes", responses)
        object.__setattr__(self, "evidence_classes", classes)

    @property
    def admission_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "constraint": self.constraint,
            "provenance": dict(self.provenance),
            "authority_epoch": self.authority_epoch,
            "response_hashes": list(self.response_hashes),
            "request_hash": self.request_hash,
            "plan_hash": self.plan_hash,
            "evidence_classes": list(self.evidence_classes),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ConstraintAdmission":
        d = _closed(
            value,
            required={
                "schema_version",
                "kind",
                "constraint",
                "provenance",
                "authority_epoch",
                "response_hashes",
                "request_hash",
                "plan_hash",
                "evidence_classes",
            },
            where="constraint_admission",
        )
        if not isinstance(d["response_hashes"], list) or not isinstance(
            d["evidence_classes"], list
        ):
            raise InventionError("ConstraintAdmission hash/class fields must be arrays")
        return cls(
            schema_version=d["schema_version"],
            kind=AdmissionKind(d["kind"]),
            constraint=d["constraint"],
            provenance=d["provenance"],
            authority_epoch=d["authority_epoch"],
            response_hashes=tuple(d["response_hashes"]),
            request_hash=d["request_hash"],
            plan_hash=d["plan_hash"],
            evidence_classes=tuple(d["evidence_classes"]),
        )


def _instantiate(formula: Formula, arguments: Sequence[str]) -> Formula:
    """Replace free x0..xn variables with named constants, respecting binders."""

    replacements = {f"x{i}": constant(value) for i, value in enumerate(arguments)}

    def term(value: Mapping[str, str], bound: set[str]) -> dict[str, str]:
        if "var" in value and value["var"] in replacements and value["var"] not in bound:
            return dict(replacements[value["var"]])
        return dict(value)

    def visit(node: Formula, bound: set[str]) -> Formula:
        op = node["op"]
        if op in ("true", "false"):
            return dict(node)
        if op == "atom":
            return {
                "op": "atom",
                "relation": node["relation"],
                "args": [term(value, bound) for value in node["args"]],
            }
        if op == "eq":
            return {
                "op": "eq",
                "sort": node["sort"],
                "left": term(node["left"], bound),
                "right": term(node["right"], bound),
            }
        if op == "not":
            return {"op": "not", "body": visit(node["body"], bound)}
        if op in ("and", "or"):
            return {"op": op, "args": [visit(value, bound) for value in node["args"]]}
        if op in ("implies", "iff"):
            return {
                "op": op,
                "left": visit(node["left"], bound),
                "right": visit(node["right"], bound),
            }
        if op in ("forall", "exists"):
            nested = set(bound)
            nested.add(node["var"])
            return {
                "op": op,
                "var": node["var"],
                "sort": node["sort"],
                "body": visit(node["body"], nested),
            }
        raise InventionError(f"cannot instantiate unknown FRSL operation {op!r}")

    return normalize_formula(visit(formula, set()))


def _validate_fact(problem: SeamProblem, offer: ObservableOffer, fact: ProvidedFact) -> None:
    if fact.relation != offer.relation:
        raise InventionError("provided fact does not bind its observable relation")
    if fact.evidence_class != offer.warrant_profile["evidence_class"]:
        raise InventionError("provided fact evidence class differs from the offer")
    if len(fact.arguments) != len(offer.sorts):
        raise InventionError("provided fact has the wrong arity")
    for value, sort in zip(fact.arguments, offer.sorts):
        if value not in problem.signature.sorts[sort]:
            raise InventionError("provided fact contains an out-of-sort value")


def build_evidence_admission(
    problem: SeamProblem,
    request: EnrichmentRequest,
    *,
    selected_plan_hash: str,
    responses: Sequence[EnrichmentResponse],
    passport: LogicPassport,
    manifest: ConservationManifest,
    epoch: str,
) -> ConstraintAdmission:
    """Admit one complete, consented observable plan as an FRSL constraint."""

    _require_digest(epoch, "epoch")
    if request.problem_hash != problem.problem_hash:
        raise InventionError("enrichment request does not bind the supplied problem")
    if request.passport_hash != passport.passport_hash or request.manifest_hash != manifest.manifest_hash:
        raise InventionError("enrichment request semantic context does not match")
    manifest.validate_for_problem(problem)
    if epoch != authority_epoch(problem.authority):
        raise InventionError("evidence admission uses a stale or foreign authority epoch")
    try:
        plan = next(candidate for candidate in request.plans if candidate.plan_hash == selected_plan_hash)
    except StopIteration as exc:
        raise InventionError("selected enrichment plan was not offered") from exc
    verified: dict[str, EnrichmentResponse] = {}
    for response in responses:
        if not verify_enrichment_response(request, response):
            raise InventionError("enrichment response proof did not replay")
        if response.responder in verified:
            raise InventionError("one responder cannot submit two operative responses")
        verified[response.responder] = response
    for subject in plan.consent_subjects:
        response = verified.get(subject)
        if response is None or response.status not in (ResponseStatus.CONSENT, ResponseStatus.PROVIDE):
            raise InventionError(f"missing operative consent from {subject!r}")
        if response.selected_plan_hash != selected_plan_hash:
            raise InventionError("consent binds a different enrichment plan")
    by_offer = {offer.offer_id: offer for offer in request.offers}
    constraints: list[Formula] = []
    fact_commitments: list[dict[str, Any]] = []
    evidence_classes: set[str] = set()
    for offer_id in plan.observable_ids:
        offer = by_offer[offer_id]
        provider_response = verified.get(offer.provider)
        if provider_response is None or provider_response.status is not ResponseStatus.PROVIDE:
            raise InventionError(f"observable {offer_id!r} lacks a provider PROVIDE response")
        facts = [fact for fact in provider_response.provided_facts if fact.relation == offer.relation]
        expected_arguments = set(
            itertools.product(*(problem.signature.sorts[sort] for sort in offer.sorts))
        )
        actual_arguments = {tuple(fact.arguments) for fact in facts}
        if len(facts) != len(actual_arguments) or actual_arguments != expected_arguments:
            raise InventionError(
                f"observable {offer_id!r} must provide exactly one Boolean for every ground tuple"
            )
        for fact in sorted(facts, key=lambda item: item.arguments):
            _validate_fact(problem, offer, fact)
            instantiated = _instantiate(offer.meaning, fact.arguments)
            constraint = instantiated if fact.truth else negate(instantiated)
            constraint = normalize_formula(constraint)
            validate_formula(constraint, signature=problem.signature, where="admitted_evidence")
            constraints.append(constraint)
            fact_commitments.append(
                {
                    "offer_id": offer_id,
                    "relation": offer.relation,
                    "arguments_hash": canonical_hash(list(fact.arguments)),
                    "truth": fact.truth,
                    "warrant_ref": fact.warrant_ref,
                    "response_hash": provider_response.response_hash,
                }
            )
            evidence_classes.add(fact.evidence_class)
    admitted = normalize_formula(conjunction(constraints))
    return ConstraintAdmission(
        kind=AdmissionKind.EVIDENCE,
        constraint=admitted,
        provenance={
            "request_hash": request.request_hash,
            "plan_hash": plan.plan_hash,
            "fact_commitments": fact_commitments,
            "providers": sorted({by_offer[offer_id].provider for offer_id in plan.observable_ids}),
            "warrant_verifiers": sorted(
                {by_offer[offer_id].warrant_profile["verifier"] for offer_id in plan.observable_ids}
            ),
        },
        authority_epoch=epoch,
        response_hashes=tuple(sorted(response.response_hash for response in verified.values())),
        request_hash=request.request_hash,
        plan_hash=plan.plan_hash,
        evidence_classes=tuple(sorted(evidence_classes)),
    )


def build_precedent_admission(
    problem: SeamProblem,
    *,
    constraint: Formula,
    j_tuple: JTuple,
    adoption: PrecedentAdoption,
    case_reason_vocabulary: tuple[str, ...],
    jurisdiction: str,
    finality_ref: str,
    applicability_ref: str,
    semantic_epoch_ref: str,
    epoch: str,
) -> ConstraintAdmission:
    """Admit only an explicitly authorized, binding precedent adoption.

    A compiled J-tuple is not itself precedent.  The separate adoption binds a
    final forum finding, an explicit reason, scope, effect, and precedential
    authority receipt.  Anything else remains case-only or persuasive.
    """

    if not jurisdiction or not finality_ref or not applicability_ref:
        raise InventionError("precedent admission requires jurisdiction, finality, and applicability")
    _require_digest(finality_ref, "precedent.finality_ref")
    _require_digest(applicability_ref, "precedent.applicability_ref")
    _require_digest(semantic_epoch_ref, "precedent.semantic_epoch_ref")
    if epoch != authority_epoch(problem.authority):
        raise InventionError("precedent admission uses a stale authority epoch")
    if dict(j_tuple.authority) != dict(problem.authority):
        raise InventionError("precedent J-tuple does not bind the seam authority")
    if adoption.status is not AdoptionStatus.ADOPTED or adoption.rule is None:
        raise InventionError("precedent admission requires a verified adopted rule")
    if adoption.rule.effect is not PrecedentEffect.BINDING_WITHIN_SCOPE:
        raise InventionError("case-only and persuasive findings cannot mutate semantic state")
    if (
        adoption.semantic_epoch != semantic_epoch_ref
        or adoption.rule.semantic_epoch != semantic_epoch_ref
    ):
        raise InventionError("precedent adoption belongs to another semantic epoch")
    if adoption.institutional_fact_hash != finality_ref:
        raise InventionError("finality reference must bind the adopted institutional fact")
    if adoption.rule.applicability_scope.scope_hash != applicability_ref:
        raise InventionError("applicability reference must bind the adopted structured scope")
    if normalize_formula(constraint) != adoption.rule.reason:
        raise InventionError("precedent constraint must equal the explicitly adopted reason")
    fresh = check_reason_vocabulary(j_tuple, case_reason_vocabulary)
    if fresh is not None:
        raise InventionError("fresh precedent reason requires institutional escalation")
    if set(case_reason_vocabulary) - set(adoption.rule.reason_vocabulary):
        raise InventionError("case introduces reasons outside the adopted precedent rule")
    validate_formula(constraint, signature=problem.signature, where="precedent_constraint")
    return ConstraintAdmission(
        kind=AdmissionKind.PRECEDENT,
        constraint=normalize_formula(constraint),
        provenance={
            "j_tuple_package_hash": j_tuple.package_hash,
            "precedent_adoption_hash": adoption.adoption_hash,
            "precedent_rule_hash": adoption.rule.rule_hash,
            "precedential_authority_token_hash": (
                adoption.rule.precedential_authority_token_hash
            ),
            "jurisdiction": jurisdiction,
            "finality_ref": finality_ref,
            "applicability_ref": applicability_ref,
            "semantic_epoch_ref": semantic_epoch_ref,
            "reason_vocabulary": sorted(set(case_reason_vocabulary)),
        },
        authority_epoch=epoch,
    )


def _effective_problem(
    problem: SeamProblem,
    admissions: Sequence[ConstraintAdmission],
    *,
    epoch: str,
) -> SeamProblem:
    if epoch != authority_epoch(problem.authority):
        raise InventionError("authority revision requires a new epoch, not a refinement")
    admissions = tuple(admissions)
    for index, admission in enumerate(admissions):
        if admission.authority_epoch != epoch:
            raise InventionError("constraint admission belongs to a different authority epoch")
        validate_formula(
            admission.constraint,
            signature=problem.signature,
            where=f"admissions[{index}].constraint",
        )
    first = problem.local_theories[0]
    enriched_first = LocalTheory(
        owner=first.owner,
        constraints=tuple(first.constraints) + tuple(admission.constraint for admission in admissions),
    )
    return dataclasses.replace(
        problem,
        local_theories=(enriched_first,) + tuple(problem.local_theories[1:]),
    )


@dataclass(frozen=True)
class SemanticState:
    base_problem_hash: str
    effective_problem_hash: str
    authority_epoch: str
    admission_hashes: tuple[str, ...]
    model_hashes: tuple[str, ...]
    _models: tuple[Mapping[str, tuple[tuple[str, ...], ...]], ...] = dataclasses.field(
        repr=False,
        compare=False,
    )

    @property
    def state_hash(self) -> str:
        return canonical_hash(self.commitment())

    def commitment(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "base_problem_hash": self.base_problem_hash,
            "effective_problem_hash": self.effective_problem_hash,
            "authority_epoch": self.authority_epoch,
            "admission_hashes": list(self.admission_hashes),
            "model_hashes": list(self.model_hashes),
        }


def semantic_state(
    problem: SeamProblem,
    admissions: Sequence[ConstraintAdmission] = (),
    *,
    epoch: str | None = None,
) -> tuple[SeamProblem, SemanticState]:
    epoch = epoch or authority_epoch(problem.authority)
    effective = _effective_problem(problem, admissions, epoch=epoch)
    models = tuple(_admissible_models(effective))
    if not models:
        raise InventionError("admitted constraints eliminate every semantic world")
    model_hashes = tuple(
        sorted(canonical_hash(structure_to_dict(dict(model))) for model in models)
    )
    state = SemanticState(
        base_problem_hash=problem.problem_hash,
        effective_problem_hash=effective.problem_hash,
        authority_epoch=epoch,
        admission_hashes=tuple(admission.admission_hash for admission in admissions),
        model_hashes=model_hashes,
        _models=models,
    )
    return effective, state


def classify_transition(
    prior: SemanticState,
    next_state: SemanticState | None = None,
    *,
    routed: bool = False,
) -> TransitionKind:
    """Classify a state movement without scalar or Boolean coercion.

    ROUTE leaves the governing state untouched and sends one unresolved case
    elsewhere.  An epoch change is REVISE.  Inside one epoch, equality is
    PRESERVE and strict world-set inclusion is REFINE.  A same-epoch widening
    or incomparable set is neither: it is rejected rather than mislabeled.
    """

    if routed:
        if next_state is not None:
            raise InventionError("ROUTE does not mutate the governing semantic state")
        return TransitionKind.ROUTE
    if next_state is None:
        raise InventionError("a non-ROUTE transition requires a next semantic state")
    if prior.base_problem_hash != next_state.base_problem_hash:
        raise InventionError("semantic transition cannot change the base problem")
    if prior.authority_epoch != next_state.authority_epoch:
        return TransitionKind.REVISE
    prior_worlds = set(prior.model_hashes)
    next_worlds = set(next_state.model_hashes)
    if next_worlds == prior_worlds:
        return TransitionKind.PRESERVE
    if next_worlds < prior_worlds:
        return TransitionKind.REFINE
    raise InventionError(
        "same-epoch state widening or replacement is invalid; create a new authority epoch"
    )


@dataclass(frozen=True)
class EnvelopeRegions:
    reachable: tuple[str, ...]
    rely: tuple[str, ...]
    refuse: tuple[str, ...]
    ambiguous: tuple[str, ...]

    def __post_init__(self) -> None:
        sets = {
            name: set(getattr(self, name)) for name in ("reachable", "rely", "refuse", "ambiguous")
        }
        if sets["rely"] & sets["refuse"] or sets["rely"] & sets["ambiguous"] or sets["refuse"] & sets["ambiguous"]:
            raise InventionError("envelope regions must be disjoint")
        if sets["reachable"] != sets["rely"] | sets["refuse"] | sets["ambiguous"]:
            raise InventionError("envelope regions must partition reachable observations")
        for name in sets:
            values = tuple(sorted(sets[name]))
            for value in values:
                _require_digest(value, f"envelope_regions.{name}[]")
            object.__setattr__(self, name, values)

    @property
    def regions_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": list(self.reachable),
            "rely": list(self.rely),
            "refuse": list(self.refuse),
            "ambiguous": list(self.ambiguous),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EnvelopeRegions":
        d = _closed(
            value,
            required={"reachable", "rely", "refuse", "ambiguous"},
            where="envelope_regions",
        )
        if any(not isinstance(d[name], list) for name in d):
            raise InventionError("EnvelopeRegions fields must be arrays")
        return cls(**{name: tuple(d[name]) for name in d})


def _package_regions(
    problem: SeamProblem,
    package: PredicatePackage,
    state: SemanticState,
) -> EnvelopeRegions:
    decisions: dict[str, str] = {}
    domains = [problem.signature.sorts[sort] for sort in problem.target_decl.sorts]
    for model in state._models:
        reduct_hash = canonical_hash(relation_reduct(model, problem.shared_vocabulary))
        for arguments in itertools.product(*domains):
            key = canonical_hash(
                {"shared_reduct_hash": reduct_hash, "target_arguments": list(arguments)}
            )
            environment = {f"x{i}": value for i, value in enumerate(arguments)}
            if package.mode == "full":
                decision = (
                    "RELY"
                    if evaluate(
                        package.definition,
                        signature=problem.signature,
                        structure=model,
                        environment=environment,
                    )
                    else "REFUSE"
                )
            else:
                rely = evaluate(
                    package.rely_when,
                    signature=problem.signature,
                    structure=model,
                    environment=environment,
                )
                refuse = evaluate(
                    package.refuse_when,
                    signature=problem.signature,
                    structure=model,
                    environment=environment,
                )
                if rely and refuse:
                    raise InventionError("package overlaps RELY and REFUSE during snapshot replay")
                decision = "RELY" if rely else "REFUSE" if refuse else "ESCALATE"
            previous = decisions.setdefault(key, decision)
            if previous != decision:
                raise InventionError("a package is not a function of its declared shared reduct")
    return EnvelopeRegions(
        reachable=tuple(decisions),
        rely=tuple(key for key, value in decisions.items() if value == "RELY"),
        refuse=tuple(key for key, value in decisions.items() if value == "REFUSE"),
        ambiguous=tuple(key for key, value in decisions.items() if value == "ESCALATE"),
    )


@dataclass(frozen=True)
class EnvelopeSnapshot:
    base_problem_hash: str
    effective_problem_hash: str
    result_hash: str
    package_hash: str
    package_mode: str
    semantic_state_hash: str
    passport_hash: str
    manifest_hash: str
    authority_epoch: str
    closure_warrant_hash: str
    semantic_epoch: str
    regions: EnvelopeRegions
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.package_mode not in {"full", "partial"}:
            raise InventionError("unsupported EnvelopeSnapshot schema or package mode")
        for name in (
            "base_problem_hash",
            "effective_problem_hash",
            "result_hash",
            "package_hash",
            "semantic_state_hash",
            "passport_hash",
            "manifest_hash",
            "authority_epoch",
            "closure_warrant_hash",
            "semantic_epoch",
        ):
            _require_digest(getattr(self, name), f"envelope_snapshot.{name}")

    @property
    def snapshot_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "base_problem_hash": self.base_problem_hash,
            "effective_problem_hash": self.effective_problem_hash,
            "result_hash": self.result_hash,
            "package_hash": self.package_hash,
            "package_mode": self.package_mode,
            "semantic_state_hash": self.semantic_state_hash,
            "passport_hash": self.passport_hash,
            "manifest_hash": self.manifest_hash,
            "authority_epoch": self.authority_epoch,
            "closure_warrant_hash": self.closure_warrant_hash,
            "semantic_epoch": self.semantic_epoch,
            "regions": self.regions.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EnvelopeSnapshot":
        d = _closed(
            value,
            required={
                "schema_version",
                "base_problem_hash",
                "effective_problem_hash",
                "result_hash",
                "package_hash",
                "package_mode",
                "semantic_state_hash",
                "passport_hash",
                "manifest_hash",
                "authority_epoch",
                "closure_warrant_hash",
                "semantic_epoch",
                "regions",
            },
            where="envelope_snapshot",
        )
        return cls(
            **{key: value for key, value in d.items() if key != "regions"},
            regions=EnvelopeRegions.from_dict(d["regions"]),
        )


def envelope_snapshot(
    base_problem: SeamProblem,
    effective_problem: SeamProblem,
    result: SynthesisResult,
    state: SemanticState,
    *,
    passport: LogicPassport,
    manifest: ConservationManifest,
    closure_warrant_hash: str = UNKNOWN_CLOSURE_WARRANT_HASH,
) -> EnvelopeSnapshot:
    if result.problem_hash != effective_problem.problem_hash or result.package is None:
        raise InventionError("snapshot requires an executable result for the effective problem")
    if result.status not in (SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL):
        raise InventionError("snapshot accepts only COMPILED or PARTIAL results")
    report = verify_package(effective_problem, result.package)
    required = (
        report.gluing is GateStatus.PASS
        and report.conservativity is GateStatus.PASS
        and report.preserved_refusals is GateStatus.PASS
        and report.receipt_binding is GateStatus.PASS
    )
    if result.status is SynthesisStatus.COMPILED:
        required = required and report.definability is GateStatus.PASS
    if not required:
        raise InventionError("snapshot package failed independent semantic gates")
    return EnvelopeSnapshot(
        base_problem_hash=base_problem.problem_hash,
        effective_problem_hash=effective_problem.problem_hash,
        result_hash=result.result_hash,
        package_hash=result.package.package_hash,
        package_mode=result.package.mode,
        semantic_state_hash=state.state_hash,
        passport_hash=passport.passport_hash,
        manifest_hash=manifest.manifest_hash,
        authority_epoch=state.authority_epoch,
        closure_warrant_hash=closure_warrant_hash,
        semantic_epoch=semantic_epoch(state.authority_epoch, closure_warrant_hash),
        regions=_package_regions(effective_problem, result.package, state),
    )


@dataclass(frozen=True)
class RefinementCertificate:
    prior_state_hash: str
    new_state_hash: str
    admitted_constraint_hash: str
    prior_snapshot_hash: str
    new_snapshot_hash: str
    state_inclusion: bool
    retained_rely: bool
    retained_refuse: bool
    ambiguity_narrowed: bool
    authority_preserved: bool
    verifier: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InventionError("unsupported RefinementCertificate schema")
        for name in (
            "prior_state_hash",
            "new_state_hash",
            "admitted_constraint_hash",
            "prior_snapshot_hash",
            "new_snapshot_hash",
        ):
            _require_digest(getattr(self, name), f"refinement_certificate.{name}")
        if not isinstance(self.verifier, Mapping) or not self.verifier:
            raise InventionError("RefinementCertificate verifier must be non-empty")
        for name in (
            "state_inclusion",
            "retained_rely",
            "retained_refuse",
            "ambiguity_narrowed",
            "authority_preserved",
        ):
            if not isinstance(getattr(self, name), bool):
                raise InventionError(f"RefinementCertificate.{name} must be Boolean")
        object.__setattr__(self, "verifier", dict(self.verifier))

    @property
    def valid(self) -> bool:
        return all(
            (
                self.state_inclusion,
                self.retained_rely,
                self.retained_refuse,
                self.ambiguity_narrowed,
                self.authority_preserved,
            )
        )

    def __bool__(self) -> bool:
        raise TypeError("RefinementCertificate has independent gates; inspect .valid")

    @property
    def certificate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "prior_state_hash": self.prior_state_hash,
            "new_state_hash": self.new_state_hash,
            "admitted_constraint_hash": self.admitted_constraint_hash,
            "prior_snapshot_hash": self.prior_snapshot_hash,
            "new_snapshot_hash": self.new_snapshot_hash,
            "state_inclusion": self.state_inclusion,
            "retained_rely": self.retained_rely,
            "retained_refuse": self.retained_refuse,
            "ambiguity_narrowed": self.ambiguity_narrowed,
            "authority_preserved": self.authority_preserved,
            "verifier": dict(self.verifier),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RefinementCertificate":
        d = _closed(
            value,
            required={
                "schema_version",
                "prior_state_hash",
                "new_state_hash",
                "admitted_constraint_hash",
                "prior_snapshot_hash",
                "new_snapshot_hash",
                "state_inclusion",
                "retained_rely",
                "retained_refuse",
                "ambiguity_narrowed",
                "authority_preserved",
                "verifier",
            },
            where="refinement_certificate",
        )
        return cls(**d)


def _certificate(
    prior_state: SemanticState,
    new_state: SemanticState,
    admission: ConstraintAdmission,
    prior_snapshot: EnvelopeSnapshot,
    new_snapshot: EnvelopeSnapshot,
) -> RefinementCertificate:
    prior_reachable = set(prior_snapshot.regions.reachable)
    new_reachable = set(new_snapshot.regions.reachable)
    still_reachable = prior_reachable & new_reachable
    return RefinementCertificate(
        prior_state_hash=prior_state.state_hash,
        new_state_hash=new_state.state_hash,
        admitted_constraint_hash=canonical_hash(admission.constraint),
        prior_snapshot_hash=prior_snapshot.snapshot_hash,
        new_snapshot_hash=new_snapshot.snapshot_hash,
        state_inclusion=set(new_state.model_hashes).issubset(prior_state.model_hashes),
        retained_rely=(set(prior_snapshot.regions.rely) & still_reachable).issubset(
            new_snapshot.regions.rely
        ),
        retained_refuse=(set(prior_snapshot.regions.refuse) & still_reachable).issubset(
            new_snapshot.regions.refuse
        ),
        ambiguity_narrowed=set(new_snapshot.regions.ambiguous).issubset(
            set(prior_snapshot.regions.ambiguous)
        ),
        authority_preserved=(
            prior_state.authority_epoch
            == new_state.authority_epoch
            == admission.authority_epoch
        ),
        verifier=REFINEMENT_VERIFIER,
    )


@dataclass(frozen=True)
class RefinementBundle:
    base_problem: SeamProblem
    prior_result: SynthesisResult
    prior_admissions: tuple[ConstraintAdmission, ...]
    admission: ConstraintAdmission
    passport: LogicPassport
    manifest: ConservationManifest
    new_result: SynthesisResult
    prior_snapshot: EnvelopeSnapshot
    new_snapshot: EnvelopeSnapshot
    certificate: RefinementCertificate
    schema_version: str = SCHEMA_VERSION

    @property
    def bundle_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "base_problem": self.base_problem.to_dict(),
            "prior_result": self.prior_result.to_dict(),
            "prior_admissions": [item.to_dict() for item in self.prior_admissions],
            "admission": self.admission.to_dict(),
            "passport": self.passport.to_dict(),
            "manifest": self.manifest.to_dict(),
            "new_result": self.new_result.to_dict(),
            "prior_snapshot": self.prior_snapshot.to_dict(),
            "new_snapshot": self.new_snapshot.to_dict(),
            "certificate": self.certificate.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RefinementBundle":
        d = _closed(
            value,
            required={
                "schema_version",
                "base_problem",
                "prior_result",
                "prior_admissions",
                "admission",
                "passport",
                "manifest",
                "new_result",
                "prior_snapshot",
                "new_snapshot",
                "certificate",
            },
            where="refinement_bundle",
        )
        if d["schema_version"] != SCHEMA_VERSION or not isinstance(d["prior_admissions"], list):
            raise InventionError("unsupported RefinementBundle schema")
        return cls(
            schema_version=d["schema_version"],
            base_problem=SeamProblem.from_dict(d["base_problem"]),
            prior_result=SynthesisResult.from_dict(d["prior_result"]),
            prior_admissions=tuple(ConstraintAdmission.from_dict(x) for x in d["prior_admissions"]),
            admission=ConstraintAdmission.from_dict(d["admission"]),
            passport=LogicPassport.from_dict(d["passport"]),
            manifest=ConservationManifest.from_dict(d["manifest"]),
            new_result=SynthesisResult.from_dict(d["new_result"]),
            prior_snapshot=EnvelopeSnapshot.from_dict(d["prior_snapshot"]),
            new_snapshot=EnvelopeSnapshot.from_dict(d["new_snapshot"]),
            certificate=RefinementCertificate.from_dict(d["certificate"]),
        )


def refine_envelope(
    base_problem: SeamProblem,
    prior_result: SynthesisResult,
    admission: ConstraintAdmission,
    *,
    passport: LogicPassport,
    manifest: ConservationManifest,
    prior_admissions: Sequence[ConstraintAdmission] = (),
    closure_warrant_hash: str = UNKNOWN_CLOSURE_WARRANT_HASH,
) -> RefinementBundle:
    epoch = authority_epoch(base_problem.authority)
    manifest.validate_for_problem(base_problem)
    if admission.authority_epoch != epoch:
        raise InventionError("authority change is REVISE, not REFINE")
    prior_effective, prior_state = semantic_state(
        base_problem,
        prior_admissions,
        epoch=epoch,
    )
    if prior_result.problem_hash != prior_effective.problem_hash:
        raise InventionError("prior result does not bind the prior semantic state")
    prior_snapshot = envelope_snapshot(
        base_problem,
        prior_effective,
        prior_result,
        prior_state,
        passport=passport,
        manifest=manifest,
        closure_warrant_hash=closure_warrant_hash,
    )
    new_admissions = tuple(prior_admissions) + (admission,)
    new_effective, new_state = semantic_state(base_problem, new_admissions, epoch=epoch)
    new_result = synthesize(new_effective)
    if new_result.status not in (SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL):
        raise InventionError(
            f"admitted constraint did not produce an executable envelope: {new_result.status.value}"
        )
    new_snapshot = envelope_snapshot(
        base_problem,
        new_effective,
        new_result,
        new_state,
        passport=passport,
        manifest=manifest,
        closure_warrant_hash=closure_warrant_hash,
    )
    certificate = _certificate(
        prior_state,
        new_state,
        admission,
        prior_snapshot,
        new_snapshot,
    )
    if not certificate.valid:
        raise InventionError("candidate refinement retracts a certified region or changes authority")
    return RefinementBundle(
        base_problem=base_problem,
        prior_result=prior_result,
        prior_admissions=tuple(prior_admissions),
        admission=admission,
        passport=passport,
        manifest=manifest,
        new_result=new_result,
        prior_snapshot=prior_snapshot,
        new_snapshot=new_snapshot,
        certificate=certificate,
    )


def verify_refinement(bundle: RefinementBundle) -> bool:
    try:
        replay = refine_envelope(
            bundle.base_problem,
            bundle.prior_result,
            bundle.admission,
            passport=bundle.passport,
            manifest=bundle.manifest,
            prior_admissions=bundle.prior_admissions,
            closure_warrant_hash=bundle.prior_snapshot.closure_warrant_hash,
        )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        replay.new_result.result_hash == bundle.new_result.result_hash
        and replay.prior_snapshot.snapshot_hash == bundle.prior_snapshot.snapshot_hash
        and replay.new_snapshot.snapshot_hash == bundle.new_snapshot.snapshot_hash
        and replay.certificate.certificate_hash == bundle.certificate.certificate_hash
        and bundle.certificate.valid
    )


class ApplicationCause(str, enum.Enum):
    DECIDED = "DECIDED"
    RESIDUAL = "RESIDUAL"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    TERM_STALE = "TERM_STALE"


@dataclass(frozen=True)
class RefinedApplicationResult:
    status: ApplicationStatus
    cause: ApplicationCause
    snapshot_hash: str
    authority_epoch: str
    application: Mapping[str, Any] | None
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError("RefinedApplicationResult is three-valued; inspect .status and .cause")

    @property
    def result_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "cause": self.cause.value,
            "snapshot_hash": self.snapshot_hash,
            "authority_epoch": self.authority_epoch,
            "application": dict(self.application) if self.application is not None else None,
            "reasons": list(self.reasons),
        }


def apply_snapshot(
    base_problem: SeamProblem,
    result: SynthesisResult,
    snapshot: EnvelopeSnapshot,
    *,
    admissions: Sequence[ConstraintAdmission],
    current_authority_epoch: str,
    shared_structure: Mapping[str, Sequence[Sequence[str]]],
    target_arguments: Sequence[str],
    adapter_version: str,
    passport: LogicPassport,
    manifest: ConservationManifest,
) -> RefinedApplicationResult:
    if current_authority_epoch != snapshot.authority_epoch:
        return RefinedApplicationResult(
            status=ApplicationStatus.ESCALATE,
            cause=ApplicationCause.TERM_STALE,
            snapshot_hash=snapshot.snapshot_hash,
            authority_epoch=current_authority_epoch,
            application=None,
            reasons=("authority or logic epoch changed; dependent term must be recompiled",),
        )
    effective, state = semantic_state(
        base_problem,
        admissions,
        epoch=current_authority_epoch,
    )
    expected_snapshot = envelope_snapshot(
        base_problem,
        effective,
        result,
        state,
        passport=passport,
        manifest=manifest,
        closure_warrant_hash=snapshot.closure_warrant_hash,
    )
    if expected_snapshot.snapshot_hash != snapshot.snapshot_hash:
        raise InventionError("snapshot does not bind the supplied term and semantic state")
    application = apply_package(
        effective,
        result.package,
        shared_structure=shared_structure,
        target_arguments=target_arguments,
        adapter_version=adapter_version,
    )
    cause = (
        ApplicationCause.RESIDUAL
        if application.status is ApplicationStatus.ESCALATE
        else ApplicationCause.DECIDED
    )
    return RefinedApplicationResult(
        status=application.status,
        cause=cause,
        snapshot_hash=snapshot.snapshot_hash,
        authority_epoch=current_authority_epoch,
        application=application.to_dict(),
        reasons=application.reasons,
    )


@dataclass(frozen=True)
class TermSupersession:
    snapshot_hash: str
    prior_authority_epoch: str
    new_authority_epoch: str
    reason: str

    def __post_init__(self) -> None:
        for name in ("snapshot_hash", "prior_authority_epoch", "new_authority_epoch"):
            _require_digest(getattr(self, name), f"term_supersession.{name}")
        if self.prior_authority_epoch == self.new_authority_epoch or not self.reason:
            raise InventionError("term supersession requires a new epoch and a reason")

    @property
    def supersession_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_hash": self.snapshot_hash,
            "prior_authority_epoch": self.prior_authority_epoch,
            "new_authority_epoch": self.new_authority_epoch,
            "reason": self.reason,
        }


def supersede_term(
    snapshot: EnvelopeSnapshot,
    *,
    new_authority: Mapping[str, Any],
    reason: str,
) -> TermSupersession:
    return TermSupersession(
        snapshot_hash=snapshot.snapshot_hash,
        prior_authority_epoch=snapshot.authority_epoch,
        new_authority_epoch=authority_epoch(new_authority),
        reason=reason,
    )


def _transition_receipt(
    action_type: str,
    subject: Mapping[str, Any],
    *,
    reference: str,
    envelope: Any,
    evidence_refs: Sequence[Mapping[str, Any]],
    timestamp: str,
    producer: Mapping[str, Any],
):
    from bulla.action_receipt import build_action_receipt

    return build_action_receipt(
        action={"type": action_type, "subject": dict(subject)},
        diagnostic_ref={"status": "reference", "ref": reference},
        envelope=envelope,
        evidence_refs=tuple(dict(item) for item in evidence_refs),
        timestamp=timestamp,
        producer=dict(producer),
    )


def mint_admission_receipt(
    admission: ConstraintAdmission,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    return _transition_receipt(
        "bulla.enrich.apply",
        {
            "admission_hash": admission.admission_hash,
            "kind": admission.kind.value,
            "authority_epoch": admission.authority_epoch,
        },
        reference=admission.admission_hash,
        envelope=envelope,
        evidence_refs=(
            {
                "name": "admitted_constraint",
                "hash": canonical_hash(admission.constraint),
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=producer,
    )


def mint_refinement_receipt(
    bundle: RefinementBundle,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    return _transition_receipt(
        "bulla.envelope.refine",
        {
            "bundle_hash": bundle.bundle_hash,
            "certificate_hash": bundle.certificate.certificate_hash,
            "prior_snapshot_hash": bundle.prior_snapshot.snapshot_hash,
            "new_snapshot_hash": bundle.new_snapshot.snapshot_hash,
        },
        reference=bundle.certificate.certificate_hash,
        envelope=envelope,
        evidence_refs=(
            {
                "name": "constraint_admission",
                "hash": bundle.admission.admission_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "refinement_certificate",
                "hash": bundle.certificate.certificate_hash,
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=producer,
    )


def mint_supersession_receipt(
    supersession: TermSupersession,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    return _transition_receipt(
        "bulla.term.supersede",
        supersession.to_dict(),
        reference=supersession.supersession_hash,
        envelope=envelope,
        evidence_refs=(),
        timestamp=timestamp,
        producer=producer,
    )
