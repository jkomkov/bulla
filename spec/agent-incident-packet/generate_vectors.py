#!/usr/bin/env python3
"""Generate the frozen HTTP and MCP incident-packet vectors.

Run from ``bulla/``:

    PYTHONPATH=src .venv/bin/python spec/agent-incident-packet/generate_vectors.py
    PYTHONPATH=src .venv/bin/python spec/agent-incident-packet/generate_vectors.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.incident_packet import (
    PROFILE,
    IncidentVerificationContext,
    build_profile_receipt,
    build_publish_receipt,
    canonical_hash,
    coverage_commitment,
    verify_incident_packet,
)
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent
VECTORS = HERE / "vectors"
CONTEXTS = HERE / "contexts"
EXPECTED = HERE / "expected-verdict.json"
ROLE_NAMES = (
    "evaluation_authority",
    "gateway",
    "boundary",
    "target",
    "trajectory_monitor",
    "incident_commander",
    "affected_party",
    "reviewer",
    "publisher",
    "correction_authority",
    "witness",
)


def _json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _ndjson(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_text(value: str) -> str:
    return _hash_bytes(value.encode("utf-8"))


def _timeline_entry(
    sequence: int,
    path: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    receipt_bytes = _json(receipt)
    return {
        "sequence": sequence,
        "receipt_path": path,
        "byte_length": len(receipt_bytes),
        "sha256": _hash_bytes(receipt_bytes),
        "attestation_hash": receipt["hashes"]["attestation"],
        "action_type": receipt["action"]["type"],
    }


def _uuid4(label: str) -> str:
    raw = bytearray(hashlib.sha256(label.encode("utf-8")).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _signer(protocol: str, role: str) -> LocalEd25519Signer:
    seed = hashlib.sha256(f"glyph-incident-packet:{protocol}:{role}".encode()).digest()
    return LocalEd25519Signer(seed=seed)


def _envelope(issuer: str, action_type: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal=issuer,
            policy=f"policy://agent-incident-packet/{action_type}",
        ),
        bounds=Bounds(scope=f"profile:{PROFILE}"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/experimental/incident-packet/challenge",
                trusted_root_ref="sha256:" + "ab" * 32,
            ),
            remedies=(
                Remedy(
                    "challenge",
                    "publish a signed incident.correct receipt",
                    "forum:agent-incident-packet",
                ),
            ),
        ),
        retention_class="operational",
        disclosure_class="auditor",
    )


def _receipt(
    protocol: str,
    role: str,
    action_type: str,
    subject: dict[str, Any],
    index: int,
    *,
    evidence_refs: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    signer = _signer(protocol, role)
    return build_profile_receipt(
        action_type=action_type,
        subject={"issuer_role": role, **subject},
        signer=signer,
        envelope=_envelope(signer.issuer, action_type),
        event_id=_uuid4(f"{protocol}:{index}:{action_type}"),
        claimed_at=f"2026-07-26T12:{index:02d}:00Z",
        evidence_refs=evidence_refs,
        producer={"bulla_version": "source", "fixture": f"{protocol}-clean"},
    )


def _artifact(
    artifact_id: str,
    *,
    path: str | None,
    role: str,
    media_type: str,
    content: bytes,
    disclosure_state: str = "released",
    withheld_ref: str | None = None,
    access_condition: str = "public",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "path": path,
        "withheld_ref": withheld_ref,
        "role": role,
        "media_type": media_type,
        "byte_length": len(content),
        "sha256": _hash_bytes(content),
        "disclosure_state": disclosure_state,
        "retention": "P90D",
        "access_condition": access_condition,
    }


def _observation(
    *,
    anchor_id: str,
    observation_id: str,
    run_id: str,
    protocol: str,
    operation_ref: str,
    phase: str,
    evidence_hash: str,
) -> dict[str, str]:
    return {
        "anchor_id": anchor_id,
        "observation_id": observation_id,
        "run_id": run_id,
        "protocol": protocol,
        "operation_ref": operation_ref,
        "phase": phase,
        "evidence_hash": evidence_hash,
    }


def _make_packet(protocol: str) -> tuple[dict[str, bytes], dict[str, Any], dict[str, Any]]:
    run_id = f"run-{protocol}-synthetic-001"
    incident_id = f"incident-{protocol}-synthetic-001"
    packet_id = f"packet-{protocol}-synthetic-001"
    roles = [
        {"role": role, "issuer": _signer(protocol, role).issuer}
        for role in ROLE_NAMES
    ]
    role_issuers = {item["role"]: item["issuer"] for item in roles}
    gateway_anchor = f"{protocol}-gateway-ingress"
    effect_anchor = f"{protocol}-target-effects"
    decision_role = "gateway" if protocol == "http" else "boundary"

    request_permit_hash = _hash_text(f"{protocol}:fixed-benign-permitted-request")
    request_refuse_hash = _hash_text(f"{protocol}:fixed-benign-refused-request")
    decision_permit_id = f"{protocol}-decision-permit-001"
    decision_refuse_id = f"{protocol}-decision-refuse-001"
    decision_permit_evidence = _hash_text(f"{protocol}:gateway-ingress:permit")
    decision_refuse_evidence = _hash_text(f"{protocol}:gateway-ingress:refuse")
    mediated_observation_id = f"{protocol}-effect-mediated-001"
    bypass_observation_id = f"{protocol}-effect-bypass-001"
    mediated_effect_hash = _hash_text(f"{protocol}:fixed-benign-mediated-effect")
    bypass_effect_hash = _hash_text(f"{protocol}:fixed-benign-direct-effect")
    mediated_evidence_hash = _hash_text(f"{protocol}:target-log:mediated")
    bypass_evidence_hash = _hash_text(f"{protocol}:target-log:bypass")
    mandate_policy = _hash_text(f"{protocol}:evaluation-policy:v1")

    decision_observations = [
        _observation(
            anchor_id=gateway_anchor,
            observation_id=decision_permit_id,
            run_id=run_id,
            protocol=protocol,
            operation_ref=request_permit_hash,
            phase="decision",
            evidence_hash=decision_permit_evidence,
        ),
        _observation(
            anchor_id=gateway_anchor,
            observation_id=decision_refuse_id,
            run_id=run_id,
            protocol=protocol,
            operation_ref=request_refuse_hash,
            phase="decision",
            evidence_hash=decision_refuse_evidence,
        ),
    ]
    effect_observations = [
        _observation(
            anchor_id=effect_anchor,
            observation_id=mediated_observation_id,
            run_id=run_id,
            protocol=protocol,
            operation_ref=mediated_effect_hash,
            phase="effect",
            evidence_hash=mediated_evidence_hash,
        ),
        _observation(
            anchor_id=effect_anchor,
            observation_id=bypass_observation_id,
            run_id=run_id,
            protocol=protocol,
            operation_ref=bypass_effect_hash,
            phase="effect",
            evidence_hash=bypass_evidence_hash,
        ),
    ]
    denominator_decisions = {
        "schema_version": 1,
        "anchor_id": gateway_anchor,
        "phase": "decision",
        "protocol": protocol,
        "observations": decision_observations,
    }
    denominator_effects = {
        "schema_version": 1,
        "anchor_id": effect_anchor,
        "phase": "effect",
        "protocol": protocol,
        "observations": effect_observations,
    }
    denominator_decisions_bytes = _json(denominator_decisions)
    denominator_effects_bytes = _json(denominator_effects)
    coverage_decisions = {
        "schema_version": 1,
        "anchor_id": gateway_anchor,
        "phase": "decision",
        "protocol": protocol,
        "denominator_sha256": _hash_bytes(denominator_decisions_bytes),
        "minimum_verification_depth": "attestation",
        "total": 2,
        "receipted": 2,
        "covered_ids": [decision_permit_id, decision_refuse_id],
        "uncovered_ids": [],
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    coverage_effects = {
        "schema_version": 1,
        "anchor_id": effect_anchor,
        "phase": "effect",
        "protocol": protocol,
        "denominator_sha256": _hash_bytes(denominator_effects_bytes),
        "minimum_verification_depth": "attestation",
        "total": 2,
        "receipted": 1,
        "covered_ids": [mediated_observation_id],
        "uncovered_ids": [bypass_observation_id],
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    coverage_decisions_bytes = _json(coverage_decisions)
    coverage_effects_bytes = _json(coverage_effects)

    receipts: list[tuple[str, dict[str, Any]]] = []
    mandate = _receipt(
        protocol,
        "evaluation_authority",
        "eval.run.authorize",
        {
            "run_id": run_id,
            "model_id": "synthetic-model/1",
            "harness_id": f"localhost-{protocol}-pilot/1",
            "safeguards_id": "fixed-benign-boundary/1",
            "authorized_targets": [f"localhost://fixed-{protocol}-target"],
            "budgets": {
                "compute_units": 10,
                "max_actions": 3,
                "duration_seconds": 60,
            },
            "prohibitions": [
                "arbitrary-network",
                "credentials",
                "offensive-payloads",
            ],
            "valid_from": "2026-07-26T12:00:00Z",
            "valid_until": "2026-07-26T12:02:00Z",
            "policy_digest": mandate_policy,
            "role_issuers": role_issuers,
        },
        0,
    )
    receipts.append(("receipts/00-mandate.json", mandate))
    mandate_ref = mandate["hashes"]["attestation"]

    permit = _receipt(
        protocol,
        decision_role,
        "capability.decide",
        {
            "run_id": run_id,
            "request_id": f"{protocol}-request-permit-001",
            "decision_event_id": decision_permit_id,
            "request_hash": request_permit_hash,
            "mandate_ref": mandate_ref,
            "policy_digest": mandate_policy,
            "decision": "PERMIT",
            "rationale_codes": ["FIXED_OPERATION_ALLOWED"],
            "anchor_id": gateway_anchor,
            "protocol": protocol,
            "evidence_hash": decision_permit_evidence,
        },
        1,
        evidence_refs=(
            {
                "name": "gateway-ingress",
                "hash": decision_permit_evidence,
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(("receipts/01-decision-permit.json", permit))

    refused = _receipt(
        protocol,
        decision_role,
        "capability.decide",
        {
            "run_id": run_id,
            "request_id": f"{protocol}-request-refuse-001",
            "decision_event_id": decision_refuse_id,
            "request_hash": request_refuse_hash,
            "mandate_ref": mandate_ref,
            "policy_digest": mandate_policy,
            "decision": "REFUSE",
            "rationale_codes": ["FIXED_OPERATION_NOT_ALLOWED"],
            "anchor_id": gateway_anchor,
            "protocol": protocol,
            "evidence_hash": decision_refuse_evidence,
        },
        2,
        evidence_refs=(
            {
                "name": "gateway-ingress",
                "hash": decision_refuse_evidence,
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(("receipts/02-decision-refuse.json", refused))

    observation = _receipt(
        protocol,
        decision_role,
        "capability.observe",
        {
            "run_id": run_id,
            "request_id": f"{protocol}-request-permit-001",
            "decision_attestation": permit["hashes"]["attestation"],
            "observation_id": mediated_observation_id,
            "anchor_id": effect_anchor,
            "protocol": protocol,
            "effect_hash": mediated_effect_hash,
            "evidence_hash": mediated_evidence_hash,
            "evidence_refs": [mediated_evidence_hash],
            "observation_class": "EFFECT_OBSERVED",
            "transport_status": (
                "HTTP_204" if protocol == "http" else "JSONRPC_RESULT_RECORDED"
            ),
            "mandate_ref": mandate_ref,
        },
        3,
        evidence_refs=(
            {
                "name": "gateway-effect-observation",
                "hash": mediated_evidence_hash,
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(("receipts/03-observation-mediated.json", observation))

    capability_lineage = [
        permit["hashes"]["attestation"],
        refused["hashes"]["attestation"],
        observation["hashes"]["attestation"],
    ]
    trajectory = _receipt(
        protocol,
        "trajectory_monitor",
        "trajectory.decide",
        {
            "run_id": run_id,
            "mandate_ref": mandate_ref,
            "ordered_lineage": capability_lineage,
            "policy_digest": mandate_policy,
            "decision": "ESCALATE",
            "rationale_codes": ["DIRECT_EFFECT_WITHOUT_BOUNDARY_RECEIPT"],
            "requested_controls": ["preserve-evidence", "review-bypass"],
        },
        4,
    )
    receipts.append(("receipts/04-trajectory.json", trajectory))

    decision_checkpoint = _receipt(
        protocol,
        decision_role,
        "incident.statement",
        {
            "statement_id": f"{protocol}-denominator-decision-checkpoint",
            "incident_id": incident_id,
            "topic": f"denominator:{gateway_anchor}:decision:{protocol}",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_bytes(denominator_decisions_bytes)],
        },
        5,
        evidence_refs=(
            {
                "name": "denominator:exact-bytes",
                "hash": _hash_bytes(denominator_decisions_bytes),
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(
        ("receipts/05-denominator-decision-checkpoint.json", decision_checkpoint)
    )

    effect_checkpoint = _receipt(
        protocol,
        "target",
        "incident.statement",
        {
            "statement_id": f"{protocol}-denominator-effect-checkpoint",
            "incident_id": incident_id,
            "topic": f"denominator:{effect_anchor}:effect:{protocol}",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_bytes(denominator_effects_bytes)],
        },
        6,
        evidence_refs=(
            {
                "name": "denominator:exact-bytes",
                "hash": _hash_bytes(denominator_effects_bytes),
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(
        ("receipts/06-denominator-effect-checkpoint.json", effect_checkpoint)
    )

    denominator_refs = [
        {
            "anchor_id": gateway_anchor,
            "phase": "decision",
            "protocol": protocol,
            "artifact_id": "denominator-decisions",
            "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
            "checkpoint_attestation": decision_checkpoint["hashes"]["attestation"],
        },
        {
            "anchor_id": effect_anchor,
            "phase": "effect",
            "protocol": protocol,
            "artifact_id": "denominator-effects",
            "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
            "checkpoint_attestation": effect_checkpoint["hashes"]["attestation"],
        },
    ]

    coverage_refs = [
        {
            "anchor_id": gateway_anchor,
            "phase": "decision",
            "protocol": protocol,
            "artifact_id": "coverage-decisions",
            "minimum_verification_depth": "attestation",
        },
        {
            "anchor_id": effect_anchor,
            "phase": "effect",
            "protocol": protocol,
            "artifact_id": "coverage-effects",
            "minimum_verification_depth": "attestation",
        },
    ]

    observed_statement = _receipt(
        protocol,
        "incident_commander",
        "incident.statement",
        {
            "statement_id": f"{protocol}-statement-observed",
            "incident_id": incident_id,
            "topic": "effect-coverage",
            "claim": "One of two target-side effects has an accepted observation receipt.",
            "epistemic_status": "observed",
            "evidence_refs": [_hash_text(f"{protocol}:effect-coverage:1-of-2")],
        },
        7,
    )
    receipts.append(("receipts/07-statement-observed.json", observed_statement))

    unresolved_statement = _receipt(
        protocol,
        "affected_party",
        "incident.statement",
        {
            "statement_id": f"{protocol}-statement-unresolved",
            "incident_id": incident_id,
            "topic": "effect-coverage",
            "claim": "The cause of the unreceipted target-side effect remains unresolved.",
            "epistemic_status": "unresolved",
            "evidence_refs": [_hash_text(f"{protocol}:unresolved:bypass-cause")],
        },
        8,
    )
    receipts.append(("receipts/08-statement-unresolved.json", unresolved_statement))

    raw_trace = _ndjson(
        {
            "classification": "private-synthetic-source",
            "events": ["permit", "refuse", "mediated", "bypass"],
            "protocol": protocol,
        }
    )
    released_trace = _ndjson(
        {
            "classification": "released-synthetic",
            "events": ["permit", "refuse", "mediated", "bypass"],
            "protocol": protocol,
        }
    )
    redaction_record = {
        "schema_version": 1,
        "redaction_id": "redact-001",
        "source_sha256": _hash_bytes(raw_trace),
        "released_sha256": _hash_bytes(released_trace),
        "tool": "fixture-redactor",
        "tool_version": "1",
        "rules_hash": _hash_text("replace private with released"),
        "disclosure_safety": "NOT_COMPUTED",
    }
    redaction_bytes = _json(redaction_record)
    redaction_hash = _hash_bytes(redaction_bytes)

    reviewer_statement = _receipt(
        protocol,
        "reviewer",
        "incident.statement",
        {
            "statement_id": f"{protocol}-statement-redaction-review",
            "incident_id": incident_id,
            "topic": "redaction:redact-001",
            "claim": "REDACTION_BINDING_REVIEWED",
            "epistemic_status": "observed",
            "evidence_refs": [redaction_hash],
        },
        9,
        evidence_refs=(
            {
                "name": "redaction-record:exact-bytes",
                "hash": redaction_hash,
                "grounding": "self_asserted",
            },
        ),
    )
    receipts.append(("receipts/09-statement-redaction-review.json", reviewer_statement))

    statement_entries = [
        {
            "receipt_path": path,
            "attestation_hash": receipt["hashes"]["attestation"],
        }
        for path, receipt in receipts
        if receipt["action"]["type"] == "incident.statement"
    ]
    timeline_prefix = [
        _timeline_entry(index + 1, path, receipt)
        for index, (path, receipt) in enumerate(receipts)
    ]
    handoff = _receipt(
        protocol,
        "incident_commander",
        "incident.handoff",
        {
            "incident_id": incident_id,
            "recipient": _signer(protocol, "affected_party").issuer,
            "mandate_ref": mandate_ref,
            "timeline_hash": canonical_hash(timeline_prefix),
            "coverage_hash": coverage_commitment(
                {
                    "denominators": denominator_refs,
                    "coverage": coverage_refs,
                    "artifacts": [
                        {
                            "artifact_id": "denominator-decisions",
                            "sha256": _hash_bytes(denominator_decisions_bytes),
                        },
                        {
                            "artifact_id": "denominator-effects",
                            "sha256": _hash_bytes(denominator_effects_bytes),
                        },
                        {
                            "artifact_id": "coverage-decisions",
                            "sha256": _hash_bytes(coverage_decisions_bytes),
                        },
                        {
                            "artifact_id": "coverage-effects",
                            "sha256": _hash_bytes(coverage_effects_bytes),
                        },
                    ],
                }
            ),
            "statement_refs": [
                item["attestation_hash"] for item in statement_entries
            ],
            "parent_refs": [
                item["attestation_hash"] for item in timeline_prefix
            ],
            "requested_actions": ["review-unreceipted-effect", "preserve-denominator"],
            "disclosure_conditions": ["synthetic-only", "no-credentials"],
            "correction_channel": "https://glyphstandard.com/experimental/incident-packet/corrections",
            "challenge_channel": "https://glyphstandard.com/experimental/incident-packet/challenge",
        },
        10,
    )
    receipts.append(("receipts/10-handoff.json", handoff))

    correction = _receipt(
        protocol,
        "correction_authority",
        "incident.correct",
        {
            "correction_id": f"{protocol}-correction-001",
            "supersedes_kind": "statement",
            "supersedes_ref": unresolved_statement["hashes"]["attestation"],
            "replacement_ref": observed_statement["hashes"]["attestation"],
            "reason": "The later reviewed statement supplies the coverage fact; the causal inference remains unresolved.",
            "correction_channel": "https://glyphstandard.com/experimental/incident-packet/corrections",
        },
        11,
    )
    receipts.append(("receipts/11-correction.json", correction))

    timeline = [
        _timeline_entry(index + 1, path, receipt)
        for index, (path, receipt) in enumerate(receipts)
    ]
    witness_root = f"team-witness://{protocol}/checkpoint-001"
    checkpoint = {
        "witness_id": f"{protocol}-team-witness-001",
        "root_ref": witness_root,
        "received_at": "2026-07-26T12:12:00Z",
        "witnessed_at": "2026-07-26T12:13:00Z",
        "included_attestation_hashes": [
            item["attestation_hash"] for item in timeline
        ],
    }
    checkpoint_hash = canonical_hash(checkpoint)
    witness = {
        "schema_version": 1,
        **checkpoint,
        "anchored_before": None,
        "checkpoint_hash": checkpoint_hash,
        "proof": _signer(protocol, "witness").sign_domain(
            "witness-checkpoint", checkpoint_hash, schema="0.4"
        ),
    }
    witness_bytes = _json(witness)

    released_files: dict[str, bytes] = {
        f"indexes/{protocol}-gateway-ingress.json": denominator_decisions_bytes,
        f"indexes/{protocol}-target-effects.json": denominator_effects_bytes,
        f"coverage/{protocol}-decisions.json": coverage_decisions_bytes,
        f"coverage/{protocol}-effects.json": coverage_effects_bytes,
        f"traces/{protocol}-released.jsonl": released_trace,
        f"redactions/{protocol}-trace.json": redaction_bytes,
        f"witness/{protocol}-checkpoint.json": witness_bytes,
    }
    artifacts = [
        _artifact(
            "denominator-decisions",
            path=f"indexes/{protocol}-gateway-ingress.json",
            role="denominator",
            media_type="application/json",
            content=denominator_decisions_bytes,
        ),
        _artifact(
            "denominator-effects",
            path=f"indexes/{protocol}-target-effects.json",
            role="denominator",
            media_type="application/json",
            content=denominator_effects_bytes,
        ),
        _artifact(
            "coverage-decisions",
            path=f"coverage/{protocol}-decisions.json",
            role="coverage",
            media_type="application/json",
            content=coverage_decisions_bytes,
        ),
        _artifact(
            "coverage-effects",
            path=f"coverage/{protocol}-effects.json",
            role="coverage",
            media_type="application/json",
            content=coverage_effects_bytes,
        ),
        _artifact(
            "trace-released",
            path=f"traces/{protocol}-released.jsonl",
            role="trace",
            media_type="application/x-ndjson",
            content=released_trace,
        ),
        _artifact(
            "trace-source-controlled",
            path=None,
            role="trace",
            media_type="application/x-ndjson",
            content=raw_trace,
            disclosure_state="controlled",
            withheld_ref=f"controlled://{protocol}/trace-source",
            access_condition="incident-reviewers",
        ),
        _artifact(
            "redaction-record",
            path=f"redactions/{protocol}-trace.json",
            role="redaction",
            media_type="application/json",
            content=redaction_bytes,
        ),
        _artifact(
            "witness-checkpoint",
            path=f"witness/{protocol}-checkpoint.json",
            role="witness",
            media_type="application/json",
            content=witness_bytes,
        ),
    ]
    correction_entries = [
        {
            "receipt_path": "receipts/11-correction.json",
            "attestation_hash": correction["hashes"]["attestation"],
        }
    ]
    core = {
        "profile": PROFILE,
        "packet_id": packet_id,
        "incident_id": incident_id,
        "revision": 1,
        "classification": "synthetic-public",
        "supersedes_core_hash": None,
        "roles": roles,
        "timeline": timeline,
        "artifacts": artifacts,
        "denominators": denominator_refs,
        "coverage": coverage_refs,
        "redactions": [
            {
                "redaction_id": "redact-001",
                "record_artifact_id": "redaction-record",
                "source_artifact_id": "trace-source-controlled",
                "released_artifact_id": "trace-released",
                "tool": "fixture-redactor",
                "tool_version": "1",
                "rules_hash": redaction_record["rules_hash"],
                "reviewer_statement_ref": reviewer_statement["hashes"]["attestation"],
            }
        ],
        "statements": statement_entries,
        "witnesses": [
            {
                "witness_id": witness["witness_id"],
                "artifact_id": "witness-checkpoint",
                "root_ref": witness_root,
            }
        ],
        "corrections": correction_entries,
    }
    publisher = _signer(protocol, "publisher")
    publish = build_publish_receipt(
        core,
        signer=publisher,
        envelope=_envelope(publisher.issuer, "incident.packet.publish"),
        event_id=_uuid4(f"{protocol}:publish"),
        claimed_at="2026-07-26T12:14:00Z",
        producer={"bulla_version": "source", "fixture": f"{protocol}-clean"},
    )
    files = {
        "packet-core.json": _json(core),
        "publish-receipt.json": _json(publish),
        **{path: _json(receipt) for path, receipt in receipts},
        **released_files,
    }
    context = {
        "accepted_issuers_by_role": {
            role: [issuer] for role, issuer in role_issuers.items()
        },
        "trusted_witness_roots": [witness_root],
        "team_controlled_roles": list(ROLE_NAMES),
    }
    return files, context, core


def _write_packet(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in sorted(files.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _render() -> tuple[dict[str, dict[str, bytes]], dict[str, dict[str, Any]], dict[str, Any]]:
    packets: dict[str, dict[str, bytes]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    expected: dict[str, Any] = {"profile": PROFILE, "vectors": {}}
    for protocol in ("http", "mcp"):
        files, context, core = _make_packet(protocol)
        packets[f"{protocol}-clean"] = files
        contexts[f"{protocol}-context.json"] = context
        with tempfile.TemporaryDirectory(prefix=f"bulla-{protocol}-vector-") as directory:
            root = Path(directory)
            _write_packet(root, files)
            result = verify_incident_packet(
                root, IncidentVerificationContext.from_dict(context)
            ).to_dict()
        expected["vectors"][f"{protocol}-clean"] = {
            "packet_core_sha256": canonical_hash(core),
            "result": result,
        }
    return packets, contexts, expected


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _check(packets: dict[str, dict[str, bytes]], contexts: dict[str, dict[str, Any]], expected: dict[str, Any]) -> int:
    wanted: dict[str, bytes] = {}
    for name, files in packets.items():
        wanted.update({f"vectors/{name}/{path}": content for path, content in files.items()})
    wanted.update({f"contexts/{name}": _json(value) for name, value in contexts.items()})
    wanted["expected-verdict.json"] = _json(expected)
    actual = _snapshot(HERE)
    actual = {
        path: value
        for path, value in actual.items()
        if path.startswith(("vectors/", "contexts/")) or path == "expected-verdict.json"
    }
    if actual == wanted:
        print("agent incident packet vectors are byte-identical")
        return 0
    missing = sorted(set(wanted) - set(actual))
    extra = sorted(set(actual) - set(wanted))
    changed = sorted(path for path in set(actual) & set(wanted) if actual[path] != wanted[path])
    print(json.dumps({"missing": missing, "extra": extra, "changed": changed}, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    packets, contexts, expected = _render()
    if args.check:
        return _check(packets, contexts, expected)
    if VECTORS.exists():
        shutil.rmtree(VECTORS)
    if CONTEXTS.exists():
        shutil.rmtree(CONTEXTS)
    for name, files in packets.items():
        _write_packet(VECTORS / name, files)
    for name, value in contexts.items():
        path = CONTEXTS / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json(value))
    EXPECTED.write_bytes(_json(expected))
    print("wrote deterministic HTTP and MCP incident packet vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
