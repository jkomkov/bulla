"""Source-only inference-clearing profile.

The profile binds one inference order to exact terms, occurrence-bound receipts,
execution evidence, receiver coverage, witness inclusion, a relying-party policy,
capital allocation, and a named payment consequence.  It deliberately does not
interpret prompts, establish model quality, bootstrap trust from bundle-carried
keys, or collapse its report into one validity bit.

This module is intentionally absent from :mod:`bulla.experimental` aggregate
exports and from packaged Bulla artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from bulla._canonical import CanonicalizationError, canonical_jcs_int
from bulla.action_receipt import verify_receipt
from bulla.experimental.checkpoint import verify_checkpoint
from bulla.receipt_parser import (
    ReceiptParseError,
    ReceiptParseLimits,
    parse_action_receipt_json,
)
from bulla.registry import Deed, verify_inclusion_record


PROFILE = "bulla.inference-clearing/0.1-experimental"
EXECUTION_PROFILE = "bulla.inference-execution-evidence/0.1-experimental"
COVERAGE_PROFILE = "bulla.inference-receiver-coverage/0.1-experimental"
CAPITAL_PROFILE = "bulla.inference-capital-allocation/0.1-experimental"
SETTLEMENT_PROFILE = "bulla.inference-settlement-report/0.1-experimental"

CORE_FILE = "clearing-core.json"
PUBLISH_FILE = "publish-receipt.json"
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SAFE_INTEGER = 9_007_199_254_740_991

ACTION_ROLES: Mapping[str, str] = MappingProxyType(
    {
        "inference.order": "buyer",
        "inference.route": "router",
        "inference.accept": "provider",
        "inference.delivery": "receiver",
        "inference.coverage.checkpoint": "receiver",
        "assurance.collateral.bind": "rail_observer",
        "assurance.guarantee.issue": "guarantor",
        "bulla.rely": "relier",
        "assurance.settlement.authorize": "settlement_authority",
        "rail.settlement.report": "rail_observer",
    }
)
ACTION_SUBJECT_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "inference.order": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "input_hash",
                "price",
            }
        ),
        "inference.route": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "provider_kind",
            }
        ),
        "inference.accept": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "accepted_model_hash",
                "execution_report_hash",
                "provider_process_claim",
            }
        ),
        "inference.delivery": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "effect_id",
                "output_hash",
                "receiver_anchor",
            }
        ),
        "inference.coverage.checkpoint": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "coverage_hash",
                "anchor_id",
                "denominator_count",
                "receipted_count",
            }
        ),
        "assurance.collateral.bind": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "capital_hash",
            }
        ),
        "assurance.guarantee.issue": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "guaranteed_claim",
                "exposure",
                "unit",
                "capital_hash",
            }
        ),
        "bulla.rely": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "policy_hash",
                "decision",
                "unmet_requirements",
                "named_consequence",
                "witness_root",
                "coverage_hash",
                "coverage_ref",
            }
        ),
        "assurance.settlement.authorize": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "eligibility",
                "destination",
                "amount",
                "unit",
                "rail_adapter",
            }
        ),
        "rail.settlement.report": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "term_root",
                "comparison_group",
                "parent_ref",
                "status",
                "authorization_ref",
                "amount",
                "unit",
                "rail_adapter",
                "settlement_hash",
            }
        ),
        "inference.clearing.publish": frozenset(
            {
                "profile",
                "issuer_role",
                "transaction_id",
                "clearing_core_hash",
                "component_hashes",
            }
        ),
    }
)
COMPONENT_NAMES = frozenset(
    {"terms", "receipts", "execution", "receiver", "witness", "assurance", "settlement"}
)
DECLARED_ROLE_NAMES = frozenset(
    {
        "buyer",
        "router",
        "provider_opaque",
        "provider_reproducible",
        "receiver",
        "witness",
        "relier",
        "settlement_authority",
        "rail_observer",
        "guarantor",
        "publisher",
    }
)
CONTEXT_ROLE_NAMES = frozenset(
    {
        "buyer",
        "router",
        "provider",
        "receiver",
        "witness",
        "relier",
        "settlement_authority",
        "rail_observer",
        "guarantor",
        "publisher",
    }
)
BASE_SEQUENCE = (
    "inference.order",
    "inference.route",
    "inference.accept",
    "inference.delivery",
    "assurance.collateral.bind",
    "inference.coverage.checkpoint",
    "bulla.rely",
)
GUARANTEE_SEQUENCE = BASE_SEQUENCE[:5] + (
    "assurance.guarantee.issue",
    "inference.coverage.checkpoint",
    "bulla.rely",
)
ELIGIBLE_SEQUENCE = BASE_SEQUENCE + (
    "assurance.settlement.authorize",
    "rail.settlement.report",
)
PROVIDER_KINDS = frozenset({"OPAQUE", "REPRODUCIBLE"})
RELIANCE = frozenset({"RELY", "REFUSE", "ESCALATE"})
PAYMENT = frozenset({"ELIGIBLE", "INELIGIBLE", "CHALLENGE_REQUIRED", "NOT_COMPUTED"})
EVIDENCE_REQUIREMENT_FIELDS = frozenset(
    {
        "output_binding",
        "model_binding",
        "relation_reproduction",
        "provider_execution_occurrence",
    }
)


class InferenceClearingError(ValueError):
    """The bundle is malformed, unsafe, ambiguous, or unsupported."""


class InferenceClearingVerificationError(InferenceClearingError):
    """A well-formed bundle failed an integrity or required trust-policy check."""


@dataclass(frozen=True)
class InferenceClearingLimits:
    max_file_bytes: int = 262_144
    max_total_bytes: int = 4_194_304
    max_files: int = 64
    max_depth: int = 24
    max_nodes: int = 25_000
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
            raise ValueError("inference-clearing limits must be positive")


@dataclass(frozen=True)
class InferenceClearingContext:
    """Relying-party trust obtained outside the clearing bundle."""

    accepted_issuers_by_role: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]]
    accepted_execution_adapters: frozenset[str] | set[str] | tuple[str, ...]
    accepted_rail_adapters: frozenset[str] | set[str] | tuple[str, ...]
    accepted_policy_hash: str
    accepted_evidence_requirements: Mapping[str, str]
    trusted_witness_roots: frozenset[str] | set[str] | tuple[str, ...]
    team_controlled_roles: frozenset[str] | set[str] | tuple[str, ...] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted_issuers_by_role, Mapping):
            raise TypeError("accepted_issuers_by_role must be a mapping")
        if set(self.accepted_issuers_by_role) != CONTEXT_ROLE_NAMES:
            raise ValueError("accepted_issuers_by_role must contain the exact closed role set")
        normalized: dict[str, frozenset[str]] = {}
        issuer_roles: dict[str, str] = {}
        for role, issuers in self.accepted_issuers_by_role.items():
            if not isinstance(role, str) or not role:
                raise ValueError("accepted issuer roles must be non-empty strings")
            values = frozenset(issuers)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"accepted issuers for {role!r} are invalid")
            for issuer in values:
                previous = issuer_roles.get(issuer)
                if previous is not None and previous != role:
                    raise ValueError(
                        f"accepted issuer {issuer!r} is assigned to both {previous!r} and {role!r}"
                    )
                issuer_roles[issuer] = role
            normalized[role] = values
        adapters = frozenset(self.accepted_execution_adapters)
        rails = frozenset(self.accepted_rail_adapters)
        roots = frozenset(self.trusted_witness_roots)
        if not adapters or not rails or not roots:
            raise ValueError("accepted adapters and trusted witness roots must be non-empty")
        if not HASH_RE.fullmatch(self.accepted_policy_hash):
            raise ValueError("accepted_policy_hash must be sha256:<64 lowercase hex>")
        requirements = dict(self.accepted_evidence_requirements)
        if set(requirements) != EVIDENCE_REQUIREMENT_FIELDS:
            raise ValueError("accepted_evidence_requirements must contain the exact closed field set")
        expected_requirements = {
            "output_binding": "VERIFIED",
            "model_binding": "TERM_BOUND",
            "relation_reproduction": "REPRODUCED",
            "provider_execution_occurrence": "NOT_REQUIRED",
        }
        if requirements != expected_requirements:
            raise ValueError("accepted_evidence_requirements contains unsupported values")
        team_roles = frozenset(self.team_controlled_roles)
        if any(not isinstance(role, str) for role in team_roles) or not team_roles <= CONTEXT_ROLE_NAMES:
            raise ValueError("team_controlled_roles must name only closed verification-context roles")
        object.__setattr__(self, "accepted_issuers_by_role", MappingProxyType(normalized))
        object.__setattr__(self, "accepted_execution_adapters", adapters)
        object.__setattr__(self, "accepted_rail_adapters", rails)
        object.__setattr__(self, "accepted_evidence_requirements", MappingProxyType(requirements))
        object.__setattr__(self, "trusted_witness_roots", roots)
        object.__setattr__(self, "team_controlled_roles", team_roles)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InferenceClearingContext":
        _exact(
            value,
            {
                "accepted_issuers_by_role",
                "accepted_execution_adapters",
                "accepted_rail_adapters",
                "accepted_policy_hash",
                "accepted_evidence_requirements",
                "trusted_witness_roots",
                "team_controlled_roles",
            },
            "verification context",
        )
        roles = value["accepted_issuers_by_role"]
        if not isinstance(roles, dict):
            raise InferenceClearingError("accepted_issuers_by_role must be an object")
        requirements = value["accepted_evidence_requirements"]
        if not isinstance(requirements, dict):
            raise InferenceClearingError("accepted_evidence_requirements must be an object")
        return cls(
            accepted_issuers_by_role={
                role: frozenset(_string_list(issuers, f"accepted_issuers_by_role.{role}"))
                for role, issuers in roles.items()
            },
            accepted_execution_adapters=frozenset(
                _string_list(value["accepted_execution_adapters"], "accepted_execution_adapters")
            ),
            accepted_rail_adapters=frozenset(
                _string_list(value["accepted_rail_adapters"], "accepted_rail_adapters")
            ),
            accepted_policy_hash=_hash(value["accepted_policy_hash"], "accepted_policy_hash"),
            accepted_evidence_requirements=requirements,
            trusted_witness_roots=frozenset(
                _string_list(value["trusted_witness_roots"], "trusted_witness_roots")
            ),
            team_controlled_roles=frozenset(
                _string_list(value["team_controlled_roles"], "team_controlled_roles")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_issuers_by_role": {
                role: sorted(issuers)
                for role, issuers in sorted(self.accepted_issuers_by_role.items())
            },
            "accepted_execution_adapters": sorted(self.accepted_execution_adapters),
            "accepted_rail_adapters": sorted(self.accepted_rail_adapters),
            "accepted_policy_hash": self.accepted_policy_hash,
            "accepted_evidence_requirements": dict(self.accepted_evidence_requirements),
            "trusted_witness_roots": sorted(self.trusted_witness_roots),
            "team_controlled_roles": sorted(self.team_controlled_roles),
        }


@dataclass(frozen=True)
class ParsedInferenceClearingBundle:
    root: Path
    core: Mapping[str, Any]
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class RelationEvidenceReport:
    adapter: str
    adapter_acceptance: str
    model_availability: str
    input_binding: str
    output_binding: str
    trace_binding: str
    model_binding: str
    relation_reproduction: str
    provider_process_claim: str
    provider_execution_occurrence: str
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError("RelationEvidenceReport has named dimensions; inspect them directly")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "adapter_acceptance": self.adapter_acceptance,
            "model_availability": self.model_availability,
            "input_binding": self.input_binding,
            "output_binding": self.output_binding,
            "trace_binding": self.trace_binding,
            "model_binding": self.model_binding,
            "relation_reproduction": self.relation_reproduction,
            "provider_process_claim": self.provider_process_claim,
            "provider_execution_occurrence": self.provider_execution_occurrence,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class InferenceClearingReport:
    transaction_id: str
    provider_kind: str
    output: str
    bundle_integrity: str
    receipt_integrity: str
    signature_status: str
    issuer_role_status: str
    term_binding: str
    lineage_integrity: str
    occurrence_binding: str
    relation_evidence: RelationEvidenceReport
    receiver_coverage: str
    receiver_total: int
    receiver_receipted: int
    uncovered_effects: tuple[str, ...]
    witness_inclusion: str
    witness_root_trust: str
    capital_allocation: str
    recourse_conveyance: str
    recourse_reachability: str
    reliance_decision: str
    payment_eligibility: str
    settlement_authorization: str
    settlement_execution: str
    denominator_completeness: str = "NOT_ESTABLISHED"
    custody: str = "NOT_COMPUTED"
    collectibility: str = "NOT_COMPUTED"
    worldly_truth: str = "NOT_COMPUTED"
    suppressed_conclusions: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError(
            "InferenceClearingReport has independent dimensions; inspect reliance_decision "
            "and payment_eligibility directly"
        )

    @property
    def exit_code(self) -> int:
        hard = {
            self.bundle_integrity,
            self.receipt_integrity,
            self.signature_status,
            self.issuer_role_status,
            self.term_binding,
            self.lineage_integrity,
            self.occurrence_binding,
            self.witness_inclusion,
            self.witness_root_trust,
        }
        return 1 if self.errors or "FAILED" in hard or "UNTRUSTED" in hard else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "transaction_id": self.transaction_id,
            "provider_kind": self.provider_kind,
            "output": self.output,
            "bundle_integrity": self.bundle_integrity,
            "receipt_integrity": self.receipt_integrity,
            "signature_status": self.signature_status,
            "issuer_role_status": self.issuer_role_status,
            "term_binding": self.term_binding,
            "lineage_integrity": self.lineage_integrity,
            "occurrence_binding": self.occurrence_binding,
            "relation_evidence": self.relation_evidence.to_dict(),
            "receiver_coverage": self.receiver_coverage,
            "receiver_total": self.receiver_total,
            "receiver_receipted": self.receiver_receipted,
            "uncovered_effects": list(self.uncovered_effects),
            "witness_inclusion": self.witness_inclusion,
            "witness_root_trust": self.witness_root_trust,
            "capital_allocation": self.capital_allocation,
            "recourse_conveyance": self.recourse_conveyance,
            "recourse_reachability": self.recourse_reachability,
            "reliance_decision": self.reliance_decision,
            "payment_eligibility": self.payment_eligibility,
            "settlement_authorization": self.settlement_authorization,
            "settlement_execution": self.settlement_execution,
            "denominator_completeness": self.denominator_completeness,
            "custody": self.custody,
            "collectibility": self.collectibility,
            "worldly_truth": self.worldly_truth,
            "suppressed_conclusions": list(self.suppressed_conclusions),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "exit_code": self.exit_code,
        }


def _exact(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise InferenceClearingError(f"{label} must be an object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise InferenceClearingError(
            f"{label} fields mismatch; missing={sorted(missing)} extra={sorted(extra)}"
        )


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise InferenceClearingError(f"{label} must be an array of non-empty strings")
    if len(set(value)) != len(value):
        raise InferenceClearingError(f"{label} contains duplicates")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise InferenceClearingError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _amount(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > SAFE_INTEGER:
        raise InferenceClearingError(f"{label} must be a non-negative safe integer")
    return value


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    try:
        encoded = canonical_jcs_int(value).encode("utf-8")
    except CanonicalizationError as exc:
        raise InferenceClearingError(f"value is not canonicalizable: {exc}") from exc
    return _sha_bytes(encoded)


def run_reference_model(model: Mapping[str, Any], inputs: list[int]) -> tuple[str, dict[str, Any]]:
    """Run the closed integer MLP and return its exact trace."""

    _exact(model, {"profile", "input_width", "hidden_weights", "hidden_bias", "output_weights", "output_bias", "labels"}, "model")
    if model["profile"] != "bulla.int8-mlp/1" or model["input_width"] != 8:
        raise InferenceClearingError("unsupported deterministic model profile")
    if len(inputs) != 8 or any(isinstance(item, bool) or not isinstance(item, int) for item in inputs):
        raise InferenceClearingError("model input must contain exactly eight integers")
    hidden_weights = model["hidden_weights"]
    hidden_bias = model["hidden_bias"]
    output_weights = model["output_weights"]
    output_bias = model["output_bias"]
    labels = model["labels"]
    if (
        not isinstance(hidden_weights, list)
        or not hidden_weights
        or len(hidden_weights) != len(hidden_bias)
        or any(not isinstance(row, list) or len(row) != 8 for row in hidden_weights)
        or not isinstance(output_weights, list)
        or len(output_weights) != 2
        or any(not isinstance(row, list) or len(row) != len(hidden_weights) for row in output_weights)
        or not isinstance(output_bias, list)
        or len(output_bias) != 2
        or labels != ["PRIMARY", "BACKUP"]
    ):
        raise InferenceClearingError("deterministic model dimensions are invalid")
    numbers = [*inputs, *hidden_bias, *output_bias]
    for row in hidden_weights + output_weights:
        numbers.extend(row)
    if any(isinstance(item, bool) or not isinstance(item, int) or abs(item) > 1_000_000 for item in numbers):
        raise InferenceClearingError("deterministic model uses unsupported integer values")
    def safe_dot(row: list[int], values: list[int], bias: int) -> int:
        total = 0
        for weight, value in zip(row, values):
            product = weight * value
            if abs(product) > SAFE_INTEGER:
                raise InferenceClearingError("deterministic model multiplication exceeds safe-integer range")
            total += product
            if abs(total) > SAFE_INTEGER:
                raise InferenceClearingError("deterministic model accumulation exceeds safe-integer range")
        total += bias
        if abs(total) > SAFE_INTEGER:
            raise InferenceClearingError("deterministic model biased sum exceeds safe-integer range")
        return total

    hidden = [max(0, safe_dot(row, inputs, hidden_bias[index])) for index, row in enumerate(hidden_weights)]
    scores = [safe_dot(row, hidden, output_bias[index]) for index, row in enumerate(output_weights)]
    selected = 1 if scores[1] >= scores[0] else 0
    output = labels[selected]
    return output, {"hidden": hidden, "scores": scores, "selected_index": selected, "output": output}


def _strict_json(raw: bytes, label: str, limits: InferenceClearingLimits) -> Any:
    if len(raw) > limits.max_file_bytes:
        raise InferenceClearingError(f"{label} exceeds {limits.max_file_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InferenceClearingError(f"{label} is not valid UTF-8") from exc

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InferenceClearingError(f"duplicate JSON member {key!r} in {label}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise InferenceClearingError(f"non-finite number {value!r} in {label}")

    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
    except InferenceClearingError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InferenceClearingError(f"invalid JSON in {label}: {exc}") from exc
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise InferenceClearingError(f"{label} exceeds JSON resource limits")
        if isinstance(current, str):
            try:
                encoded = current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise InferenceClearingError(f"{label} contains a lone Unicode surrogate") from exc
            if len(encoded) > limits.max_string_bytes:
                raise InferenceClearingError(f"{label} contains an oversized string")
        elif isinstance(current, int) and not isinstance(current, bool):
            if abs(current) > SAFE_INTEGER:
                raise InferenceClearingError(f"{label} contains an unsafe integer")
        elif isinstance(current, float):
            raise InferenceClearingError(f"{label} contains a floating-point number")
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return value


def _safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise InferenceClearingError(f"{label} must be a normalized relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise InferenceClearingError(f"{label} must be a normalized relative POSIX path")
    if path.as_posix() != value:
        raise InferenceClearingError(f"{label} is not normalized")
    return value


def _walk(root: Path, limits: InferenceClearingLimits) -> dict[str, bytes]:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise InferenceClearingError(f"cannot inspect bundle root: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InferenceClearingError("bundle root must be a non-symlink directory")
    files: dict[str, bytes] = {}
    total = 0
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    for current, directories, filenames, directory_fd in os.fwalk(root, follow_symlinks=False):
        current_path = Path(current)
        for name in directories:
            directory_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(directory_metadata.st_mode):
                child = current_path / name
                raise InferenceClearingError(f"bundle contains symlink directory {child.relative_to(root)}")
        for name in sorted(filenames):
            child = current_path / name
            relative = _safe_path(child.relative_to(root).as_posix(), "bundle member")
            child_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(child_metadata.st_mode):
                raise InferenceClearingError(f"bundle contains symlink member {relative}")
            if not stat.S_ISREG(child_metadata.st_mode):
                raise InferenceClearingError(f"bundle contains non-regular member {relative}")
            if len(files) >= limits.max_files or child_metadata.st_size > limits.max_file_bytes:
                raise InferenceClearingError("bundle exceeds file limits")
            descriptor = -1
            try:
                descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=directory_fd)
                opened_metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_metadata.st_mode)
                    or opened_metadata.st_dev != child_metadata.st_dev
                    or opened_metadata.st_ino != child_metadata.st_ino
                ):
                    raise InferenceClearingError(f"bundle member {relative} changed before open")
                chunks: list[bytes] = []
                remaining = limits.max_file_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                after_metadata = os.fstat(descriptor)
                if (
                    len(raw) != opened_metadata.st_size
                    or after_metadata.st_dev != opened_metadata.st_dev
                    or after_metadata.st_ino != opened_metadata.st_ino
                    or after_metadata.st_size != opened_metadata.st_size
                ):
                    raise InferenceClearingError(f"bundle member {relative} changed while reading")
            except OSError as exc:
                raise InferenceClearingError(f"cannot safely read bundle member {relative}: {exc}") from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            total += len(raw)
            if total > limits.max_total_bytes:
                raise InferenceClearingError("bundle exceeds total-byte limit")
            files[relative] = raw
    return files


def _validate_core(core: Any) -> None:
    _exact(
        core,
        {
            "profile",
            "transaction_id",
            "revision",
            "provider_kind",
            "comparison_group",
            "role_issuers",
            "term_root",
            "ordered_receipts",
            "artifacts",
        },
        "clearing core",
    )
    if core["profile"] != PROFILE or core["revision"] != 1:
        raise InferenceClearingError("unsupported clearing profile or revision")
    if core["provider_kind"] not in PROVIDER_KINDS:
        raise InferenceClearingError("unknown provider kind")
    if not isinstance(core["transaction_id"], str) or not core["transaction_id"]:
        raise InferenceClearingError("transaction_id must be a non-empty string")
    _hash(core["comparison_group"], "comparison_group")
    _hash(core["term_root"], "term_root")
    roles = core["role_issuers"]
    if not isinstance(roles, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in roles.items()):
        raise InferenceClearingError("role_issuers must map roles to issuer strings")
    if len(set(roles.values())) != len(roles):
        raise InferenceClearingError("role_issuers must not reuse one issuer across technical roles")
    if set(roles) != DECLARED_ROLE_NAMES:
        raise InferenceClearingError("role_issuers does not match the closed technical-role set")
    receipts = core["ordered_receipts"]
    if not isinstance(receipts, list) or not receipts:
        raise InferenceClearingError("ordered_receipts must be non-empty")
    for index, item in enumerate(receipts):
        _exact(item, {"action_type", "role", "path", "event", "attestation"}, f"ordered_receipts[{index}]")
        if item["action_type"] not in ACTION_ROLES or item["role"] != ACTION_ROLES[item["action_type"]]:
            raise InferenceClearingError(f"ordered_receipts[{index}] has an unsupported action or role")
        _safe_path(item["path"], f"ordered_receipts[{index}].path")
        _hash(item["event"], f"ordered_receipts[{index}].event")
        _hash(item["attestation"], f"ordered_receipts[{index}].attestation")
    for field in ("path", "event", "attestation"):
        values = [item[field] for item in receipts]
        if len(values) != len(set(values)):
            raise InferenceClearingError(f"ordered_receipts contains duplicate {field} values")
    artifacts = core["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise InferenceClearingError("artifacts must be non-empty")
    paths: list[str] = []
    for index, item in enumerate(artifacts):
        _exact(item, {"path", "media_type", "byte_length", "sha256"}, f"artifacts[{index}]")
        paths.append(_safe_path(item["path"], f"artifacts[{index}].path"))
        if item["path"].split("/", 1)[0] not in COMPONENT_NAMES:
            raise InferenceClearingError(f"artifacts[{index}] is outside the closed bundle layout")
        expected_media_type = (
            "application/json"
            if item["path"].endswith(".json")
            else "application/octet-stream"
        )
        if item["media_type"] != expected_media_type:
            raise InferenceClearingError(
                f"artifacts[{index}] has the wrong media type for {item['path']}"
            )
        if isinstance(item["byte_length"], bool) or not isinstance(item["byte_length"], int) or item["byte_length"] < 0:
            raise InferenceClearingError("artifact byte_length must be a non-negative integer")
        _hash(item["sha256"], f"artifacts[{index}].sha256")
    if len(paths) != len(set(paths)):
        raise InferenceClearingError("artifact paths must be unique")
    if paths != sorted(paths):
        raise InferenceClearingError("artifact manifest must use deterministic path ordering")
    if not {item["path"] for item in receipts}.issubset(set(paths)):
        raise InferenceClearingError("every ordered receipt must be declared as an artifact")


def parse_inference_clearing_bundle(
    bundle_dir: str | Path,
    *,
    limits: InferenceClearingLimits = InferenceClearingLimits(),
) -> ParsedInferenceClearingBundle:
    root = Path(bundle_dir)
    files = _walk(root, limits)
    if CORE_FILE not in files or PUBLISH_FILE not in files:
        raise InferenceClearingError("bundle is missing clearing-core.json or publish-receipt.json")
    core = _strict_json(files[CORE_FILE], CORE_FILE, limits)
    _validate_core(core)
    declared = {CORE_FILE, PUBLISH_FILE, *(item["path"] for item in core["artifacts"])}
    undeclared = sorted(set(files) - declared)
    missing = sorted(declared - set(files))
    if undeclared or missing:
        raise InferenceClearingError(f"bundle membership mismatch; undeclared={undeclared} missing={missing}")
    for item in core["artifacts"]:
        raw = files[item["path"]]
        if len(raw) != item["byte_length"] or _sha_bytes(raw) != item["sha256"]:
            raise InferenceClearingVerificationError(
                f"artifact commitment mismatch for {item['path']}"
            )
    return ParsedInferenceClearingBundle(root.resolve(), MappingProxyType(core), MappingProxyType(files))


def component_hashes(core: Mapping[str, Any]) -> dict[str, str]:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in sorted(COMPONENT_NAMES)}
    for item in core["artifacts"]:
        prefix = item["path"].split("/", 1)[0]
        if prefix not in grouped:
            raise InferenceClearingError(f"artifact {item['path']} is outside the closed bundle layout")
        grouped[prefix].append(dict(item))
    return {name: canonical_hash(items) for name, items in grouped.items()}


def _parse_json_file(parsed: ParsedInferenceClearingBundle, path: str, limits: InferenceClearingLimits) -> Any:
    try:
        raw = parsed.files[path]
    except KeyError as exc:
        raise InferenceClearingError(f"bundle is missing required artifact {path}") from exc
    return _strict_json(raw, path, limits)


def _parse_json_object(
    parsed: ParsedInferenceClearingBundle,
    path: str,
    limits: InferenceClearingLimits,
) -> dict[str, Any]:
    value = _parse_json_file(parsed, path, limits)
    if not isinstance(value, dict):
        raise InferenceClearingError(f"{path} must contain a JSON object")
    return value


def _receipt_subject(receipt: Mapping[str, Any], action_type: str) -> Mapping[str, Any]:
    if receipt.get("schema_version") != "0.4" or (receipt.get("action") or {}).get("type") != action_type:
        raise InferenceClearingError(f"receipt is not ActionReceipt v0.4 {action_type}")
    subject = (receipt.get("action") or {}).get("subject")
    if not isinstance(subject, dict):
        raise InferenceClearingError(f"{action_type} subject must be an object")
    try:
        expected_fields = set(ACTION_SUBJECT_FIELDS[action_type])
    except KeyError as exc:
        raise InferenceClearingError(f"unsupported receipt action {action_type}") from exc
    _exact(subject, expected_fields, f"{action_type} subject")
    if subject["profile"] != PROFILE or not isinstance(subject["transaction_id"], str) or not subject["transaction_id"]:
        raise InferenceClearingError(f"{action_type} subject has an invalid profile or transaction")
    expected_role = ACTION_ROLES.get(action_type, "publisher")
    if subject["issuer_role"] != expected_role:
        raise InferenceClearingError(f"{action_type} subject has an invalid issuer role")
    for key in (
        "term_root",
        "comparison_group",
        "input_hash",
        "accepted_model_hash",
        "execution_report_hash",
        "output_hash",
        "capital_hash",
        "coverage_hash",
        "policy_hash",
        "witness_root",
        "settlement_hash",
        "clearing_core_hash",
    ):
        if key in subject:
            _hash(subject[key], f"{action_type}.{key}")
    for reference_name in ("parent_ref", "coverage_ref"):
        if reference_name not in subject:
            continue
        _exact(subject[reference_name], {"event", "attestation"}, f"{action_type}.{reference_name}")
        _hash(subject[reference_name]["event"], f"{action_type}.{reference_name}.event")
        _hash(subject[reference_name]["attestation"], f"{action_type}.{reference_name}.attestation")
    if "authorization_ref" in subject:
        _exact(subject["authorization_ref"], {"event", "attestation"}, f"{action_type}.authorization_ref")
        _hash(subject["authorization_ref"]["event"], f"{action_type}.authorization_ref.event")
        _hash(subject["authorization_ref"]["attestation"], f"{action_type}.authorization_ref.attestation")
    if "price" in subject:
        _exact(subject["price"], {"amount", "unit"}, f"{action_type}.price")
        _amount(subject["price"]["amount"], f"{action_type}.price.amount")
        if not isinstance(subject["price"]["unit"], str) or not subject["price"]["unit"]:
            raise InferenceClearingError(f"{action_type}.price.unit must be non-empty")
    if "amount" in subject:
        _amount(subject["amount"], f"{action_type}.amount")
    if "exposure" in subject:
        _amount(subject["exposure"], f"{action_type}.exposure")
    for count_name in ("denominator_count", "receipted_count"):
        if count_name in subject:
            _amount(subject[count_name], f"{action_type}.{count_name}")
    if "unmet_requirements" in subject:
        _string_list(subject["unmet_requirements"], f"{action_type}.unmet_requirements")
    if "provider_process_claim" in subject and subject["provider_process_claim"] not in {
        "self_asserted",
        "absent",
    }:
        raise InferenceClearingError(
            f"{action_type}.provider_process_claim is outside the closed vocabulary"
        )
    return subject


def _direct_authority_is_verified(
    receipt: Mapping[str, Any],
    issuer: Any,
    verdict: Any,
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


def _validate_term(term: Any) -> None:
    _exact(
        term,
        {
            "profile",
            "transaction_id",
            "comparison_group",
            "input_hash",
            "expected_model_hash",
            "output_schema",
            "required_evidence",
            "policy_hash",
            "price",
            "capital_requirement",
            "coverage_anchor",
            "witness_policy",
            "recourse",
        },
        "term document",
    )
    for key in ("comparison_group", "input_hash", "expected_model_hash", "policy_hash"):
        _hash(term[key], f"term document.{key}")
    if term["profile"] != PROFILE:
        raise InferenceClearingError("term document profile is unsupported")
    _exact(term["output_schema"], {"type", "values"}, "term document.output_schema")
    if term["output_schema"] != {"type": "enum", "values": ["PRIMARY", "BACKUP"]}:
        raise InferenceClearingError("term output schema is unsupported")
    _exact(term["required_evidence"], set(EVIDENCE_REQUIREMENT_FIELDS), "term document.required_evidence")
    if term["required_evidence"] != {
        "output_binding": "VERIFIED",
        "model_binding": "TERM_BOUND",
        "relation_reproduction": "REPRODUCED",
        "provider_execution_occurrence": "NOT_REQUIRED",
    }:
        raise InferenceClearingError("term evidence requirements are unsupported")
    if not isinstance(term["transaction_id"], str) or not term["transaction_id"]:
        raise InferenceClearingError("term transaction_id must be non-empty")
    for key in ("price", "capital_requirement"):
        _exact(term[key], {"amount", "unit"}, f"term document.{key}")
        _amount(term[key]["amount"], f"term document.{key}.amount")
        if term[key]["unit"] != "sat":
            raise InferenceClearingError(f"term document.{key}.unit must be sat")
    if not isinstance(term["coverage_anchor"], str) or not term["coverage_anchor"]:
        raise InferenceClearingError("term coverage_anchor must be non-empty")
    if term["witness_policy"] != "accepted-signed-checkpoint-and-inclusion":
        raise InferenceClearingError("term witness policy is unsupported")
    _exact(term["recourse"], {"challenge_window", "forum"}, "term document.recourse")
    if any(not isinstance(term["recourse"][key], str) or not term["recourse"][key] for key in term["recourse"]):
        raise InferenceClearingError("term recourse fields must be non-empty strings")


def _validate_inclusions(value: Any, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise InferenceClearingError(f"{label} must be an array of objects")
    attestations = [item.get("attestation") for item in value]
    if any(not isinstance(item, str) or not item for item in attestations):
        raise InferenceClearingError(f"{label} entries must carry attestation identifiers")
    if len(attestations) != len(set(attestations)):
        raise InferenceClearingError(f"{label} contains duplicate attestations")
    return {item["attestation"]: item for item in value}


def _verify_execution(
    report: Mapping[str, Any],
    model: Mapping[str, Any] | None,
    output_bytes: bytes,
    term: Mapping[str, Any],
    context: InferenceClearingContext,
    provider_kind: str,
) -> tuple[RelationEvidenceReport, str]:
    _exact(
        report,
        {
            "profile",
            "adapter",
            "model_id",
            "model_hash",
            "model_available",
            "input",
            "input_hash",
            "output",
            "output_hash",
            "trace",
            "trace_hash",
            "provider_process_claim",
        },
        "execution report",
    )
    if report["profile"] != EXECUTION_PROFILE or report["provider_process_claim"] not in {"self_asserted", "absent"}:
        raise InferenceClearingError("execution report profile or process claim is unsupported")
    for key in ("model_hash", "input_hash", "output_hash", "trace_hash"):
        _hash(report[key], f"execution report.{key}")
    if not isinstance(report["adapter"], str) or not report["adapter"]:
        raise InferenceClearingError("execution adapter must be non-empty")
    if not isinstance(report["model_id"], str) or not report["model_id"]:
        raise InferenceClearingError("execution model_id must be non-empty")
    if not isinstance(report["model_available"], bool):
        raise InferenceClearingError("execution model_available must be Boolean")
    retained_model_available = model is not None
    if report["model_available"] is not retained_model_available:
        raise InferenceClearingError(
            "execution model availability does not match the retained artifacts"
        )
    if not isinstance(report["input"], list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in report["input"]
    ):
        raise InferenceClearingError("execution input must be an integer array")
    if report["output"] not in {"PRIMARY", "BACKUP"}:
        raise InferenceClearingError("execution output is outside the closed label set")
    adapter = report["adapter"]
    expected_adapter = {
        "OPAQUE": "provider-assertion/1",
        "REPRODUCIBLE": "bulla.int8-mlp-recompute/1",
    }.get(provider_kind)
    if adapter != expected_adapter:
        raise InferenceClearingError(
            "execution adapter does not match the selected provider kind"
        )
    accepted = "ACCEPTED" if adapter in context.accepted_execution_adapters else "UNACCEPTED"
    reasons: list[str] = []
    input_binding = "VERIFIED" if canonical_hash(report["input"]) == report["input_hash"] else "FAILED"
    expected_output_bytes = (report["output"] + "\n").encode("utf-8")
    output_binding = (
        "VERIFIED"
        if output_bytes == expected_output_bytes and _sha_bytes(output_bytes) == report["output_hash"]
        else "FAILED"
    )
    trace_binding = "NOT_COMPUTED"
    model_availability = "AVAILABLE" if retained_model_available else "UNAVAILABLE"
    model_binding = "UNAVAILABLE" if model is None else "UNBOUND"
    relation_reproduction = "UNAVAILABLE"
    if adapter == "bulla.int8-mlp-recompute/1":
        if model is None:
            raise InferenceClearingError(
                "recomputation adapter requires execution/model.json"
            )
        computed_model_hash = canonical_hash(model)
        if computed_model_hash != report["model_hash"]:
            reasons.append("retained model does not match the execution report")
        if report["model_hash"] != term["expected_model_hash"]:
            reasons.append("retained model was not bound by the accepted terms")
        if computed_model_hash == report["model_hash"] == term["expected_model_hash"]:
            model_binding = "TERM_BOUND"
        computed_output, trace = run_reference_model(model, report["input"])
        if computed_output != report["output"]:
            reasons.append("recomputed output differs")
            output_binding = "FAILED"
        trace_binding = "VERIFIED" if trace == report["trace"] and canonical_hash(trace) == report["trace_hash"] else "FAILED"
        if model_binding == "TERM_BOUND" and trace_binding == "VERIFIED" and input_binding == "VERIFIED" and output_binding == "VERIFIED":
            relation_reproduction = "REPRODUCED"
        else:
            relation_reproduction = "FAILED"
    elif adapter == "provider-assertion/1":
        if model is not None:
            raise InferenceClearingError(
                "provider-assertion adapter must not carry execution/model.json"
            )
        trace_binding = "NOT_APPLICABLE"
    else:
        raise InferenceClearingError("unsupported execution adapter")
    if accepted != "ACCEPTED":
        reasons.append("execution adapter is not accepted by the external context")
    return (
        RelationEvidenceReport(
            adapter=adapter,
            adapter_acceptance=accepted,
            model_availability=model_availability,
            input_binding=input_binding,
            output_binding=output_binding,
            trace_binding=trace_binding,
            model_binding=model_binding,
            relation_reproduction=relation_reproduction,
            provider_process_claim=report["provider_process_claim"].upper(),
            provider_execution_occurrence="NOT_ESTABLISHED",
            reasons=tuple(reasons),
        ),
        report["output"],
    )


def verify_inference_clearing_bundle(
    bundle_dir: str | Path,
    context: InferenceClearingContext,
    *,
    limits: InferenceClearingLimits = InferenceClearingLimits(),
) -> InferenceClearingReport:
    """Verify a clearing bundle without contacting its provider or issuer."""

    try:
        parsed = parse_inference_clearing_bundle(bundle_dir, limits=limits)
        core = parsed.core
        term = _parse_json_object(parsed, "terms/term-document.json", limits)
        execution = _parse_json_object(parsed, "execution/report.json", limits)
        model = (
            _parse_json_object(parsed, "execution/model.json", limits)
            if "execution/model.json" in parsed.files
            else None
        )
        coverage = _parse_json_object(parsed, "receiver/coverage.json", limits)
        capital = _parse_json_object(parsed, "assurance/capital.json", limits)
        decision_checkpoint = _parse_json_object(parsed, "witness/decision-checkpoint.json", limits)
        decision_inclusions = _parse_json_file(parsed, "witness/decision-inclusions.json", limits)
        final_checkpoint = _parse_json_object(parsed, "witness/final-checkpoint.json", limits)
        final_inclusions = _parse_json_file(parsed, "witness/final-inclusions.json", limits)
        settlement = _parse_json_object(parsed, "settlement/report.json", limits)
        rail_evidence = _parse_json_object(parsed, "settlement/rail-evidence.json", limits)
    except InferenceClearingError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceClearingError(str(exc)) from exc

    _exact(
        settlement,
        {
            "profile",
            "transaction_id",
            "rail_adapter",
            "status",
            "amount",
            "unit",
            "synthetic",
            "authorization_ref",
            "rail_evidence_hash",
        },
        "settlement report",
    )
    _exact(
        rail_evidence,
        {
            "profile",
            "binding_id",
            "locked_amount",
            "unit",
            "reported_execution",
            "synthetic",
        },
        "rail evidence",
    )
    _amount(rail_evidence["locked_amount"], "rail evidence.locked_amount")
    if (
        rail_evidence["profile"] != "bulla.fixture-escrow-evidence/1"
        or not isinstance(rail_evidence["binding_id"], str)
        or not rail_evidence["binding_id"]
        or rail_evidence["unit"] != "sat"
        or rail_evidence["reported_execution"]
        not in {"NOT_ATTEMPTED", "EXECUTED", "FAILED"}
        or rail_evidence["synthetic"] is not True
    ):
        raise InferenceClearingError("rail evidence is outside the closed fixture profile")

    errors: list[str] = []
    warnings: list[str] = []
    suppressed: list[str] = []
    core_hash = canonical_hash(dict(core))
    expected_components = component_hashes(core)

    publish_raw = parsed.files[PUBLISH_FILE]
    try:
        publish = parse_action_receipt_json(
            publish_raw,
            limits=ReceiptParseLimits(max_bytes=limits.max_file_bytes),
        ).to_dict()
    except ReceiptParseError as exc:
        raise InferenceClearingError(f"invalid publish receipt: {exc}") from exc
    publish_verdict = verify_receipt(publish)
    publish_subject = _receipt_subject(publish, "inference.clearing.publish")
    expected_publish = {
        "profile": PROFILE,
        "issuer_role": "publisher",
        "transaction_id": core["transaction_id"],
        "clearing_core_hash": core_hash,
        "component_hashes": expected_components,
    }
    bundle_integrity = "VERIFIED"
    publisher = (publish.get("signature") or {}).get("issuer")
    if (
        publish_subject != expected_publish
        or not publish_verdict.ok
        or publish_verdict.verified_to != "attestation"
        or not _direct_authority_is_verified(publish, publisher, publish_verdict)
    ):
        bundle_integrity = "FAILED"
        errors.append("publish receipt does not authenticate the clearing core and component manifests")
    if (
        publisher != core["role_issuers"]["publisher"]
        or publisher not in context.accepted_issuers_by_role.get("publisher", frozenset())
    ):
        errors.append("publisher is not accepted by the external context")
        bundle_integrity = "FAILED"

    receipts: list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    receipt_integrity = "VERIFIED"
    signature_status = "VERIFIED"
    issuer_role_status = "ACCEPTED"
    occurrence_binding = "VERIFIED"
    for item in core["ordered_receipts"]:
        try:
            receipt = parse_action_receipt_json(
                parsed.files[item["path"]],
                limits=ReceiptParseLimits(max_bytes=limits.max_file_bytes),
            ).to_dict()
        except ReceiptParseError as exc:
            raise InferenceClearingError(f"invalid receipt {item['path']}: {exc}") from exc
        verdict = verify_receipt(receipt)
        subject = _receipt_subject(receipt, item["action_type"])
        receipts.append((item, receipt, subject))
        hashes = receipt.get("hashes") or {}
        if not verdict.ok or hashes.get("event") != item["event"] or hashes.get("attestation") != item["attestation"]:
            receipt_integrity = "FAILED"
            errors.append(f"receipt verification failed for {item['path']}")
        issuer = (receipt.get("signature") or {}).get("issuer")
        if verdict.verified_to != "attestation" or not _direct_authority_is_verified(receipt, issuer, verdict):
            signature_status = "FAILED"
            errors.append(f"receipt lacks direct signer-bound authority for {item['path']}")
        declared_role = (
            "provider_opaque"
            if item["role"] == "provider" and core["provider_kind"] == "OPAQUE"
            else "provider_reproducible"
            if item["role"] == "provider"
            else item["role"]
        )
        if (
            issuer != core["role_issuers"][declared_role]
            or issuer not in context.accepted_issuers_by_role.get(item["role"], frozenset())
        ):
            issuer_role_status = "REJECTED"
            errors.append(f"issuer is not accepted for role {item['role']}")
        if subject.get("profile") != PROFILE or subject.get("transaction_id") != core["transaction_id"]:
            occurrence_binding = "FAILED"
            errors.append(f"receipt does not bind the clearing transaction for {item['path']}")

    sequence = tuple(item[0]["action_type"] for item in receipts)
    if sequence not in {
        BASE_SEQUENCE,
        GUARANTEE_SEQUENCE,
        ELIGIBLE_SEQUENCE,
        GUARANTEE_SEQUENCE + ("assurance.settlement.authorize", "rail.settlement.report"),
    }:
        errors.append(f"unsupported receipt sequence {sequence}")
        lineage_integrity = "FAILED"
    else:
        lineage_integrity = "VERIFIED"
    previous: dict[str, str] | None = None
    for index, (item, receipt, subject) in enumerate(receipts):
        if index == 0:
            if "parent_ref" in subject:
                lineage_integrity = "FAILED"
                errors.append("order receipt must not contain parent_ref")
        else:
            if subject.get("parent_ref") != previous:
                lineage_integrity = "FAILED"
                errors.append(f"parent lineage mismatch for {item['path']}")
        previous = {
            "event": receipt["hashes"]["event"],
            "attestation": receipt["hashes"]["attestation"],
        }

    term_binding = "VERIFIED"
    if canonical_hash(term) != core["term_root"]:
        term_binding = "FAILED"
        errors.append("term document does not match term_root")
    _validate_term(term)
    if (
        term["profile"] != PROFILE
        or term["transaction_id"] != core["transaction_id"]
        or term["comparison_group"] != core["comparison_group"]
        or term["policy_hash"] != context.accepted_policy_hash
    ):
        term_binding = "FAILED"
        errors.append("term document does not bind the transaction, comparison group, or accepted policy")
    for _, _, subject in receipts:
        if subject.get("term_root") != core["term_root"]:
            term_binding = "FAILED"
            errors.append("a receipt does not bind the exact term root")
        if subject.get("comparison_group") != core["comparison_group"]:
            term_binding = "FAILED"
            errors.append("a receipt does not bind the exact comparison group")

    execution_report, output = _verify_execution(
        execution,
        model,
        parsed.files["execution/output.bin"],
        term,
        context,
        core["provider_kind"],
    )
    if execution["input_hash"] != term["input_hash"]:
        term_binding = "FAILED"
        errors.append("execution input does not match the term document")
    if term["required_evidence"] != dict(context.accepted_evidence_requirements):
        term_binding = "FAILED"
        errors.append("term evidence policy does not match the externally accepted policy")
    accept_subject = next(subject for item, _, subject in receipts if item["action_type"] == "inference.accept")
    delivery_subject = next(subject for item, _, subject in receipts if item["action_type"] == "inference.delivery")
    route_subject = next(subject for item, _, subject in receipts if item["action_type"] == "inference.route")
    order_subject = next(subject for item, _, subject in receipts if item["action_type"] == "inference.order")
    if order_subject["input_hash"] != term["input_hash"] or order_subject["price"] != term["price"]:
        errors.append("buyer order does not bind the exact input and price")
        occurrence_binding = "FAILED"
    if route_subject["provider_kind"] != core["provider_kind"]:
        errors.append("route receipt does not bind the selected provider kind")
        occurrence_binding = "FAILED"
    if (
        accept_subject.get("accepted_model_hash") != term["expected_model_hash"]
        or accept_subject.get("execution_report_hash") != canonical_hash(execution)
        or accept_subject.get("provider_process_claim")
        != execution["provider_process_claim"]
    ):
        errors.append(
            "provider acceptance does not bind the term-selected model and execution report"
        )
        occurrence_binding = "FAILED"
    if (
        delivery_subject.get("output_hash") != execution["output_hash"]
        or delivery_subject.get("receiver_anchor") != term["coverage_anchor"]
    ):
        errors.append("receiver delivery does not bind the returned output and coverage anchor")
        occurrence_binding = "FAILED"

    _exact(
        coverage,
        {"profile", "transaction_id", "anchor_id", "provenance", "denominator_ids", "receipted_ids"},
        "receiver coverage",
    )
    if (
        coverage["profile"] != COVERAGE_PROFILE
        or coverage["transaction_id"] != core["transaction_id"]
        or coverage["anchor_id"] != term["coverage_anchor"]
        or coverage["provenance"] != "PATH_SEPARATE_TEAM_CONTROLLED"
    ):
        raise InferenceClearingError("receiver coverage is for a different profile or transaction")
    denominator = _string_list(coverage["denominator_ids"], "denominator_ids")
    receipted = _string_list(coverage["receipted_ids"], "receipted_ids")
    phantom = sorted(set(receipted) - set(denominator))
    if phantom:
        raise InferenceClearingError(f"receiver coverage contains phantom receipt ids {phantom}")
    uncovered = tuple(item for item in denominator if item not in set(receipted))
    receiver_coverage = "COVERED" if not uncovered else "UNCOVERED"
    effect_id = delivery_subject.get("effect_id")
    if effect_id not in receipted:
        errors.append("receiver delivery effect is not in the receipted set")
    coverage_receipt = next(
        (entry for entry in receipts if entry[0]["action_type"] == "inference.coverage.checkpoint"),
        None,
    )
    coverage_subject: Mapping[str, Any] = coverage_receipt[2] if coverage_receipt else {}
    if (
        coverage_receipt is None
        or coverage_subject.get("coverage_hash") != canonical_hash(coverage)
        or coverage_subject.get("anchor_id") != coverage["anchor_id"]
        or coverage_subject.get("denominator_count") != len(denominator)
        or coverage_subject.get("receipted_count") != len(receipted)
    ):
        errors.append("receiver coverage checkpoint does not bind the exact denominator")
        occurrence_binding = "FAILED"

    checkpoint_verdict = verify_checkpoint(decision_checkpoint)
    witness_inclusion = "VERIFIED"
    witness_root_trust = "TRUSTED" if decision_checkpoint.get("root") in context.trusted_witness_roots else "UNTRUSTED"
    if not checkpoint_verdict.ok:
        witness_inclusion = "FAILED"
        errors.append("decision checkpoint signature failed")
    witness_issuer = decision_checkpoint.get("operator")
    if (
        witness_issuer != core["role_issuers"]["witness"]
        or witness_issuer not in context.accepted_issuers_by_role.get("witness", frozenset())
    ):
        witness_root_trust = "UNTRUSTED"
        errors.append("witness operator is not accepted by the external context")
    pre_reliance = [
        receipt
        for item, receipt, _ in receipts
        if item["action_type"] not in {"bulla.rely", "assurance.settlement.authorize", "rail.settlement.report"}
    ]
    if decision_checkpoint.get("tree_size") != len(pre_reliance):
        witness_inclusion = "FAILED"
        errors.append("decision checkpoint tree size differs from pre-reliance receipt count")
    inclusions_by_attestation = _validate_inclusions(decision_inclusions, "decision inclusions")
    if set(inclusions_by_attestation) != {receipt["hashes"]["attestation"] for receipt in pre_reliance}:
        raise InferenceClearingVerificationError(
            "decision inclusions do not exactly cover the pre-reliance receipt set"
        )
    for receipt in pre_reliance:
        attestation = receipt["hashes"]["attestation"]
        inclusion = inclusions_by_attestation.get(attestation)
        issuer = (receipt.get("signature") or {}).get("issuer")
        expected_leaf = (
            "sha256:"
            + Deed(
                issuer,
                receipt["hashes"]["content"],
                attestation,
            ).leaf().hex()
            if isinstance(issuer, str)
            else ""
        )
        if inclusion is None or inclusion.get("tree_size") != decision_checkpoint.get("tree_size") or not verify_inclusion_record(
            inclusion,
            trusted_root=decision_checkpoint["root"],
            expected_leaf=expected_leaf,
        ):
            witness_inclusion = "FAILED"
            errors.append(f"missing or invalid witness inclusion for {attestation}")
    final_checkpoint_verdict = verify_checkpoint(final_checkpoint)
    if (
        not final_checkpoint_verdict.ok
        or final_checkpoint.get("operator") != witness_issuer
        or final_checkpoint.get("previous_checkpoint_hash") != decision_checkpoint.get("checkpoint_hash")
        or final_checkpoint.get("tree_size") != len(receipts)
        or final_checkpoint.get("root") not in context.trusted_witness_roots
    ):
        witness_inclusion = "FAILED"
        errors.append("final witness checkpoint does not extend the accepted decision checkpoint")
    final_by_attestation = _validate_inclusions(final_inclusions, "final inclusions")
    if set(final_by_attestation) != {receipt["hashes"]["attestation"] for _, receipt, _ in receipts}:
        raise InferenceClearingVerificationError(
            "final inclusions do not exactly cover the retained receipt set"
        )
    for _, receipt, _ in receipts:
        attestation = receipt["hashes"]["attestation"]
        issuer = (receipt.get("signature") or {}).get("issuer")
        expected_leaf = (
            "sha256:"
            + Deed(issuer, receipt["hashes"]["content"], attestation).leaf().hex()
            if isinstance(issuer, str)
            else ""
        )
        inclusion = final_by_attestation.get(attestation)
        if inclusion is None or inclusion.get("tree_size") != final_checkpoint.get("tree_size") or not verify_inclusion_record(
            inclusion,
            trusted_root=final_checkpoint["root"],
            expected_leaf=expected_leaf,
        ):
            witness_inclusion = "FAILED"
            errors.append(f"missing or invalid final witness inclusion for {attestation}")

    _exact(
        capital,
        {"profile", "transaction_id", "binding_id", "unit", "locked_amount", "allocated_amount", "external_encumbrance", "rail_adapter", "rail_evidence_hash"},
        "capital allocation",
    )
    if capital["profile"] != CAPITAL_PROFILE or capital["transaction_id"] != core["transaction_id"]:
        raise InferenceClearingError("capital allocation is for a different transaction")
    _amount(capital["locked_amount"], "capital.locked_amount")
    _amount(capital["allocated_amount"], "capital.allocated_amount")
    if capital["external_encumbrance"] != "NOT_COMPUTED":
        raise InferenceClearingError("external collateral encumbrance must remain NOT_COMPUTED")
    if capital["rail_evidence_hash"] != canonical_hash(rail_evidence):
        errors.append("capital allocation does not bind the retained rail evidence")
    capital_subject = next(subject for item, _, subject in receipts if item["action_type"] == "assurance.collateral.bind")
    if capital_subject["capital_hash"] != canonical_hash(capital):
        errors.append("capital receipt does not bind the capital allocation")
        occurrence_binding = "FAILED"
    if capital["unit"] != term["capital_requirement"]["unit"]:
        capital_allocation = "UNIT_MISMATCH"
    elif capital["allocated_amount"] > capital["locked_amount"] or capital["allocated_amount"] < term["capital_requirement"]["amount"]:
        capital_allocation = "ALLOCATION_SHORTFALL"
    else:
        capital_allocation = "ALLOCATION_ADEQUATE"
    if capital["rail_adapter"] not in context.accepted_rail_adapters:
        capital_allocation = "UNACCEPTED_RAIL"

    clear_negative: list[str] = []
    requirements = context.accepted_evidence_requirements
    if execution_report.output_binding != requirements["output_binding"]:
        clear_negative.append("required output binding was not established")
    if execution_report.model_binding != requirements["model_binding"]:
        clear_negative.append("required term-bound model was not established")
    if execution_report.relation_reproduction != requirements["relation_reproduction"]:
        clear_negative.append("required computational relation was not reproduced")
    if requirements["provider_execution_occurrence"] != "NOT_REQUIRED":
        clear_negative.append("historical provider execution is not established")
    if execution_report.adapter_acceptance != "ACCEPTED":
        clear_negative.append("execution adapter was not accepted")
    if receiver_coverage != "COVERED":
        clear_negative.append("required receiver coverage was not complete")
    if witness_inclusion != "VERIFIED" or witness_root_trust != "TRUSTED":
        clear_negative.append("required witness evidence was not established")
    if capital_allocation != "ALLOCATION_ADEQUATE":
        clear_negative.append("required capital allocation was not established")
    if term_binding != "VERIFIED" or issuer_role_status != "ACCEPTED":
        clear_negative.append("required terms or authority were not established")
    hard_failure = bool(errors) or bundle_integrity != "VERIFIED" or receipt_integrity != "VERIFIED"
    expected_reliance = "NOT_COMPUTED" if hard_failure else ("REFUSE" if clear_negative else "RELY")
    expected_payment = "NOT_COMPUTED" if hard_failure else ("INELIGIBLE" if clear_negative else "ELIGIBLE")
    rely_subject = next(subject for item, _, subject in receipts if item["action_type"] == "bulla.rely")
    if (
        rely_subject.get("policy_hash") != context.accepted_policy_hash
        or rely_subject.get("decision") != expected_reliance
        or rely_subject.get("witness_root") != decision_checkpoint["root"]
        or rely_subject.get("unmet_requirements") != clear_negative
        or rely_subject.get("named_consequence") != "RELEASE_PAYMENT"
        or rely_subject.get("coverage_hash") != canonical_hash(coverage)
        or rely_subject.get("coverage_ref")
        != (
            {
                "event": coverage_receipt[1]["hashes"]["event"],
                "attestation": coverage_receipt[1]["hashes"]["attestation"],
            }
            if coverage_receipt is not None
            else None
        )
    ):
        errors.append("reliance receipt does not match recomputed policy decision")
        expected_reliance = "NOT_COMPUTED"
        expected_payment = "NOT_COMPUTED"
        suppressed.extend(("reliance_decision", "payment_eligibility"))

    authorize = [subject for item, _, subject in receipts if item["action_type"] == "assurance.settlement.authorize"]
    rail_receipts = [subject for item, _, subject in receipts if item["action_type"] == "rail.settlement.report"]
    if not authorize:
        settlement_authorization = "NOT_ISSUED"
    else:
        authorization_matches = (
            expected_payment == "ELIGIBLE"
            and len(authorize) == 1
            and authorize[0].get("eligibility") == "ELIGIBLE"
            and authorize[0].get("amount") == term["price"]["amount"]
            and authorize[0].get("unit") == term["price"]["unit"]
            and authorize[0].get("rail_adapter") == settlement.get("rail_adapter")
            and isinstance(authorize[0].get("destination"), str)
            and bool(authorize[0].get("destination"))
        )
        settlement_authorization = "AUTHORIZED" if authorization_matches else "INVALID"
        if settlement_authorization == "INVALID":
            errors.append("settlement authorization is not supported by the computed eligibility")

    expected_authorization_ref = next(
        (
            {"event": receipt["hashes"]["event"], "attestation": receipt["hashes"]["attestation"]}
            for item, receipt, _ in receipts
            if item["action_type"] == "assurance.settlement.authorize"
        ),
        None,
    )
    if not rail_receipts:
        settlement_execution = "NOT_ATTEMPTED"
    else:
        rail_matches = (
            settlement_authorization == "AUTHORIZED"
            and len(rail_receipts) == 1
            and rail_receipts[0].get("status") == "EXECUTED"
            and rail_receipts[0].get("authorization_ref") == expected_authorization_ref
            and rail_receipts[0].get("amount") == term["price"]["amount"]
            and rail_receipts[0].get("unit") == term["price"]["unit"]
            and rail_receipts[0].get("rail_adapter") == settlement.get("rail_adapter")
            and rail_receipts[0].get("settlement_hash") == canonical_hash(settlement)
        )
        settlement_execution = "EXECUTED" if rail_matches else "INVALID"
        if settlement_execution == "INVALID":
            errors.append("settlement execution is not supported by a valid authorization")

    if settlement["profile"] != SETTLEMENT_PROFILE or settlement["transaction_id"] != core["transaction_id"]:
        raise InferenceClearingError("settlement report is for a different transaction")
    _amount(settlement["amount"], "settlement.amount")
    if (
        settlement["amount"] != term["price"]["amount"]
        or settlement["unit"] != term["price"]["unit"]
        or settlement["synthetic"] is not True
        or settlement["rail_evidence_hash"] != canonical_hash(rail_evidence)
    ):
        errors.append("settlement artifact does not bind the exact synthetic price")
    if settlement["authorization_ref"] != expected_authorization_ref:
        errors.append("settlement artifact does not bind the settlement authorization")
    if settlement["rail_adapter"] not in context.accepted_rail_adapters:
        errors.append("settlement rail is not accepted by the external context")
    if settlement["status"] != settlement_execution:
        errors.append("settlement artifact does not match the receipt-derived execution state")
        settlement_execution = "INVALID"

    if expected_reliance == "NOT_COMPUTED":
        suppressed.extend(item for item in ("reliance_decision", "payment_eligibility") if item not in suppressed)
    warnings.append("coverage is relative to the supplied receiver record")
    warnings.append("team-operated role separation does not establish organizational independence")

    return InferenceClearingReport(
        transaction_id=core["transaction_id"],
        provider_kind=core["provider_kind"],
        output=output,
        bundle_integrity=bundle_integrity,
        receipt_integrity=receipt_integrity,
        signature_status=signature_status,
        issuer_role_status=issuer_role_status,
        term_binding=term_binding,
        lineage_integrity=lineage_integrity,
        occurrence_binding=occurrence_binding,
        relation_evidence=execution_report,
        receiver_coverage=receiver_coverage,
        receiver_total=len(denominator),
        receiver_receipted=len(receipted),
        uncovered_effects=uncovered,
        witness_inclusion=witness_inclusion,
        witness_root_trust=witness_root_trust,
        capital_allocation=capital_allocation,
        recourse_conveyance=(
            "GUARANTEE_NAMED"
            if any(item["action_type"] == "assurance.guarantee.issue" for item, _, _ in receipts)
            else "NAMED"
        ),
        recourse_reachability="NOT_COMPUTED",
        reliance_decision=expected_reliance,
        payment_eligibility=expected_payment,
        settlement_authorization=settlement_authorization,
        settlement_execution=settlement_execution,
        suppressed_conclusions=tuple(dict.fromkeys(suppressed)),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


__all__ = [
    "CAPITAL_PROFILE",
    "COVERAGE_PROFILE",
    "EXECUTION_PROFILE",
    "PROFILE",
    "SETTLEMENT_PROFILE",
    "RelationEvidenceReport",
    "InferenceClearingContext",
    "InferenceClearingError",
    "InferenceClearingLimits",
    "InferenceClearingReport",
    "InferenceClearingVerificationError",
    "ParsedInferenceClearingBundle",
    "canonical_hash",
    "component_hashes",
    "parse_inference_clearing_bundle",
    "run_reference_model",
    "verify_inference_clearing_bundle",
]
