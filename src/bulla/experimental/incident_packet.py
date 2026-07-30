"""Experimental verifier for the Glyph Agent Incident Packet profile.

This module is intentionally not re-exported from :mod:`bulla`.  It implements
the source-only ``glyph.agent-incident-packet/0.1-draft`` profile without
changing ActionReceipt, the stable CLI, or package metadata.

The packet is a manifest over opaque evidence bytes and ActionReceipt v0.4
records.  It does not define a telemetry format, bootstrap trust from keys
inside the packet, decide disclosure safety, or make a reliance decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from bulla._canonical import CanonicalizationError, canonical_jcs_int
from bulla.action_receipt import (
    ActionReceipt,
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.receipt_parser import (
    ReceiptParseError,
    ReceiptParseLimits,
    parse_action_receipt_json,
)


_DESCRIPTOR_RELATIVE_WALK_SUPPORTED = (
    os.name == "posix"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)

PROFILE = "glyph.agent-incident-packet/0.1-draft"
PACKET_CORE = "packet-core.json"
PUBLISH_RECEIPT = "publish-receipt.json"
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
UTC_INSTANT_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)

ACTION_TYPES = frozenset(
    {
        "eval.run.authorize",
        "capability.decide",
        "capability.observe",
        "trajectory.decide",
        "incident.statement",
        "incident.handoff",
        "incident.packet.publish",
        "incident.correct",
    }
)
ACTION_ALLOWED_ROLES: Mapping[str, frozenset[str] | None] = MappingProxyType(
    {
        "eval.run.authorize": frozenset({"evaluation_authority"}),
        "capability.decide": frozenset({"gateway", "boundary"}),
        "capability.observe": frozenset({"gateway", "boundary", "target"}),
        "trajectory.decide": frozenset({"trajectory_monitor"}),
        "incident.statement": None,
        "incident.handoff": frozenset({"incident_commander"}),
        "incident.packet.publish": frozenset({"publisher"}),
        "incident.correct": frozenset({"correction_authority"}),
    }
)
DISCLOSURE_STATES = frozenset({"released", "controlled", "withheld"})
PHASES = frozenset({"decision", "effect"})
PROTOCOLS = frozenset({"http", "mcp"})
OBSERVATION_CLASSES = frozenset(
    {"EFFECT_OBSERVED", "NO_EFFECT_OBSERVED", "OUTCOME_UNKNOWN"}
)
DECISIONS = frozenset({"PERMIT", "REFUSE"})
TRAJECTORY_DECISIONS = frozenset({"PROCEED", "ESCALATE", "REFUSE_AND_FREEZE"})
EPISTEMIC_STATUSES = frozenset(
    {"observed", "inferred", "counterparty_confirmed", "unresolved"}
)
COVERAGE_DEPTHS = {"digest": 1, "attestation": 2}
DENOMINATOR_PROVENANCE = frozenset(
    {
        "PATH_SEPARATE_TEAM_CONTROLLED",
        "SEPARATELY_CONTROLLED",
        "UNKNOWN",
    }
)

_CORE_FIELDS = {
    "profile",
    "packet_id",
    "incident_id",
    "revision",
    "classification",
    "supersedes_core_hash",
    "roles",
    "timeline",
    "artifacts",
    "denominators",
    "coverage",
    "redactions",
    "statements",
    "witnesses",
    "corrections",
}
_COMPONENT_NAMES = (
    "roles",
    "timeline",
    "artifacts",
    "denominators",
    "coverage",
    "redactions",
    "statements",
    "witnesses",
    "corrections",
)


def denominator_checkpoint_topic(
    anchor_id: str,
    phase: str,
    protocol: str,
) -> str:
    """Return the closed statement topic for one denominator checkpoint."""
    return f"denominator:{anchor_id}:{phase}:{protocol}"


class IncidentPacketError(ValueError):
    """The packet is malformed, unsupported, ambiguous, or unsafe to inspect."""


@dataclass(frozen=True)
class IncidentParseLimits:
    """Resource limits applied before any cryptographic or semantic checks."""

    max_file_bytes: int = 2_097_152
    max_total_bytes: int = 16_777_216
    max_files: int = 512
    max_depth: int = 40
    max_nodes: int = 100_000
    max_string_bytes: int = 524_288

    def __post_init__(self) -> None:
        if min(
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_files,
            self.max_depth,
            self.max_nodes,
            self.max_string_bytes,
        ) <= 0:
            raise ValueError("incident packet parse limits must be positive")


@dataclass(frozen=True)
class IncidentVerificationContext:
    """Trust supplied by the relying party from outside the packet.

    ``accepted_issuers_by_role`` maps profile role names to accepted issuer
    identifiers.  A role or key declared by ``packet-core.json`` is descriptive
    until it agrees with this context.  ``trusted_witness_roots`` is likewise
    never populated from a packet-carried checkpoint.
    """

    accepted_issuers_by_role: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]]
    trusted_witness_roots: frozenset[str] | set[str] | tuple[str, ...] = frozenset()
    team_controlled_roles: frozenset[str] | set[str] | tuple[str, ...] = frozenset()

    def __post_init__(self) -> None:
        normalized: dict[str, frozenset[str]] = {}
        if not isinstance(self.accepted_issuers_by_role, Mapping):
            raise TypeError("accepted_issuers_by_role must be a mapping")
        for role, issuers in self.accepted_issuers_by_role.items():
            if not isinstance(role, str) or not role:
                raise ValueError("accepted issuer roles must be non-empty strings")
            values = frozenset(issuers)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"accepted issuers for role {role!r} must be non-empty strings")
            normalized[role] = values
        issuer_roles: dict[str, str] = {}
        for role, issuers in normalized.items():
            for issuer in issuers:
                if issuer in issuer_roles:
                    raise ValueError(
                        f"accepted issuer {issuer!r} is assigned to both "
                        f"{issuer_roles[issuer]!r} and {role!r}"
                    )
                issuer_roles[issuer] = role
        roots = frozenset(self.trusted_witness_roots)
        if any(not isinstance(value, str) or not value for value in roots):
            raise ValueError("trusted witness roots must be non-empty strings")
        team_roles = frozenset(self.team_controlled_roles)
        if any(not isinstance(value, str) or not value for value in team_roles):
            raise ValueError("team-controlled roles must be non-empty strings")
        object.__setattr__(self, "accepted_issuers_by_role", MappingProxyType(normalized))
        object.__setattr__(self, "trusted_witness_roots", roots)
        object.__setattr__(self, "team_controlled_roles", team_roles)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IncidentVerificationContext":
        _exact(value, {"accepted_issuers_by_role", "trusted_witness_roots", "team_controlled_roles"}, "context")
        accepted = value["accepted_issuers_by_role"]
        if not isinstance(accepted, dict):
            raise IncidentPacketError("context.accepted_issuers_by_role must be an object")
        return cls(
            accepted_issuers_by_role={
                str(role): frozenset(_string_list(issuers, f"context.accepted_issuers_by_role.{role}"))
                for role, issuers in accepted.items()
            },
            trusted_witness_roots=frozenset(
                _string_list(value["trusted_witness_roots"], "context.trusted_witness_roots")
            ),
            team_controlled_roles=frozenset(
                _string_list(value["team_controlled_roles"], "context.team_controlled_roles")
            ),
        )


@dataclass(frozen=True)
class ParsedIncidentPacket:
    root: Path
    core: Mapping[str, Any]
    publish_receipt: bytes
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class AnchorCoverageVerification:
    anchor_id: str
    phase: str
    protocol: str
    status: str
    provenance: str
    total: int
    receipted: int
    covered_ids: tuple[str, ...] = ()
    uncovered_ids: tuple[str, ...] = ()
    phantom_receipt_ids: tuple[str, ...] = ()
    invalid_receipts: tuple[Mapping[str, str], ...] = ()
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError(
            "The truth value of AnchorCoverageVerification is ambiguous; "
            "inspect .status and the named sets."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "phase": self.phase,
            "protocol": self.protocol,
            "status": self.status,
            "provenance": self.provenance,
            "total": self.total,
            "receipted": self.receipted,
            "covered_ids": list(self.covered_ids),
            "uncovered_ids": list(self.uncovered_ids),
            "phantom_receipt_ids": list(self.phantom_receipt_ids),
            "invalid_receipts": [dict(item) for item in self.invalid_receipts],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class IncidentPacketVerification:
    """Multidimensional verification result.

    There is deliberately no single valid/trusted Boolean.  Integrity,
    authenticity, coverage, disclosure availability, witness trust, conflicts,
    and reliance remain distinct.
    """

    packet_integrity: str
    artifact_integrity: str
    receipt_integrity: str
    signature_status: str
    issuer_authenticity: str
    authority_binding: str
    occurrence_binding: str
    timeline_integrity: str
    lineage_integrity: str
    trace_integrity: str
    redaction_binding: str
    coverage_status: str
    denominator_provenance: str
    witness_inclusion: str
    witness_root_trust: str
    temporal_evidence: Mapping[str, tuple[str, ...]]
    party_conflicts: tuple[str, ...]
    correction_status: str
    unavailable_artifacts: tuple[str, ...]
    suppressed_conclusions: tuple[str, ...]
    coverage: tuple[AnchorCoverageVerification, ...]
    disclosure_safety: str = "NOT_COMPUTED"
    reliance: str = "NOT_COMPUTED"
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError(
            "The truth value of IncidentPacketVerification is ambiguous; inspect "
            "the named dimensions. Reliance is not computed by this verifier."
        )

    @property
    def exit_code(self) -> int:
        """Return the repository-checker exit code for a completed verification."""
        hard = {
            self.packet_integrity,
            self.artifact_integrity,
            self.receipt_integrity,
            self.signature_status,
            self.issuer_authenticity,
            self.authority_binding,
            self.occurrence_binding,
            self.timeline_integrity,
            self.lineage_integrity,
            self.trace_integrity,
            self.redaction_binding,
            self.coverage_status,
            self.witness_inclusion,
            self.correction_status,
        }
        return 1 if "FAILED" in hard else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "packet_integrity": self.packet_integrity,
            "artifact_integrity": self.artifact_integrity,
            "receipt_integrity": self.receipt_integrity,
            "signature_status": self.signature_status,
            "issuer_authenticity": self.issuer_authenticity,
            "authority_binding": self.authority_binding,
            "occurrence_binding": self.occurrence_binding,
            "timeline_integrity": self.timeline_integrity,
            "lineage_integrity": self.lineage_integrity,
            "trace_integrity": self.trace_integrity,
            "redaction_binding": self.redaction_binding,
            "coverage_status": self.coverage_status,
            "coverage": [item.to_dict() for item in self.coverage],
            "denominator_provenance": self.denominator_provenance,
            "witness_inclusion": self.witness_inclusion,
            "witness_root_trust": self.witness_root_trust,
            "temporal_evidence": {
                name: list(values) for name, values in self.temporal_evidence.items()
            },
            "party_conflicts": list(self.party_conflicts),
            "correction_status": self.correction_status,
            "unavailable_artifacts": list(self.unavailable_artifacts),
            "suppressed_conclusions": list(self.suppressed_conclusions),
            "disclosure_safety": self.disclosure_safety,
            "reliance": self.reliance,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "exit_code": self.exit_code,
        }


def _plain_canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_canonical_value(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    """Return the profile's SHA-256 commitment under ActionReceipt v0.4 canon."""
    try:
        encoded = canonical_jcs_int(_plain_canonical_value(value)).encode("utf-8")
    except CanonicalizationError as exc:
        raise IncidentPacketError(str(exc)) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def component_hashes(packet_core: Mapping[str, Any]) -> dict[str, str]:
    """Compute the component-manifest commitments carried by the publish receipt."""
    return {name: canonical_hash(packet_core[name]) for name in _COMPONENT_NAMES}


def coverage_commitment(packet_core: Mapping[str, Any]) -> str:
    """Bind denominator/checkpoint and coverage refs to exact artifact bytes."""
    artifacts = {
        item["artifact_id"]: item for item in packet_core["artifacts"]
    }
    committed = {
        name: [
            {
                **dict(item),
                "artifact_sha256": artifacts[item["artifact_id"]]["sha256"],
            }
            for item in packet_core[name]
        ]
        for name in ("denominators", "coverage")
    }
    return canonical_hash(committed)


def build_profile_receipt(
    *,
    action_type: str,
    subject: Mapping[str, Any],
    signer: Any,
    envelope: Any,
    event_id: str,
    claimed_at: str,
    evidence_refs: Iterable[Mapping[str, Any]] = (),
    anchor_ref: Mapping[str, Any] | None = None,
    producer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and sign one closed-profile ActionReceipt v0.4 document."""
    candidate = dict(subject)
    candidate.setdefault("profile", PROFILE)
    _validate_action_subject(action_type, candidate)
    unsigned = build_action_receipt_v04(
        action={"type": action_type, "subject": candidate},
        diagnostic_ref={"status": "not_applicable"},
        envelope=envelope,
        event_id=event_id,
        claimed_at=claimed_at,
        evidence_refs=tuple(dict(item) for item in evidence_refs),
        anchor_ref=dict(anchor_ref or {}),
        producer=dict(producer or {"bulla_version": "source"}),
    )
    return sign_action_receipt_v04(unsigned, signer).to_dict()


def build_publish_receipt(
    packet_core: Mapping[str, Any],
    *,
    signer: Any,
    envelope: Any,
    event_id: str,
    claimed_at: str,
    issuer_role: str = "publisher",
    producer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the non-circular receipt over a completed ``packet-core.json``."""
    _validate_packet_core(packet_core)
    subject = {
        "profile": PROFILE,
        "issuer_role": issuer_role,
        "packet_id": packet_core["packet_id"],
        "packet_core_hash": canonical_hash(packet_core),
        "component_hashes": component_hashes(packet_core),
    }
    return build_profile_receipt(
        action_type="incident.packet.publish",
        subject=subject,
        signer=signer,
        envelope=envelope,
        event_id=event_id,
        claimed_at=claimed_at,
        evidence_refs=(
            {
                "name": "recomputation:packet-core",
                "hash": subject["packet_core_hash"],
                "grounding": "execution_verified",
            },
        ),
        producer=producer,
    )


def released_artifact_entry(
    *,
    artifact_id: str,
    path: str,
    role: str,
    media_type: str,
    content: bytes,
    retention: str,
    access_condition: str,
) -> dict[str, Any]:
    """Build one released-artifact manifest entry from its exact bytes."""
    normalized = _safe_relative_path(path, "artifact path")
    if not isinstance(content, bytes):
        raise TypeError("released artifact content must be exact bytes")
    return {
        "artifact_id": _nonempty(artifact_id, "artifact_id"),
        "path": normalized,
        "withheld_ref": None,
        "role": _nonempty(role, "artifact role"),
        "media_type": _nonempty(media_type, "artifact media_type"),
        "byte_length": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "disclosure_state": "released",
        "retention": _nonempty(retention, "artifact retention"),
        "access_condition": _nonempty(access_condition, "artifact access_condition"),
    }


def unavailable_artifact_entry(
    *,
    artifact_id: str,
    withheld_ref: str,
    role: str,
    media_type: str,
    byte_length: int,
    sha256: str,
    disclosure_state: str,
    retention: str,
    access_condition: str,
) -> dict[str, Any]:
    """Build a controlled/withheld entry without placing its bytes in the packet."""
    if disclosure_state not in {"controlled", "withheld"}:
        raise IncidentPacketError(
            "unavailable artifact disclosure_state must be controlled or withheld"
        )
    if isinstance(byte_length, bool) or not isinstance(byte_length, int) or byte_length < 0:
        raise IncidentPacketError("unavailable artifact byte_length must be non-negative")
    _hash(sha256, "unavailable artifact sha256")
    return {
        "artifact_id": _nonempty(artifact_id, "artifact_id"),
        "path": None,
        "withheld_ref": _nonempty(withheld_ref, "withheld_ref"),
        "role": _nonempty(role, "artifact role"),
        "media_type": _nonempty(media_type, "artifact media_type"),
        "byte_length": byte_length,
        "sha256": sha256,
        "disclosure_state": disclosure_state,
        "retention": _nonempty(retention, "artifact retention"),
        "access_condition": _nonempty(access_condition, "artifact access_condition"),
    }


def _mkdir_component(root: Path, relative_parent: PurePosixPath) -> None:
    current = root
    for part in relative_parent.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise IncidentPacketError(
                    f"packet output component is not a real directory: "
                    f"{current.relative_to(root)}"
                )
            continue
        current.mkdir(mode=0o700)


def _write_new_member(root: Path, relative: str, content: bytes) -> None:
    normalized = _safe_relative_path(relative, "packet output member")
    path = root / PurePosixPath(normalized)
    _mkdir_component(root, PurePosixPath(normalized).parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise IncidentPacketError(
            f"could not create packet member {normalized!r} exclusively: {exc}"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
    finally:
        os.close(descriptor)


def assemble_incident_packet(
    packet_dir: str | Path,
    *,
    packet_id: str,
    incident_id: str,
    classification: str,
    role_issuers: Mapping[str, str],
    timeline_receipts: Iterable[tuple[str, Mapping[str, Any]]],
    artifact_entries: Iterable[Mapping[str, Any]],
    released_artifacts: Mapping[str, bytes],
    denominators: Iterable[Mapping[str, Any]],
    coverage: Iterable[Mapping[str, Any]],
    publisher_signer: Any,
    publisher_envelope: Any,
    publish_event_id: str,
    publish_claimed_at: str,
    revision: int = 1,
    supersedes_core_hash: str | None = None,
    redactions: Iterable[Mapping[str, Any]] = (),
    statements: Iterable[Mapping[str, Any]] = (),
    witnesses: Iterable[Mapping[str, Any]] = (),
    corrections: Iterable[Mapping[str, Any]] = (),
    producer: Mapping[str, Any] | None = None,
) -> ParsedIncidentPacket:
    """Assemble a conforming packet into a newly created directory.

    The caller supplies runtime evidence bytes and semantic component
    references; this helper derives the ordered timeline and publish receipt,
    validates the complete core, creates every member exclusively with
    no-follow semantics, and parses the result before returning it.
    """
    root = Path(packet_dir)
    if root.exists() or root.is_symlink():
        raise IncidentPacketError("packet output directory must not already exist")
    parent = root.parent.resolve(strict=True)
    if not parent.is_dir():
        raise IncidentPacketError("packet output parent must be a real directory")
    root = parent / root.name
    root.mkdir(mode=0o700, exist_ok=False)

    receipt_items = [(str(path), dict(document)) for path, document in timeline_receipts]
    timeline: list[dict[str, Any]] = []
    receipt_files: dict[str, bytes] = {}
    for index, (path, document) in enumerate(receipt_items):
        normalized = _safe_relative_path(path, f"timeline_receipts[{index}].path")
        if not normalized.startswith("receipts/"):
            raise IncidentPacketError("timeline receipts must be written under receipts/")
        if normalized in receipt_files:
            raise IncidentPacketError(f"duplicate timeline receipt path {normalized!r}")
        try:
            receipt = ActionReceipt.from_dict(document)
        except Exception as exc:
            raise IncidentPacketError(
                f"timeline receipt {normalized!r} is malformed: {exc}"
            ) from exc
        receipt_bytes = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        timeline.append(
            {
                "sequence": index + 1,
                "receipt_path": normalized,
                "byte_length": len(receipt_bytes),
                "sha256": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest(),
                "attestation_hash": receipt.attestation_hash,
                "action_type": receipt.action["type"],
            }
        )
        receipt_files[normalized] = receipt_bytes

    artifacts = [dict(item) for item in artifact_entries]
    expected_released = {
        item["path"]
        for item in artifacts
        if item.get("disclosure_state") == "released"
    }
    if set(released_artifacts) != expected_released:
        raise IncidentPacketError(
            "released_artifacts paths must equal the released artifact manifest paths"
        )
    for path, content in released_artifacts.items():
        if not isinstance(content, bytes):
            raise TypeError(f"released artifact {path!r} must be exact bytes")

    core = {
        "profile": PROFILE,
        "packet_id": packet_id,
        "incident_id": incident_id,
        "revision": revision,
        "classification": classification,
        "supersedes_core_hash": supersedes_core_hash,
        "roles": [
            {"role": role, "issuer": issuer}
            for role, issuer in role_issuers.items()
        ],
        "timeline": timeline,
        "artifacts": artifacts,
        "denominators": [dict(item) for item in denominators],
        "coverage": [dict(item) for item in coverage],
        "redactions": [dict(item) for item in redactions],
        "statements": [dict(item) for item in statements],
        "witnesses": [dict(item) for item in witnesses],
        "corrections": [dict(item) for item in corrections],
    }
    _validate_packet_core(core)
    publish = build_publish_receipt(
        core,
        signer=publisher_signer,
        envelope=publisher_envelope,
        event_id=publish_event_id,
        claimed_at=publish_claimed_at,
        producer=producer,
    )
    members = {
        PACKET_CORE: (
            json.dumps(core, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
        PUBLISH_RECEIPT: (
            json.dumps(publish, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
        **receipt_files,
        **dict(released_artifacts),
    }
    try:
        for relative, content in sorted(members.items()):
            _write_new_member(root, relative, content)
        return parse_incident_packet(root)
    except Exception:
        # Do not recursively delete a path that may have changed identity after
        # creation. The caller receives the exact new directory for inspection.
        raise


def _reject_constant(value: str) -> None:
    raise IncidentPacketError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IncidentPacketError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _enforce_json_limits(value: Any, limits: IncidentParseLimits, label: str) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise IncidentPacketError(f"{label} exceeds {limits.max_nodes} aggregate JSON nodes")
        if depth > limits.max_depth:
            raise IncidentPacketError(f"{label} exceeds maximum JSON depth {limits.max_depth}")
        if isinstance(current, str):
            try:
                size = len(current.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise IncidentPacketError(f"{label} contains a lone Unicode surrogate") from exc
            if size > limits.max_string_bytes:
                raise IncidentPacketError(
                    f"{label} contains a string over {limits.max_string_bytes} UTF-8 bytes"
                )
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _load_json(raw: bytes, label: str, limits: IncidentParseLimits) -> Any:
    if len(raw) > limits.max_file_bytes:
        raise IncidentPacketError(f"{label} exceeds {limits.max_file_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IncidentPacketError(f"{label} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except IncidentPacketError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise IncidentPacketError(f"invalid JSON in {label}: {exc}") from exc
    _enforce_json_limits(value, limits, label)
    return value


def parse_strict_json_bytes(
    raw: bytes,
    *,
    label: str = "JSON input",
    limits: IncidentParseLimits = IncidentParseLimits(),
) -> Any:
    """Parse one bounded JSON document with duplicate-member rejection."""
    return _load_json(raw, label, limits)


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncidentPacketError(f"{label} must be a non-empty relative POSIX path")
    if "\\" in value or "\x00" in value:
        raise IncidentPacketError(f"{label} contains an unsafe path separator or NUL")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("/")
        or any(
            part in {"", ".", ".."} or ":" in part
            for part in path.parts
        )
    ):
        raise IncidentPacketError(f"{label} must be a normalized relative path")
    normalized = path.as_posix()
    if normalized != value:
        raise IncidentPacketError(f"{label} must be normalized ({normalized!r})")
    return normalized


def _is_symlink_or_reparse_point(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _packet_read_open_flags(*, require_directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if require_directory:
        return flags | getattr(os, "O_DIRECTORY", 0)
    return flags | getattr(os, "O_BINARY", 0)


def _walk_packet_path_fallback(
    root: Path,
    limits: IncidentParseLimits,
) -> dict[str, bytes]:
    """Read a packet on hosts without descriptor-relative directory APIs."""
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise IncidentPacketError(f"cannot inspect packet root: {exc}") from exc
    if _is_symlink_or_reparse_point(root_metadata):
        raise IncidentPacketError(
            "packet root must not be a symlink or reparse point"
        )
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise IncidentPacketError(f"packet directory does not exist: {root}")
    files: dict[str, bytes] = {}
    total = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            path = current_path / name
            metadata = path.lstat()
            if _is_symlink_or_reparse_point(metadata):
                raise IncidentPacketError(
                    "packet contains a symlink or reparse-point directory: "
                    f"{path.relative_to(root)}"
                )
        for name in sorted(filenames):
            path = current_path / name
            relative = _safe_relative_path(path.relative_to(root).as_posix(), "packet file")
            metadata = path.lstat()
            if (
                _is_symlink_or_reparse_point(metadata)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise IncidentPacketError(f"packet contains a non-regular file: {relative}")
            if len(files) >= limits.max_files:
                raise IncidentPacketError(f"packet exceeds {limits.max_files} files")
            try:
                descriptor = os.open(
                    path,
                    _packet_read_open_flags(require_directory=False),
                )
            except OSError as exc:
                raise IncidentPacketError(
                    f"could not open packet member {relative!r} without following "
                    f"a final symlink: {exc}"
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise IncidentPacketError(
                        f"packet contains a non-regular file: {relative}"
                    )
                size = metadata.st_size
                if size > limits.max_file_bytes:
                    raise IncidentPacketError(
                        f"{relative} exceeds {limits.max_file_bytes} bytes"
                    )
                chunks: list[bytes] = []
                remaining = size + 1
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)
                if len(content) != size:
                    raise IncidentPacketError(
                        f"packet member {relative!r} changed size while being read"
                    )
            finally:
                os.close(descriptor)
            total += size
            if total > limits.max_total_bytes:
                raise IncidentPacketError(f"packet exceeds {limits.max_total_bytes} total bytes")
            files[relative] = content
    return files


def _walk_packet(root: Path, limits: IncidentParseLimits) -> dict[str, bytes]:
    """Read a packet without reopening descendants through mutable path names."""
    if not _DESCRIPTOR_RELATIVE_WALK_SUPPORTED:
        return _walk_packet_path_fallback(root, limits)

    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise IncidentPacketError(f"cannot inspect packet root: {exc}") from exc
    if _is_symlink_or_reparse_point(root_metadata) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise IncidentPacketError(
            "packet root must be a non-symlink, non-reparse-point directory"
        )

    files: dict[str, bytes] = {}
    total = 0
    visited_directories: set[tuple[int, int]] = set()

    def open_child(
        directory_fd: int,
        name: str,
        expected: os.stat_result,
        *,
        require_directory: bool,
        relative: str,
    ) -> int:
        try:
            descriptor = os.open(
                name,
                _packet_read_open_flags(require_directory=require_directory),
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise IncidentPacketError(
                f"unsafe or unreadable packet member {relative!r}: {exc}"
            ) from exc
        actual = os.fstat(descriptor)
        expected_kind = stat.S_ISDIR if require_directory else stat.S_ISREG
        if (
            not expected_kind(actual.st_mode)
            or (actual.st_dev, actual.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            os.close(descriptor)
            raise IncidentPacketError(
                f"packet member {relative!r} changed before descriptor open"
            )
        return descriptor

    def walk(directory_fd: int, prefix: str) -> None:
        nonlocal total
        directory_metadata = os.fstat(directory_fd)
        directory_identity = (
            directory_metadata.st_dev,
            directory_metadata.st_ino,
        )
        if directory_identity in visited_directories:
            raise IncidentPacketError(
                f"packet directory cycle detected at {prefix or '.'!r}"
            )
        visited_directories.add(directory_identity)
        try:
            names = sorted(os.listdir(directory_fd))
            for name in names:
                relative = _safe_relative_path(
                    f"{prefix}/{name}" if prefix else name,
                    "packet file",
                )
                try:
                    metadata = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise IncidentPacketError(
                        f"cannot inspect packet member {relative!r}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    raise IncidentPacketError(
                        f"packet contains a symlink: {relative}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = open_child(
                        directory_fd,
                        name,
                        metadata,
                        require_directory=True,
                        relative=relative,
                    )
                    try:
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise IncidentPacketError(
                        f"packet contains a non-regular file: {relative}"
                    )
                if len(files) >= limits.max_files:
                    raise IncidentPacketError(
                        f"packet exceeds {limits.max_files} files"
                    )
                if metadata.st_size > limits.max_file_bytes:
                    raise IncidentPacketError(
                        f"{relative} exceeds {limits.max_file_bytes} bytes"
                    )
                descriptor = open_child(
                    directory_fd,
                    name,
                    metadata,
                    require_directory=False,
                    relative=relative,
                )
                try:
                    chunks: list[bytes] = []
                    remaining = metadata.st_size + 1
                    while remaining:
                        chunk = os.read(
                            descriptor,
                            min(65_536, remaining),
                        )
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    content = b"".join(chunks)
                    final = os.fstat(descriptor)
                    if (
                        len(content) != metadata.st_size
                        or (
                            final.st_dev,
                            final.st_ino,
                            final.st_size,
                        )
                        != (
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_size,
                        )
                    ):
                        raise IncidentPacketError(
                            f"packet member {relative!r} changed while being read"
                        )
                finally:
                    os.close(descriptor)
                total += len(content)
                if total > limits.max_total_bytes:
                    raise IncidentPacketError(
                        f"packet exceeds {limits.max_total_bytes} total bytes"
                    )
                files[relative] = content
        finally:
            visited_directories.remove(directory_identity)

    try:
        root_fd = os.open(
            root,
            _packet_read_open_flags(require_directory=True),
        )
    except OSError as exc:
        raise IncidentPacketError(
            f"cannot open packet root without following symlinks: {exc}"
        ) from exc
    try:
        opened_root = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (opened_root.st_dev, opened_root.st_ino)
            != (root_metadata.st_dev, root_metadata.st_ino)
        ):
            raise IncidentPacketError(
                "packet root changed before descriptor open"
            )
        walk(root_fd, "")
    finally:
        os.close(root_fd)
    return files


def parse_incident_packet(
    packet_dir: str | Path,
    *,
    limits: IncidentParseLimits = IncidentParseLimits(),
) -> ParsedIncidentPacket:
    """Parse a directory packet at a fail-closed byte and path boundary."""
    root = Path(packet_dir)
    files = _walk_packet(root, limits)
    for required in (PACKET_CORE, PUBLISH_RECEIPT):
        if required not in files:
            raise IncidentPacketError(f"packet is missing required file {required!r}")
    core = _load_json(files[PACKET_CORE], PACKET_CORE, limits)
    _validate_packet_core(core)

    declared = {PACKET_CORE, PUBLISH_RECEIPT}
    for index, entry in enumerate(core["timeline"]):
        declared.add(_safe_relative_path(entry["receipt_path"], f"timeline[{index}].receipt_path"))
    for entry in core["artifacts"]:
        if entry["disclosure_state"] == "released":
            declared.add(entry["path"])
    undeclared = sorted(set(files) - declared)
    if undeclared:
        raise IncidentPacketError(f"packet contains undeclared files: {undeclared}")
    missing = sorted(declared - set(files))
    if missing:
        raise IncidentPacketError(f"packet is missing declared released files: {missing}")

    artifacts_by_path = {
        entry["path"]: entry
        for entry in core["artifacts"]
        if entry["disclosure_state"] == "released"
    }
    duplicate_members = set(artifacts_by_path) & {
        item["receipt_path"] for item in core["timeline"]
    }
    if duplicate_members:
        raise IncidentPacketError(
            f"receipt paths must not also be declared as artifacts: {sorted(duplicate_members)}"
        )
    return ParsedIncidentPacket(
        root=root.resolve(),
        core=MappingProxyType(core),
        publish_receipt=files[PUBLISH_RECEIPT],
        files=MappingProxyType(files),
    )


def _exact(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise IncidentPacketError(f"{label} must be an object")
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing:
        raise IncidentPacketError(f"{label} is missing required fields {sorted(missing)}")
    if unknown:
        raise IncidentPacketError(f"{label} has unknown fields {sorted(unknown)}")


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncidentPacketError(f"{label} must be a non-empty string")
    return value


def _parse_instant(value: Any, label: str) -> datetime:
    text = _nonempty(value, label)
    if UTC_INSTANT_PATTERN.fullmatch(text) is None:
        raise IncidentPacketError(f"{label} must be an RFC 3339 UTC instant ending in Z")
    try:
        instant = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise IncidentPacketError(f"{label} is not a valid RFC 3339 instant") from exc
    if instant.utcoffset() is None:
        raise IncidentPacketError(f"{label} must carry an explicit UTC offset")
    return instant


def _hash(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise IncidentPacketError(f"{label} must be a lowercase sha256 digest")
    return value


def _string_list(value: Any, label: str, *, unique: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise IncidentPacketError(f"{label} must be an array")
    result = [_nonempty(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if unique and len(set(result)) != len(result):
        raise IncidentPacketError(f"{label} must not contain duplicates")
    return result


def _object_list(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise IncidentPacketError(f"{label} must be an array")
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise IncidentPacketError(f"{label}[{index}] must be an object")
    return value


def _validate_packet_core(core: Any) -> None:
    _exact(core, _CORE_FIELDS, "packet-core")
    if core["profile"] != PROFILE:
        raise IncidentPacketError(f"unsupported packet profile {core['profile']!r}")
    _nonempty(core["packet_id"], "packet-core.packet_id")
    _nonempty(core["incident_id"], "packet-core.incident_id")
    if isinstance(core["revision"], bool) or not isinstance(core["revision"], int) or core["revision"] < 1:
        raise IncidentPacketError("packet-core.revision must be a positive integer")
    _nonempty(core["classification"], "packet-core.classification")
    _hash(core["supersedes_core_hash"], "packet-core.supersedes_core_hash", nullable=True)

    role_names: set[str] = set()
    role_issuers: set[str] = set()
    for index, item in enumerate(_object_list(core["roles"], "packet-core.roles")):
        _exact(item, {"role", "issuer"}, f"packet-core.roles[{index}]")
        role = _nonempty(item["role"], f"packet-core.roles[{index}].role")
        issuer = _nonempty(item["issuer"], f"packet-core.roles[{index}].issuer")
        if role in role_names:
            raise IncidentPacketError(f"duplicate declared role {role!r}")
        if issuer in role_issuers:
            raise IncidentPacketError(
                f"issuer {issuer!r} is assigned to more than one packet role"
            )
        role_names.add(role)
        role_issuers.add(issuer)

    timeline_hashes: set[str] = set()
    timeline_paths: set[str] = set()
    timeline_types_by_path: dict[str, str] = {}
    timeline_hash_by_path: dict[str, str] = {}
    for index, item in enumerate(_object_list(core["timeline"], "packet-core.timeline")):
        _exact(
            item,
            {
                "sequence",
                "receipt_path",
                "byte_length",
                "sha256",
                "attestation_hash",
                "action_type",
            },
            f"packet-core.timeline[{index}]",
        )
        if (
            isinstance(item["sequence"], bool)
            or not isinstance(item["sequence"], int)
            or item["sequence"] != index + 1
        ):
            raise IncidentPacketError("packet-core.timeline sequence must be contiguous from 1")
        path = _safe_relative_path(item["receipt_path"], f"packet-core.timeline[{index}].receipt_path")
        if not path.startswith("receipts/"):
            raise IncidentPacketError("timeline receipt paths must be under receipts/")
        if (
            isinstance(item["byte_length"], bool)
            or not isinstance(item["byte_length"], int)
            or item["byte_length"] < 0
        ):
            raise IncidentPacketError(
                f"packet-core.timeline[{index}].byte_length must be a non-negative integer"
            )
        _hash(item["sha256"], f"packet-core.timeline[{index}].sha256")
        digest = _hash(item["attestation_hash"], f"packet-core.timeline[{index}].attestation_hash")
        action_type = _nonempty(item["action_type"], f"packet-core.timeline[{index}].action_type")
        if action_type not in ACTION_TYPES - {"incident.packet.publish"}:
            raise IncidentPacketError(f"unsupported timeline action type {action_type!r}")
        if path in timeline_paths or digest in timeline_hashes:
            raise IncidentPacketError("timeline paths and attestation hashes must be unique")
        timeline_paths.add(path)
        timeline_hashes.add(digest)
        timeline_types_by_path[path] = action_type
        timeline_hash_by_path[path] = digest

    artifact_ids: set[str] = set()
    artifact_paths: set[str] = set()
    artifacts: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(_object_list(core["artifacts"], "packet-core.artifacts")):
        label = f"packet-core.artifacts[{index}]"
        _exact(
            item,
            {
                "artifact_id",
                "path",
                "withheld_ref",
                "role",
                "media_type",
                "byte_length",
                "sha256",
                "disclosure_state",
                "retention",
                "access_condition",
            },
            label,
        )
        artifact_id = _nonempty(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id in artifact_ids:
            raise IncidentPacketError(f"duplicate artifact id {artifact_id!r}")
        artifact_ids.add(artifact_id)
        artifacts[artifact_id] = item
        state = item["disclosure_state"]
        if state not in DISCLOSURE_STATES:
            raise IncidentPacketError(f"{label}.disclosure_state is unsupported")
        if isinstance(item["byte_length"], bool) or not isinstance(item["byte_length"], int) or item["byte_length"] < 0:
            raise IncidentPacketError(f"{label}.byte_length must be a non-negative integer")
        _hash(item["sha256"], f"{label}.sha256")
        _nonempty(item["role"], f"{label}.role")
        _nonempty(item["media_type"], f"{label}.media_type")
        _nonempty(item["retention"], f"{label}.retention")
        _nonempty(item["access_condition"], f"{label}.access_condition")
        if state == "released":
            path = _safe_relative_path(item["path"], f"{label}.path")
            if path in {PACKET_CORE, PUBLISH_RECEIPT} or path.startswith("receipts/"):
                raise IncidentPacketError(f"{label}.path overlaps a reserved packet member")
            if path in artifact_paths:
                raise IncidentPacketError(f"duplicate artifact path {path!r}")
            artifact_paths.add(path)
            if item["withheld_ref"] is not None:
                raise IncidentPacketError(f"{label}.withheld_ref must be null when released")
        else:
            if item["path"] is not None:
                raise IncidentPacketError(f"{label}.path must be null when {state}")
            _nonempty(item["withheld_ref"], f"{label}.withheld_ref")

    denominator_entries = _object_list(
        core["denominators"], "packet-core.denominators"
    )
    if not denominator_entries:
        raise IncidentPacketError(
            "packet-core.denominators must declare at least one coverage anchor"
        )
    coverage_entries = _object_list(core["coverage"], "packet-core.coverage")
    if not coverage_entries:
        raise IncidentPacketError(
            "packet-core.coverage must declare at least one coverage report"
        )

    denominator_keys: set[tuple[str, str, str]] = set()
    denominator_ids: set[str] = set()
    denominator_checkpoints: set[str] = set()
    denominator_phase_counts: dict[str, dict[str, int]] = {}
    for index, item in enumerate(denominator_entries):
        label = f"packet-core.denominators[{index}]"
        _exact(
            item,
            {
                "anchor_id",
                "phase",
                "protocol",
                "artifact_id",
                "provenance",
                "checkpoint_attestation",
            },
            label,
        )
        key = _coverage_key(item, label)
        if key in denominator_keys:
            raise IncidentPacketError(f"duplicate denominator anchor/phase/protocol {key!r}")
        denominator_keys.add(key)
        artifact_id = _nonempty(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts:
            raise IncidentPacketError(f"{label} references unknown artifact {artifact_id!r}")
        if artifacts[artifact_id]["role"] != "denominator":
            raise IncidentPacketError(f"{label} artifact role must be 'denominator'")
        if artifact_id in denominator_ids:
            raise IncidentPacketError(f"denominator artifact {artifact_id!r} is reused")
        if item["provenance"] not in DENOMINATOR_PROVENANCE:
            raise IncidentPacketError(f"{label}.provenance is unsupported")
        checkpoint = _hash(
            item["checkpoint_attestation"],
            f"{label}.checkpoint_attestation",
        )
        if checkpoint in denominator_checkpoints:
            raise IncidentPacketError(
                "each denominator must use a distinct checkpoint attestation"
            )
        denominator_checkpoints.add(checkpoint)
        phases = denominator_phase_counts.setdefault(
            item["protocol"], {"decision": 0, "effect": 0}
        )
        phases[item["phase"]] += 1
        denominator_ids.add(artifact_id)
    if any(
        counts != {"decision": 1, "effect": 1}
        for counts in denominator_phase_counts.values()
    ):
        raise IncidentPacketError(
            "each represented protocol must declare exactly one decision "
            "denominator and one effect denominator"
        )

    coverage_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(coverage_entries):
        label = f"packet-core.coverage[{index}]"
        _exact(
            item,
            {"anchor_id", "phase", "protocol", "artifact_id", "minimum_verification_depth"},
            label,
        )
        key = _coverage_key(item, label)
        if key in coverage_keys:
            raise IncidentPacketError(f"duplicate coverage anchor/phase/protocol {key!r}")
        coverage_keys.add(key)
        if key not in denominator_keys:
            raise IncidentPacketError(f"{label} has no matching denominator")
        artifact_id = _nonempty(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts:
            raise IncidentPacketError(f"{label} references unknown artifact {artifact_id!r}")
        if artifacts[artifact_id]["role"] != "coverage":
            raise IncidentPacketError(f"{label} artifact role must be 'coverage'")
        if artifact_id in denominator_ids:
            raise IncidentPacketError("coverage and denominator must use distinct artifacts")
        if item["minimum_verification_depth"] != "attestation":
            raise IncidentPacketError(
                f"{label}.minimum_verification_depth must be 'attestation'"
            )
    if coverage_keys != denominator_keys:
        raise IncidentPacketError("every denominator must have exactly one coverage report")

    component_ids: dict[str, set[str]] = {
        "redactions": set(),
        "statements": set(),
        "witnesses": set(),
        "corrections": set(),
    }
    for name, fields in (
        (
            "redactions",
            {
                "redaction_id",
                "record_artifact_id",
                "source_artifact_id",
                "released_artifact_id",
                "tool",
                "tool_version",
                "rules_hash",
                "reviewer_statement_ref",
            },
        ),
        ("statements", {"receipt_path", "attestation_hash"}),
        ("witnesses", {"witness_id", "artifact_id", "root_ref"}),
        ("corrections", {"receipt_path", "attestation_hash"}),
    ):
        if name == "witnesses" and len(core[name]) > 1:
            raise IncidentPacketError(
                "packet-core.witnesses supports at most one witness reference "
                "in this draft"
            )
        for index, item in enumerate(_object_list(core[name], f"packet-core.{name}")):
            label = f"packet-core.{name}[{index}]"
            _exact(item, fields, label)
            if name == "redactions":
                component_id = _nonempty(item["redaction_id"], f"{label}.redaction_id")
                if component_id in component_ids[name]:
                    raise IncidentPacketError(f"duplicate redaction id {component_id!r}")
                component_ids[name].add(component_id)
                for key in (
                    "record_artifact_id",
                    "source_artifact_id",
                    "released_artifact_id",
                ):
                    if item[key] not in artifacts:
                        raise IncidentPacketError(f"{label}.{key} references an unknown artifact")
                if artifacts[item["record_artifact_id"]]["role"] != "redaction":
                    raise IncidentPacketError(
                        f"{label}.record_artifact_id artifact role must be 'redaction'"
                    )
                if artifacts[item["record_artifact_id"]]["disclosure_state"] != "released":
                    raise IncidentPacketError(
                        f"{label}.record_artifact_id must be released"
                    )
                if item["source_artifact_id"] == item["released_artifact_id"]:
                    raise IncidentPacketError(f"{label} source and released artifacts must differ")
                if item["record_artifact_id"] in {
                    item["source_artifact_id"],
                    item["released_artifact_id"],
                }:
                    raise IncidentPacketError(
                        f"{label} record artifact must differ from source and release"
                    )
                if artifacts[item["released_artifact_id"]]["disclosure_state"] != "released":
                    raise IncidentPacketError(f"{label} released artifact must be released")
                _nonempty(item["tool"], f"{label}.tool")
                _nonempty(item["tool_version"], f"{label}.tool_version")
                _hash(item["rules_hash"], f"{label}.rules_hash")
                _hash(item["reviewer_statement_ref"], f"{label}.reviewer_statement_ref")
            elif name in {"statements", "corrections"}:
                path = _safe_relative_path(item["receipt_path"], f"{label}.receipt_path")
                digest = _hash(item["attestation_hash"], f"{label}.attestation_hash")
                if path not in timeline_paths or digest not in timeline_hashes:
                    raise IncidentPacketError(f"{label} must reference a timeline receipt")
                if timeline_hash_by_path[path] != digest:
                    raise IncidentPacketError(f"{label} path and hash reference different receipts")
                expected_type = (
                    "incident.statement" if name == "statements" else "incident.correct"
                )
                if timeline_types_by_path[path] != expected_type:
                    raise IncidentPacketError(
                        f"{label} must reference an {expected_type} timeline receipt"
                    )
                key = f"{path}|{digest}"
                if key in component_ids[name]:
                    raise IncidentPacketError(f"duplicate {name[:-1]} reference {path!r}")
                component_ids[name].add(key)
            else:
                witness_id = _nonempty(item["witness_id"], f"{label}.witness_id")
                if witness_id in component_ids[name]:
                    raise IncidentPacketError(f"duplicate witness id {witness_id!r}")
                component_ids[name].add(witness_id)
                if item["artifact_id"] not in artifacts:
                    raise IncidentPacketError(f"{label}.artifact_id references an unknown artifact")
                if artifacts[item["artifact_id"]]["role"] != "witness":
                    raise IncidentPacketError(f"{label} artifact role must be 'witness'")
                _nonempty(item["root_ref"], f"{label}.root_ref")


def _coverage_key(value: Mapping[str, Any], label: str) -> tuple[str, str, str]:
    anchor = _nonempty(value["anchor_id"], f"{label}.anchor_id")
    phase = value["phase"]
    protocol = value["protocol"]
    if phase not in PHASES:
        raise IncidentPacketError(f"{label}.phase is unsupported")
    if protocol not in PROTOCOLS:
        raise IncidentPacketError(f"{label}.protocol is unsupported")
    return anchor, phase, protocol


_ACTION_FIELDS: dict[str, set[str]] = {
    "eval.run.authorize": {
        "profile", "issuer_role", "run_id", "model_id", "harness_id", "safeguards_id",
        "authorized_targets", "budgets", "prohibitions", "valid_from", "valid_until",
        "policy_digest", "role_issuers",
    },
    "capability.decide": {
        "profile", "issuer_role", "run_id", "request_id", "decision_event_id",
        "request_hash", "mandate_ref", "policy_digest", "decision", "rationale_codes",
        "anchor_id", "protocol", "evidence_hash",
    },
    "capability.observe": {
        "profile", "issuer_role", "run_id", "request_id", "decision_attestation",
        "observation_id", "anchor_id", "protocol", "effect_hash", "evidence_refs",
        "observation_class", "transport_status", "mandate_ref", "evidence_hash",
    },
    "trajectory.decide": {
        "profile", "issuer_role", "run_id", "mandate_ref", "ordered_lineage",
        "policy_digest", "decision", "rationale_codes", "requested_controls",
    },
    "incident.statement": {
        "profile", "issuer_role", "statement_id", "incident_id", "topic", "claim",
        "epistemic_status", "evidence_refs",
    },
    "incident.handoff": {
        "profile", "issuer_role", "incident_id", "recipient", "mandate_ref",
        "timeline_hash", "coverage_hash", "statement_refs", "parent_refs",
        "requested_actions", "disclosure_conditions", "correction_channel",
        "challenge_channel",
    },
    "incident.packet.publish": {
        "profile", "issuer_role", "packet_id", "packet_core_hash", "component_hashes",
    },
    "incident.correct": {
        "profile", "issuer_role", "correction_id", "supersedes_kind",
        "supersedes_ref", "replacement_ref", "reason", "correction_channel",
    },
}


def _validate_action_subject(action_type: str, subject: Any) -> None:
    if action_type not in ACTION_TYPES:
        raise IncidentPacketError(f"action type {action_type!r} is outside the closed profile")
    _exact(subject, _ACTION_FIELDS[action_type], f"{action_type}.subject")
    if subject["profile"] != PROFILE:
        raise IncidentPacketError(f"{action_type}.subject.profile must be {PROFILE!r}")
    issuer_role = _nonempty(subject["issuer_role"], f"{action_type}.subject.issuer_role")
    allowed_roles = ACTION_ALLOWED_ROLES[action_type]
    if allowed_roles is not None and issuer_role not in allowed_roles:
        raise IncidentPacketError(
            f"{action_type}.subject.issuer_role must be one of {sorted(allowed_roles)}"
        )

    if action_type == "eval.run.authorize":
        for field_name in (
            "run_id", "model_id", "harness_id", "safeguards_id",
            "valid_from", "valid_until",
        ):
            _nonempty(subject[field_name], f"{action_type}.subject.{field_name}")
        _hash(subject["policy_digest"], f"{action_type}.subject.policy_digest")
        _string_list(subject["authorized_targets"], "eval.run.authorize.authorized_targets", unique=True)
        _string_list(subject["prohibitions"], "eval.run.authorize.prohibitions", unique=True)
        _exact(subject["budgets"], {"compute_units", "max_actions", "duration_seconds"}, "eval.run.authorize.budgets")
        for name, value in subject["budgets"].items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise IncidentPacketError(f"eval.run.authorize.budgets.{name} must be a non-negative integer")
        if not isinstance(subject["role_issuers"], Mapping) or not subject["role_issuers"]:
            raise IncidentPacketError("eval.run.authorize.role_issuers must be a non-empty object")
        for role, issuer in subject["role_issuers"].items():
            _nonempty(role, "eval.run.authorize.role_issuers role")
            _nonempty(issuer, f"eval.run.authorize.role_issuers.{role}")
        if _parse_instant(
            subject["valid_from"], "eval.run.authorize.valid_from"
        ) >= _parse_instant(
            subject["valid_until"], "eval.run.authorize.valid_until"
        ):
            raise IncidentPacketError(
                "eval.run.authorize.valid_from must precede valid_until"
            )
        return

    if action_type == "capability.decide":
        for name in ("run_id", "request_id", "decision_event_id", "anchor_id"):
            _nonempty(subject[name], f"{action_type}.subject.{name}")
        for name in ("request_hash", "mandate_ref", "policy_digest", "evidence_hash"):
            _hash(subject[name], f"{action_type}.subject.{name}")
        if subject["decision"] not in DECISIONS:
            raise IncidentPacketError("capability.decide.decision must be PERMIT or REFUSE")
        _string_list(subject["rationale_codes"], "capability.decide.rationale_codes", unique=True)
        if subject["protocol"] not in PROTOCOLS:
            raise IncidentPacketError("capability.decide.protocol is unsupported")
        return

    if action_type == "capability.observe":
        for name in ("run_id", "request_id", "observation_id", "anchor_id", "transport_status"):
            _nonempty(subject[name], f"{action_type}.subject.{name}")
        for name in ("decision_attestation", "effect_hash", "mandate_ref", "evidence_hash"):
            _hash(subject[name], f"{action_type}.subject.{name}")
        if subject["observation_class"] not in OBSERVATION_CLASSES:
            raise IncidentPacketError("capability.observe.observation_class is unsupported")
        if subject["protocol"] not in PROTOCOLS:
            raise IncidentPacketError("capability.observe.protocol is unsupported")
        refs = _string_list(subject["evidence_refs"], "capability.observe.evidence_refs", unique=True)
        for index, digest in enumerate(refs):
            _hash(digest, f"capability.observe.evidence_refs[{index}]")
        return

    if action_type == "trajectory.decide":
        _nonempty(subject["run_id"], "trajectory.decide.run_id")
        for name in ("mandate_ref", "policy_digest"):
            _hash(subject[name], f"trajectory.decide.{name}")
        lineage = _string_list(subject["ordered_lineage"], "trajectory.decide.ordered_lineage", unique=True)
        for index, digest in enumerate(lineage):
            _hash(digest, f"trajectory.decide.ordered_lineage[{index}]")
        if subject["decision"] not in TRAJECTORY_DECISIONS:
            raise IncidentPacketError("trajectory.decide.decision is unsupported")
        _string_list(subject["rationale_codes"], "trajectory.decide.rationale_codes", unique=True)
        _string_list(subject["requested_controls"], "trajectory.decide.requested_controls", unique=True)
        return

    if action_type == "incident.statement":
        for name in ("statement_id", "incident_id", "topic", "claim"):
            _nonempty(subject[name], f"incident.statement.{name}")
        if subject["epistemic_status"] not in EPISTEMIC_STATUSES:
            raise IncidentPacketError("incident.statement.epistemic_status is unsupported")
        if (
            subject["epistemic_status"] == "counterparty_confirmed"
            and issuer_role != "affected_party"
        ):
            raise IncidentPacketError(
                "incident.statement.counterparty_confirmed requires "
                "issuer_role 'affected_party'"
            )
        refs = _string_list(subject["evidence_refs"], "incident.statement.evidence_refs", unique=True)
        for index, digest in enumerate(refs):
            _hash(digest, f"incident.statement.evidence_refs[{index}]")
        return

    if action_type == "incident.handoff":
        for name in (
            "incident_id", "recipient", "correction_channel", "challenge_channel",
        ):
            _nonempty(subject[name], f"incident.handoff.{name}")
        for name in ("mandate_ref", "timeline_hash", "coverage_hash"):
            _hash(subject[name], f"incident.handoff.{name}")
        for name in ("statement_refs", "parent_refs"):
            values = _string_list(subject[name], f"incident.handoff.{name}", unique=True)
            for index, digest in enumerate(values):
                _hash(digest, f"incident.handoff.{name}[{index}]")
        _string_list(subject["requested_actions"], "incident.handoff.requested_actions", unique=True)
        _string_list(subject["disclosure_conditions"], "incident.handoff.disclosure_conditions", unique=True)
        return

    if action_type == "incident.packet.publish":
        _nonempty(subject["packet_id"], "incident.packet.publish.packet_id")
        _hash(subject["packet_core_hash"], "incident.packet.publish.packet_core_hash")
        _exact(subject["component_hashes"], set(_COMPONENT_NAMES), "incident.packet.publish.component_hashes")
        for name, digest in subject["component_hashes"].items():
            _hash(digest, f"incident.packet.publish.component_hashes.{name}")
        return

    if action_type == "incident.correct":
        for name in ("correction_id", "reason", "correction_channel"):
            _nonempty(subject[name], f"incident.correct.{name}")
        if subject["supersedes_kind"] not in {"packet", "statement"}:
            raise IncidentPacketError("incident.correct.supersedes_kind is unsupported")
        _hash(subject["supersedes_ref"], "incident.correct.supersedes_ref")
        _hash(subject["replacement_ref"], "incident.correct.replacement_ref")


def _artifact_map(core: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["artifact_id"]: item for item in core["artifacts"]}


def _receipt_role_accepted(
    receipt: Mapping[str, Any],
    context: IncidentVerificationContext,
) -> tuple[str, str]:
    subject = receipt["action"]["subject"]
    role = subject["issuer_role"]
    issuer = (receipt.get("signature") or {}).get("issuer")
    accepted = context.accepted_issuers_by_role.get(role, frozenset())
    if issuer not in accepted:
        return role, "FAILED"
    return role, "VERIFIED"


def _validate_profile_receipt(
    raw: bytes,
    *,
    context: IncidentVerificationContext,
    label: str,
    expected_action: str | None = None,
    declared_roles: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        parsed = parse_action_receipt_json(
            raw,
            ReceiptParseLimits(
                max_bytes=min(len(raw) + 1, 2_097_152),
                max_depth=40,
                max_nodes=100_000,
                max_string_bytes=524_288,
            ),
        )
    except (ReceiptParseError, ValueError) as exc:
        return None, [f"{label}: {exc}"]
    document = parsed.to_dict()
    action = document.get("action")
    if not isinstance(action, Mapping) or set(action) != {"type", "subject"}:
        return None, [f"{label}: action must contain exactly type and subject"]
    action_type = action["type"]
    if expected_action is not None and action_type != expected_action:
        errors.append(f"{label}: expected action {expected_action!r}, got {action_type!r}")
    subject_valid = True
    try:
        _validate_action_subject(action_type, action["subject"])
    except IncidentPacketError as exc:
        subject_valid = False
        errors.append(f"{label}: {exc}")
    if document["schema_version"] != "0.4":
        errors.append(f"{label}: new profile records must use ActionReceipt v0.4")
    if document.get("conventions"):
        errors.append(
            f"{label}: {PROFILE} requires conventions to be empty"
        )
    try:
        _parse_instant(document.get("claimed_at"), f"{label}.claimed_at")
    except IncidentPacketError as exc:
        errors.append(str(exc))
    verification = verify_receipt(document)
    if not verification.ok or verification.verified_to != "attestation":
        errors.append(
            f"{label}: receipt did not reach attestation verification "
            f"({verification.verified_to}: {'; '.join(verification.reasons)})"
        )
    if verification.authority_authentic != "verified":
        errors.append(f"{label}: authority binding is {verification.authority_authentic}")
    issuer = (document.get("signature") or {}).get("issuer")
    mandate = document.get("mandate")
    authority = (
        mandate.get("authority")
        if isinstance(mandate, Mapping)
        else None
    )
    if (
        not isinstance(authority, Mapping)
        or (mandate.get("deed_schema") or "0.2") != "0.2"
        or authority.get("delegation") != []
        or authority.get("principal") != issuer
    ):
        errors.append(
            f"{label}: incident profile requires direct deed_schema 0.2 "
            "authority with an empty delegation and principal equal to the "
            "accepted signer"
        )
    if subject_valid:
        role, role_status = _receipt_role_accepted(document, context)
        if role_status != "VERIFIED":
            errors.append(
                f"{label}: issuer is not accepted for declared role {role!r}"
            )
        if declared_roles is not None and declared_roles.get(role) != issuer:
            errors.append(
                f"{label}: issuer does not equal the packet-core issuer for "
                f"role {role!r}"
            )
    if document.get("occurrence") is None:
        errors.append(f"{label}: occurrence proof is required")

    evidence = document.get("evidence_refs") or []
    if subject_valid and action_type == "capability.decide":
        if any(item.get("grounding") != "self_asserted" for item in evidence):
            errors.append(f"{label}: gateway decision evidence must be self_asserted")
        if [item.get("hash") for item in evidence] != [
            action["subject"]["evidence_hash"]
        ]:
            errors.append(
                f"{label}: decision evidence_hash must equal the sole "
                "ActionReceipt evidence hash"
            )
    elif subject_valid and action_type == "capability.observe":
        expected_grounding = (
            "counterparty_signed"
            if action["subject"]["issuer_role"] == "target"
            else "self_asserted"
        )
        if any(item.get("grounding") != expected_grounding for item in evidence):
            errors.append(
                f"{label}: {action['subject']['issuer_role']} observation evidence "
                f"must be {expected_grounding}"
            )
        if [item.get("hash") for item in evidence] != action["subject"]["evidence_refs"]:
            errors.append(
                f"{label}: observation evidence_refs must equal the ordered "
                "ActionReceipt evidence hashes"
            )
    if (
        subject_valid
        and action["subject"]["issuer_role"] in context.team_controlled_roles
    ):
        if any(item.get("grounding") == "third_party_anchored" for item in evidence):
            errors.append(
                f"{label}: team-controlled infrastructure cannot claim third_party_anchored"
            )
    if any(item.get("grounding") == "execution_verified" for item in evidence):
        if not all(str(item.get("name", "")).startswith("recomputation:") for item in evidence if item.get("grounding") == "execution_verified"):
            errors.append(
                f"{label}: execution_verified is limited to named deterministic recomputations"
            )
    return document, errors


def _load_artifact_json(
    packet: ParsedIncidentPacket,
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_id: str,
    limits: IncidentParseLimits,
) -> Any:
    artifact = artifacts[artifact_id]
    if artifact["disclosure_state"] != "released":
        return None
    return _load_json(packet.files[artifact["path"]], artifact["path"], limits)


def _validate_observation(value: Any, label: str) -> tuple[str, str, str, str, str, str]:
    _exact(
        value,
        {"anchor_id", "observation_id", "run_id", "protocol", "operation_ref", "phase", "evidence_hash"},
        label,
    )
    anchor = _nonempty(value["anchor_id"], f"{label}.anchor_id")
    observation_id = _nonempty(value["observation_id"], f"{label}.observation_id")
    run_id = _nonempty(value["run_id"], f"{label}.run_id")
    if value["protocol"] not in PROTOCOLS:
        raise IncidentPacketError(f"{label}.protocol is unsupported")
    if value["phase"] not in PHASES:
        raise IncidentPacketError(f"{label}.phase is unsupported")
    operation_ref = _hash(value["operation_ref"], f"{label}.operation_ref")
    evidence_hash = _hash(value["evidence_hash"], f"{label}.evidence_hash")
    return anchor, observation_id, run_id, value["protocol"], operation_ref, evidence_hash


def _coverage_receipt_projection(document: Mapping[str, Any]) -> tuple[tuple[str, str, str], dict[str, str]] | None:
    action_type = document["action"]["type"]
    subject = document["action"]["subject"]
    if action_type == "capability.decide":
        key = (subject["anchor_id"], "decision", subject["protocol"])
        projection = {
            "anchor_id": subject["anchor_id"],
            "observation_id": subject["decision_event_id"],
            "run_id": subject["run_id"],
            "protocol": subject["protocol"],
            "operation_ref": subject["request_hash"],
            "phase": "decision",
            "evidence_hash": subject["evidence_hash"],
        }
        return key, projection
    if action_type == "capability.observe":
        key = (subject["anchor_id"], "effect", subject["protocol"])
        projection = {
            "anchor_id": subject["anchor_id"],
            "observation_id": subject["observation_id"],
            "run_id": subject["run_id"],
            "protocol": subject["protocol"],
            "operation_ref": subject["effect_hash"],
            "phase": "effect",
            "evidence_hash": subject["evidence_hash"],
        }
        return key, projection
    return None


def _verify_coverage(
    packet: ParsedIncidentPacket,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    receipts: Mapping[str, Mapping[str, Any]],
    invalid_receipts: Mapping[str, tuple[str, ...]],
    invalid_denominator_checkpoints: Mapping[
        tuple[str, str, str], tuple[str, ...]
    ],
    limits: IncidentParseLimits,
) -> tuple[tuple[AnchorCoverageVerification, ...], list[str]]:
    results: list[AnchorCoverageVerification] = []
    errors: list[str] = []
    denominator_by_key = {
        _coverage_key(item, "denominator"): item for item in packet.core["denominators"]
    }
    coverage_by_key = {
        _coverage_key(item, "coverage"): item for item in packet.core["coverage"]
    }
    projections: dict[tuple[str, str, str], dict[str, tuple[dict[str, str], str]]] = {}
    duplicate_projection_ids: dict[tuple[str, str, str], set[str]] = {}
    projection_invalid: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for path, document in receipts.items():
        if path in invalid_receipts:
            continue
        item = _coverage_receipt_projection(document)
        if item is None:
            continue
        key, projection = item
        bucket = projections.setdefault(key, {})
        observation_id = projection["observation_id"]
        if observation_id in bucket or observation_id in duplicate_projection_ids.get(key, set()):
            previous = bucket.pop(observation_id, None)
            duplicates = duplicate_projection_ids.setdefault(key, set())
            duplicates.add(observation_id)
            if previous is not None:
                projection_invalid.setdefault(key, []).append(
                    {
                        "source": previous[1],
                        "reason": f"duplicate accepted receipt projection for {observation_id!r}",
                    }
                )
            projection_invalid.setdefault(key, []).append(
                {
                    "source": path,
                    "reason": f"duplicate accepted receipt projection for {observation_id!r}",
                }
            )
        else:
            bucket[observation_id] = (projection, path)
    for path, reasons in invalid_receipts.items():
        # An invalid decision/observation is reported against every matching
        # coverage group only if its untrusted JSON could be parsed enough to
        # identify that group. Fail closed by never letting it reduce gaps.
        try:
            candidate = _load_json(packet.files[path], path, limits)
            item = _coverage_receipt_projection(candidate)
        except Exception:
            item = None
        if item is not None:
            key, _ = item
            projection_invalid.setdefault(key, []).append(
                {"source": path, "reason": "; ".join(reasons)}
            )

    for key in sorted(denominator_by_key):
        denominator_ref = denominator_by_key[key]
        coverage_ref = coverage_by_key[key]
        provenance = denominator_ref["provenance"]
        reasons: list[str] = []
        invalid = projection_invalid.get(key, [])
        checkpoint_problems = invalid_denominator_checkpoints.get(key, ())
        if checkpoint_problems:
            results.append(
                AnchorCoverageVerification(
                    anchor_id=key[0],
                    phase=key[1],
                    protocol=key[2],
                    status="NOT_COMPUTED",
                    provenance=provenance,
                    total=0,
                    receipted=0,
                    invalid_receipts=tuple(invalid),
                    reasons=checkpoint_problems,
                )
            )
            continue
        try:
            denominator = _load_artifact_json(
                packet, artifacts, denominator_ref["artifact_id"], limits
            )
            if denominator is None:
                raise IncidentPacketError("denominator artifact is unavailable")
            _exact(
                denominator,
                {"schema_version", "anchor_id", "phase", "protocol", "observations"},
                "denominator",
            )
            if isinstance(denominator["schema_version"], bool) or denominator["schema_version"] != 1:
                raise IncidentPacketError("denominator.schema_version must be 1")
            if (
                denominator["anchor_id"],
                denominator["phase"],
                denominator["protocol"],
            ) != key:
                raise IncidentPacketError("denominator identity does not match packet-core")
            observation_rows = _object_list(
                denominator["observations"],
                "denominator.observations",
            )
            if not observation_rows:
                raise IncidentPacketError(
                    "denominator.observations must contain at least one event"
                )
            observations: list[dict[str, str]] = []
            observed_ids: set[str] = set()
            for index, raw in enumerate(observation_rows):
                _validate_observation(raw, f"denominator.observations[{index}]")
                if (
                    raw["anchor_id"],
                    raw["phase"],
                    raw["protocol"],
                ) != key:
                    raise IncidentPacketError(
                        f"denominator.observations[{index}] belongs to a different anchor/phase/protocol"
                    )
                if raw["observation_id"] in observed_ids:
                    raise IncidentPacketError(
                        f"duplicate denominator observation id {raw['observation_id']!r}"
                    )
                observed_ids.add(raw["observation_id"])
                observations.append(dict(raw))
        except IncidentPacketError as exc:
            results.append(
                AnchorCoverageVerification(
                    anchor_id=key[0],
                    phase=key[1],
                    protocol=key[2],
                    status="NOT_COMPUTED",
                    provenance=provenance,
                    total=0,
                    receipted=0,
                    invalid_receipts=tuple(invalid),
                    reasons=(str(exc),),
                )
            )
            continue

        receipt_by_id = projections.get(key, {})
        covered: list[str] = []
        for observation in observations:
            candidate = receipt_by_id.get(observation["observation_id"])
            if candidate is None:
                continue
            projected, path = candidate
            if projected == observation:
                covered.append(observation["observation_id"])
            else:
                invalid.append(
                    {
                        "source": path,
                        "reason": "receipt projection does not equal the denominator observation",
                    }
                )
        uncovered = [
            item["observation_id"]
            for item in observations
            if item["observation_id"] not in set(covered)
        ]
        phantom = sorted(set(receipt_by_id) - {item["observation_id"] for item in observations})
        recomputed = {
            "schema_version": 1,
            "anchor_id": key[0],
            "phase": key[1],
            "protocol": key[2],
            "denominator_sha256": artifacts[denominator_ref["artifact_id"]]["sha256"],
            "minimum_verification_depth": coverage_ref["minimum_verification_depth"],
            "total": len(observations),
            "receipted": len(covered),
            "covered_ids": covered,
            "uncovered_ids": uncovered,
            "phantom_receipt_ids": phantom,
            "invalid_receipts": invalid,
        }
        if artifacts[coverage_ref["artifact_id"]]["disclosure_state"] != "released":
            results.append(
                AnchorCoverageVerification(
                    anchor_id=key[0],
                    phase=key[1],
                    protocol=key[2],
                    status="NOT_COMPUTED",
                    provenance=provenance,
                    total=len(observations),
                    receipted=len(covered),
                    covered_ids=tuple(covered),
                    uncovered_ids=tuple(uncovered),
                    phantom_receipt_ids=tuple(phantom),
                    invalid_receipts=tuple(
                        MappingProxyType(dict(item)) for item in invalid
                    ),
                    reasons=("declared coverage artifact is unavailable",),
                )
            )
            continue
        report_status = "COMPUTED"
        try:
            declared = _load_artifact_json(packet, artifacts, coverage_ref["artifact_id"], limits)
            _exact(declared, set(recomputed), "coverage report")
            if isinstance(declared["schema_version"], bool) or declared["schema_version"] != 1:
                raise IncidentPacketError("coverage report schema_version must be integer 1")
            for name in ("total", "receipted"):
                if isinstance(declared[name], bool) or not isinstance(declared[name], int) or declared[name] < 0:
                    raise IncidentPacketError(
                        f"coverage report {name} must be a non-negative integer"
                    )
            if declared != recomputed:
                report_status = "FAILED"
                reasons.append("declared coverage report does not equal the recomputed report")
                errors.append(
                    f"coverage report for {key!r} does not equal the recomputed report"
                )
        except IncidentPacketError as exc:
            report_status = "FAILED"
            reasons.append(str(exc))
            errors.append(f"coverage report for {key!r}: {exc}")
        results.append(
            AnchorCoverageVerification(
                anchor_id=key[0],
                phase=key[1],
                protocol=key[2],
                status=report_status,
                provenance=provenance,
                total=len(observations),
                receipted=len(covered),
                covered_ids=tuple(covered),
                uncovered_ids=tuple(uncovered),
                phantom_receipt_ids=tuple(phantom),
                invalid_receipts=tuple(MappingProxyType(dict(item)) for item in invalid),
                reasons=tuple(reasons),
            )
        )
    return tuple(results), errors


def verify_incident_packet(
    packet: ParsedIncidentPacket | str | Path,
    context: IncidentVerificationContext,
    *,
    limits: IncidentParseLimits = IncidentParseLimits(),
) -> IncidentPacketVerification:
    """Verify a parsed packet without network access or packet-supplied trust."""
    if not isinstance(packet, ParsedIncidentPacket):
        packet = parse_incident_packet(packet, limits=limits)
    core = packet.core
    errors: list[str] = []
    warnings: list[str] = []
    suppressed: list[str] = []
    unavailable = tuple(
        item["artifact_id"]
        for item in core["artifacts"]
        if item["disclosure_state"] != "released"
    )
    artifacts = _artifact_map(core)
    released_trace_ids = {
        item["artifact_id"]
        for item in core["artifacts"]
        if item["role"] == "trace" and item["disclosure_state"] == "released"
    }

    artifact_failed = False
    trace_failed = False
    for artifact_id, item in artifacts.items():
        if item["disclosure_state"] != "released":
            continue
        raw = packet.files[item["path"]]
        actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        if len(raw) != item["byte_length"] or actual_hash != item["sha256"]:
            artifact_failed = True
            if item["role"] == "trace":
                trace_failed = True
            errors.append(f"artifact {artifact_id!r} byte length or digest mismatch")

    declared_roles = {item["role"]: item["issuer"] for item in core["roles"]}
    role_failed = False
    for role, issuer in declared_roles.items():
        if issuer not in context.accepted_issuers_by_role.get(role, frozenset()):
            role_failed = True
            errors.append(f"packet-declared issuer for role {role!r} is not accepted out of band")

    receipts: dict[str, dict[str, Any]] = {}
    receipt_errors: dict[str, tuple[str, ...]] = {}
    receipt_failed = False
    signature_failed = False
    authority_failed = False
    occurrence_failed = False
    timeline_byte_failed = False
    for item in core["timeline"]:
        path = item["receipt_path"]
        receipt_bytes = packet.files[path]
        actual_receipt_hash = (
            "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
        )
        byte_problems: list[str] = []
        if (
            len(receipt_bytes) != item["byte_length"]
            or actual_receipt_hash != item["sha256"]
        ):
            timeline_byte_failed = True
            byte_problems.append(
                f"{path}: exact bytes do not match packet timeline commitment"
            )
        document, problems = _validate_profile_receipt(
            receipt_bytes,
            context=context,
            label=path,
            expected_action=item["action_type"],
            declared_roles=declared_roles,
        )
        problems[:0] = byte_problems
        if document is not None:
            receipts[path] = document
            if document["hashes"]["attestation"] != item["attestation_hash"]:
                problems.append(f"{path}: attestation hash does not match packet timeline")
        if problems:
            receipt_failed = True
            if any("attestation verification" in problem for problem in problems):
                signature_failed = True
            if any("issuer" in problem for problem in problems):
                role_failed = True
            if any("authority" in problem for problem in problems):
                authority_failed = True
            if any("occurrence" in problem for problem in problems):
                occurrence_failed = True
            receipt_errors[path] = tuple(problems)
            errors.extend(problems)

    publish, publish_problems = _validate_profile_receipt(
        packet.publish_receipt,
        context=context,
        label=PUBLISH_RECEIPT,
        expected_action="incident.packet.publish",
        declared_roles=declared_roles,
    )
    publish_binding_failed = False
    if publish is None or publish_problems:
        publish_binding_failed = True
    else:
        subject = publish["action"]["subject"]
        if subject["packet_id"] != core["packet_id"]:
            publish_problems.append("publish-receipt.json: packet_id does not match packet-core")
        if subject["packet_core_hash"] != canonical_hash(core):
            publish_problems.append("publish-receipt.json: packet_core_hash mismatch")
        if subject["component_hashes"] != component_hashes(core):
            publish_problems.append("publish-receipt.json: component_hashes mismatch")
    if publish_problems:
        publish_binding_failed = True
        receipt_failed = True
        errors.extend(publish_problems)
        if any("attestation verification" in problem for problem in publish_problems):
            signature_failed = True
        if any("issuer" in problem for problem in publish_problems):
            role_failed = True
        if any("authority" in problem for problem in publish_problems):
            authority_failed = True
        if any("occurrence" in problem for problem in publish_problems):
            occurrence_failed = True

    timeline_failed = timeline_byte_failed
    lineage_failed = False
    ordered = [receipts.get(item["receipt_path"]) for item in core["timeline"]]
    capability_protocols = {
        protocol
        for document in ordered
        if document is not None
        and document["action"]["type"]
        in {"capability.decide", "capability.observe"}
        and isinstance(document["action"]["subject"], Mapping)
        and isinstance(
            protocol := document["action"]["subject"].get("protocol"),
            str,
        )
    }
    denominator_protocols = {
        item["protocol"] for item in core["denominators"]
    }
    if capability_protocols != denominator_protocols:
        timeline_failed = True
        errors.append(
            "capability receipt protocols and denominator protocols must match"
        )
    by_attestation = {
        document["hashes"]["attestation"]: (index, document)
        for index, document in enumerate(ordered)
        if document is not None
    }
    mandates_by_ref = {
        document["hashes"]["attestation"]: (index, document)
        for index, document in enumerate(ordered)
        if document is not None
        and document["action"]["type"] == "eval.run.authorize"
        and core["timeline"][index]["receipt_path"] not in receipt_errors
    }
    for mandate_index, mandate in mandates_by_ref.values():
        if mandate["action"]["subject"]["role_issuers"] != declared_roles:
            timeline_failed = True
            authority_failed = True
            errors.append(
                "eval.run.authorize role_issuers does not equal packet-core roles"
            )
        mandate_path = core["timeline"][mandate_index]["receipt_path"]
        if mandate_path in receipt_errors:
            authority_failed = True
    for index, document in enumerate(ordered):
        if document is None:
            continue
        current_path = core["timeline"][index]["receipt_path"]
        if current_path in receipt_errors:
            continue
        action_type = document["action"]["type"]
        subject = document["action"]["subject"]
        mandate: dict[str, Any] | None = None
        if action_type in {
            "capability.decide",
            "capability.observe",
            "trajectory.decide",
            "incident.handoff",
        }:
            resolved_mandate = mandates_by_ref.get(subject["mandate_ref"])
            if resolved_mandate is None or resolved_mandate[0] >= index:
                timeline_failed = True
                authority_failed = True
                errors.append(f"{current_path}: mandate_ref does not resolve")
            else:
                mandate_index, mandate = resolved_mandate
                mandate_path = core["timeline"][mandate_index]["receipt_path"]
                if mandate_path in receipt_errors:
                    authority_failed = True
                    errors.append(
                        f"{current_path}: mandate_ref "
                        "does not resolve to an accepted attestation"
                    )
        if action_type in {"incident.statement", "incident.handoff"}:
            if subject["incident_id"] != core["incident_id"]:
                timeline_failed = True
                errors.append(
                    f"{current_path}: incident_id does not match packet-core"
                )
        if action_type == "capability.observe":
            if mandate is not None and subject["run_id"] != mandate["action"]["subject"]["run_id"]:
                authority_failed = True
                errors.append(
                    f"{current_path}: run_id does not "
                    "equal the referenced mandate run_id"
                )
            parent = by_attestation.get(subject["decision_attestation"])
            if parent is None or parent[0] >= index:
                lineage_failed = True
                errors.append(
                    f"{current_path}: decision parent is missing or not prior"
                )
            else:
                parent_subject = parent[1]["action"]["subject"]
                parent_path = core["timeline"][parent[0]]["receipt_path"]
                if (
                    parent[1]["action"]["type"] != "capability.decide"
                    or parent_path in receipt_errors
                    or parent_subject["run_id"] != subject["run_id"]
                    or parent_subject["request_id"] != subject["request_id"]
                    or parent_subject["decision"] != "PERMIT"
                    or parent_subject["protocol"] != subject["protocol"]
                    or parent_subject["mandate_ref"] != subject["mandate_ref"]
                ):
                    lineage_failed = True
                    if (
                        parent_subject.get("mandate_ref") != subject["mandate_ref"]
                        or parent_path in receipt_errors
                    ):
                        authority_failed = True
                    errors.append(
                        f"{current_path}: observation parent does not bind a "
                        "permitted matching request"
                    )
        elif action_type == "capability.decide":
            if mandate is not None:
                mandate_subject = mandate["action"]["subject"]
                if subject["run_id"] != mandate_subject["run_id"]:
                    authority_failed = True
                    errors.append(
                        f"{current_path}: run_id does "
                        "not equal the referenced mandate run_id"
                    )
                if subject["policy_digest"] != mandate_subject["policy_digest"]:
                    authority_failed = True
                    errors.append(
                        f"{current_path}: policy_digest "
                        "does not equal the referenced mandate policy_digest"
                    )
                claimed = _parse_instant(
                    document["claimed_at"],
                    f"{current_path}.claimed_at",
                )
                if not (
                    _parse_instant(
                        mandate_subject["valid_from"],
                        "eval.run.authorize.valid_from",
                    )
                    <= claimed
                    <= _parse_instant(
                        mandate_subject["valid_until"],
                        "eval.run.authorize.valid_until",
                    )
                ):
                    authority_failed = True
                    errors.append(
                        f"{current_path}: authenticated "
                        "decision claimed_at is outside the mandate's claimed "
                        "validity interval"
                    )
        elif action_type == "trajectory.decide":
            lineage = subject["ordered_lineage"]
            expected_members = [
                (prior_index, prior)
                for prior_index, prior in enumerate(ordered[:index])
                if prior is not None
                and prior["action"]["type"] in {
                    "capability.decide", "capability.observe"
                }
                and isinstance(prior["action"]["subject"], Mapping)
                and prior["action"]["subject"].get("run_id") == subject["run_id"]
            ]
            expected = [
                prior["hashes"]["attestation"]
                for _, prior in expected_members
            ]
            invalid_lineage_member = any(
                core["timeline"][prior_index]["receipt_path"] in receipt_errors
                for prior_index, _ in expected_members
            )
            if lineage != expected or invalid_lineage_member:
                lineage_failed = True
                errors.append(
                    f"{current_path}: ordered_lineage "
                    "does not equal the exact accepted prior capability lineage"
                )
            if mandate is not None:
                mandate_subject = mandate["action"]["subject"]
                if subject["run_id"] != mandate_subject["run_id"]:
                    authority_failed = True
                    errors.append(
                        f"{current_path}: run_id does "
                        "not equal the referenced mandate run_id"
                    )
                if subject["policy_digest"] != mandate_subject["policy_digest"]:
                    authority_failed = True
                    errors.append(
                        f"{current_path}: policy_digest "
                        "does not equal the referenced mandate policy_digest"
                    )
        elif action_type == "incident.handoff":
            statement_refs = [
                item["attestation_hash"] for item in core["statements"]
            ]
            statement_indexes = [
                by_attestation[attestation][0]
                for attestation in statement_refs
                if attestation in by_attestation
            ]
            invalid_statement = any(
                core["timeline"][statement_index]["receipt_path"] in receipt_errors
                for statement_index in statement_indexes
            )
            if (
                subject["statement_refs"] != statement_refs
                or len(statement_indexes) != len(statement_refs)
                or any(statement_index >= index for statement_index in statement_indexes)
                or invalid_statement
            ):
                timeline_failed = True
                errors.append(
                    "incident.handoff statement_refs must equal the ordered "
                    "named prior statement attestations"
                )
            timeline_prefix = list(core["timeline"][:index])
            if subject["timeline_hash"] != canonical_hash(timeline_prefix):
                timeline_failed = True
                errors.append(
                    "incident.handoff timeline_hash does not recompute over the "
                    "ordered timeline prefix available before the handoff"
                )
            if subject["coverage_hash"] != coverage_commitment(core):
                timeline_failed = True
                errors.append("incident.handoff coverage_hash does not recompute")
            prior = [
                item["attestation_hash"]
                for item in core["timeline"][:index]
            ]
            if subject["parent_refs"] != prior:
                timeline_failed = True
                errors.append(
                    "incident.handoff parent_refs must equal the exact ordered "
                    "prior timeline attestations"
                )

    named_statement_hashes = {
        item["attestation_hash"] for item in core["statements"]
    }
    handoff_indexes = [
        index
        for index, document in enumerate(ordered)
        if document is not None
        and document["action"]["type"] == "incident.handoff"
    ]
    invalid_denominator_checkpoints: dict[
        tuple[str, str, str], tuple[str, ...]
    ] = {}
    for denominator_ref in core["denominators"]:
        key = _coverage_key(denominator_ref, "denominator")
        checkpoint_ref = denominator_ref["checkpoint_attestation"]
        checkpoint = by_attestation.get(checkpoint_ref)
        problems: list[str] = []
        if (
            checkpoint is None
            or checkpoint_ref not in named_statement_hashes
            or checkpoint[1]["action"]["type"] != "incident.statement"
        ):
            problems.append(
                "checkpoint_attestation must resolve to a named incident.statement"
            )
        else:
            checkpoint_index, checkpoint_document = checkpoint
            checkpoint_path = core["timeline"][checkpoint_index]["receipt_path"]
            if checkpoint_path in receipt_errors:
                problems.append(
                    "denominator checkpoint receipt did not pass accepted "
                    "attestation verification"
                )
            else:
                subject = checkpoint_document["action"]["subject"]
                expected_role = (
                    "target"
                    if key[1] == "effect"
                    else "gateway"
                    if key[2] == "http"
                    else "boundary"
                )
                if subject["issuer_role"] != expected_role:
                    problems.append(
                        "denominator checkpoint issuer_role must match phase "
                        f"topology ({expected_role!r})"
                    )
                expected_topic = denominator_checkpoint_topic(*key)
                if subject["topic"] != expected_topic:
                    problems.append(
                        "denominator checkpoint topic must equal "
                        f"{expected_topic!r}"
                    )
                if subject["epistemic_status"] != "observed":
                    problems.append(
                        "denominator checkpoint epistemic_status must be "
                        "'observed'"
                    )
                if subject["claim"] != "ORDERED_DENOMINATOR_SNAPSHOT":
                    problems.append(
                        "denominator checkpoint claim must be "
                        "'ORDERED_DENOMINATOR_SNAPSHOT'"
                    )
                artifact_hash = artifacts[
                    denominator_ref["artifact_id"]
                ]["sha256"]
                if subject["evidence_refs"] != [artifact_hash]:
                    problems.append(
                        "denominator checkpoint evidence_refs must contain only "
                        "the exact denominator artifact byte SHA"
                    )
                top_level_evidence = checkpoint_document["evidence_refs"]
                if (
                    len(top_level_evidence) != 1
                    or top_level_evidence[0].get("hash") != artifact_hash
                    or top_level_evidence[0].get("grounding") != "self_asserted"
                ):
                    problems.append(
                        "denominator checkpoint ActionReceipt evidence must be "
                        "one self_asserted reference to the exact artifact byte "
                        "SHA"
                    )
                if any(
                    checkpoint_index >= handoff_index
                    for handoff_index in handoff_indexes
                ):
                    problems.append(
                        "denominator checkpoint must occur before every "
                        "incident.handoff"
                    )
        if problems:
            timeline_failed = True
            invalid_denominator_checkpoints[key] = tuple(problems)
            errors.extend(
                f"denominator checkpoint {key!r}: {problem}"
                for problem in problems
            )

    coverage_results, coverage_errors = _verify_coverage(
        packet,
        artifacts=artifacts,
        receipts=receipts,
        invalid_receipts=receipt_errors,
        invalid_denominator_checkpoints=invalid_denominator_checkpoints,
        limits=limits,
    )
    errors.extend(coverage_errors)
    computed = [item for item in coverage_results if item.status == "COMPUTED"]
    coverage_status = (
        "FAILED"
        if any(item.status == "FAILED" for item in coverage_results)
        else "NOT_COMPUTED"
        if any(item.status == "NOT_COMPUTED" for item in coverage_results)
        else "COMPUTED"
    )
    if any(item.uncovered_ids for item in computed):
        warnings.append("one or more denominator observations have no accepted covering receipt")
    if any(item.phantom_receipt_ids for item in computed):
        warnings.append("one or more receipt projections are absent from their denominator")
    if computed:
        warnings.append(
            "denominator checkpoints authenticate what the path-separated "
            "observer reported; they do not establish completeness or "
            "organizational independence"
        )

    redaction_failed = False
    statement_hashes = {item["attestation_hash"] for item in core["statements"]}
    for redaction in core["redactions"]:
        record_artifact = artifacts[redaction["record_artifact_id"]]
        source = artifacts[redaction["source_artifact_id"]]
        released = artifacts[redaction["released_artifact_id"]]
        try:
            record = _load_artifact_json(
                packet,
                artifacts,
                redaction["record_artifact_id"],
                limits,
            )
            _exact(
                record,
                {
                    "schema_version",
                    "redaction_id",
                    "source_sha256",
                    "released_sha256",
                    "tool",
                    "tool_version",
                    "rules_hash",
                    "disclosure_safety",
                },
                "redaction record",
            )
            if (
                isinstance(record["schema_version"], bool)
                or record["schema_version"] != 1
            ):
                raise IncidentPacketError(
                    "redaction record schema_version must be integer 1"
                )
            expected_record = {
                "schema_version": 1,
                "redaction_id": redaction["redaction_id"],
                "source_sha256": source["sha256"],
                "released_sha256": released["sha256"],
                "tool": redaction["tool"],
                "tool_version": redaction["tool_version"],
                "rules_hash": redaction["rules_hash"],
                "disclosure_safety": "NOT_COMPUTED",
            }
            if record != expected_record:
                raise IncidentPacketError(
                    "redaction record does not equal core and artifact bindings"
                )
        except IncidentPacketError as exc:
            redaction_failed = True
            errors.append(
                f"redaction {redaction['redaction_id']!r} record: {exc}"
            )
        if source["disclosure_state"] == "released":
            warnings.append(
                f"redaction {redaction['redaction_id']!r} source is public; profile permits this but does not infer safety"
            )
        if released["disclosure_state"] != "released":
            redaction_failed = True
            errors.append(
                f"redaction {redaction['redaction_id']!r} released artifact is unavailable"
            )
        if redaction["reviewer_statement_ref"] not in statement_hashes:
            redaction_failed = True
            errors.append(
                f"redaction {redaction['redaction_id']!r} reviewer statement does not resolve"
            )
        else:
            reviewer_item = next(
                item for item in core["statements"]
                if item["attestation_hash"]
                == redaction["reviewer_statement_ref"]
            )
            reviewer_document = receipts.get(reviewer_item["receipt_path"])
            reviewer_subject = (
                reviewer_document["action"]["subject"]
                if reviewer_document is not None
                else {}
            )
            record_hash = record_artifact["sha256"]
            reviewer_evidence = (
                reviewer_document["evidence_refs"]
                if reviewer_document is not None
                else []
            )
            if (
                reviewer_document is None
                or reviewer_item["receipt_path"] in receipt_errors
                or reviewer_subject.get("issuer_role") != "reviewer"
                or reviewer_subject.get("topic")
                != f"redaction:{redaction['redaction_id']}"
                or reviewer_subject.get("epistemic_status") != "observed"
                or reviewer_subject.get("claim") != "REDACTION_BINDING_REVIEWED"
                or reviewer_subject.get("evidence_refs") != [record_hash]
                or len(reviewer_evidence) != 1
                or reviewer_evidence[0].get("hash") != record_hash
                or reviewer_evidence[0].get("grounding") != "self_asserted"
            ):
                redaction_failed = True
                errors.append(
                    f"redaction {redaction['redaction_id']!r} reviewer statement "
                    "must be an accepted reviewer observation with closed claim "
                    "and sole self_asserted evidence binding the redaction record"
                )

    topics: dict[str, set[tuple[str, str]]] = {}
    for item in core["statements"]:
        if item["receipt_path"] in receipt_errors:
            continue
        document = receipts.get(item["receipt_path"])
        if document is None:
            continue
        subject = document["action"]["subject"]
        topics.setdefault(subject["topic"], set()).add(
            (subject["claim"], subject["epistemic_status"])
        )
    conflicts = tuple(sorted(topic for topic, claims in topics.items() if len(claims) > 1))
    if conflicts:
        warnings.append("conflicting party statements are preserved without adjudication")

    statement_indexes = {
        item["attestation_hash"]: by_attestation[item["attestation_hash"]][0]
        for item in core["statements"]
        if item["attestation_hash"] in by_attestation
        and item["receipt_path"] not in receipt_errors
    }
    released_packet_refs: set[str] = set()
    packet_reference_invalid = False
    for artifact in artifacts.values():
        if (
            artifact["role"] != "packet-core"
            or artifact["disclosure_state"] != "released"
        ):
            continue
        try:
            referenced_core = _load_json(
                packet.files[artifact["path"]],
                artifact["path"],
                limits,
            )
            _validate_packet_core(referenced_core)
            released_packet_refs.add(canonical_hash(referenced_core))
        except IncidentPacketError as exc:
            packet_reference_invalid = True
            errors.append(
                f"released packet-core artifact {artifact['artifact_id']!r} "
                f"is not a conforming packet core: {exc}"
            )

    corrections_by_ref: dict[tuple[str, str], set[str]] = {}
    correction_invalid = False
    for item in core["corrections"]:
        document = receipts.get(item["receipt_path"])
        if document is None or item["receipt_path"] in receipt_errors:
            correction_invalid = True
            errors.append(
                "correction receipt does not resolve to an accepted attestation"
            )
            continue
        subject = document["action"]["subject"]
        correction_index = by_attestation[document["hashes"]["attestation"]][0]
        kind = subject["supersedes_kind"]
        supersedes = subject["supersedes_ref"]
        replacement = subject["replacement_ref"]
        if kind == "statement":
            supersedes_index = statement_indexes.get(supersedes)
            replacement_index = statement_indexes.get(replacement)
            if supersedes_index is None or supersedes_index >= correction_index:
                correction_invalid = True
                errors.append(
                    "incident.correct statement supersedes_ref must resolve to "
                    "a prior named statement"
                )
            if replacement_index is None or replacement_index >= correction_index:
                correction_invalid = True
                errors.append(
                    "incident.correct statement replacement_ref must resolve to "
                    "a prior named statement"
                )
        else:
            predecessor_refs = set(released_packet_refs)
            if core["supersedes_core_hash"] is not None:
                predecessor_refs.add(core["supersedes_core_hash"])
            if supersedes not in predecessor_refs:
                correction_invalid = True
                errors.append(
                    "incident.correct packet supersedes_ref must resolve to "
                    "packet-core.supersedes_core_hash or a released packet-core artifact"
                )
            if replacement not in released_packet_refs:
                correction_invalid = True
                errors.append(
                    "incident.correct packet replacement_ref must resolve to "
                    "a released packet-core artifact"
                )
            if supersedes == replacement:
                correction_invalid = True
                errors.append(
                    "incident.correct packet supersedes_ref and replacement_ref "
                    "must differ"
                )
        corrections_by_ref.setdefault((kind, supersedes), set()).add(replacement)
    correction_invalid |= packet_reference_invalid
    correction_status = (
        "FAILED"
        if correction_invalid
        else "FORKED"
        if any(len(values) > 1 for values in corrections_by_ref.values())
        else "PRESENT"
        if corrections_by_ref
        else "NONE"
    )
    if correction_status == "FORKED":
        warnings.append("correction fork preserved; verifier does not select latest by actor time")

    temporal: dict[str, list[str]] = {
        "claimed_at": [
            document["claimed_at"]
            for index, document in enumerate(ordered)
            if document is not None
            and core["timeline"][index]["receipt_path"] not in receipt_errors
        ]
        + (
            [publish["claimed_at"]]
            if publish is not None and not publish_problems
            else []
        ),
        "received_at": [],
        "witnessed_at": [],
        "anchored_before": [],
    }
    witness_inclusion = "NOT_COMPUTED" if not core["witnesses"] else "VERIFIED"
    witness_trust = "NOT_COMPUTED" if not core["witnesses"] else "TRUSTED"
    timeline_attestations = {
        item["attestation_hash"] for item in core["timeline"]
    }
    for witness_ref in core["witnesses"]:
        witness = _load_artifact_json(packet, artifacts, witness_ref["artifact_id"], limits)
        if witness is None:
            witness_inclusion = "UNAVAILABLE"
            witness_trust = "UNAVAILABLE"
            continue
        try:
            _exact(
                witness,
                {
                    "schema_version", "witness_id", "root_ref", "received_at",
                    "witnessed_at", "anchored_before", "included_attestation_hashes",
                    "checkpoint_hash", "proof",
                },
                "witness",
            )
            if isinstance(witness["schema_version"], bool) or witness["schema_version"] != 1:
                raise IncidentPacketError("witness.schema_version must be 1")
            if witness["witness_id"] != witness_ref["witness_id"] or witness["root_ref"] != witness_ref["root_ref"]:
                raise IncidentPacketError("witness identity does not match packet-core")
            included = set(
                _string_list(
                    witness["included_attestation_hashes"],
                    "witness.included_attestation_hashes",
                    unique=True,
                )
            )
            for digest in included:
                _hash(digest, "witness.included_attestation_hashes[]")
            root_trusted = witness["root_ref"] in context.trusted_witness_roots
            if not root_trusted:
                witness_trust = "UNTRUSTED"
            received_at = _nonempty(witness["received_at"], "witness.received_at")
            witnessed_at = _nonempty(witness["witnessed_at"], "witness.witnessed_at")
            if _parse_instant(received_at, "witness.received_at") > _parse_instant(
                witnessed_at, "witness.witnessed_at"
            ):
                raise IncidentPacketError(
                    "witness.received_at must not follow witnessed_at"
                )
            checkpoint_preimage = {
                "witness_id": witness["witness_id"],
                "root_ref": witness["root_ref"],
                "received_at": received_at,
                "witnessed_at": witnessed_at,
                "included_attestation_hashes": witness[
                    "included_attestation_hashes"
                ],
            }
            expected_checkpoint = canonical_hash(checkpoint_preimage)
            if witness["checkpoint_hash"] != expected_checkpoint:
                raise IncidentPacketError("witness checkpoint_hash does not recompute")
            from bulla.identity import verify_proof_domain

            authenticity = verify_proof_domain(
                "witness-checkpoint",
                expected_checkpoint,
                witness["proof"],
                schema="0.4",
            )
            witness_issuer = (witness["proof"] or {}).get("issuer")
            if not authenticity.authentic:
                raise IncidentPacketError(
                    "witness checkpoint proof is not authentic: "
                    + (authenticity.detail or authenticity.method)
                )
            if witness_issuer != declared_roles.get("witness"):
                raise IncidentPacketError(
                    "witness checkpoint issuer does not equal packet-core witness role"
                )
            if witness_issuer not in context.accepted_issuers_by_role.get(
                "witness", frozenset()
            ):
                raise IncidentPacketError(
                    "witness checkpoint issuer is not accepted out of band"
                )
            if not timeline_attestations <= included:
                raise IncidentPacketError(
                    "witness does not include every timeline attestation"
                )
            if witness["anchored_before"] is not None:
                raise IncidentPacketError(
                    "anchored_before requires separate external anchor evidence; "
                    "this checkpoint schema does not carry one"
                )
            if not root_trusted:
                if witness_inclusion == "VERIFIED":
                    witness_inclusion = "NOT_COMPUTED"
                warnings.append(
                    "packet-carried witness root is not trusted out of band"
                )
                continue
            temporal["received_at"].append(received_at)
            temporal["witnessed_at"].append(witnessed_at)
        except IncidentPacketError as exc:
            witness_inclusion = "FAILED"
            errors.append(str(exc))

    packet_failed = publish_binding_failed
    hard_content_failure = packet_failed or artifact_failed or receipt_failed
    hard_failure = any(
        (
            hard_content_failure,
            signature_failed,
            role_failed,
            authority_failed,
            occurrence_failed,
            timeline_failed,
            lineage_failed,
            trace_failed,
            redaction_failed,
            coverage_status == "FAILED",
            witness_inclusion == "FAILED",
            correction_status == "FAILED",
        )
    )
    if hard_failure:
        suppressed.append("reliance")
    if hard_content_failure:
        suppressed.extend(
            [
                "disclosure_safety",
                "claims depending on failed packet, artifact, or receipt integrity",
            ]
        )
    if timeline_failed or lineage_failed:
        suppressed.append("timeline and lineage dependent conclusions")
    if coverage_status != "COMPUTED":
        suppressed.append("coverage conclusions")
    if redaction_failed:
        suppressed.append("redaction and disclosure conclusions")
    if core["witnesses"] and witness_inclusion != "VERIFIED":
        suppressed.append("witness-derived temporal labels")
    if correction_status == "FAILED":
        suppressed.append("correction resolution")
    return IncidentPacketVerification(
        packet_integrity="FAILED" if packet_failed else "VERIFIED",
        artifact_integrity="FAILED" if artifact_failed else "VERIFIED",
        receipt_integrity="FAILED" if receipt_failed else "VERIFIED",
        signature_status="FAILED" if signature_failed else "VERIFIED",
        issuer_authenticity="FAILED" if role_failed else "VERIFIED",
        authority_binding="FAILED" if authority_failed else "VERIFIED",
        occurrence_binding="FAILED" if occurrence_failed else "VERIFIED",
        timeline_integrity="FAILED" if timeline_failed else "VERIFIED",
        lineage_integrity="FAILED" if lineage_failed else "VERIFIED",
        trace_integrity=(
            "FAILED"
            if trace_failed
            else "VERIFIED"
            if released_trace_ids
            else "NOT_COMPUTED"
        ),
        redaction_binding="FAILED" if redaction_failed else "VERIFIED",
        coverage_status=coverage_status,
        denominator_provenance=(
            "SEPARATELY_CONTROLLED"
            if core["denominators"]
            and all(
                item["provenance"] == "SEPARATELY_CONTROLLED"
                for item in core["denominators"]
            )
            else "PATH_SEPARATE_TEAM_CONTROLLED"
            if core["denominators"]
            and all(
                item["provenance"] == "PATH_SEPARATE_TEAM_CONTROLLED"
                for item in core["denominators"]
            )
            else "MIXED_OR_UNKNOWN"
        ),
        witness_inclusion=witness_inclusion,
        witness_root_trust=witness_trust,
        temporal_evidence=MappingProxyType(
            {name: tuple(values) for name, values in temporal.items()}
        ),
        party_conflicts=conflicts,
        correction_status=correction_status,
        unavailable_artifacts=unavailable,
        suppressed_conclusions=tuple(suppressed),
        coverage=coverage_results,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


__all__: tuple[str, ...] = ()
