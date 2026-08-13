"""Source-only Acceptance Contract alpha.

An Acceptance Contract binds one retained transaction to a receiver-authored
evidence policy and one contemplated consequence.  The receipt preserves the
claim; this module decides what the supplied evidence permits under the exact
external context.  Missing evidence produces a conditional request, never a
fabricated cure or an implicit policy relaxation.

This module is intentionally absent from :mod:`bulla.experimental` exports and
from packaged Bulla artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from bulla._canonical import CanonicalizationError, canonical_jcs_int
from bulla.action_receipt import verify_receipt


PROFILE = "bulla.acceptance-contract/0.1-experimental"
CONTEXT_PROFILE = "bulla.acceptance-context/0.1-experimental"
REPORT_PROFILE = "bulla.acceptance-report/0.1-experimental"
CORE_PROFILE = "bulla.acceptance-core/0.1-experimental"
EVIDENCE_REQUEST_PROFILE = "bulla.acceptance-evidence-request/0.1-experimental"
CORE_FILE = "acceptance-core.json"
CONTRACT_FILE = "contract.json"
PUBLISH_FILE = "publish-receipt.json"
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SAFE_INTEGER = 9_007_199_254_740_991

DECISIONS = frozenset(
    {"PROCEED", "HOLD_FOR_EVIDENCE", "REFUSE", "ESCALATE", "NOT_COMPUTED"}
)
CONSEQUENCES = frozenset(
    {"ELIGIBLE", "INELIGIBLE", "CHALLENGE_REQUIRED", "NOT_COMPUTED"}
)
ROUTE_PRECEDENCE = MappingProxyType(
    {"PROCEED": 0, "HOLD_FOR_EVIDENCE": 1, "ESCALATE": 2, "REFUSE": 3}
)
EXPECTED_ROLES = frozenset(
    {
        "release_buyer",
        "deploy_agent",
        "staging_controller",
        "rollback_test_runner",
        "release_authority",
        "publisher",
    }
)


class AcceptanceContractError(ValueError):
    """The acceptance bundle or context is malformed, unsafe, or unsupported."""


class AcceptanceContractVerificationError(AcceptanceContractError):
    """A well-formed bundle failed integrity or external trust checks."""


def canonical_hash(value: Any) -> str:
    try:
        raw = canonical_jcs_int(value).encode("utf-8")
    except CanonicalizationError as exc:
        raise AcceptanceContractError(f"value is not canonicalizable: {exc}") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes, label: str, limits: "AcceptanceLimits") -> Any:
    if len(raw) > limits.max_file_bytes:
        raise AcceptanceContractError(f"{label} exceeds file limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceContractError(f"{label} is not valid UTF-8") from exc

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise AcceptanceContractError(
                    f"duplicate JSON member {key!r} in {label}"
                )
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise AcceptanceContractError(f"non-finite number {value!r} in {label}")

    try:
        value = json.loads(
            text, object_pairs_hook=unique, parse_constant=reject_constant
        )
    except AcceptanceContractError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise AcceptanceContractError(f"invalid JSON in {label}: {exc}") from exc
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise AcceptanceContractError(f"{label} exceeds JSON resource limits")
        if isinstance(current, str):
            try:
                encoded = current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise AcceptanceContractError(
                    f"{label} contains a lone Unicode surrogate"
                ) from exc
            if len(encoded) > limits.max_string_bytes:
                raise AcceptanceContractError(f"{label} contains an oversized string")
        elif isinstance(current, int) and not isinstance(current, bool):
            if abs(current) > SAFE_INTEGER:
                raise AcceptanceContractError(f"{label} contains an unsafe integer")
        elif isinstance(current, float):
            raise AcceptanceContractError(f"{label} contains a floating-point number")
        elif isinstance(current, Mapping):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return value


@dataclass(frozen=True)
class AcceptanceLimits:
    max_file_bytes: int = 262_144
    max_total_bytes: int = 2_097_152
    max_files: int = 48
    max_depth: int = 20
    max_nodes: int = 12_000
    max_string_bytes: int = 16_384

    def __post_init__(self) -> None:
        if min(
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_files,
            self.max_depth,
            self.max_nodes,
            self.max_string_bytes,
        ) <= 0:
            raise ValueError("acceptance-contract limits must be positive")


@dataclass(frozen=True)
class AcceptanceContext:
    """Receiver trust and policy adoption supplied outside the bundle."""

    accepted_issuers_by_role: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]]
    accepted_contract_hash: str
    accepted_policy_authorities: frozenset[str] | set[str] | tuple[str, ...]
    accepted_adapters: frozenset[str] | set[str] | tuple[str, ...]
    authority_epoch: int
    semantic_epoch: int

    def __post_init__(self) -> None:
        if set(self.accepted_issuers_by_role) != EXPECTED_ROLES:
            raise ValueError("accepted issuers must contain the exact closed role set")
        normalized: dict[str, frozenset[str]] = {}
        assigned: dict[str, str] = {}
        for role, issuers in self.accepted_issuers_by_role.items():
            values = frozenset(issuers)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"accepted issuers for {role!r} are invalid")
            for issuer in values:
                previous = assigned.get(issuer)
                if previous is not None and previous != role:
                    raise ValueError("one accepted issuer cannot occupy multiple roles")
                assigned[issuer] = role
            normalized[role] = values
        if not _is_hash(self.accepted_contract_hash):
            raise ValueError("accepted_contract_hash must be sha256")
        authorities = frozenset(self.accepted_policy_authorities)
        adapters = frozenset(self.accepted_adapters)
        if not authorities or not adapters:
            raise ValueError("accepted policy authorities and adapters must be non-empty")
        if any(not isinstance(value, str) or not value for value in authorities | adapters):
            raise ValueError("accepted policy authorities and adapters are invalid")
        for name, value in (
            ("authority_epoch", self.authority_epoch),
            ("semantic_epoch", self.semantic_epoch),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= SAFE_INTEGER
            ):
                raise ValueError(f"{name} must be a non-negative safe integer")
        object.__setattr__(self, "accepted_issuers_by_role", MappingProxyType(normalized))
        object.__setattr__(self, "accepted_policy_authorities", authorities)
        object.__setattr__(self, "accepted_adapters", adapters)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcceptanceContext":
        _exact(
            value,
            {
                "profile",
                "accepted_issuers_by_role",
                "accepted_contract_hash",
                "accepted_policy_authorities",
                "accepted_adapters",
                "authority_epoch",
                "semantic_epoch",
            },
            "acceptance context",
        )
        if value["profile"] != CONTEXT_PROFILE:
            raise AcceptanceContractError("unsupported acceptance context profile")
        roles = _object(value["accepted_issuers_by_role"], "accepted_issuers_by_role")
        return cls(
            accepted_issuers_by_role={
                role: frozenset(_strings(items, f"accepted_issuers_by_role.{role}"))
                for role, items in roles.items()
            },
            accepted_contract_hash=_hash(value["accepted_contract_hash"], "accepted_contract_hash"),
            accepted_policy_authorities=frozenset(
                _strings(value["accepted_policy_authorities"], "accepted_policy_authorities")
            ),
            accepted_adapters=frozenset(
                _strings(value["accepted_adapters"], "accepted_adapters")
            ),
            authority_epoch=_integer(value["authority_epoch"], "authority_epoch"),
            semantic_epoch=_integer(value["semantic_epoch"], "semantic_epoch"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": CONTEXT_PROFILE,
            "accepted_issuers_by_role": {
                role: sorted(values)
                for role, values in sorted(self.accepted_issuers_by_role.items())
            },
            "accepted_contract_hash": self.accepted_contract_hash,
            "accepted_policy_authorities": sorted(self.accepted_policy_authorities),
            "accepted_adapters": sorted(self.accepted_adapters),
            "authority_epoch": self.authority_epoch,
            "semantic_epoch": self.semantic_epoch,
        }


@dataclass(frozen=True)
class AcceptanceContract:
    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        _validate_contract(self.value)
        object.__setattr__(self, "value", MappingProxyType(dict(self.value)))

    @property
    def contract_hash(self) -> str:
        return canonical_hash(dict(self.value))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


@dataclass(frozen=True)
class RequirementResult:
    requirement_id: str
    status: str
    route: str
    observed_value: str
    accepted_values: tuple[str, ...]
    source_path: str
    evidence_request_id: str | None
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError("RequirementResult has named dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "status": self.status,
            "route": self.route,
            "observed_value": self.observed_value,
            "accepted_values": list(self.accepted_values),
            "source_path": self.source_path,
            "evidence_request_id": self.evidence_request_id,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class AcceptanceReport:
    transaction_id: str
    contract_hash: str
    record_integrity: str
    term_binding: str
    provider_acceptance: str
    sender_authority: str
    receiver_authority: str
    receiver_observation: str
    receiver_coverage: str
    correction_state: str
    requirements: tuple[RequirementResult, ...]
    decision: str
    conditional_evidence_requests: tuple[Mapping[str, Any], ...]
    conditional_closure_sets: tuple[tuple[str, ...], ...]
    closure_minimality: str
    consequence: str
    consequence_eligibility: str
    authorization: str
    execution: str
    worldly_truth: str = "NOT_COMPUTED"
    historical_execution: str = "NOT_ESTABLISHED"
    denominator_completeness: str = "NOT_ESTABLISHED"
    limitations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError("unsupported acceptance decision")
        if self.consequence_eligibility not in CONSEQUENCES:
            raise ValueError("unsupported consequence eligibility")

    def __bool__(self) -> bool:
        raise TypeError("AcceptanceReport has independent dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": REPORT_PROFILE,
            "transaction_id": self.transaction_id,
            "contract_hash": self.contract_hash,
            "record_integrity": self.record_integrity,
            "term_binding": self.term_binding,
            "provider_acceptance": self.provider_acceptance,
            "sender_authority": self.sender_authority,
            "receiver_authority": self.receiver_authority,
            "receiver_observation": self.receiver_observation,
            "receiver_coverage": self.receiver_coverage,
            "correction_state": self.correction_state,
            "requirements": [item.to_dict() for item in self.requirements],
            "decision": self.decision,
            "conditional_evidence_requests": [
                dict(item) for item in self.conditional_evidence_requests
            ],
            "conditional_closure_sets": [list(item) for item in self.conditional_closure_sets],
            "closure_minimality": self.closure_minimality,
            "consequence": self.consequence,
            "consequence_eligibility": self.consequence_eligibility,
            "authorization": self.authorization,
            "execution": self.execution,
            "worldly_truth": self.worldly_truth,
            "historical_execution": self.historical_execution,
            "denominator_completeness": self.denominator_completeness,
            "limitations": list(self.limitations),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ParsedAcceptanceBundle:
    root: Path
    files: Mapping[str, bytes]
    core: Mapping[str, Any]
    contract: AcceptanceContract


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _exact(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AcceptanceContractError(f"{label} fields must be exactly {sorted(expected)}")


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AcceptanceContractError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AcceptanceContractError(f"{label} must be a non-empty string")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AcceptanceContractError(f"{label} must be a non-empty array")
    result = tuple(_string(item, f"{label} item") for item in value)
    if len(set(result)) != len(result):
        raise AcceptanceContractError(f"{label} must not contain duplicates")
    return result


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= SAFE_INTEGER:
        raise AcceptanceContractError(f"{label} must be a non-negative safe integer")
    return value


def _hash(value: Any, label: str) -> str:
    if not _is_hash(value):
        raise AcceptanceContractError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _safe_path(value: Any, label: str) -> str:
    path = _string(value, label)
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or path in {"", "."}
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != path
    ):
        raise AcceptanceContractError(f"{label} is unsafe")
    return path


def _validate_evidence_request(value: Mapping[str, Any]) -> None:
    _exact(
        value,
        {
            "profile",
            "request_id",
            "requirement_id",
            "artifact_class",
            "accepted_adapter",
            "accepted_values",
            "required_bindings",
            "challenge_path",
            "conditional_statement",
        },
        "evidence request",
    )
    if value["profile"] != EVIDENCE_REQUEST_PROFILE:
        raise AcceptanceContractError("unsupported evidence request profile")
    _string(value["request_id"], "evidence request id")
    _string(value["requirement_id"], "evidence request requirement id")
    _string(value["artifact_class"], "evidence request artifact class")
    _string(value["accepted_adapter"], "evidence request adapter")
    _strings(value["accepted_values"], "evidence request accepted values")
    bindings = _object(value["required_bindings"], "evidence request bindings")
    _exact(
        bindings,
        {
            "transaction_id",
            "term_root",
            "contract_id",
            "contract_revision",
            "build_hash",
            "environment",
            "issuer_role",
        },
        "evidence request bindings",
    )
    for name, item in bindings.items():
        if name == "contract_revision":
            _integer(item, "evidence request binding contract_revision")
        else:
            _string(item, f"evidence request binding {name}")
    _string(value["challenge_path"], "evidence request challenge path")
    statement = _string(value["conditional_statement"], "conditional statement")
    if not statement.startswith("If a newly supplied and verified artifact"):
        raise AcceptanceContractError("evidence request must state its conditional nature")


def _validate_requirement(value: Mapping[str, Any]) -> None:
    _exact(
        value,
        {
            "requirement_id",
            "source_path",
            "source_action",
            "source_role",
            "adapter",
            "result_field",
            "accepted_values",
            "negative_values",
            "absence_route",
            "negative_route",
            "ambiguity_route",
            "evidence_request_id",
        },
        "acceptance requirement",
    )
    _string(value["requirement_id"], "requirement id")
    _safe_path(value["source_path"], "requirement source path")
    _string(value["source_action"], "requirement source action")
    if value["source_role"] not in EXPECTED_ROLES:
        raise AcceptanceContractError("requirement source role is unsupported")
    _string(value["adapter"], "requirement adapter")
    _string(value["result_field"], "requirement result field")
    accepted = _strings(value["accepted_values"], "requirement accepted values")
    negatives = _strings(value["negative_values"], "requirement negative values")
    if set(accepted) & set(negatives):
        raise AcceptanceContractError("accepted and negative values must be disjoint")
    for name in ("absence_route", "negative_route", "ambiguity_route"):
        if value[name] not in {"HOLD_FOR_EVIDENCE", "REFUSE", "ESCALATE"}:
            raise AcceptanceContractError(f"{name} has an unsupported route")
    request_id = value["evidence_request_id"]
    if request_id is not None:
        _string(request_id, "requirement evidence request id")
    if value["absence_route"] == "HOLD_FOR_EVIDENCE" and request_id is None:
        raise AcceptanceContractError("HOLD_FOR_EVIDENCE requires an evidence request")


def _validate_contract(value: Mapping[str, Any]) -> None:
    _exact(
        value,
        {
            "profile",
            "contract_id",
            "revision",
            "semantic_epoch",
            "authority_epoch",
            "policy_authority_ref",
            "transaction_id",
            "terms",
            "term_root",
            "contemplated_consequence",
            "requirements",
            "receiver_coverage_requirement",
            "correction_policy",
            "challenge_policy",
            "evidence_requests",
            "authorization_policy",
            "execution_policy",
        },
        "acceptance contract",
    )
    if value["profile"] != PROFILE:
        raise AcceptanceContractError("unsupported acceptance contract profile")
    _string(value["contract_id"], "contract id")
    _integer(value["revision"], "contract revision")
    _integer(value["semantic_epoch"], "semantic epoch")
    _integer(value["authority_epoch"], "authority epoch")
    _string(value["policy_authority_ref"], "policy authority reference")
    _string(value["transaction_id"], "transaction id")
    terms = _object(value["terms"], "terms")
    _exact(terms, {"build_hash", "environment", "claim", "consequence"}, "terms")
    _hash(terms["build_hash"], "terms build hash")
    _string(terms["environment"], "terms environment")
    _string(terms["claim"], "terms claim")
    _string(terms["consequence"], "terms consequence")
    if value["term_root"] != canonical_hash(dict(terms)):
        raise AcceptanceContractError("term_root does not match exact terms")
    consequence = _object(value["contemplated_consequence"], "contemplated consequence")
    _exact(
        consequence,
        {"action_type", "subject_hash", "authority_ref"},
        "contemplated consequence",
    )
    if consequence["action_type"] != "release.promote":
        raise AcceptanceContractError("alpha supports only release.promote")
    _hash(consequence["subject_hash"], "consequence subject hash")
    _string(consequence["authority_ref"], "consequence authority reference")
    if consequence["authority_ref"] != value["policy_authority_ref"]:
        raise AcceptanceContractError(
            "consequence authority must equal the declared policy authority"
        )
    requirements = value["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise AcceptanceContractError("requirements must be a non-empty array")
    for requirement in requirements:
        _validate_requirement(_object(requirement, "requirement"))
    requirement_ids = [item["requirement_id"] for item in requirements]
    if len(set(requirement_ids)) != len(requirement_ids):
        raise AcceptanceContractError("requirement ids must be unique")
    requests = value["evidence_requests"]
    if not isinstance(requests, list):
        raise AcceptanceContractError("evidence_requests must be an array")
    for request in requests:
        _validate_evidence_request(_object(request, "evidence request"))
    request_ids = [item["request_id"] for item in requests]
    if len(set(request_ids)) != len(request_ids):
        raise AcceptanceContractError("evidence request ids must be unique")
    by_request = {item["request_id"]: item for item in requests}
    for requirement in requirements:
        request_id = requirement["evidence_request_id"]
        if request_id is not None:
            request = by_request.get(request_id)
            if request is None or request["requirement_id"] != requirement["requirement_id"]:
                raise AcceptanceContractError("requirement evidence request binding is invalid")
            if request["accepted_adapter"] != requirement["adapter"]:
                raise AcceptanceContractError("evidence request adapter does not match requirement")
            if request["accepted_values"] != requirement["accepted_values"]:
                raise AcceptanceContractError(
                    "evidence request accepted values do not match requirement"
                )
    coverage = _object(value["receiver_coverage_requirement"], "receiver coverage requirement")
    _exact(coverage, {"required", "anchor_id", "accepted_status"}, "receiver coverage requirement")
    if coverage["required"] is not True or coverage["accepted_status"] != "COVERED":
        raise AcceptanceContractError("alpha requires explicit COVERED receiver coverage")
    _string(coverage["anchor_id"], "receiver coverage anchor")
    correction = _object(value["correction_policy"], "correction policy")
    _exact(correction, {"required_state", "new_revision_required"}, "correction policy")
    if correction != {"required_state": "CURRENT", "new_revision_required": True}:
        raise AcceptanceContractError("unsupported correction policy")
    challenge = _object(value["challenge_policy"], "challenge policy")
    _exact(challenge, {"path", "semantic_route"}, "challenge policy")
    _string(challenge["path"], "challenge path")
    if challenge["semantic_route"] != "ESCALATE":
        raise AcceptanceContractError("semantic disputes must escalate")
    authorization = _object(value["authorization_policy"], "authorization policy")
    execution = _object(value["execution_policy"], "execution policy")
    if authorization != {"eligible_state": "NOT_ISSUED"}:
        raise AcceptanceContractError("eligibility must leave authorization NOT_ISSUED")
    if execution != {"eligible_state": "NOT_ATTEMPTED"}:
        raise AcceptanceContractError("eligibility must leave execution NOT_ATTEMPTED")


def parse_acceptance_context(
    raw: bytes, *, limits: AcceptanceLimits | None = None
) -> AcceptanceContext:
    active = limits or AcceptanceLimits()
    try:
        value = _strict_json(raw, "acceptance context", active)
    except Exception as exc:
        raise AcceptanceContractError(str(exc)) from exc
    return AcceptanceContext.from_dict(_object(value, "acceptance context"))


def parse_acceptance_bundle(
    root: str | Path, *, limits: AcceptanceLimits | None = None
) -> ParsedAcceptanceBundle:
    active = limits or AcceptanceLimits()
    directory = Path(root)
    if not directory.is_dir() or directory.is_symlink():
        raise AcceptanceContractError("acceptance bundle must be a real directory")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        _safe_path(relative, "bundle member")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AcceptanceContractError(f"bundle member {relative} is a symlink")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise AcceptanceContractError(f"bundle member {relative} is not a regular file")
        raw = path.read_bytes()
        if len(raw) > active.max_file_bytes:
            raise AcceptanceContractError(f"bundle member {relative} exceeds file limit")
        total += len(raw)
        if total > active.max_total_bytes:
            raise AcceptanceContractError("acceptance bundle exceeds total byte limit")
        files[relative] = raw
    if not files or len(files) > active.max_files:
        raise AcceptanceContractError("acceptance bundle file count is invalid")
    if CORE_FILE not in files or CONTRACT_FILE not in files or PUBLISH_FILE not in files:
        raise AcceptanceContractError("acceptance bundle is missing a required top-level member")
    try:
        core = _object(_strict_json(files[CORE_FILE], CORE_FILE, active), CORE_FILE)
        contract_value = _object(
            _strict_json(files[CONTRACT_FILE], CONTRACT_FILE, active), CONTRACT_FILE
        )
    except Exception as exc:
        raise AcceptanceContractError(str(exc)) from exc
    contract = AcceptanceContract(contract_value)
    _validate_core(core, files, contract)
    return ParsedAcceptanceBundle(directory, MappingProxyType(files), core, contract)


def _validate_core(
    core: Mapping[str, Any], files: Mapping[str, bytes], contract: AcceptanceContract
) -> None:
    _exact(
        core,
        {
            "profile",
            "transaction_id",
            "contract_hash",
            "term_root",
            "roles",
            "artifacts",
        },
        "acceptance core",
    )
    if core["profile"] != CORE_PROFILE:
        raise AcceptanceContractError("unsupported acceptance core profile")
    if core["transaction_id"] != contract.value["transaction_id"]:
        raise AcceptanceContractError("core transaction does not match contract")
    if core["contract_hash"] != contract.contract_hash:
        raise AcceptanceContractError("core contract hash does not match contract")
    if core["term_root"] != contract.value["term_root"]:
        raise AcceptanceContractError("core term root does not match contract")
    roles = _object(core["roles"], "core roles")
    if set(roles) != EXPECTED_ROLES or any(
        not isinstance(value, str) or not value for value in roles.values()
    ):
        raise AcceptanceContractError("core roles must contain the exact closed role set")
    artifacts = core["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise AcceptanceContractError("core artifacts must be a non-empty array")
    declared: dict[str, Mapping[str, Any]] = {}
    for item in artifacts:
        artifact = _object(item, "artifact")
        _exact(artifact, {"path", "media_type", "byte_length", "sha256"}, "artifact")
        path = _safe_path(artifact["path"], "artifact path")
        if path in declared:
            raise AcceptanceContractError("artifact paths must be unique")
        _string(artifact["media_type"], "artifact media type")
        _integer(artifact["byte_length"], "artifact byte length")
        _hash(artifact["sha256"], "artifact sha256")
        declared[path] = artifact
    expected_members = set(files) - {CORE_FILE, PUBLISH_FILE}
    if set(declared) != expected_members:
        raise AcceptanceContractError("bundle membership does not equal the core manifest")
    for path, artifact in declared.items():
        raw = files[path]
        if artifact["byte_length"] != len(raw) or artifact["sha256"] != _sha(raw):
            raise AcceptanceContractVerificationError(f"artifact commitment failed for {path}")


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _receipt_at(parsed: ParsedAcceptanceBundle, path: str) -> Mapping[str, Any] | None:
    raw = parsed.files.get(path)
    if raw is None:
        return None
    try:
        receipt = _object(_strict_json(raw, path, AcceptanceLimits()), path)
    except Exception as exc:
        raise AcceptanceContractError(str(exc)) from exc
    return receipt


def _direct_authority_is_verified(
    receipt: Mapping[str, Any], issuer: Any, verdict: Any
) -> bool:
    mandate = receipt.get("mandate")
    authority = mandate.get("authority") if isinstance(mandate, Mapping) else None
    return bool(
        verdict.authority_authentic == "verified"
        and isinstance(issuer, str)
        and isinstance(mandate, Mapping)
        and (mandate.get("deed_schema") or "0.2") == "0.2"
        and isinstance(authority, Mapping)
        and authority.get("principal") == issuer
        and authority.get("delegation") == []
    )


def _verify_receipt_source(
    receipt: Mapping[str, Any],
    *,
    path: str,
    action_type: str,
    role: str,
    adapter: str,
    parsed: ParsedAcceptanceBundle,
    context: AcceptanceContext,
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    result = verify_receipt(dict(receipt))
    signature = receipt.get("signature") or {}
    issuer = signature.get("issuer")
    if (
        not result.ok
        or result.verified_to != "attestation"
        or not _direct_authority_is_verified(receipt, issuer, result)
    ):
        errors.append(f"{path}: receipt did not reach authorization verification")
    if issuer not in context.accepted_issuers_by_role[role]:
        errors.append(f"{path}: issuer is not accepted for role {role}")
    action = _object(receipt.get("action"), f"{path} action")
    subject = _object(action.get("subject"), f"{path} subject")
    if action.get("type") != action_type:
        errors.append(f"{path}: action type mismatch")
    expected = {
        "profile",
        "issuer_role",
        "transaction_id",
        "contract_hash",
        "term_root",
        "build_hash",
        "environment",
        "adapter",
        "result",
        "parent_ref",
        "authority_epoch",
        "semantic_epoch",
    }
    if set(subject) != expected:
        errors.append(f"{path}: action subject fields are not closed")
        return False, tuple(errors)
    contract = parsed.contract.value
    terms = contract["terms"]
    checks = {
        "profile": PROFILE,
        "issuer_role": role,
        "transaction_id": contract["transaction_id"],
        "contract_hash": parsed.contract.contract_hash,
        "term_root": contract["term_root"],
        "build_hash": terms["build_hash"],
        "environment": terms["environment"],
        "adapter": adapter,
        "authority_epoch": contract["authority_epoch"],
        "semantic_epoch": contract["semantic_epoch"],
    }
    for field, expected_value in checks.items():
        if subject.get(field) != expected_value:
            errors.append(f"{path}: {field} binding mismatch")
    if adapter not in context.accepted_adapters:
        errors.append(f"{path}: adapter is not accepted by external context")
    if not isinstance(subject.get("result"), Mapping):
        errors.append(f"{path}: result must be an object")
    else:
        errors.extend(
            _adapter_binding_errors(
                subject["result"],
                adapter=adapter,
                path=path,
                contract=parsed.contract,
            )
        )
    if not isinstance(subject.get("parent_ref"), Mapping):
        errors.append(f"{path}: parent_ref must be an object")
    return not errors, tuple(errors)


def _adapter_binding_errors(
    result: Mapping[str, Any],
    *,
    adapter: str,
    path: str,
    contract: AcceptanceContract,
) -> tuple[str, ...]:
    errors: list[str] = []
    value = contract.value
    terms = value["terms"]
    if adapter == "bulla.receiver-observation/0.1":
        expected = {"observation_status", "observed_build_hash"}
        if set(result) != expected or result.get("observed_build_hash") != terms["build_hash"]:
            errors.append(f"{path}: receiver observation binding mismatch")
    elif adapter == "bulla.receiver-coverage/0.1":
        expected = {
            "coverage_status",
            "anchor_id",
            "observed_effect_ids",
            "receipted_effect_ids",
            "unmatched_effect_ids",
        }
        if set(result) != expected:
            errors.append(f"{path}: receiver coverage fields are not closed")
        else:
            observed = result["observed_effect_ids"]
            receipted = result["receipted_effect_ids"]
            unmatched = result["unmatched_effect_ids"]
            if (
                result["anchor_id"]
                != value["receiver_coverage_requirement"]["anchor_id"]
                or not _unique_string_array(observed)
                or not _unique_string_array(receipted, allow_empty=True)
                or not _unique_string_array(unmatched, allow_empty=True)
                or not set(receipted).issubset(set(observed))
                or sorted(set(observed) - set(receipted)) != sorted(unmatched)
                or (
                    result["coverage_status"] == "COVERED"
                    and len(unmatched) != 0
                )
                or (
                    result["coverage_status"] == "UNCOVERED"
                    and len(unmatched) == 0
                )
            ):
                errors.append(f"{path}: receiver coverage binding mismatch")
    elif adapter == "bulla.correction-state/0.1":
        expected = {"correction_state", "evaluated_contract_hash"}
        if set(result) != expected or result.get(
            "evaluated_contract_hash"
        ) != contract.contract_hash:
            errors.append(f"{path}: correction-state binding mismatch")
    elif adapter == "bulla.rollback-test/0.1":
        expected = {"rollback_status", "tested_build_hash", "tested_environment"}
        if (
            set(result) != expected
            or result.get("tested_build_hash") != terms["build_hash"]
            or result.get("tested_environment") != terms["environment"]
        ):
            errors.append(f"{path}: rollback-test binding mismatch")
    return tuple(errors)


def _unique_string_array(value: Any, *, allow_empty: bool = False) -> bool:
    return bool(
        isinstance(value, list)
        and (allow_empty or value)
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _parent_pair(receipt: Mapping[str, Any]) -> dict[str, str]:
    hashes = receipt["hashes"]
    return {"event": hashes["event"], "attestation": hashes["attestation"]}


def _not_computed(
    parsed: ParsedAcceptanceBundle,
    errors: list[str],
    requirements: tuple[RequirementResult, ...] = (),
) -> AcceptanceReport:
    contract = parsed.contract.value
    return AcceptanceReport(
        transaction_id=contract["transaction_id"],
        contract_hash=parsed.contract.contract_hash,
        record_integrity="FAILED",
        term_binding="NOT_COMPUTED",
        provider_acceptance="NOT_COMPUTED",
        sender_authority="NOT_COMPUTED",
        receiver_authority="NOT_COMPUTED",
        receiver_observation="NOT_COMPUTED",
        receiver_coverage="NOT_COMPUTED",
        correction_state="NOT_COMPUTED",
        requirements=requirements,
        decision="NOT_COMPUTED",
        conditional_evidence_requests=(),
        conditional_closure_sets=(),
        closure_minimality="NOT_COMPUTED",
        consequence=contract["contemplated_consequence"]["action_type"],
        consequence_eligibility="NOT_COMPUTED",
        authorization="NOT_ISSUED",
        execution="NOT_ATTEMPTED",
        limitations=(
            "Failed integrity or trust checks suppress dependent conclusions.",
        ),
        errors=tuple(errors),
    )


def evaluate_acceptance_bundle(
    bundle: ParsedAcceptanceBundle | str | Path,
    context: AcceptanceContext,
    *,
    limits: AcceptanceLimits | None = None,
) -> AcceptanceReport:
    parsed = (
        bundle
        if isinstance(bundle, ParsedAcceptanceBundle)
        else parse_acceptance_bundle(bundle, limits=limits)
    )
    contract = parsed.contract.value
    errors: list[str] = []
    forced_refusals: list[str] = []
    if parsed.contract.contract_hash != context.accepted_contract_hash:
        errors.append("contract hash is not adopted by the external context")
    if contract["policy_authority_ref"] not in context.accepted_policy_authorities:
        errors.append("policy authority is not accepted by the external context")
    if contract["authority_epoch"] != context.authority_epoch:
        forced_refusals.append("authority epoch mismatch")
    if contract["semantic_epoch"] != context.semantic_epoch:
        forced_refusals.append("semantic epoch mismatch")

    publish = _receipt_at(parsed, PUBLISH_FILE)
    if publish is None:
        raise AcceptanceContractError("publish receipt is missing")
    publish_result = verify_receipt(dict(publish))
    publish_subject = _object(
        _object(publish.get("action"), "publish action").get("subject"),
        "publish subject",
    )
    expected_publish_subject = {
        "profile": PROFILE,
        "issuer_role": "publisher",
        "transaction_id": contract["transaction_id"],
        "acceptance_core_hash": canonical_hash(dict(parsed.core)),
        "contract_hash": parsed.contract.contract_hash,
    }
    if (
        not publish_result.ok
        or publish_result.verified_to != "attestation"
        or not _direct_authority_is_verified(
            publish,
            (publish.get("signature") or {}).get("issuer"),
            publish_result,
        )
        or (publish.get("signature") or {}).get("issuer")
        not in context.accepted_issuers_by_role["publisher"]
        or publish_subject != expected_publish_subject
    ):
        errors.append("publish receipt did not authenticate the exact acceptance core")

    requirement_results: list[RequirementResult] = []
    receipts_by_path: dict[str, Mapping[str, Any]] = {}
    for requirement in contract["requirements"]:
        path = requirement["source_path"]
        receipt = _receipt_at(parsed, path)
        if receipt is None:
            route = requirement["absence_route"]
            requirement_results.append(
                RequirementResult(
                    requirement_id=requirement["requirement_id"],
                    status="UNAVAILABLE",
                    route=route,
                    observed_value="UNAVAILABLE",
                    accepted_values=tuple(requirement["accepted_values"]),
                    source_path=path,
                    evidence_request_id=requirement["evidence_request_id"],
                    reasons=("The required artifact is absent from the retained bundle.",),
                )
            )
            continue
        receipts_by_path[path] = receipt
        ok, source_errors = _verify_receipt_source(
            receipt,
            path=path,
            action_type=requirement["source_action"],
            role=requirement["source_role"],
            adapter=requirement["adapter"],
            parsed=parsed,
            context=context,
        )
        if not ok:
            trust_errors = tuple(
                item
                for item in source_errors
                if "issuer is not accepted" in item
                or "adapter is not accepted" in item
            )
            errors.extend(trust_errors)
            requirement_results.append(
                RequirementResult(
                    requirement_id=requirement["requirement_id"],
                    status="FAILED",
                    route="REFUSE",
                    observed_value="NOT_COMPUTED",
                    accepted_values=tuple(requirement["accepted_values"]),
                    source_path=path,
                    evidence_request_id=None,
                    reasons=source_errors,
                )
            )
            continue
        subject = receipt["action"]["subject"]
        observed = subject["result"].get(requirement["result_field"])
        if not isinstance(observed, str) or not observed:
            route = requirement["ambiguity_route"]
            status = "UNRESOLVED"
            observed_value = "NOT_COMPUTED"
        elif observed in requirement["accepted_values"]:
            route = "PROCEED"
            status = "SATISFIED"
            observed_value = observed
        elif observed in requirement["negative_values"]:
            route = requirement["negative_route"]
            status = "NEGATIVE"
            observed_value = observed
        else:
            route = requirement["ambiguity_route"]
            status = "UNRESOLVED"
            observed_value = observed
        requirement_results.append(
            RequirementResult(
                requirement_id=requirement["requirement_id"],
                status=status,
                route=route,
                observed_value=observed_value,
                accepted_values=tuple(requirement["accepted_values"]),
                source_path=path,
                evidence_request_id=(
                    requirement["evidence_request_id"]
                    if route == "HOLD_FOR_EVIDENCE"
                    else None
                ),
            )
        )

    # The request and provider acceptance are the first two requirements.
    by_id = {item.requirement_id: item for item in requirement_results}
    request_receipt = receipts_by_path.get("records/01-request.json")
    accept_receipt = receipts_by_path.get("records/02-accept.json")
    ready_receipt = receipts_by_path.get("records/03-staging-ready.json")
    if request_receipt and accept_receipt:
        if accept_receipt["action"]["subject"]["parent_ref"] != _parent_pair(request_receipt):
            forced_refusals.append(
                "provider acceptance does not bind the exact request occurrence"
            )
    if accept_receipt and ready_receipt:
        if ready_receipt["action"]["subject"]["parent_ref"] != _parent_pair(accept_receipt):
            forced_refusals.append(
                "staging-ready claim does not bind the exact provider acceptance"
            )

    if errors:
        return _not_computed(parsed, errors, tuple(requirement_results))

    routes = [item.route for item in requirement_results]
    routes.extend("REFUSE" for _item in forced_refusals)
    decision = max(routes, key=lambda route: ROUTE_PRECEDENCE[route]) if routes else "NOT_COMPUTED"
    if decision == "PROCEED":
        eligibility = "ELIGIBLE"
    elif decision == "ESCALATE":
        eligibility = "CHALLENGE_REQUIRED"
    else:
        eligibility = "INELIGIBLE"

    request_catalog = {
        item["request_id"]: item for item in contract["evidence_requests"]
    }
    requested_ids = tuple(
        sorted(
            item.evidence_request_id
            for item in requirement_results
            if item.route == "HOLD_FOR_EVIDENCE" and item.evidence_request_id is not None
        )
    )
    requests = tuple(request_catalog[item] for item in requested_ids)
    closure_sets = (requested_ids,) if requested_ids and decision == "HOLD_FOR_EVIDENCE" else ()
    receiver = by_id.get("receiver-observation")
    coverage = by_id.get("receiver-coverage")
    correction = by_id.get("correction-current")
    return AcceptanceReport(
        transaction_id=contract["transaction_id"],
        contract_hash=parsed.contract.contract_hash,
        record_integrity=(
            "FAILED"
            if any(item.status == "FAILED" for item in requirement_results)
            else "VERIFIED"
        ),
        term_binding=(
            "FAILED"
            if any(
                "binding mismatch" in reason
                for item in requirement_results
                for reason in item.reasons
            )
            else "VERIFIED"
        ),
        provider_acceptance=(
            "VERIFIED"
            if by_id.get("provider-acceptance") is not None
            and by_id["provider-acceptance"].status == "SATISFIED"
            else "NOT_COMPUTED"
        ),
        sender_authority="VERIFIED",
        receiver_authority=(
            "VERIFIED"
            if receiver is not None and receiver.status == "SATISFIED"
            else "NOT_COMPUTED"
        ),
        receiver_observation=(
            "VERIFIED"
            if receiver is not None and receiver.status == "SATISFIED"
            else "NOT_COMPUTED"
        ),
        receiver_coverage=(
            "COVERED"
            if coverage is not None and coverage.status == "SATISFIED"
            else "UNCOVERED"
        ),
        correction_state=(
            "CURRENT"
            if correction is not None and correction.status == "SATISFIED"
            else "STALE"
        ),
        requirements=tuple(requirement_results),
        decision=decision,
        conditional_evidence_requests=requests,
        conditional_closure_sets=closure_sets,
        closure_minimality=(
            "INCLUSION_MINIMAL_WITHIN_DECLARED_CATALOG" if closure_sets else "NOT_APPLICABLE"
        ),
        consequence=contract["contemplated_consequence"]["action_type"],
        consequence_eligibility=eligibility,
        authorization="NOT_ISSUED",
        execution="NOT_ATTEMPTED",
        limitations=(
            "A conditional evidence request does not establish that the requested "
            "artifact exists or will satisfy the policy.",
            "The supplied receiver record does not establish denominator completeness.",
            "Eligibility is not authorization or execution.",
            "The retained records do not establish that a deployment occurred.",
        ),
        errors=tuple(forced_refusals),
    )


def report_exit_code(report: AcceptanceReport) -> int:
    return 1 if report.decision == "NOT_COMPUTED" else 0
