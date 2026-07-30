#!/usr/bin/env python3
"""Generate and byte-check the deterministic control-plane full-loop packet.

Run from ``bulla/``:

    PYTHONPATH=src .venv/bin/python spec/control-plane-alpha/generate_vectors.py
    PYTHONPATH=src .venv/bin/python spec/control-plane-alpha/generate_vectors.py --check
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.control_plane_alpha import (
    DEPLOYMENT_GATE,
    PROFILE as CONTROL_PROFILE,
    ControlPlaneVerificationContext,
    verify_control_plane_fixture,
)
from bulla.experimental.incident_packet import (
    PROFILE as INCIDENT_PROFILE,
    build_profile_receipt,
    build_publish_receipt,
    canonical_hash,
    coverage_commitment,
)
from bulla.experimental.checkpoint import issue_checkpoint
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


HERE = Path(__file__).resolve().parent
VECTOR_ID = "full-loop"
VECTOR_ROOT = HERE / "vectors" / VECTOR_ID
CONTEXT = HERE / "contexts" / "alpha-context.json"
INCIDENT_CONTEXT = HERE / "contexts" / "incident-context.json"
EXPECTED = HERE / "expected-verdict.json"
RUN_ID = "run-mcp-control-alpha-001"
INCIDENT_ID = "incident-mcp-control-alpha-001"
PACKET_ID = "packet-mcp-control-alpha-001"
POLICY_REF = "policy://bulla-control-plane-alpha/synthetic-authzen"
POLICY_DOCUMENT = {
    "profile": CONTROL_PROFILE,
    "policy_id": POLICY_REF,
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
POLICY_HASH = canonical_hash(POLICY_DOCUMENT)
ROLES = (
    "evaluation_authority",
    "boundary",
    "target",
    "incident_commander",
    "publisher",
    "witness",
)


def _json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_text(value: str) -> str:
    return _hash_bytes(value.encode("utf-8"))


def _uuid4(label: str) -> str:
    raw = bytearray(hashlib.sha256(label.encode("utf-8")).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(
        seed=hashlib.sha256(f"bulla-control-plane-alpha:{role}".encode()).digest()
    )


def _envelope(role: str, action_type: str) -> RecourseEnvelope:
    signer = _signer(role)
    return RecourseEnvelope(
        authority=Authority(
            principal=signer.issuer,
            policy=f"{POLICY_REF}@{POLICY_HASH}",
        ),
        bounds=Bounds(scope=f"profile:{INCIDENT_PROFILE};action:{action_type}"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://github.com/jkomkov/res-agentica/issues",
                trusted_root_ref="https://github.com/jkomkov/res-agentica",
            ),
            remedies=(
                Remedy(
                    "recompute",
                    "verify the deterministic incident packet",
                    "packet:packet-core.json",
                ),
                Remedy(
                    "challenge",
                    "publish a signed incident correction",
                    "forum:control-plane-alpha",
                ),
            ),
        ),
        retention_class="operational",
        disclosure_class="auditor",
    )


def _packet_receipt(
    role: str,
    action_type: str,
    subject: dict[str, Any],
    index: int,
    *,
    evidence_hash: str | None = None,
    evidence_name: str = "exact-evidence",
    evidence_grounding: str | None = None,
) -> dict[str, Any]:
    evidence = ()
    if evidence_hash is not None:
        grounding = evidence_grounding or (
            "counterparty_signed"
            if action_type == "capability.observe" and role == "target"
            else "self_asserted"
        )
        evidence = (
            {
                "name": evidence_name,
                "hash": evidence_hash,
                "grounding": grounding,
            },
        )
    return build_profile_receipt(
        action_type=action_type,
        subject={"issuer_role": role, **subject},
        signer=_signer(role),
        envelope=_envelope(role, action_type),
        event_id=_uuid4(f"{RUN_ID}:{index}:{action_type}"),
        claimed_at=f"2026-07-26T12:{index:02d}:00Z",
        evidence_refs=evidence,
        producer={
            "bulla_version": "source",
            "fixture": VECTOR_ID,
            "outer_profile": CONTROL_PROFILE,
        },
    )


def _timeline_entry(
    sequence: int, path: str, receipt: dict[str, Any]
) -> dict[str, Any]:
    content = _json(receipt)
    return {
        "sequence": sequence,
        "receipt_path": path,
        "byte_length": len(content),
        "sha256": _hash_bytes(content),
        "attestation_hash": receipt["hashes"]["attestation"],
        "action_type": receipt["action"]["type"],
    }


def _artifact(
    artifact_id: str,
    path: str,
    role: str,
    content: bytes,
    *,
    media_type: str = "application/json",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "path": path,
        "withheld_ref": None,
        "role": role,
        "media_type": media_type,
        "byte_length": len(content),
        "sha256": _hash_bytes(content),
        "disclosure_state": "released",
        "retention": "P180D",
        "access_condition": "public-synthetic",
    }


def _service_witness(
    receipts: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    log_id = "deedlog://control-plane-alpha/service"
    deeds = [
        {
            "issuer": receipt["signature"]["issuer"],
            "content_hash": receipt["hashes"]["content"],
            "attestation_hash": receipt["hashes"]["attestation"],
        }
        for _, receipt in receipts
    ]
    with tempfile.TemporaryDirectory(prefix="bulla-control-deedlog-") as directory:
        log = DeedLog(Path(directory) / "deeds.jsonl")
        for deed in deeds[:3]:
            log.append(Deed(**deed))
        prefix = issue_checkpoint(
            log,
            _signer("witness"),
            log_id=log_id,
            issued_at="2026-07-26T12:07:00Z",
        )
        for deed in deeds[3:]:
            log.append(Deed(**deed))
        final = issue_checkpoint(
            log,
            _signer("witness"),
            log_id=log_id,
            previous=prefix,
            issued_at="2026-07-26T12:08:00Z",
        )
        consistency = log.consistency(prefix.tree_size)
        inclusion = log.inclusion_by_attestation(
            receipts[1][1]["hashes"]["attestation"]
        )
    files: dict[str, bytes] = {
        "witness/service/deeds.json": _json(
            {
                "schema_version": 1,
                "profile": CONTROL_PROFILE,
                "log_id": log_id,
                "records": deeds,
            }
        ),
        "witness/service/checkpoint-prefix.json": _json(prefix.to_dict()),
        "witness/service/checkpoint-final.json": _json(final.to_dict()),
        "witness/service/consistency.json": _json(consistency),
        "witness/service/inclusion-permit.json": _json(inclusion),
    }
    retained_records: list[dict[str, Any]] = []
    archive_records: list[dict[str, str]] = []
    for sequence, (source_path, receipt) in enumerate(receipts, start=1):
        content = _json(receipt)
        retained_records.append(
            {
                "source_path": source_path,
                "archive_sequence": sequence,
                "byte_length": len(content),
                "sha256": _hash_bytes(content),
            }
        )
        archive_records.append(
            {
                "source_path": source_path,
                "bytes_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    files["witness/service/retained-receipts.json"] = _json(
        {
            "schema_version": 1,
            "profile": CONTROL_PROFILE,
            "encoding": "base64",
            "records": archive_records,
        }
    )
    files["witness/service/retained-index.json"] = _json(
        {
            "schema_version": 1,
            "profile": CONTROL_PROFILE,
            "records": retained_records,
        }
    )
    metadata = {
        "log_id": log_id,
        "operator_role": "witness",
        "deeds_path": "witness/service/deeds.json",
        "prefix_checkpoint_path": "witness/service/checkpoint-prefix.json",
        "final_checkpoint_path": "witness/service/checkpoint-final.json",
        "consistency_path": "witness/service/consistency.json",
        "inclusion_path": "witness/service/inclusion-permit.json",
        "retained_index_path": "witness/service/retained-index.json",
        "retained_archive_path": "witness/service/retained-receipts.json",
        "artifacts": [
            {"path": path, "sha256": _hash_bytes(content)}
            for path, content in sorted(files.items())
        ],
        "final_checkpoint_hash": final.checkpoint_hash,
    }
    return files, metadata


def _request(
    request_id: str,
    value: str,
    valid_from: str,
    valid_until: str,
) -> dict[str, Any]:
    return {
        "subject": {
            "type": "principal",
            "id": "agent:synthetic-alpha",
        },
        "action": {"name": "tools/call"},
        "resource": {
            "type": "mcp-tool",
            "id": "mcp://synthetic-receiver/tools/sandbox.append",
        },
        "context": {
            "profile": CONTROL_PROFILE,
            "run_id": RUN_ID,
            "request_id": request_id,
            "tool_name": "sandbox.append",
            "audience": "synthetic-receiver",
            "pdp_id": "bulla-closed-authzen/1",
            "policy_digest": POLICY_HASH,
            "arguments_hash": canonical_hash({"value": value}),
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
    }


def _decision(
    request: dict[str, Any],
    decision_id: str,
    sequence: int,
    permitted: bool,
) -> dict[str, Any]:
    return {
        "decision": permitted,
        "context": {
            "profile": CONTROL_PROFILE,
            "run_id": RUN_ID,
            "request_id": request["context"]["request_id"],
            "decision_id": decision_id,
            "request_hash": canonical_hash(request),
            "sequence": sequence,
            "reason_code": (
                "FIXED_OPERATION_ALLOWED"
                if permitted
                else "CONFIGURED_REFUSAL"
            ),
            "policy_ref": POLICY_REF,
            "policy_hash": POLICY_HASH,
            "policy_digest": POLICY_HASH,
            "pdp_id": "bulla-closed-authzen/1",
            "audience": "synthetic-receiver",
            "tool_name": "sandbox.append",
            "arguments_hash": request["context"]["arguments_hash"],
            "valid_until": request["context"]["valid_until"],
        },
    }


def _build() -> tuple[dict[str, bytes], dict[str, Any], dict[str, Any]]:
    role_issuers = {role: _signer(role).issuer for role in ROLES}
    decision_anchor = "mcp-boundary-ingress"
    effect_anchor = "mcp-target-effects"

    permit_request = _request(
        "request-permit-001",
        "alpha-mediated",
        "2026-07-26T12:01:00Z",
        "2026-07-26T12:02:00Z",
    )
    refuse_request = _request(
        "request-refuse-001",
        "alpha-refused",
        "2026-07-26T12:02:00Z",
        "2026-07-26T12:03:00Z",
    )
    permit_decision = _decision(
        permit_request, "decision-permit-001", 1, True
    )
    refuse_decision = _decision(
        refuse_request, "decision-refuse-001", 2, False
    )
    decision_permit_evidence = canonical_hash(permit_decision)
    decision_refuse_evidence = canonical_hash(refuse_decision)
    mediated_effect_hash = _hash_text(
        "mcp:synthetic-receiver:sandbox.append:alpha-mediated"
    )
    bypass_effect_hash = _hash_text(
        "direct:synthetic-backend:sandbox.append:alpha-direct-bypass"
    )
    mediated_effect_evidence = _hash_text("mcp:target-log:mediated")
    bypass_effect_evidence = _hash_text("mcp:target-log:bypass")

    decision_denominator = {
        "schema_version": 1,
        "anchor_id": decision_anchor,
        "phase": "decision",
        "protocol": "mcp",
        "observations": [
            {
                "anchor_id": decision_anchor,
                "observation_id": permit_decision["context"]["decision_id"],
                "run_id": RUN_ID,
                "protocol": "mcp",
                "operation_ref": canonical_hash(permit_request),
                "phase": "decision",
                "evidence_hash": decision_permit_evidence,
            },
            {
                "anchor_id": decision_anchor,
                "observation_id": refuse_decision["context"]["decision_id"],
                "run_id": RUN_ID,
                "protocol": "mcp",
                "operation_ref": canonical_hash(refuse_request),
                "phase": "decision",
                "evidence_hash": decision_refuse_evidence,
            },
        ],
    }
    effect_denominator = {
        "schema_version": 1,
        "anchor_id": effect_anchor,
        "phase": "effect",
        "protocol": "mcp",
        "observations": [
            {
                "anchor_id": effect_anchor,
                "observation_id": "effect-mediated-001",
                "run_id": RUN_ID,
                "protocol": "mcp",
                "operation_ref": mediated_effect_hash,
                "phase": "effect",
                "evidence_hash": mediated_effect_evidence,
            },
            {
                "anchor_id": effect_anchor,
                "observation_id": "effect-bypass-001",
                "run_id": RUN_ID,
                "protocol": "mcp",
                "operation_ref": bypass_effect_hash,
                "phase": "effect",
                "evidence_hash": bypass_effect_evidence,
            },
        ],
    }
    decision_denominator_bytes = _json(decision_denominator)
    effect_denominator_bytes = _json(effect_denominator)
    decision_coverage = {
        "schema_version": 1,
        "anchor_id": decision_anchor,
        "phase": "decision",
        "protocol": "mcp",
        "denominator_sha256": _hash_bytes(decision_denominator_bytes),
        "minimum_verification_depth": "attestation",
        "total": 2,
        "receipted": 2,
        "covered_ids": ["decision-permit-001", "decision-refuse-001"],
        "uncovered_ids": [],
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    effect_coverage = {
        "schema_version": 1,
        "anchor_id": effect_anchor,
        "phase": "effect",
        "protocol": "mcp",
        "denominator_sha256": _hash_bytes(effect_denominator_bytes),
        "minimum_verification_depth": "attestation",
        "total": 2,
        "receipted": 1,
        "covered_ids": ["effect-mediated-001"],
        "uncovered_ids": ["effect-bypass-001"],
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    decision_coverage_bytes = _json(decision_coverage)
    effect_coverage_bytes = _json(effect_coverage)

    receipts: list[tuple[str, dict[str, Any]]] = []
    mandate = _packet_receipt(
        "evaluation_authority",
        "eval.run.authorize",
        {
            "run_id": RUN_ID,
            "model_id": "synthetic-model/1",
            "harness_id": "bulla-control-plane-alpha/mcp-sandbox/1",
            "safeguards_id": "fixed-benign-boundary/1",
            "authorized_targets": [
                "mcp://synthetic-receiver/tools/sandbox.append"
            ],
            "budgets": {
                "compute_units": 10,
                "max_actions": 3,
                "duration_seconds": 600,
            },
            "prohibitions": [
                "arbitrary-paths",
                "arbitrary-network",
                "commands",
                "credentials",
                "destructive-host-operations",
            ],
            "valid_from": "2026-07-26T12:00:00Z",
            "valid_until": "2026-07-26T12:10:00Z",
            "policy_digest": POLICY_HASH,
            "role_issuers": role_issuers,
        },
        0,
    )
    receipts.append(("receipts/00-mandate.json", mandate))
    mandate_ref = mandate["hashes"]["attestation"]

    permit_receipt = _packet_receipt(
        "boundary",
        "capability.decide",
        {
            "run_id": RUN_ID,
            "request_id": permit_request["context"]["request_id"],
            "decision_event_id": permit_decision["context"]["decision_id"],
            "request_hash": canonical_hash(permit_request),
            "mandate_ref": mandate_ref,
            "policy_digest": POLICY_HASH,
            "decision": "PERMIT",
            "rationale_codes": [permit_decision["context"]["reason_code"]],
            "anchor_id": decision_anchor,
            "protocol": "mcp",
            "evidence_hash": decision_permit_evidence,
        },
        1,
        evidence_hash=decision_permit_evidence,
    )
    receipts.append(("receipts/01-decision-permit.json", permit_receipt))

    refuse_receipt = _packet_receipt(
        "boundary",
        "capability.decide",
        {
            "run_id": RUN_ID,
            "request_id": refuse_request["context"]["request_id"],
            "decision_event_id": refuse_decision["context"]["decision_id"],
            "request_hash": canonical_hash(refuse_request),
            "mandate_ref": mandate_ref,
            "policy_digest": POLICY_HASH,
            "decision": "REFUSE",
            "rationale_codes": [refuse_decision["context"]["reason_code"]],
            "anchor_id": decision_anchor,
            "protocol": "mcp",
            "evidence_hash": decision_refuse_evidence,
        },
        2,
        evidence_hash=decision_refuse_evidence,
    )
    receipts.append(("receipts/02-decision-refuse.json", refuse_receipt))

    observation_receipt = _packet_receipt(
        "boundary",
        "capability.observe",
        {
            "run_id": RUN_ID,
            "request_id": permit_request["context"]["request_id"],
            "decision_attestation": permit_receipt["hashes"]["attestation"],
            "observation_id": "effect-mediated-001",
            "anchor_id": effect_anchor,
            "protocol": "mcp",
            "effect_hash": mediated_effect_hash,
            "evidence_refs": [mediated_effect_evidence],
            "observation_class": "EFFECT_OBSERVED",
            "transport_status": "JSONRPC_RESULT_RECORDED",
            "mandate_ref": mandate_ref,
            "evidence_hash": mediated_effect_evidence,
        },
        3,
        evidence_hash=mediated_effect_evidence,
    )
    receipts.append(("receipts/03-observation-mediated.json", observation_receipt))

    decision_checkpoint = _packet_receipt(
        "boundary",
        "incident.statement",
        {
            "statement_id": "denominator-decision-checkpoint",
            "incident_id": INCIDENT_ID,
            "topic": f"denominator:{decision_anchor}:decision:mcp",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_bytes(decision_denominator_bytes)],
        },
        4,
        evidence_hash=_hash_bytes(decision_denominator_bytes),
    )
    receipts.append(
        ("receipts/04-denominator-decision-checkpoint.json", decision_checkpoint)
    )

    effect_checkpoint = _packet_receipt(
        "target",
        "incident.statement",
        {
            "statement_id": "denominator-effect-checkpoint",
            "incident_id": INCIDENT_ID,
            "topic": f"denominator:{effect_anchor}:effect:mcp",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_bytes(effect_denominator_bytes)],
        },
        5,
        evidence_hash=_hash_bytes(effect_denominator_bytes),
    )
    receipts.append(
        ("receipts/05-denominator-effect-checkpoint.json", effect_checkpoint)
    )

    denominator_refs = [
        {
            "anchor_id": decision_anchor,
            "phase": "decision",
            "protocol": "mcp",
            "artifact_id": "denominator-decisions",
            "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
            "checkpoint_attestation": decision_checkpoint["hashes"]["attestation"],
        },
        {
            "anchor_id": effect_anchor,
            "phase": "effect",
            "protocol": "mcp",
            "artifact_id": "denominator-effects",
            "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
            "checkpoint_attestation": effect_checkpoint["hashes"]["attestation"],
        },
    ]
    coverage_refs = [
        {
            "anchor_id": decision_anchor,
            "phase": "decision",
            "protocol": "mcp",
            "artifact_id": "coverage-decisions",
            "minimum_verification_depth": "attestation",
        },
        {
            "anchor_id": effect_anchor,
            "phase": "effect",
            "protocol": "mcp",
            "artifact_id": "coverage-effects",
            "minimum_verification_depth": "attestation",
        },
    ]
    coverage_hash = coverage_commitment(
        {
            "denominators": denominator_refs,
            "coverage": coverage_refs,
            "artifacts": [
                {
                    "artifact_id": "denominator-decisions",
                    "sha256": _hash_bytes(decision_denominator_bytes),
                },
                {
                    "artifact_id": "denominator-effects",
                    "sha256": _hash_bytes(effect_denominator_bytes),
                },
                {
                    "artifact_id": "coverage-decisions",
                    "sha256": _hash_bytes(decision_coverage_bytes),
                },
                {
                    "artifact_id": "coverage-effects",
                    "sha256": _hash_bytes(effect_coverage_bytes),
                },
            ],
        }
    )
    gap_statement = _packet_receipt(
        "incident_commander",
        "incident.statement",
        {
            "statement_id": "effect-coverage-gap-unresolved",
            "incident_id": INCIDENT_ID,
            "topic": "effect-coverage",
            "claim": "EFFECT_RECEIPT_GAP_CAUSE_UNRESOLVED",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_bytes(effect_coverage_bytes)],
        },
        6,
        evidence_hash=_hash_bytes(effect_coverage_bytes),
        evidence_name="recomputation:effect-coverage-report",
        evidence_grounding="execution_verified",
    )
    receipts.append(
        ("receipts/06-effect-coverage-gap-unresolved.json", gap_statement)
    )
    prefix = [
        _timeline_entry(index + 1, path, receipt)
        for index, (path, receipt) in enumerate(receipts)
    ]
    handoff = _packet_receipt(
        "incident_commander",
        "incident.handoff",
        {
            "incident_id": INCIDENT_ID,
            "recipient": role_issuers["publisher"],
            "mandate_ref": mandate_ref,
            "timeline_hash": canonical_hash(prefix),
            "coverage_hash": coverage_hash,
            "statement_refs": [
                decision_checkpoint["hashes"]["attestation"],
                effect_checkpoint["hashes"]["attestation"],
                gap_statement["hashes"]["attestation"],
            ],
            "parent_refs": [item["attestation_hash"] for item in prefix],
            "requested_actions": [
                "review-unreceipted-effect",
                "preserve-denominator",
            ],
            "disclosure_conditions": ["synthetic-only", "no-credentials"],
            "correction_channel": "https://github.com/jkomkov/res-agentica/issues",
            "challenge_channel": "https://github.com/jkomkov/res-agentica/issues",
        },
        7,
    )
    receipts.append(("receipts/07-handoff.json", handoff))
    timeline = [
        _timeline_entry(index + 1, path, receipt)
        for index, (path, receipt) in enumerate(receipts)
    ]

    standards_pin = {
        "schema_version": 1,
        "profile": CONTROL_PROFILE,
        "as_of": "2026-07-29",
        "standard": "Model Context Protocol",
        "target_protocol_revision": "2026-07-28",
        "source_status": "FINAL",
        "official_draft_url": (
            "https://github.com/modelcontextprotocol/modelcontextprotocol/"
            "blob/main/schema/draft/schema.json"
        ),
        "pinned_snapshot_url": (
            "https://raw.githubusercontent.com/modelcontextprotocol/"
            "modelcontextprotocol/271ecc9accafdd9b83a3c869fa67c22953b2af80/"
            "schema/2026-07-28/schema.json"
        ),
        "snapshot_commit": "271ecc9accafdd9b83a3c869fa67c22953b2af80",
        "snapshot_sha256": (
            "sha256:ef70b61f99b6d2e5e3b46863822eab08dff6a45bedc7a08914e0e5b133f40203"
        ),
        "final_schema_url": (
            "https://github.com/modelcontextprotocol/modelcontextprotocol/"
            "blob/271ecc9accafdd9b83a3c869fa67c22953b2af80/"
            "schema/2026-07-28/schema.json"
        ),
        "final_document_digest": (
            "sha256:ef70b61f99b6d2e5e3b46863822eab08dff6a45bedc7a08914e0e5b133f40203"
        ),
        "deployment_gate": DEPLOYMENT_GATE,
        "promotion_condition": (
            "The final MCP schema is pinned. Production remains blocked by the "
            "separate operational blocker ledger."
        ),
        "rc_comparison": {
            "snapshot_commit": "71e306956a4959c9655e5036be215d41986596e6",
            "snapshot_sha256": (
                "sha256:9281c4890630e2d1e61792fa23b4084c4ea360cd58519610cd050545ab7b8708"
            ),
            "added_definitions": [
                "SubscriptionsListenResultMetaObject",
                "SubscriptionsListenResultResponse",
            ],
            "removed_definitions": ["SubscriptionsListenResultMeta"],
            "changed_definitions": ["SubscriptionsListenResult"],
            "implemented_subset_impact": "NO_CHANGE_SUBSCRIPTIONS_LISTEN_RESULT_ONLY",
        },
        "authzen": {
            "standard": "OpenID AuthZEN Authorization API",
            "version": "1.0",
            "status": "FINAL",
            "published": "2026-01-11",
            "official_url": (
                "https://openid.net/specs/authorization-api-1_0.html"
            ),
            "document_sha256": (
                "sha256:f0ee89cc4a688f9f409dc323c8342579d74c45dc776a6b04a622ba9738de82bc"
            ),
            "context_note": (
                "The decision response context is permitted by Authorization "
                "API 1.0; keys inside it are specific to the Bulla profile."
            ),
        },
    }
    service_witness_files, service_witness = _service_witness(receipts)
    run_state = {
        "schema_version": 1,
        "profile": CONTROL_PROFILE,
        "vector_id": VECTOR_ID,
        "run_id": RUN_ID,
        "status": "FINALIZED",
        "mandate_receipt_path": "receipts/00-mandate.json",
        "transactions": [
            {
                "request_id": permit_request["context"]["request_id"],
                "decision_id": permit_decision["context"]["decision_id"],
                "request_path": "authzen/requests/01-permit.json",
                "decision_path": "authzen/decisions/02-permit.json",
                "decision_receipt_path": "receipts/01-decision-permit.json",
            },
            {
                "request_id": refuse_request["context"]["request_id"],
                "decision_id": refuse_decision["context"]["decision_id"],
                "request_path": "authzen/requests/03-refuse.json",
                "decision_path": "authzen/decisions/04-refuse.json",
                "decision_receipt_path": "receipts/02-decision-refuse.json",
            },
        ],
        "effect_receipt_paths": ["receipts/03-observation-mediated.json"],
        "decision_checkpoint_path": (
            "receipts/04-denominator-decision-checkpoint.json"
        ),
        "effect_checkpoint_path": "receipts/05-denominator-effect-checkpoint.json",
        "gap_statement_path": "receipts/06-effect-coverage-gap-unresolved.json",
        "handoff_receipt_path": "receipts/07-handoff.json",
        "transitions": [
            {
                "sequence": 1,
                "state": "CREATED",
                "evidence_ref": standards_pin["snapshot_sha256"],
            },
            {
                "sequence": 2,
                "state": "AUTHORIZED",
                "evidence_ref": mandate["hashes"]["attestation"],
            },
            {
                "sequence": 3,
                "state": "MEDIATED_EFFECT_RECORDED",
                "evidence_ref": observation_receipt["hashes"]["attestation"],
            },
            {
                "sequence": 4,
                "state": "BYPASS_RECORDED",
                "evidence_ref": _hash_bytes(effect_denominator_bytes),
            },
            {
                "sequence": 5,
                "state": "FINALIZED",
                "evidence_ref": service_witness["final_checkpoint_hash"],
            },
        ],
        "errors": [],
    }
    service_manifest = {
        "schema_version": 1,
        "profile": CONTROL_PROFILE,
        "vector_id": VECTOR_ID,
        "run_id": RUN_ID,
        "incident_packet_profile": INCIDENT_PROFILE,
        "packet_core_path": "packet-core.json",
        "publish_receipt_path": "publish-receipt.json",
        "run_state_path": "run-state.json",
        "standards_pin_path": "standards-pin.json",
        "deployment_gate": DEPLOYMENT_GATE,
        "production_status": "BLOCKED_NO_REMOTE_MUTATION_AUTHORIZED",
        "mcp_schema_snapshot_sha256": standards_pin["snapshot_sha256"],
        "service_witness": {
            key: value
            for key, value in service_witness.items()
            if key != "final_checkpoint_hash"
        },
    }
    released = {
        "service-manifest.json": _json(service_manifest),
        "run-state.json": _json(run_state),
        "standards-pin.json": _json(standards_pin),
        "authzen/requests/01-permit.json": _json(permit_request),
        "authzen/decisions/02-permit.json": _json(permit_decision),
        "authzen/requests/03-refuse.json": _json(refuse_request),
        "authzen/decisions/04-refuse.json": _json(refuse_decision),
        "indexes/decision-denominator.json": decision_denominator_bytes,
        "indexes/effect-denominator.json": effect_denominator_bytes,
        "coverage/decisions.json": decision_coverage_bytes,
        "coverage/effects.json": effect_coverage_bytes,
        **service_witness_files,
    }
    artifacts = [
        _artifact("service-manifest", "service-manifest.json", "control-plane-manifest", released["service-manifest.json"]),
        _artifact("run-state", "run-state.json", "control-plane-run-state", released["run-state.json"]),
        _artifact("standards-pin", "standards-pin.json", "standards-pin", released["standards-pin.json"]),
        _artifact("authzen-request-permit", "authzen/requests/01-permit.json", "authzen-request", released["authzen/requests/01-permit.json"]),
        _artifact("authzen-decision-permit", "authzen/decisions/02-permit.json", "authzen-decision", released["authzen/decisions/02-permit.json"]),
        _artifact("authzen-request-refuse", "authzen/requests/03-refuse.json", "authzen-request", released["authzen/requests/03-refuse.json"]),
        _artifact("authzen-decision-refuse", "authzen/decisions/04-refuse.json", "authzen-decision", released["authzen/decisions/04-refuse.json"]),
        _artifact("denominator-decisions", "indexes/decision-denominator.json", "denominator", decision_denominator_bytes),
        _artifact("denominator-effects", "indexes/effect-denominator.json", "denominator", effect_denominator_bytes),
        _artifact("coverage-decisions", "coverage/decisions.json", "coverage", decision_coverage_bytes),
        _artifact("coverage-effects", "coverage/effects.json", "coverage", effect_coverage_bytes),
    ]
    artifacts.extend(
        _artifact(
            "service-witness-" + path.replace("/", "-").removesuffix(".json"),
            path,
            "service-witness",
            content,
        )
        for path, content in sorted(service_witness_files.items())
    )

    witness_root = "witness://bulla-control-plane-alpha/public-alpha-v1"
    checkpoint_core = {
        "witness_id": "bulla-control-plane-alpha-witness",
        "root_ref": witness_root,
        "received_at": "2026-07-26T12:07:00Z",
        "witnessed_at": "2026-07-26T12:08:00Z",
        "included_attestation_hashes": [
            item["attestation_hash"] for item in timeline
        ],
    }
    checkpoint_hash = canonical_hash(checkpoint_core)
    witness = {
        "schema_version": 1,
        **checkpoint_core,
        "anchored_before": None,
        "checkpoint_hash": checkpoint_hash,
        "proof": _signer("witness").sign_domain(
            "witness-checkpoint", checkpoint_hash, schema="0.4"
        ),
    }
    witness_bytes = _json(witness)
    released["witness/checkpoint.json"] = witness_bytes
    artifacts.append(
        _artifact(
            "witness-checkpoint",
            "witness/checkpoint.json",
            "witness",
            witness_bytes,
        )
    )
    core = {
        "profile": INCIDENT_PROFILE,
        "packet_id": PACKET_ID,
        "incident_id": INCIDENT_ID,
        "revision": 1,
        "classification": "synthetic-public",
        "supersedes_core_hash": None,
        "roles": [
            {"role": role, "issuer": issuer}
            for role, issuer in role_issuers.items()
        ],
        "timeline": timeline,
        "artifacts": artifacts,
        "denominators": denominator_refs,
        "coverage": coverage_refs,
        "redactions": [],
        "statements": [
            {
                "receipt_path": (
                    "receipts/04-denominator-decision-checkpoint.json"
                ),
                "attestation_hash": decision_checkpoint["hashes"]["attestation"],
            },
            {
                "receipt_path": (
                    "receipts/05-denominator-effect-checkpoint.json"
                ),
                "attestation_hash": effect_checkpoint["hashes"]["attestation"],
            },
            {
                "receipt_path": (
                    "receipts/06-effect-coverage-gap-unresolved.json"
                ),
                "attestation_hash": gap_statement["hashes"]["attestation"],
            },
        ],
        "witnesses": [
            {
                "witness_id": witness["witness_id"],
                "artifact_id": "witness-checkpoint",
                "root_ref": witness_root,
            }
        ],
        "corrections": [],
    }
    publish = build_publish_receipt(
        core,
        signer=_signer("publisher"),
        envelope=_envelope("publisher", "incident.packet.publish"),
        event_id=_uuid4(f"{RUN_ID}:publish"),
        claimed_at="2026-07-26T12:09:00Z",
        producer={
            "bulla_version": "source",
            "fixture": VECTOR_ID,
            "outer_profile": CONTROL_PROFILE,
        },
    )
    files = {
        "packet-core.json": _json(core),
        "publish-receipt.json": _json(publish),
        **{path: _json(receipt) for path, receipt in receipts},
        **released,
    }
    context = {
        "profile": CONTROL_PROFILE,
        "accepted_issuers_by_role": {
            role: [issuer] for role, issuer in role_issuers.items()
        },
        "trusted_witness_roots": [witness_root],
        "team_controlled_roles": list(ROLES),
        "trusted_policy_hashes": [POLICY_HASH],
        "role_identities": {
            role: {
                "issuer": _signer(role).issuer,
                "verification_method": _signer(role).verification_method,
                "public_key_sha256": _hash_bytes(_signer(role).public_key),
            }
            for role in ROLES
        },
    }
    return files, context, core


def _write_packet(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in sorted(files.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _render() -> tuple[dict[str, bytes], bytes, bytes, bytes]:
    files, context, core = _build()
    with tempfile.TemporaryDirectory(prefix="bulla-control-alpha-") as directory:
        root = Path(directory)
        _write_packet(root, files)
        result = verify_control_plane_fixture(
            root, ControlPlaneVerificationContext.from_dict(context)
        )
    if result.exit_code != 0:
        raise RuntimeError(f"generated fixture does not verify: {result.to_dict()}")
    expected = {
        "profile": CONTROL_PROFILE,
        "vectors": {
            VECTOR_ID: {
                "packet_core_sha256": canonical_hash(core),
                "result": result.to_dict(),
            }
        },
    }
    incident_context = {
        key: context[key]
        for key in (
            "accepted_issuers_by_role",
            "team_controlled_roles",
            "trusted_witness_roots",
        )
    }
    return files, _json(context), _json(incident_context), _json(expected)


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _check(
    files: dict[str, bytes],
    context: bytes,
    incident_context: bytes,
    expected: bytes,
) -> int:
    wanted = {
        **{f"vectors/{VECTOR_ID}/{path}": content for path, content in files.items()},
        "contexts/alpha-context.json": context,
        "contexts/incident-context.json": incident_context,
        "expected-verdict.json": expected,
    }
    actual = {
        path: content
        for path, content in _snapshot(HERE).items()
        if path.startswith(("vectors/", "contexts/"))
        or path == "expected-verdict.json"
    }
    if actual == wanted:
        print("control-plane-alpha vectors are byte-identical")
        return 0
    print(
        json.dumps(
            {
                "missing": sorted(set(wanted) - set(actual)),
                "extra": sorted(set(actual) - set(wanted)),
                "changed": sorted(
                    path
                    for path in set(actual) & set(wanted)
                    if actual[path] != wanted[path]
                ),
            },
            indent=2,
        )
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files, context, incident_context, expected = _render()
    if args.check:
        return _check(files, context, incident_context, expected)
    vectors = HERE / "vectors"
    contexts = HERE / "contexts"
    if vectors.exists():
        shutil.rmtree(vectors)
    if contexts.exists():
        shutil.rmtree(contexts)
    _write_packet(VECTOR_ROOT, files)
    CONTEXT.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT.write_bytes(context)
    INCIDENT_CONTEXT.write_bytes(incident_context)
    EXPECTED.write_bytes(expected)
    print("wrote deterministic control-plane full-loop incident packet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
