"""Source-only verifier for ``bulla.control-plane-alpha/0.1-experimental``.

The outer service manifest and AuthZEN-shaped records use the control-plane
profile.  Receipts exported into an incident packet use the existing closed
``glyph.agent-incident-packet/0.1-draft`` profile and vocabulary unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from bulla.action_receipt import ActionReceipt
from bulla.experimental.incident_packet import (
    PROFILE as INCIDENT_PROFILE,
    IncidentPacketError,
    IncidentParseLimits,
    IncidentVerificationContext,
    canonical_hash,
    parse_incident_packet,
    parse_strict_json_bytes,
    verify_incident_packet,
)
from bulla.identity import pubkey_from_did_key
from bulla.experimental.checkpoint import (
    WitnessCheckpoint,
    verify_checkpoint,
    verify_checkpoint_extension,
)
from bulla.registry import Deed, deed_leaf, merkle_root, verify_inclusion_record


PROFILE = "bulla.control-plane-alpha/0.1-experimental"
DEPLOYMENT_GATE = "PASSED"
MCP_TARGET_REVISION = "2026-07-28"
SERVICE_MANIFEST = "service-manifest.json"
RUN_STATE = "run-state.json"
APPROVED_TIMELINE = (
    "eval.run.authorize",
    "capability.decide",
    "capability.decide",
    "capability.observe",
    "incident.statement",
    "incident.statement",
    "incident.statement",
    "incident.handoff",
)
TRUST_ROLES = frozenset(
    {
        "evaluation_authority",
        "boundary",
        "target",
        "witness",
        "incident_commander",
        "publisher",
    }
)
RUN_STATUSES = (
    "CREATED",
    "AUTHORIZED",
    "MEDIATED_EFFECT_RECORDED",
    "BYPASS_RECORDED",
    "FINALIZED",
)
RUN_TRANSITIONS = MappingProxyType(
    {
        "CREATED": frozenset({"AUTHORIZED"}),
        "AUTHORIZED": frozenset({"MEDIATED_EFFECT_RECORDED"}),
        "MEDIATED_EFFECT_RECORDED": frozenset({"BYPASS_RECORDED"}),
        "BYPASS_RECORDED": frozenset({"FINALIZED"}),
        "FINALIZED": frozenset(),
    }
)
ERROR_CODES = frozenset(
    {
        "COVERAGE_MISMATCH",
        "INCIDENT_PACKET_FAILED",
        "MALFORMED_FIXTURE",
        "MAPPING_MISMATCH",
        "RUN_STATE_INVALID",
        "STANDARDS_GATE_INVALID",
        "UNTRUSTED_POLICY",
    }
)
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")

_REQUEST_FIELDS = {"subject", "action", "resource", "context"}
_REQUEST_CONTEXT_FIELDS = {
    "profile",
    "run_id",
    "request_id",
    "tool_name",
    "arguments_hash",
    "audience",
    "pdp_id",
    "policy_digest",
    "valid_from",
    "valid_until",
}
_DECISION_FIELDS = {"decision", "context"}
_DECISION_CONTEXT_FIELDS = {
    "profile",
    "run_id",
    "request_id",
    "decision_id",
    "request_hash",
    "sequence",
    "reason_code",
    "policy_ref",
    "policy_hash",
    "policy_digest",
    "pdp_id",
    "audience",
    "tool_name",
    "arguments_hash",
    "valid_until",
}
_POLICY_REF = "policy://bulla-control-plane-alpha/synthetic-authzen"
_POLICY_DOCUMENT = {
    "profile": PROFILE,
    "policy_id": _POLICY_REF,
    "operation": "sandbox.append",
    "scenarios": {
        "mediated_append": "PERMIT",
        "refused_append": "REFUSE",
    },
    "values": {
        "mediated_append": "alpha-mediated",
        "direct_effect": "alpha-direct-bypass",
    },
}
_POLICY_HASH = canonical_hash(_POLICY_DOCUMENT)
_SCENARIO_ARGUMENT_HASHES = {
    "mediated_append": canonical_hash({"value": "alpha-mediated"}),
    "refused_append": canonical_hash({"value": "alpha-refused"}),
}
_ARGUMENT_HASHES = frozenset(_SCENARIO_ARGUMENT_HASHES.values())
_FIXED_RESOURCE = "mcp://synthetic-receiver/tools/sandbox.append"


class ControlPlaneProtocolError(ValueError):
    """Closed machine-readable protocol failure."""

    def __init__(self, code: str, detail: str, *, exit_code: int = 2) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown control-plane error code {code!r}")
        if exit_code not in (1, 2):
            raise ValueError("exit_code must be 1 or 2")
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class ControlPlaneLimits:
    """Outer limits plus the reused incident-packet parser limits."""

    max_file_bytes: int = 65_536
    max_total_bytes: int = 2_097_152
    max_files: int = 32
    max_json_depth: int = 24
    max_json_nodes: int = 20_000
    max_string_bytes: int = 65_536
    max_transactions: int = 2

    def __post_init__(self) -> None:
        if min(
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_files,
            self.max_json_depth,
            self.max_json_nodes,
            self.max_string_bytes,
            self.max_transactions,
        ) <= 0:
            raise ValueError("control-plane limits must be positive")

    def incident_limits(self) -> IncidentParseLimits:
        return IncidentParseLimits(
            max_file_bytes=self.max_file_bytes,
            max_total_bytes=self.max_total_bytes,
            max_files=self.max_files,
            max_depth=self.max_json_depth,
            max_nodes=self.max_json_nodes,
            max_string_bytes=self.max_string_bytes,
        )


@dataclass(frozen=True)
class ControlPlaneVerificationContext:
    """Out-of-band packet issuer, witness-root, and policy trust."""

    accepted_issuers_by_role: Mapping[
        str, frozenset[str] | set[str] | tuple[str, ...]
    ]
    trusted_witness_roots: frozenset[str] | set[str] | tuple[str, ...]
    team_controlled_roles: frozenset[str] | set[str] | tuple[str, ...]
    trusted_policy_hashes: frozenset[str] | set[str] | tuple[str, ...]
    role_identities: Mapping[str, Mapping[str, str]]

    def __post_init__(self) -> None:
        incident = IncidentVerificationContext(
            accepted_issuers_by_role=self.accepted_issuers_by_role,
            trusted_witness_roots=self.trusted_witness_roots,
            team_controlled_roles=self.team_controlled_roles,
        )
        if set(incident.accepted_issuers_by_role) != TRUST_ROLES:
            raise ValueError("accepted_issuers_by_role must exactly cover six roles")
        if any(
            len(issuers) != 1
            for issuers in incident.accepted_issuers_by_role.values()
        ):
            raise ValueError(
                "accepted_issuers_by_role must pin exactly one issuer per role"
            )
        if incident.team_controlled_roles != TRUST_ROLES:
            raise ValueError("team_controlled_roles must exactly cover six roles")
        policy_hashes = frozenset(self.trusted_policy_hashes)
        if not policy_hashes:
            raise ValueError("trusted_policy_hashes must not be empty")
        for value in policy_hashes:
            _hash(value, "trusted_policy_hashes[]")
        if not isinstance(self.role_identities, Mapping):
            raise TypeError("role_identities must be a mapping")
        identities: dict[str, Mapping[str, str]] = {}
        if set(self.role_identities) != set(incident.accepted_issuers_by_role):
            raise ValueError(
                "role_identities must exactly cover accepted_issuers_by_role"
            )
        for role, identity in self.role_identities.items():
            _exact(
                identity,
                {"issuer", "verification_method", "public_key_sha256"},
                f"role_identities.{role}",
            )
            issuer = _nonempty(identity["issuer"], f"role_identities.{role}.issuer")
            verification_method = _nonempty(
                identity["verification_method"],
                f"role_identities.{role}.verification_method",
            )
            fingerprint = _hash(
                identity["public_key_sha256"],
                f"role_identities.{role}.public_key_sha256",
            )
            if (
                issuer not in incident.accepted_issuers_by_role[role]
                or verification_method != issuer
                or not issuer.startswith("did:key:")
            ):
                raise ValueError(
                    f"role identity {role!r} does not pin its accepted did:key issuer"
                )
            actual_fingerprint = (
                "sha256:" + hashlib.sha256(pubkey_from_did_key(issuer)).hexdigest()
            )
            if fingerprint != actual_fingerprint:
                raise ValueError(
                    f"role identity {role!r} public_key_sha256 does not match did:key"
                )
            identities[role] = MappingProxyType(
                {
                    "issuer": issuer,
                    "verification_method": verification_method,
                    "public_key_sha256": fingerprint,
                }
            )
        object.__setattr__(
            self, "accepted_issuers_by_role", incident.accepted_issuers_by_role
        )
        object.__setattr__(
            self, "trusted_witness_roots", incident.trusted_witness_roots
        )
        object.__setattr__(
            self, "team_controlled_roles", incident.team_controlled_roles
        )
        object.__setattr__(self, "trusted_policy_hashes", policy_hashes)
        object.__setattr__(self, "role_identities", MappingProxyType(identities))

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ControlPlaneVerificationContext":
        _exact(
            value,
            {
                "profile",
                "accepted_issuers_by_role",
                "trusted_witness_roots",
                "team_controlled_roles",
                "trusted_policy_hashes",
                "role_identities",
            },
            "trust context",
        )
        if value["profile"] != PROFILE:
            raise ControlPlaneProtocolError(
                "MALFORMED_FIXTURE", "trust context profile mismatch"
            )
        accepted = value["accepted_issuers_by_role"]
        if not isinstance(accepted, Mapping):
            raise ControlPlaneProtocolError(
                "MALFORMED_FIXTURE",
                "accepted_issuers_by_role must be an object",
            )
        return cls(
            accepted_issuers_by_role={
                str(role): frozenset(_strings(items, f"accepted issuers for {role}"))
                for role, items in accepted.items()
            },
            trusted_witness_roots=frozenset(
                _strings(value["trusted_witness_roots"], "trusted_witness_roots")
            ),
            team_controlled_roles=frozenset(
                _strings(value["team_controlled_roles"], "team_controlled_roles")
            ),
            trusted_policy_hashes=frozenset(
                _strings(value["trusted_policy_hashes"], "trusted_policy_hashes")
            ),
            role_identities=value["role_identities"],
        )

    def incident_context(self) -> IncidentVerificationContext:
        return IncidentVerificationContext(
            accepted_issuers_by_role=self.accepted_issuers_by_role,
            trusted_witness_roots=self.trusted_witness_roots,
            team_controlled_roles=self.team_controlled_roles,
        )


@dataclass(frozen=True)
class Coverage:
    covered: int
    total: int
    covered_ids: tuple[str, ...]
    uncovered_ids: tuple[str, ...]

    def __bool__(self) -> bool:
        raise TypeError("coverage truth is ambiguous; inspect covered and total")

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "total": self.total,
            "covered_ids": list(self.covered_ids),
            "uncovered_ids": list(self.uncovered_ids),
        }


@dataclass(frozen=True)
class ControlPlaneVerification:
    profile: str
    vector_id: str
    run_id: str
    run_status: str
    deployment_gate: str
    standards_status: str
    authzen_mapping: str
    incident_packet_integrity: str
    issuer_authenticity: str
    witness_inclusion: str
    witness_root_trust: str
    retained_receipts: str
    service_witness_inclusion: str
    service_witness_consistency: str
    decision_coverage: Coverage
    effect_coverage: Coverage
    errors: tuple[Mapping[str, str], ...]
    warnings: tuple[str, ...]
    reliance: str
    exit_code: int

    def __bool__(self) -> bool:
        raise TypeError("verification truth is ambiguous; inspect named dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "vector_id": self.vector_id,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "deployment_gate": self.deployment_gate,
            "standards_status": self.standards_status,
            "authzen_mapping": self.authzen_mapping,
            "incident_packet_integrity": self.incident_packet_integrity,
            "issuer_authenticity": self.issuer_authenticity,
            "witness_inclusion": self.witness_inclusion,
            "witness_root_trust": self.witness_root_trust,
            "retained_receipts": self.retained_receipts,
            "service_witness_inclusion": self.service_witness_inclusion,
            "service_witness_consistency": self.service_witness_consistency,
            "decision_coverage": self.decision_coverage.to_dict(),
            "effect_coverage": self.effect_coverage.to_dict(),
            "errors": [dict(item) for item in self.errors],
            "warnings": list(self.warnings),
            "reliance": self.reliance,
            "exit_code": self.exit_code,
        }


def _exact(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must be an object"
        )
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} is missing fields {sorted(missing)}"
        )
    if unknown:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} has unknown fields {sorted(unknown)}"
        )


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must be a non-empty string"
        )
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must be an integer >= {minimum}"
        )
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must be an array of non-empty strings"
        )
    if len(set(value)) != len(value):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must not contain duplicates"
        )
    return tuple(value)


def _load_json(
    content: bytes,
    label: str,
    limits: ControlPlaneLimits,
) -> Mapping[str, Any]:
    try:
        value = parse_strict_json_bytes(
            content,
            label=label,
            limits=limits.incident_limits(),
        )
    except IncidentPacketError as exc:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", str(exc)
        ) from exc
    if not isinstance(value, Mapping):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"{label} must contain an object"
        )
    return value


def validate_authzen_request(value: Mapping[str, Any]) -> None:
    """Validate the exact closed request mapping."""
    _exact(value, _REQUEST_FIELDS, "AuthZEN request")
    _exact(value["subject"], {"type", "id"}, "AuthZEN request.subject")
    _exact(value["action"], {"name"}, "AuthZEN request.action")
    _exact(value["resource"], {"type", "id"}, "AuthZEN request.resource")
    _exact(value["context"], _REQUEST_CONTEXT_FIELDS, "AuthZEN request.context")
    if (
        value["subject"]["type"] != "principal"
        or value["subject"]["id"] != "agent:synthetic-alpha"
        or value["action"]["name"] != "tools/call"
        or value["resource"]["type"] != "mcp-tool"
        or value["resource"]["id"] != _FIXED_RESOURCE
    ):
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN request constants do not match the profile",
            exit_code=1,
        )
    _nonempty(value["subject"]["id"], "AuthZEN request.subject.id")
    context = value["context"]
    if context["profile"] != PROFILE:
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH", "AuthZEN request profile mismatch", exit_code=1
        )
    for field in ("run_id", "request_id", "valid_from", "valid_until"):
        _nonempty(context[field], f"AuthZEN request.context.{field}")
    if (
        context["tool_name"] != "sandbox.append"
        or context["audience"] != "synthetic-receiver"
        or context["pdp_id"] != "bulla-closed-authzen/1"
        or context["policy_digest"] != _POLICY_HASH
    ):
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN request control constants do not match the fixed profile",
            exit_code=1,
        )
    _hash(context["arguments_hash"], "AuthZEN request.context.arguments_hash")
    if context["arguments_hash"] not in _ARGUMENT_HASHES:
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN request arguments are not alpha-mediated or alpha-refused",
            exit_code=1,
        )


def validate_authzen_decision(value: Mapping[str, Any]) -> None:
    """Validate the exact closed decision mapping."""
    _exact(value, _DECISION_FIELDS, "AuthZEN decision")
    if not isinstance(value["decision"], bool):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", "AuthZEN decision.decision must be boolean"
        )
    _exact(value["context"], _DECISION_CONTEXT_FIELDS, "AuthZEN decision.context")
    context = value["context"]
    if context["profile"] != PROFILE:
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH", "AuthZEN decision profile mismatch", exit_code=1
        )
    for field in ("run_id", "request_id", "decision_id", "reason_code", "policy_ref", "valid_until"):
        _nonempty(context[field], f"AuthZEN decision.context.{field}")
    _integer(context["sequence"], "AuthZEN decision.context.sequence", minimum=1)
    _hash(context["request_hash"], "AuthZEN decision.context.request_hash")
    _hash(context["policy_hash"], "AuthZEN decision.context.policy_hash")
    _hash(context["policy_digest"], "AuthZEN decision.context.policy_digest")
    _hash(context["arguments_hash"], "AuthZEN decision.context.arguments_hash")
    if context["policy_ref"] != _POLICY_REF:
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN decision policy_ref is not the fixed policy",
            exit_code=1,
        )
    expected_reason = (
        "FIXED_OPERATION_ALLOWED"
        if value["decision"]
        else "CONFIGURED_REFUSAL"
    )
    if context["reason_code"] != expected_reason:
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN decision reason_code does not match its boolean decision",
            exit_code=1,
        )
    if (
        context["policy_hash"] != _POLICY_HASH
        or context["policy_digest"] != _POLICY_HASH
        or context["pdp_id"] != "bulla-closed-authzen/1"
        or context["audience"] != "synthetic-receiver"
        or context["tool_name"] != "sandbox.append"
        or context["arguments_hash"] not in _ARGUMENT_HASHES
    ):
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "AuthZEN decision context constants do not match the fixed profile",
            exit_code=1,
        )


def _validate_authzen_slot(
    request: Mapping[str, Any],
    decision: Mapping[str, Any],
    transaction_index: int,
) -> tuple[str, bool, str]:
    if transaction_index not in (0, 1):
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            "the control-plane alpha has exactly two AuthZEN transaction slots",
            exit_code=1,
        )
    scenario = "mediated_append" if transaction_index == 0 else "refused_append"
    expected_boolean = transaction_index == 0
    expected_reason = (
        "FIXED_OPERATION_ALLOWED"
        if expected_boolean
        else "CONFIGURED_REFUSAL"
    )
    if (
        request["context"]["arguments_hash"]
        != _SCENARIO_ARGUMENT_HASHES[scenario]
        or decision["decision"] is not expected_boolean
        or decision["context"]["reason_code"] != expected_reason
        or decision["context"]["sequence"] != transaction_index + 1
    ):
        raise ControlPlaneProtocolError(
            "MAPPING_MISMATCH",
            f"AuthZEN transaction slot {transaction_index + 1} does not match "
            f"the frozen {scenario} scenario",
            exit_code=1,
        )
    return scenario, expected_boolean, expected_reason


def validate_run_transition(previous: str, current: str) -> None:
    if previous not in RUN_STATUSES or current not in RUN_STATUSES:
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", "run transition contains an unknown status"
        )
    if current not in RUN_TRANSITIONS[previous]:
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", f"invalid run transition {previous!r} -> {current!r}"
        )


def _validate_standards_pin(value: Mapping[str, Any]) -> None:
    _exact(
        value,
        {
            "schema_version",
            "profile",
            "as_of",
            "standard",
            "target_protocol_revision",
            "source_status",
            "official_draft_url",
            "pinned_snapshot_url",
            "snapshot_commit",
            "snapshot_sha256",
            "final_schema_url",
            "final_document_digest",
            "deployment_gate",
            "promotion_condition",
            "rc_comparison",
            "authzen",
        },
        "standards pin",
    )
    if (
        value["schema_version"] != 1
        or value["profile"] != PROFILE
        or value["standard"] != "Model Context Protocol"
        or value["target_protocol_revision"] != MCP_TARGET_REVISION
        or value["source_status"] != "FINAL"
        or value["deployment_gate"] != DEPLOYMENT_GATE
        or value["final_document_digest"] != value["snapshot_sha256"]
    ):
        raise ControlPlaneProtocolError(
            "STANDARDS_GATE_INVALID",
            "standards pin must identify the RC/draft and keep production blocked",
            exit_code=1,
        )
    for field in (
        "as_of",
        "official_draft_url",
        "pinned_snapshot_url",
        "snapshot_commit",
        "final_schema_url",
        "promotion_condition",
    ):
        _nonempty(value[field], f"standards pin.{field}")
    _hash(value["snapshot_sha256"], "standards pin.snapshot_sha256")
    comparison = value["rc_comparison"]
    _exact(
        comparison,
        {
            "snapshot_commit",
            "snapshot_sha256",
            "added_definitions",
            "removed_definitions",
            "changed_definitions",
            "implemented_subset_impact",
        },
        "standards pin.rc_comparison",
    )
    if (
        comparison["implemented_subset_impact"]
        != "NO_CHANGE_SUBSCRIPTIONS_LISTEN_RESULT_ONLY"
        or comparison["added_definitions"]
        != [
            "SubscriptionsListenResultMetaObject",
            "SubscriptionsListenResultResponse",
        ]
        or comparison["removed_definitions"]
        != ["SubscriptionsListenResultMeta"]
        or comparison["changed_definitions"] != ["SubscriptionsListenResult"]
    ):
        raise ControlPlaneProtocolError(
            "STANDARDS_GATE_INVALID",
            "standards pin RC comparison is not the reviewed final-schema delta",
            exit_code=1,
        )
    _nonempty(
        comparison["snapshot_commit"],
        "standards pin.rc_comparison.snapshot_commit",
    )
    _hash(
        comparison["snapshot_sha256"],
        "standards pin.rc_comparison.snapshot_sha256",
    )
    authzen = value["authzen"]
    _exact(
        authzen,
        {
            "standard",
            "version",
            "status",
            "published",
            "official_url",
            "document_sha256",
            "context_note",
        },
        "standards pin.authzen",
    )
    if (
        authzen["standard"] != "OpenID AuthZEN Authorization API"
        or authzen["version"] != "1.0"
        or authzen["status"] != "FINAL"
        or authzen["published"] != "2026-01-11"
        or authzen["official_url"]
        != "https://openid.net/specs/authorization-api-1_0.html"
    ):
        raise ControlPlaneProtocolError(
            "STANDARDS_GATE_INVALID",
            "standards pin must identify AuthZEN Authorization API 1.0 final",
            exit_code=1,
        )
    _nonempty(authzen["context_note"], "standards pin.authzen.context_note")
    _hash(authzen["document_sha256"], "standards pin.authzen.document_sha256")


def _validate_service_manifest(value: Mapping[str, Any]) -> None:
    _exact(
        value,
        {
            "schema_version",
            "profile",
            "vector_id",
            "run_id",
            "incident_packet_profile",
            "packet_core_path",
            "publish_receipt_path",
            "run_state_path",
            "standards_pin_path",
            "deployment_gate",
            "production_status",
            "mcp_schema_snapshot_sha256",
            "service_witness",
        },
        "service manifest",
    )
    if (
        value["schema_version"] != 1
        or value["profile"] != PROFILE
        or value["incident_packet_profile"] != INCIDENT_PROFILE
        or value["packet_core_path"] != "packet-core.json"
        or value["publish_receipt_path"] != "publish-receipt.json"
        or value["deployment_gate"] != DEPLOYMENT_GATE
        or value["production_status"]
        != "BLOCKED_NO_REMOTE_MUTATION_AUTHORIZED"
    ):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", "service manifest constants mismatch"
        )
    for field in ("vector_id", "run_id", "run_state_path", "standards_pin_path"):
        _nonempty(value[field], f"service manifest.{field}")
    _hash(
        value["mcp_schema_snapshot_sha256"],
        "service manifest.mcp_schema_snapshot_sha256",
    )
    service = value["service_witness"]
    _exact(
        service,
        {
            "log_id",
            "operator_role",
            "deeds_path",
            "prefix_checkpoint_path",
            "final_checkpoint_path",
            "consistency_path",
            "inclusion_path",
            "retained_index_path",
            "retained_archive_path",
            "artifacts",
        },
        "service manifest.service_witness",
    )
    if service["operator_role"] != "witness":
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE",
            "service witness operator_role must be witness",
        )
    for field in (
        "log_id",
        "deeds_path",
        "prefix_checkpoint_path",
        "final_checkpoint_path",
        "consistency_path",
        "inclusion_path",
        "retained_index_path",
        "retained_archive_path",
    ):
        _nonempty(service[field], f"service witness.{field}")
    if not isinstance(service["artifacts"], list):
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", "service witness.artifacts must be an array"
        )
    paths: set[str] = set()
    for index, artifact in enumerate(service["artifacts"]):
        _exact(
            artifact,
            {"path", "sha256"},
            f"service witness.artifacts[{index}]",
        )
        path = _nonempty(
            artifact["path"], f"service witness.artifacts[{index}].path"
        )
        if path in paths:
            raise ControlPlaneProtocolError(
                "MALFORMED_FIXTURE", "service witness artifact paths repeat"
            )
        paths.add(path)
        _hash(
            artifact["sha256"],
            f"service witness.artifacts[{index}].sha256",
        )


def _validate_run_state(value: Mapping[str, Any], limits: ControlPlaneLimits) -> None:
    _exact(
        value,
        {
            "schema_version",
            "profile",
            "vector_id",
            "run_id",
            "status",
            "mandate_receipt_path",
            "transactions",
            "effect_receipt_paths",
            "decision_checkpoint_path",
            "effect_checkpoint_path",
            "gap_statement_path",
            "handoff_receipt_path",
            "transitions",
            "errors",
        },
        "run state",
    )
    if value["schema_version"] != 1 or value["profile"] != PROFILE:
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", "run state schema/profile mismatch"
        )
    if value["status"] not in RUN_STATUSES:
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", "run state status is unknown"
        )
    for field in (
        "vector_id",
        "run_id",
        "mandate_receipt_path",
        "decision_checkpoint_path",
        "effect_checkpoint_path",
        "gap_statement_path",
        "handoff_receipt_path",
    ):
        _nonempty(value[field], f"run state.{field}")
    transactions = value["transactions"]
    if (
        not isinstance(transactions, list)
        or len(transactions) != 2
        or len(transactions) > limits.max_transactions
    ):
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID",
            "run state must contain exactly one permit and one refusal transaction",
        )
    fields = {
        "request_id",
        "decision_id",
        "request_path",
        "decision_path",
        "decision_receipt_path",
    }
    request_ids: set[str] = set()
    decision_ids: set[str] = set()
    for index, transaction in enumerate(transactions):
        _exact(transaction, fields, f"run state.transactions[{index}]")
        for field in fields:
            _nonempty(transaction[field], f"run state.transactions[{index}].{field}")
        if (
            transaction["request_id"] in request_ids
            or transaction["decision_id"] in decision_ids
        ):
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID", "transaction identifiers must be unique"
            )
        request_ids.add(transaction["request_id"])
        decision_ids.add(transaction["decision_id"])
    _strings(value["effect_receipt_paths"], "run state.effect_receipt_paths")
    transitions = value["transitions"]
    if not isinstance(transitions, list) or len(transitions) != len(RUN_STATUSES):
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID",
            "run state.transitions must materialize the complete progression",
        )
    for index, transition in enumerate(transitions):
        _exact(
            transition,
            {"sequence", "state", "evidence_ref"},
            f"run state.transitions[{index}]",
        )
        if (
            transition["sequence"] != index + 1
            or transition["state"] != RUN_STATUSES[index]
        ):
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID",
                "run state transitions must be the exact ordered progression",
            )
        _hash(
            transition["evidence_ref"],
            f"run state.transitions[{index}].evidence_ref",
        )
    if not isinstance(value["errors"], list):
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", "run state.errors must be an array"
        )
    for index, error in enumerate(value["errors"]):
        _exact(error, {"code", "sequence", "detail"}, f"run state.errors[{index}]")
        if error["code"] not in ERROR_CODES:
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID", "run state error code is not closed"
            )
        _integer(error["sequence"], f"run state.errors[{index}].sequence", minimum=1)
        _nonempty(error["detail"], f"run state.errors[{index}].detail")
    if value["status"] == "FINALIZED" and value["errors"]:
        raise ControlPlaneProtocolError(
            "RUN_STATE_INVALID", "FINALIZED run state must not carry errors"
        )


def _read_json(
    packet: Any,
    path: str,
    limits: ControlPlaneLimits,
) -> Mapping[str, Any]:
    content = packet.files.get(path)
    if content is None:
        raise ControlPlaneProtocolError(
            "MALFORMED_FIXTURE", f"packet is missing {path!r}"
        )
    return _load_json(content, path, limits)


def _verify_service_witness(
    packet: Any,
    manifest: Mapping[str, Any],
    context: ControlPlaneVerificationContext,
    limits: ControlPlaneLimits,
) -> str:
    service = manifest["service_witness"]
    declared_artifacts = {
        item["path"]: item["sha256"] for item in service["artifacts"]
    }
    for path, digest in declared_artifacts.items():
        content = packet.files.get(path)
        if content is None or (
            "sha256:" + hashlib.sha256(content).hexdigest()
        ) != digest:
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                f"service witness artifact {path!r} is missing or changed",
                exit_code=1,
            )
    deeds_document = _read_json(packet, service["deeds_path"], limits)
    _exact(
        deeds_document,
        {"schema_version", "profile", "log_id", "records"},
        "service witness deeds",
    )
    if (
        deeds_document["schema_version"] != 1
        or deeds_document["profile"] != PROFILE
        or deeds_document["log_id"] != service["log_id"]
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness deed log schema/profile/log_id mismatch",
            exit_code=1,
        )
    records = deeds_document["records"]
    if not isinstance(records, list) or len(records) != len(packet.core["timeline"]):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness deed records must equal the packet timeline length",
            exit_code=1,
        )
    deeds: list[Deed] = []
    for index, (record, timeline) in enumerate(
        zip(records, packet.core["timeline"], strict=True)
    ):
        _exact(
            record,
            {"issuer", "content_hash", "attestation_hash"},
            f"service witness deeds.records[{index}]",
        )
        _nonempty(record["issuer"], f"service witness deeds.records[{index}].issuer")
        _hash(
            record["content_hash"],
            f"service witness deeds.records[{index}].content_hash",
        )
        _hash(
            record["attestation_hash"],
            f"service witness deeds.records[{index}].attestation_hash",
        )
        receipt = ActionReceipt.from_dict(
            dict(_read_json(packet, timeline["receipt_path"], limits))
        )
        expected = {
            "issuer": receipt.signature["issuer"],
            "content_hash": receipt.content_hash,
            "attestation_hash": receipt.attestation_hash,
        }
        if dict(record) != expected:
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                "service witness deed triple does not match exact packet receipt",
                exit_code=1,
            )
        deeds.append(Deed(**record))

    prefix_document = _read_json(
        packet, service["prefix_checkpoint_path"], limits
    )
    final_document = _read_json(
        packet, service["final_checkpoint_path"], limits
    )
    prefix = WitnessCheckpoint.from_dict(prefix_document)
    final = WitnessCheckpoint.from_dict(final_document)
    expected_operator = context.role_identities["witness"]["issuer"]
    if (
        prefix.operator != expected_operator
        or final.operator != expected_operator
        or prefix.log_id != service["log_id"]
        or final.log_id != service["log_id"]
        or prefix.tree_size != 3
        or final.tree_size != len(deeds)
        or not verify_checkpoint(prefix).ok
        or not verify_checkpoint(final).ok
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness checkpoints are not authentic exact prefix/final heads",
            exit_code=1,
        )
    deed_leaves = [deed.leaf() for deed in deeds]
    expected_prefix_root = "sha256:" + merkle_root(
        deed_leaves[: prefix.tree_size]
    ).hex()
    expected_final_root = "sha256:" + merkle_root(deed_leaves).hex()
    if (
        prefix.root != expected_prefix_root
        or final.root != expected_final_root
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness checkpoint roots do not match the exact ordered deeds",
            exit_code=1,
        )
    consistency = _read_json(packet, service["consistency_path"], limits)
    if not verify_checkpoint_extension(prefix, final, consistency).ok:
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness prefix-to-final consistency proof failed",
            exit_code=1,
        )
    inclusion = _read_json(packet, service["inclusion_path"], limits)
    if not verify_inclusion_record(
        dict(inclusion),
        trusted_root=final.root,
        expected_leaf=deed_leaf(records[1]),
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness permit inclusion proof failed",
            exit_code=1,
        )

    retained = _read_json(packet, service["retained_index_path"], limits)
    _exact(
        retained,
        {"schema_version", "profile", "records"},
        "retained receipt index",
    )
    if retained["schema_version"] != 1 or retained["profile"] != PROFILE:
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "retained receipt index schema/profile mismatch",
            exit_code=1,
        )
    retained_records = retained["records"]
    archive = _read_json(packet, service["retained_archive_path"], limits)
    _exact(
        archive,
        {"schema_version", "profile", "encoding", "records"},
        "retained receipt archive",
    )
    if (
        archive["schema_version"] != 1
        or archive["profile"] != PROFILE
        or archive["encoding"] != "base64"
        or not isinstance(archive["records"], list)
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "retained receipt archive schema/profile/encoding mismatch",
            exit_code=1,
        )
    if (
        not isinstance(retained_records, list)
        or len(retained_records) != len(packet.core["timeline"])
        or len(archive["records"]) != len(packet.core["timeline"])
    ):
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness must retain exact bytes for every logged timeline receipt",
            exit_code=1,
        )
    expected_sources = [
        item["receipt_path"] for item in packet.core["timeline"]
    ]
    sources: list[str] = []
    for index, (record, archived) in enumerate(
        zip(retained_records, archive["records"], strict=True)
    ):
        _exact(
            record,
            {"source_path", "archive_sequence", "byte_length", "sha256"},
            f"retained receipt index.records[{index}]",
        )
        _exact(
            archived,
            {"source_path", "bytes_base64"},
            f"retained receipt archive.records[{index}]",
        )
        source = _nonempty(
            record["source_path"],
            f"retained receipt index.records[{index}].source_path",
        )
        source_bytes = packet.files.get(source)
        try:
            retained_bytes = base64.b64decode(
                archived["bytes_base64"], validate=True
            )
        except (ValueError, TypeError) as exc:
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                "retained receipt archive contains invalid base64",
                exit_code=1,
            ) from exc
        if (
            base64.b64encode(retained_bytes).decode("ascii")
            != archived["bytes_base64"]
        ):
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                "retained receipt archive contains noncanonical base64",
                exit_code=1,
            )
        if (
            source_bytes is None
            or source_bytes != retained_bytes
            or archived["source_path"] != source
            or record["archive_sequence"] != index + 1
            or len(retained_bytes) != record["byte_length"]
            or (
                "sha256:" + hashlib.sha256(retained_bytes).hexdigest()
            )
            != record["sha256"]
        ):
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                "service witness retained receipt bytes do not match source exactly",
                exit_code=1,
            )
        sources.append(source)
    if sources != expected_sources:
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness retained order does not equal the packet timeline",
            exit_code=1,
        )
    expected_artifacts = {
        service["deeds_path"],
        service["prefix_checkpoint_path"],
        service["final_checkpoint_path"],
        service["consistency_path"],
        service["inclusion_path"],
        service["retained_index_path"],
        service["retained_archive_path"],
    }
    if set(declared_artifacts) != expected_artifacts:
        raise ControlPlaneProtocolError(
            "INCIDENT_PACKET_FAILED",
            "service witness manifest artifact set is not closed",
            exit_code=1,
        )
    return final.checkpoint_hash


def _failure(error: ControlPlaneProtocolError) -> ControlPlaneVerification:
    empty = Coverage(0, 0, (), ())
    return ControlPlaneVerification(
        profile=PROFILE,
        vector_id="UNKNOWN",
        run_id="UNKNOWN",
        run_status="UNKNOWN",
        deployment_gate=DEPLOYMENT_GATE,
        standards_status="FAILED",
        authzen_mapping="FAILED",
        incident_packet_integrity="FAILED",
        issuer_authenticity="FAILED",
        witness_inclusion="FAILED",
        witness_root_trust="FAILED",
        retained_receipts="FAILED",
        service_witness_inclusion="FAILED",
        service_witness_consistency="FAILED",
        decision_coverage=empty,
        effect_coverage=empty,
        errors=(error.to_dict(),),
        warnings=(),
        reliance="NOT_COMPUTED",
        exit_code=error.exit_code,
    )


def verify_control_plane_fixture(
    fixture_dir: str | Path,
    context: ControlPlaneVerificationContext,
    *,
    limits: ControlPlaneLimits = ControlPlaneLimits(),
) -> ControlPlaneVerification:
    """Verify the outer control loop and its exported incident packet."""
    try:
        try:
            packet = parse_incident_packet(
                fixture_dir, limits=limits.incident_limits()
            )
            incident = verify_incident_packet(
                fixture_dir,
                context.incident_context(),
                limits=limits.incident_limits(),
            )
        except IncidentPacketError as exc:
            raise ControlPlaneProtocolError(
                "MALFORMED_FIXTURE", str(exc)
            ) from exc
        if incident.exit_code != 0:
            raise ControlPlaneProtocolError(
                "INCIDENT_PACKET_FAILED",
                "; ".join(incident.errors),
                exit_code=1,
            )
        manifest = _read_json(packet, SERVICE_MANIFEST, limits)
        _validate_service_manifest(manifest)
        final_service_checkpoint = _verify_service_witness(
            packet, manifest, context, limits
        )
        run = _read_json(packet, manifest["run_state_path"], limits)
        _validate_run_state(run, limits)
        pin = _read_json(packet, manifest["standards_pin_path"], limits)
        _validate_standards_pin(pin)
        if (
            manifest["vector_id"] != run["vector_id"]
            or manifest["run_id"] != run["run_id"]
            or manifest["mcp_schema_snapshot_sha256"] != pin["snapshot_sha256"]
        ):
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH",
                "service manifest, run state, and standards pin do not bind",
                exit_code=1,
            )
        timeline_types = tuple(item["action_type"] for item in packet.core["timeline"])
        if timeline_types != APPROVED_TIMELINE:
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH",
                f"incident timeline is outside the approved vocabulary/order: {timeline_types}",
                exit_code=1,
            )
        receipts = {
            item["receipt_path"]: ActionReceipt.from_dict(
                dict(_read_json(packet, item["receipt_path"], limits))
            )
            for item in packet.core["timeline"]
        }
        timeline_paths_by_type: dict[str, list[str]] = {}
        timeline_path_by_attestation: dict[str, str] = {}
        for item in packet.core["timeline"]:
            timeline_paths_by_type.setdefault(item["action_type"], []).append(
                item["receipt_path"]
            )
            timeline_path_by_attestation[item["attestation_hash"]] = item[
                "receipt_path"
            ]
        expected_effect_paths = timeline_paths_by_type.get(
            "capability.observe", []
        )
        expected_handoff_paths = timeline_paths_by_type.get(
            "incident.handoff", []
        )
        denominator_paths: dict[str, str] = {}
        for denominator in packet.core["denominators"]:
            phase = denominator["phase"]
            checkpoint_path = timeline_path_by_attestation.get(
                denominator["checkpoint_attestation"]
            )
            if phase in denominator_paths or checkpoint_path is None:
                raise ControlPlaneProtocolError(
                    "MAPPING_MISMATCH",
                    "packet denominator checkpoint does not resolve uniquely",
                    exit_code=1,
                )
            denominator_paths[phase] = checkpoint_path
        if (
            list(run["effect_receipt_paths"]) != expected_effect_paths
            or len(expected_effect_paths) != 1
            or run["decision_checkpoint_path"]
            != denominator_paths.get("decision")
            or run["effect_checkpoint_path"]
            != denominator_paths.get("effect")
            or len(expected_handoff_paths) != 1
            or run["handoff_receipt_path"] != expected_handoff_paths[0]
        ):
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID",
                "run state receipt paths do not resolve to the exact accepted packet receipts",
                exit_code=1,
            )
        mandate = receipts.get(run["mandate_receipt_path"])
        if mandate is None or mandate.action["type"] != "eval.run.authorize":
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH", "run state mandate receipt does not resolve"
            )
        if mandate.action["subject"]["profile"] != INCIDENT_PROFILE:
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH", "packet receipt used the outer profile"
            )
        gap = receipts.get(run["gap_statement_path"])
        if gap is None:
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH", "coverage-gap statement path does not resolve"
            )
        gap_subject = gap.action["subject"]
        expected_coverage_hash = next(
            item["sha256"]
            for item in packet.core["artifacts"]
            if item["artifact_id"] == "coverage-effects"
        )
        if (
            gap.action["type"] != "incident.statement"
            or gap_subject["issuer_role"] != "incident_commander"
            or gap_subject["topic"] != "effect-coverage"
            or gap_subject["claim"] != "EFFECT_RECEIPT_GAP_CAUSE_UNRESOLVED"
            or gap_subject["epistemic_status"] != "observed"
            or gap_subject["evidence_refs"] != [expected_coverage_hash]
        ):
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH",
                "coverage-gap statement does not bind the unresolved recomputed gap",
                exit_code=1,
            )
        policy_hashes: set[str] = set()
        for transaction_index, transaction in enumerate(run["transactions"]):
            request = _read_json(packet, transaction["request_path"], limits)
            decision = _read_json(
                packet, transaction["decision_path"], limits
            )
            validate_authzen_request(request)
            validate_authzen_decision(decision)
            _, expected_boolean, _ = _validate_authzen_slot(
                request,
                decision,
                transaction_index,
            )
            decision_receipt = receipts.get(transaction["decision_receipt_path"])
            if decision_receipt is None:
                raise ControlPlaneProtocolError(
                    "MAPPING_MISMATCH", "decision receipt path does not resolve"
                )
            subject = decision_receipt.action["subject"]
            expected_word = "PERMIT" if expected_boolean else "REFUSE"
            if (
                request["context"]["run_id"] != run["run_id"]
                or decision["context"]["run_id"] != run["run_id"]
                or request["context"]["request_id"] != transaction["request_id"]
                or decision["context"]["request_id"] != transaction["request_id"]
                or decision["context"]["decision_id"] != transaction["decision_id"]
                or decision["context"]["request_hash"] != canonical_hash(request)
                or decision["context"]["sequence"] != transaction_index + 1
                or decision["context"]["arguments_hash"]
                != request["context"]["arguments_hash"]
                or decision["context"]["valid_until"]
                != request["context"]["valid_until"]
                or decision["context"]["policy_digest"]
                != request["context"]["policy_digest"]
                or subject["profile"] != INCIDENT_PROFILE
                or subject["run_id"] != run["run_id"]
                or subject["request_id"] != transaction["request_id"]
                or subject["decision_event_id"] != transaction["decision_id"]
                or subject["request_hash"] != canonical_hash(request)
                or subject["policy_digest"] != decision["context"]["policy_hash"]
                or subject["decision"] != expected_word
                or subject["rationale_codes"] != [decision["context"]["reason_code"]]
            ):
                raise ControlPlaneProtocolError(
                    "MAPPING_MISMATCH",
                    f"AuthZEN/incident mapping mismatch for {transaction['request_id']!r}",
                    exit_code=1,
                )
            policy_hashes.add(subject["policy_digest"])
        if not policy_hashes <= context.trusted_policy_hashes:
            raise ControlPlaneProtocolError(
                "UNTRUSTED_POLICY",
                "a decision policy hash is not trusted out of band",
                exit_code=1,
            )
        effect_denominator = next(
            (
                item
                for item in packet.core["denominators"]
                if item["phase"] == "effect"
            ),
            None,
        )
        effect_denominator_artifact = next(
            (
                item
                for item in packet.core["artifacts"]
                if effect_denominator is not None
                and item["artifact_id"] == effect_denominator["artifact_id"]
            ),
            None,
        )
        effect_receipt = receipts[expected_effect_paths[0]]
        if effect_denominator_artifact is None:
            raise ControlPlaneProtocolError(
                "MAPPING_MISMATCH",
                "effect denominator does not resolve to an accepted packet artifact",
                exit_code=1,
            )
        expected_transition_evidence = (
            pin["snapshot_sha256"],
            mandate.attestation_hash,
            effect_receipt.attestation_hash,
            effect_denominator_artifact["sha256"],
            final_service_checkpoint,
        )
        actual_transition_evidence = tuple(
            item["evidence_ref"] for item in run["transitions"]
        )
        if actual_transition_evidence != expected_transition_evidence:
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID",
                "run state transitions do not bind the exact generated evidence",
                exit_code=1,
            )
        if run["status"] != "FINALIZED":
            raise ControlPlaneProtocolError(
                "RUN_STATE_INVALID", "full-loop vector must end FINALIZED"
            )
        decision = next(item for item in incident.coverage if item.phase == "decision")
        effect = next(item for item in incident.coverage if item.phase == "effect")
        decision_coverage = Coverage(
            decision.receipted,
            decision.total,
            decision.covered_ids,
            decision.uncovered_ids,
        )
        effect_coverage = Coverage(
            effect.receipted,
            effect.total,
            effect.covered_ids,
            effect.uncovered_ids,
        )
        if (decision.receipted, decision.total, effect.receipted, effect.total) != (
            2,
            2,
            1,
            2,
        ):
            raise ControlPlaneProtocolError(
                "COVERAGE_MISMATCH",
                "full-loop coverage must derive as decision 2/2 and effect 1/2",
                exit_code=1,
            )
        return ControlPlaneVerification(
            profile=PROFILE,
            vector_id=run["vector_id"],
            run_id=run["run_id"],
            run_status=run["status"],
            deployment_gate=pin["deployment_gate"],
            standards_status="RC_DRAFT_PINNED_FINAL_UNPUBLISHED",
            authzen_mapping="VERIFIED",
            incident_packet_integrity=incident.packet_integrity,
            issuer_authenticity=incident.issuer_authenticity,
            witness_inclusion=incident.witness_inclusion,
            witness_root_trust=incident.witness_root_trust,
            retained_receipts="VERIFIED",
            service_witness_inclusion="VERIFIED",
            service_witness_consistency="VERIFIED",
            decision_coverage=decision_coverage,
            effect_coverage=effect_coverage,
            errors=(),
            warnings=(
                "MCP 2026-07-28 final is not published; production promotion is blocked.",
                "One direct-bypass effect remains uncovered by an accepted receipt.",
            ),
            reliance="NOT_COMPUTED",
            exit_code=0,
        )
    except ControlPlaneProtocolError as exc:
        return _failure(exc)
