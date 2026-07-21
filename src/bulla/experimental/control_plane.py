"""Research-only semantic control plane: compile once, apply many times.

The profile composes existing artifacts instead of creating another receipt wire
format.  A ``SeamProblem`` and ``SynthesisResult`` remain canonical artifacts;
ordinary ActionReceipts bind invention and governed selection acts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from bulla._canonical import CANON_VERSION, canonical_json
from bulla.experimental.frsl import (
    FRSLError,
    canonical_hash,
    evaluate,
    normalize_structure,
    structure_to_dict,
)
from bulla.experimental.invention import (
    ChoiceAnalysis,
    GateStatus,
    InventionError,
    PredicatePackage,
    SeamProblem,
    SynthesisResult,
    SynthesisStatus,
    verify_package,
)

PROFILE = "bulla.semantic-invention/0.1-draft"
CACHE_SCHEMA = "0.1-draft"
SELECTION_ACTION_TYPE = "bulla.invent.select"
APPLICATION_ACTION_TYPE = "bulla.invent.apply"


class ApplicationStatus(str, Enum):
    RELY = "RELY"
    REFUSE = "REFUSE"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class ApplicationResult:
    status: ApplicationStatus
    problem_hash: str
    package_hash: str
    structure_hash: str
    target_arguments: tuple[str, ...]
    adapter_version: str
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError(
            "ApplicationResult is three-valued; inspect .status, never use boolean coercion"
        )

    @property
    def result_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "profile": PROFILE,
            "status": self.status.value,
            "problem_hash": self.problem_hash,
            "package_hash": self.package_hash,
            "structure_hash": self.structure_hash,
            "target_arguments": list(self.target_arguments),
            "adapter_version": self.adapter_version,
            "reasons": list(self.reasons),
        }


def compilation_key(
    problem: SeamProblem,
    *,
    adapter_version: str,
    verifier: Mapping[str, Any],
    candidate_grammar: str = "FRSL-1 DNF cubes over declared feature atoms",
) -> str:
    if not adapter_version:
        raise InventionError("adapter_version must be non-empty")
    if not isinstance(verifier, Mapping) or not verifier:
        raise InventionError("verifier descriptor must be a non-empty object")
    return canonical_hash(
        {
            "profile": PROFILE,
            "canon_version": CANON_VERSION,
            "problem_hash": problem.problem_hash,
            "protected_signatures": {
                owner: list(names)
                for owner, names in sorted(problem.protected_signatures.items())
            },
            "synthesis_policy": problem.synthesis_policy.to_dict(),
            "adapter_version": adapter_version,
            "verifier": dict(verifier),
            "candidate_grammar": candidate_grammar,
        }
    )


def _target_arguments(problem: SeamProblem, target_arguments: Sequence[str]) -> tuple[str, ...]:
    arguments = tuple(target_arguments)
    if len(arguments) != problem.target_decl.arity:
        raise InventionError("target_arguments have the wrong arity")
    for value, sort in zip(arguments, problem.target_decl.sorts):
        if value not in problem.signature.sorts[sort]:
            raise InventionError(
                f"target argument {value!r} is outside declared sort {sort!r}"
            )
    return arguments


def apply_package(
    problem: SeamProblem,
    package: PredicatePackage,
    *,
    shared_structure: Mapping[str, Sequence[Sequence[str]]],
    target_arguments: Sequence[str],
    adapter_version: str,
) -> ApplicationResult:
    """Apply an independently accepted package to one adapter-produced structure.

    The adapter must disclose exactly the shared vocabulary.  Supplying the target
    itself or an undeclared relation is rejected, preventing private-state leakage.
    """
    if set(shared_structure) != set(problem.shared_vocabulary):
        missing = sorted(set(problem.shared_vocabulary) - set(shared_structure))
        extra = sorted(set(shared_structure) - set(problem.shared_vocabulary))
        raise InventionError(
            f"adapter structure must contain exactly shared_vocabulary; missing={missing} extra={extra}"
        )
    report = verify_package(problem, package)
    required = (
        report.gluing is GateStatus.PASS
        and report.conservativity is GateStatus.PASS
        and report.preserved_refusals is GateStatus.PASS
        and report.receipt_binding is GateStatus.PASS
    )
    if package.mode == "full":
        required = required and report.definability is GateStatus.PASS
    if not required:
        raise InventionError(
            "package failed independent application gates: " + "; ".join(report.reasons)
        )
    arguments = _target_arguments(problem, target_arguments)
    try:
        structure = normalize_structure(shared_structure, problem.signature)
    except FRSLError as exc:
        raise InventionError(str(exc)) from exc
    environment = {f"x{i}": value for i, value in enumerate(arguments)}
    reasons: list[str] = []
    if package.mode == "full":
        status = (
            ApplicationStatus.RELY
            if evaluate(
                package.definition,
                signature=problem.signature,
                structure=structure,
                environment=environment,
            )
            else ApplicationStatus.REFUSE
        )
    else:
        rely = evaluate(
            package.rely_when,
            signature=problem.signature,
            structure=structure,
            environment=environment,
        )
        refuse = evaluate(
            package.refuse_when,
            signature=problem.signature,
            structure=structure,
            environment=environment,
        )
        if rely and refuse:
            raise InventionError("accepted package produced overlapping RELY and REFUSE")
        if rely:
            status = ApplicationStatus.RELY
        elif refuse:
            status = ApplicationStatus.REFUSE
        else:
            status = ApplicationStatus.ESCALATE
            reasons.append("the input lies in the certified partial-package residual")
    return ApplicationResult(
        status=status,
        problem_hash=problem.problem_hash,
        package_hash=package.package_hash,
        structure_hash=canonical_hash(structure_to_dict(structure)),
        target_arguments=arguments,
        adapter_version=adapter_version,
        reasons=tuple(reasons),
    )


class CompiledTermCache:
    """Content-addressed cache that accepts only independently replayed packages."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key.startswith("sha256:") or len(key) != 71:
            raise InventionError("cache key must be a full sha256 digest")
        return self.root / (key.split(":", 1)[1] + ".json")

    def put(
        self,
        problem: SeamProblem,
        result: SynthesisResult,
        *,
        adapter_version: str,
    ) -> str:
        if result.status not in (SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL):
            raise InventionError("only executable COMPILED/PARTIAL results may be cached")
        if result.package is None or result.problem_hash != problem.problem_hash:
            raise InventionError("cache result does not bind an executable package")
        report = verify_package(problem, result.package)
        if not (
            report.gluing is GateStatus.PASS
            and report.conservativity is GateStatus.PASS
            and report.preserved_refusals is GateStatus.PASS
            and report.receipt_binding is GateStatus.PASS
        ):
            raise InventionError("cache refused a package that failed independent replay")
        key = compilation_key(
            problem,
            adapter_version=adapter_version,
            verifier=result.verifier,
        )
        payload = {
            "schema_version": CACHE_SCHEMA,
            "profile": PROFILE,
            "compilation_key": key,
            "adapter_version": adapter_version,
            "problem": problem.to_dict(),
            "result": result.to_dict(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(key)
        temporary = destination.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(canonical_json(payload), encoding="utf-8")
        temporary.replace(destination)
        return key

    def get(
        self,
        key: str,
        *,
        adapter_version: str,
    ) -> tuple[SeamProblem, SynthesisResult]:
        payload = json.loads(self._path(key).read_text(encoding="utf-8"))
        if set(payload) != {
            "schema_version",
            "profile",
            "compilation_key",
            "adapter_version",
            "problem",
            "result",
        }:
            raise InventionError("cached term has missing or unknown fields")
        if payload["schema_version"] != CACHE_SCHEMA or payload["profile"] != PROFILE:
            raise InventionError("cached term uses an unsupported profile")
        if payload["compilation_key"] != key or payload["adapter_version"] != adapter_version:
            raise InventionError("cached term is stale for this adapter/version")
        problem = SeamProblem.from_dict(payload["problem"])
        result = SynthesisResult.from_dict(payload["result"])
        expected = compilation_key(
            problem,
            adapter_version=adapter_version,
            verifier=result.verifier,
        )
        if expected != key:
            raise InventionError("cached term no longer matches its committed inputs")
        if result.package is None:
            raise InventionError("cached result is not executable")
        report = verify_package(problem, result.package)
        if report.receipt_binding is not GateStatus.PASS:
            raise InventionError("cached package failed replay")
        return problem, result


def _selected_package(result: SynthesisResult, package_hash: str) -> PredicatePackage:
    if result.status is not SynthesisStatus.CHOICE_REQUIRED or result.choice_analysis is None:
        raise InventionError("selection requires a CHOICE_REQUIRED result")
    by_hash = {x.package_hash: x for x in result.alternatives}
    try:
        return by_hash[package_hash]
    except KeyError as exc:
        raise InventionError("selected package was not an offered alternative") from exc


def mint_selection_receipt(
    problem: SeamProblem,
    result: SynthesisResult,
    *,
    selected_package_hash: str,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    from bulla.action_receipt import build_action_receipt

    package = _selected_package(result, selected_package_hash)
    analysis: ChoiceAnalysis = result.choice_analysis
    selected_class = next(
        item for item in analysis.classes if selected_package_hash in item.package_hashes
    )
    selection_pin = canonical_hash(
        {
            "problem_hash": problem.problem_hash,
            "result_hash": result.result_hash,
            "selected_package_hash": selected_package_hash,
            "choice_class": selected_class.class_id,
            "selector_authority": dict(analysis.selector_authority),
        }
    )
    return build_action_receipt(
        action={
            "type": SELECTION_ACTION_TYPE,
            "subject": {
                "problem_hash": problem.problem_hash,
                "result_hash": result.result_hash,
                "choice_kind": analysis.kind.value,
                "choice_class": selected_class.class_id,
                "selected_package_hash": package.package_hash,
                "selection_pin": selection_pin,
            },
        },
        diagnostic_ref={"status": "reference", "ref": selection_pin},
        envelope=envelope,
        evidence_refs=(
            {
                "name": "choice_result",
                "hash": result.result_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "selected_package",
                "hash": package.package_hash,
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )


def verify_selection_receipt(
    receipt: Mapping[str, Any],
    problem: SeamProblem,
    result: SynthesisResult,
    *,
    public_key: bytes | None = None,
) -> dict[str, Any]:
    from bulla.action_receipt import verify_receipt

    receipt_doc = dict(receipt)
    verification = verify_receipt(receipt_doc, public_key=public_key)
    action = receipt_doc.get("action")
    action = action if isinstance(action, dict) else {}
    subject = action.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    selected_hash = subject.get("selected_package_hash")
    try:
        package = _selected_package(result, selected_hash)
        selected_class = next(
            item
            for item in result.choice_analysis.classes
            if package.package_hash in item.package_hashes
        )
    except (InventionError, StopIteration, TypeError):
        package = None
        selected_class = None
    authority = result.choice_analysis.selector_authority if result.choice_analysis else {}
    envelope = receipt_doc.get("mandate")
    envelope = envelope if isinstance(envelope, dict) else {}
    mandate = envelope.get("authority")
    mandate = mandate if isinstance(mandate, dict) else {}
    expected_pin = (
        canonical_hash(
            {
                "problem_hash": problem.problem_hash,
                "result_hash": result.result_hash,
                "selected_package_hash": package.package_hash,
                "choice_class": selected_class.class_id,
                "selector_authority": dict(authority),
            }
        )
        if package is not None and selected_class is not None
        else None
    )
    checks = {
        "receipt_authentic": bool(
            verification.ok and verification.authority_authentic == "verified"
        ),
        "action_type": action.get("type") == SELECTION_ACTION_TYPE,
        "subject_shape": set(subject)
        == {
            "problem_hash",
            "result_hash",
            "choice_kind",
            "choice_class",
            "selected_package_hash",
            "selection_pin",
        },
        "problem_binding": subject.get("problem_hash") == problem.problem_hash,
        "result_binding": subject.get("result_hash") == result.result_hash,
        "offered_alternative": package is not None,
        "choice_class": selected_class is not None
        and subject.get("choice_class") == selected_class.class_id,
        "choice_kind": result.choice_analysis is not None
        and subject.get("choice_kind") == result.choice_analysis.kind.value,
        "selection_pin": expected_pin is not None
        and subject.get("selection_pin") == expected_pin,
        "selector_principal": mandate.get("principal") == authority.get("principal"),
        "selector_policy": mandate.get("policy") == authority.get("policy"),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "selected_package_hash": package.package_hash if package else None,
        "receipt_verified_to": verification.verified_to,
    }


def verify_invention_receipt(
    receipt: Mapping[str, Any],
    problem: SeamProblem,
    result: SynthesisResult,
) -> dict[str, Any]:
    from bulla.action_receipt import verify_receipt

    document = dict(receipt)
    verification = verify_receipt(document)
    action = document.get("action")
    action = action if isinstance(action, dict) else {}
    subject = action.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    expected_artifact = canonical_hash(
        {
            "result_hash": result.result_hash,
            "package_hash": result.package.package_hash if result.package else None,
            "certificate_hash": (
                canonical_hash(result.certificate.to_dict()) if result.certificate else None
            ),
        }
    )
    checks = {
        "receipt_authentic": bool(
            verification.ok and verification.authority_authentic == "verified"
        ),
        "action_type": action.get("type") == "bulla.invent",
        "problem_binding": subject.get("problem_hash") == problem.problem_hash,
        "result_binding": subject.get("result_hash") == result.result_hash,
        "outcome_binding": subject.get("outcome") == result.status.value,
        "artifact_binding": subject.get("artifact_hash") == expected_artifact,
    }
    return {"ok": all(checks.values()), "checks": checks}


def reliance_scope(policy: Any) -> dict[str, Any]:
    """Exact executable scope for a ``bulla.rely`` act under one pinned policy.

    The scope is evaluated against the reliance receipt's ``action.subject`` by
    the ordinary v0.3 bounds-conformance machinery.  It cannot authorize a
    differently named or differently hashed policy by textual similarity.
    """
    from bulla.reliance import ESCALATE, REFUSE, RELY, ReliancePolicy

    if not isinstance(policy, ReliancePolicy):
        raise InventionError("policy must be an explicit ReliancePolicy")
    policy_ref = f"{policy.name}@{policy.policy_hash}"
    return {
        "form": "jsonschema+quantum/1",
        "schema": {
            "type": "object",
            "required": ["relied_on", "policy", "decision"],
            "properties": {
                "policy": {"type": "string", "const": policy_ref},
                "decision": {
                    "type": "string",
                    "enum": [RELY, REFUSE, ESCALATE],
                },
            },
            # The closed executable subset has scalar property predicates only.
            # ``verify_reliance`` separately enforces the exact three-field subject
            # and full nested relied_on reference; permitting the nested value here
            # avoids pretending the bounds evaluator understands object schemas.
            "additionalProperties": True,
        },
    }


def build_control_plane_reliance_receipt(
    *,
    relied_on: dict[str, Any],
    policy: Any,
    envelope: Any,
    decision: Any = None,
    public_key: bytes | None = None,
    timestamp: str = "",
    producer: dict[str, Any] | None = None,
):
    """Build reliance only when mandate and executable scope pin the policy.

    This is a research-profile wrapper over the stable ``bulla.rely`` format;
    it does not change that format or loosen its verifier.
    """
    from bulla.reliance import RelianceError, ReliancePolicy, build_reliance_receipt

    if not isinstance(policy, ReliancePolicy):
        raise RelianceError("policy must be an explicit ReliancePolicy")
    policy_ref = f"{policy.name}@{policy.policy_hash}"
    authority = getattr(envelope, "authority", None)
    bounds = getattr(envelope, "bounds", None)
    if authority is None or authority.policy != policy_ref:
        raise RelianceError("control-plane reliance mandate must pin the exact policy name@hash")
    if bounds is None or canonical_json(bounds.scope) != canonical_json(reliance_scope(policy)):
        raise RelianceError("control-plane reliance bounds must equal the exact executable policy scope")
    if getattr(envelope, "deed_schema", None) != "0.3":
        raise RelianceError("control-plane reliance requires deed_schema 0.3 structured bounds")
    return build_reliance_receipt(
        relied_on=relied_on,
        policy=policy,
        envelope=envelope,
        decision=decision,
        public_key=public_key,
        timestamp=timestamp,
        producer=producer,
    )


def verify_control_plane_reliance(
    reliance: Mapping[str, Any],
    relied_on: Mapping[str, Any],
    policy: Any,
    *,
    public_key: bytes | None = None,
) -> dict[str, Any]:
    """Recompute stable reliance plus the exact mandate/scope bindings."""
    from bulla.reliance import ReliancePolicy, verify_reliance

    if not isinstance(policy, ReliancePolicy):
        raise InventionError("policy must be an explicit ReliancePolicy")
    document = dict(reliance)
    stable = verify_reliance(document, dict(relied_on), policy, public_key=public_key)
    mandate = document.get("mandate") if isinstance(document.get("mandate"), dict) else {}
    authority = mandate.get("authority") if isinstance(mandate.get("authority"), dict) else {}
    bounds = mandate.get("bounds") if isinstance(mandate.get("bounds"), dict) else {}
    expected_ref = f"{policy.name}@{policy.policy_hash}"
    checks = {
        "stable_reliance": stable.ok,
        "policy_mandate": authority.get("policy") == expected_ref,
        "scope_exact": canonical_json(bounds.get("scope")) == canonical_json(reliance_scope(policy)),
        "scope_conforms": stable.receipt_verification.bounds_conformance == "conforms",
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "decision": stable.decision.outcome if stable.decision is not None else None,
    }


def mint_application_receipt(
    application: ApplicationResult,
    *,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
):
    """Record one data-plane evaluation in the ordinary ActionReceipt format."""
    from bulla.action_receipt import build_action_receipt

    return build_action_receipt(
        action={
            "type": APPLICATION_ACTION_TYPE,
            "subject": {
                "application_result_hash": application.result_hash,
                "problem_hash": application.problem_hash,
                "package_hash": application.package_hash,
                "structure_hash": application.structure_hash,
                "target_arguments": list(application.target_arguments),
                "adapter_version": application.adapter_version,
                "decision": application.status.value,
            },
        },
        diagnostic_ref={"status": "reference", "ref": application.result_hash},
        envelope=envelope,
        evidence_refs=(
            {
                "name": "compiled_package",
                "hash": application.package_hash,
                "grounding": "execution_verified",
            },
            {
                "name": "adapter_structure",
                "hash": application.structure_hash,
                "grounding": "execution_verified",
            },
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )


def verify_application_receipt(
    receipt: Mapping[str, Any],
    problem: SeamProblem,
    package: PredicatePackage,
    *,
    shared_structure: Mapping[str, Sequence[Sequence[str]]],
    target_arguments: Sequence[str],
    adapter_version: str,
) -> dict[str, Any]:
    """Authenticate the act and replay the exact package evaluation it claims."""
    from bulla.action_receipt import verify_receipt

    document = dict(receipt)
    receipt_verification = verify_receipt(document)
    recomputed = apply_package(
        problem,
        package,
        shared_structure=shared_structure,
        target_arguments=target_arguments,
        adapter_version=adapter_version,
    )
    action = document.get("action") if isinstance(document.get("action"), dict) else {}
    subject = action.get("subject") if isinstance(action.get("subject"), dict) else {}
    expected = {
        "application_result_hash": recomputed.result_hash,
        "problem_hash": recomputed.problem_hash,
        "package_hash": recomputed.package_hash,
        "structure_hash": recomputed.structure_hash,
        "target_arguments": list(recomputed.target_arguments),
        "adapter_version": recomputed.adapter_version,
        "decision": recomputed.status.value,
    }
    evidence = document.get("evidence_refs") if isinstance(document.get("evidence_refs"), list) else []
    expected_evidence = {
        ("compiled_package", recomputed.package_hash, "execution_verified"),
        ("adapter_structure", recomputed.structure_hash, "execution_verified"),
    }
    actual_evidence = {
        (item.get("name"), item.get("hash"), item.get("grounding"))
        for item in evidence
        if isinstance(item, dict)
    }
    checks = {
        "receipt_authentic": bool(
            receipt_verification.ok
            and receipt_verification.authority_authentic == "verified"
        ),
        "action_type": action.get("type") == APPLICATION_ACTION_TYPE,
        "subject_exact": subject == expected,
        "diagnostic_binding": document.get("diagnostic_ref")
        == {"status": "reference", "ref": recomputed.result_hash},
        "evidence_binding": expected_evidence == actual_evidence,
    }
    return {"ok": all(checks.values()), "checks": checks, "application": recomputed.to_dict()}
