"""Team-operated localhost pilots for the Agent Incident Packet profile.

These pilots exercise real process boundaries without exposing a general
network proxy, arbitrary MCP tool, credential, or host path.  They are
source-only experimental evidence.  The target/backend denominator is
path-separate from the receipting boundary but remains under the same project
control domain.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError
from urllib.parse import urlsplit

from bulla._canonical import canonical_jcs_int
from bulla.action_receipt import verify_receipt
from bulla.experimental.incident_packet import (
    IncidentVerificationContext,
    assemble_incident_packet,
    build_profile_receipt,
    denominator_checkpoint_topic,
    released_artifact_entry,
    verify_incident_packet,
)
from bulla.identity import LocalEd25519Signer
from bulla.wrap import operational_envelope


PROFILE = "glyph.agent-incident-packet/0.1-draft"
DENOMINATOR_PROVENANCE = "PATH_SEPARATE_TEAM_CONTROLLED"
_MCP_EVENT_KEY = "org.resagentica.bulla/observationId"
_MCP_REQUEST_KEY = "org.resagentica.bulla/requestId"


class _RejectRedirects(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_LOCAL_HTTP_OPENER = urllib_request.build_opener(
    urllib_request.ProxyHandler({}),
    _RejectRedirects(),
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number rejected: {value}")


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON member rejected: {key}")
        value[key] = item
    return value


def _strict_json_loads(value: bytes | str) -> Any:
    return json.loads(
        value,
        object_pairs_hook=_reject_duplicate_members,
        parse_constant=_reject_json_constant,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validity_window() -> tuple[str, str]:
    start = datetime.now(timezone.utc)
    end = start + timedelta(minutes=5)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _validated_loopback_port(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1024
        or value > 65535
    ):
        raise ValueError(
            f"{label} must be an explicit ephemeral TCP port from 1024 to 65535"
        )
    return value


def _validate_http_loopback_url(url: Any, *, allowed_path: str) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("HTTP pilot URL must be a non-empty string")
    if allowed_path not in {"/act", "/effect"}:
        raise ValueError("HTTP pilot allowed_path is outside the closed route set")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("HTTP pilot URL has an invalid authority or port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.netloc != f"127.0.0.1:{port}"
        or parsed.path != allowed_path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "HTTP pilot URL must use http://127.0.0.1:<port> with the exact "
            "allowed path and no credentials, query, or fragment"
        )
    _validated_loopback_port(port, "HTTP pilot URL port")
    return url


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(canonical_jcs_int(value).encode("utf-8"))


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
    finally:
        os.close(descriptor)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(payload)
    finally:
        os.close(descriptor)


def _append_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(payload)
    finally:
        os.close(descriptor)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        _strict_json_loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _new_output_root(output_dir: str | Path) -> Path:
    requested = Path(output_dir)
    if requested.exists() or requested.is_symlink():
        raise FileExistsError("pilot output directory must not already exist")
    if requested.name in {"", ".", ".."}:
        raise ValueError("pilot output directory must name a new child directory")
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir():
        raise NotADirectoryError(parent)
    root = parent / requested.name
    root.mkdir(mode=0o700, exist_ok=False)
    return root


def _make_receipt(
    *,
    action_type: str,
    subject: dict[str, Any],
    signer: LocalEd25519Signer,
    policy: str,
    event_id: str,
    evidence_refs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    envelope = operational_envelope(
        principal=signer.issuer,
        policy=policy,
        scope=f"{PROFILE}:{action_type}",
        forum_endpoint="local://incident-pilot/challenge",
        forum_root="team-operated:unanchored",
    )
    receipt = build_profile_receipt(
        action_type=action_type,
        subject=subject,
        signer=signer,
        envelope=envelope,
        event_id=event_id,
        claimed_at=_now(),
        evidence_refs=evidence_refs,
        producer={"profile": PROFILE, "pilot": "team-operated-localhost"},
    )
    verdict = verify_receipt(receipt)
    if not verdict.ok or verdict.verified_to != "attestation":
        raise RuntimeError(f"{action_type} did not verify to attestation")
    return receipt


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


@dataclass(frozen=True)
class PilotRun:
    protocol: str
    output_dir: Path
    run_id: str
    role_issuers: dict[str, str]
    decision_observations: tuple[dict[str, Any], ...]
    effect_observations: tuple[dict[str, Any], ...]
    receipts: tuple[dict[str, Any], ...]
    expected_decision_coverage: str = "2/2"
    expected_effect_coverage: str = "1/2"
    denominator_provenance: str = DENOMINATOR_PROVENANCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "classification": "team-operated isolated localhost runtime pilot",
            "protocol": self.protocol,
            "run_id": self.run_id,
            "role_issuers": dict(self.role_issuers),
            "decision_observations": list(self.decision_observations),
            "effect_observations": list(self.effect_observations),
            "receipts": list(self.receipts),
            "expected": {
                "decision_coverage": self.expected_decision_coverage,
                "effect_coverage": self.expected_effect_coverage,
                "denominator_provenance": self.denominator_provenance,
            },
        }


def _receipt_subject(receipt: dict[str, Any]) -> dict[str, Any]:
    return (receipt.get("action") or {}).get("subject") or {}


def _eligible_receipt_ids(
    run: PilotRun,
    *,
    phase: str,
    anchor_id: str,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    role = "gateway" if run.protocol == "http" else "boundary"
    accepted_issuer = run.role_issuers[role]
    expected_action = (
        "capability.decide" if phase == "decision" else "capability.observe"
    )
    member = "decision_event_id" if phase == "decision" else "observation_id"
    eligible: dict[str, dict[str, str]] = {}
    duplicated: set[str] = set()
    invalid: list[str] = []
    for receipt in run.receipts:
        action = receipt.get("action") or {}
        if action.get("type") != expected_action:
            continue
        subject = _receipt_subject(receipt)
        identifier = subject.get(member)
        label = identifier if isinstance(identifier, str) else "<missing-id>"
        verdict = verify_receipt(receipt)
        valid = (
            verdict.ok
            and verdict.verified_to == "attestation"
            and receipt.get("schema_version") == "0.4"
            and (receipt.get("signature") or {}).get("issuer") == accepted_issuer
            and subject.get("profile") == PROFILE
            and subject.get("run_id") == run.run_id
            and subject.get("protocol") == run.protocol
            and subject.get("anchor_id") == anchor_id
            and isinstance(identifier, str)
        )
        if not valid:
            invalid.append(label)
            continue
        if identifier in duplicated:
            invalid.append(label)
            continue
        if identifier in eligible:
            eligible.pop(identifier)
            duplicated.add(identifier)
            invalid.extend([label, label])
            continue
        if phase == "decision":
            projection = {
                "anchor_id": subject["anchor_id"],
                "observation_id": subject["decision_event_id"],
                "run_id": subject["run_id"],
                "protocol": run.protocol,
                "operation_ref": subject["request_hash"],
                "phase": "decision",
                "evidence_hash": subject["evidence_hash"],
            }
        else:
            projection = {
                "anchor_id": subject["anchor_id"],
                "observation_id": subject["observation_id"],
                "run_id": subject["run_id"],
                "protocol": run.protocol,
                "operation_ref": subject["effect_hash"],
                "phase": "effect",
                "evidence_hash": subject["evidence_hash"],
            }
        eligible[identifier] = projection
    return eligible, invalid


def _coverage_dimension(
    run: PilotRun,
    *,
    phase: str,
    observations: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    expected_anchor = (
        f"{run.protocol}-gateway-ingress"
        if phase == "decision" and run.protocol == "http"
        else "mcp-boundary-ingress"
        if phase == "decision"
        else f"{run.protocol}-target-effects"
        if run.protocol == "http"
        else "mcp-backend-effects"
    )
    identifiers = [row.get("observation_id") for row in observations]
    denominator_valid = (
        bool(observations)
        and len(identifiers) == len(set(identifiers))
        and all(isinstance(item, str) and item for item in identifiers)
        and all(
            row.get("anchor_id") == expected_anchor
            and row.get("run_id") == run.run_id
            and row.get("protocol") == run.protocol
            and row.get("phase") == phase
            for row in observations
        )
    )
    if not denominator_valid:
        return {
            "anchor_id": expected_anchor,
            "status": "NOT_COMPUTED",
            "covered": [],
            "uncovered": [],
            "coverage": "NOT_COMPUTED",
            "invalid_receipts": [],
            "limitation": "denominator IDs or anchor fields are invalid",
        }
    eligible, invalid = _eligible_receipt_ids(
        run, phase=phase, anchor_id=expected_anchor
    )
    ids = [item for item in identifiers if isinstance(item, str)]
    covered = [
        row["observation_id"]
        for row in observations
        if eligible.get(row["observation_id"]) == row
    ]
    for row in observations:
        identifier = row["observation_id"]
        if identifier in eligible and eligible[identifier] != row:
            invalid.append(identifier)
    return {
        "anchor_id": expected_anchor,
        "status": "COMPUTED",
        "covered": covered,
        "uncovered": [item for item in ids if item not in covered],
        "coverage": f"{len(covered)}/{len(ids)}",
        "invalid_receipts": invalid,
    }


def summarize_coverage(run: PilotRun) -> dict[str, Any]:
    return {
        "decision": _coverage_dimension(
            run,
            phase="decision",
            observations=run.decision_observations,
        ),
        "effect": _coverage_dimension(
            run,
            phase="effect",
            observations=run.effect_observations,
        ),
        "denominator_provenance": DENOMINATOR_PROVENANCE,
    }


def _runtime_timeline(
    receipts: tuple[dict[str, Any], ...],
) -> list[tuple[str, dict[str, Any]]]:
    mandates = [
        item
        for item in receipts
        if (item.get("action") or {}).get("type") == "eval.run.authorize"
    ]
    decisions = [
        item
        for item in receipts
        if (item.get("action") or {}).get("type") == "capability.decide"
    ]
    observations = [
        item
        for item in receipts
        if (item.get("action") or {}).get("type") == "capability.observe"
    ]
    if len(mandates) != 1 or len(decisions) != 2 or len(observations) != 1:
        raise RuntimeError("runtime pilot did not produce the closed receipt sequence")
    decisions.sort(
        key=lambda item: 0
        if _receipt_subject(item).get("decision") == "PERMIT"
        else 1
    )
    ordered = mandates + decisions + observations
    labels = ("00-mandate", "01-decision-permit", "02-decision-refuse", "03-observe")
    return [
        (f"receipts/{label}.json", receipt)
        for label, receipt in zip(labels, ordered, strict=True)
    ]


def _finalize_runtime_packet(
    run: PilotRun,
    *,
    publisher: LocalEd25519Signer,
    decision_observer: LocalEd25519Signer,
    effect_observer: LocalEd25519Signer,
    path_results: dict[str, Any],
    extra_trace: bytes | None = None,
) -> dict[str, Any]:
    coverage = summarize_coverage(run)
    if (
        coverage["decision"]["status"] != "COMPUTED"
        or coverage["effect"]["status"] != "COMPUTED"
    ):
        raise RuntimeError("runtime denominator is not computable")
    protocol = run.protocol
    decision_anchor = coverage["decision"]["anchor_id"]
    effect_anchor = coverage["effect"]["anchor_id"]
    decision_document = {
        "schema_version": 1,
        "anchor_id": decision_anchor,
        "phase": "decision",
        "protocol": protocol,
        "observations": list(run.decision_observations),
    }
    effect_document = {
        "schema_version": 1,
        "anchor_id": effect_anchor,
        "phase": "effect",
        "protocol": protocol,
        "observations": list(run.effect_observations),
    }
    decision_bytes = _json_bytes(decision_document)
    effect_bytes = _json_bytes(effect_document)

    def report(dimension: dict[str, Any], denominator: bytes) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "anchor_id": dimension["anchor_id"],
            "phase": (
                "decision" if dimension is coverage["decision"] else "effect"
            ),
            "protocol": protocol,
            "denominator_sha256": _sha256_bytes(denominator),
            "minimum_verification_depth": "attestation",
            "total": len(dimension["covered"]) + len(dimension["uncovered"]),
            "receipted": len(dimension["covered"]),
            "covered_ids": dimension["covered"],
            "uncovered_ids": dimension["uncovered"],
            "phantom_receipt_ids": [],
            "invalid_receipts": dimension["invalid_receipts"],
        }

    decision_report = report(coverage["decision"], decision_bytes)
    effect_report = report(coverage["effect"], effect_bytes)
    decision_report_bytes = _json_bytes(decision_report)
    effect_report_bytes = _json_bytes(effect_report)
    trace_document = {
        "schema_version": 1,
        "profile": PROFILE,
        "run_id": run.run_id,
        "protocol": protocol,
        "adapter": {
            "id": f"bulla-localhost-{protocol}-fixed",
            "version": "1",
            "scope": "exact retained pilot response bytes and identifiers",
        },
        "paths": path_results,
    }
    trace_bytes = _json_bytes(trace_document)
    released: dict[str, bytes] = {
        f"indexes/{protocol}-boundary-ingress.json": decision_bytes,
        f"indexes/{protocol}-effect-denominator.json": effect_bytes,
        f"coverage/{protocol}-decisions.json": decision_report_bytes,
        f"coverage/{protocol}-effects.json": effect_report_bytes,
        f"traces/{protocol}-runtime.json": trace_bytes,
    }
    if extra_trace is not None:
        released[f"traces/{protocol}-fixed-effect.txt"] = extra_trace
    artifacts = [
        released_artifact_entry(
            artifact_id="denominator-decisions",
            path=f"indexes/{protocol}-boundary-ingress.json",
            role="denominator",
            media_type="application/json",
            content=decision_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="denominator-effects",
            path=f"indexes/{protocol}-effect-denominator.json",
            role="denominator",
            media_type="application/json",
            content=effect_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="coverage-decisions",
            path=f"coverage/{protocol}-decisions.json",
            role="coverage",
            media_type="application/json",
            content=decision_report_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="coverage-effects",
            path=f"coverage/{protocol}-effects.json",
            role="coverage",
            media_type="application/json",
            content=effect_report_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="trace-runtime",
            path=f"traces/{protocol}-runtime.json",
            role="trace",
            media_type="application/json",
            content=trace_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
    ]
    if extra_trace is not None:
        artifacts.append(
            released_artifact_entry(
                artifact_id="trace-fixed-effect",
                path=f"traces/{protocol}-fixed-effect.txt",
                role="trace",
                media_type="text/plain",
                content=extra_trace,
                retention="P7D",
                access_condition="pilot-operator",
            )
        )
    denominators = [
        {
            "anchor_id": decision_anchor,
            "phase": "decision",
            "protocol": protocol,
            "artifact_id": "denominator-decisions",
            "provenance": DENOMINATOR_PROVENANCE,
        },
        {
            "anchor_id": effect_anchor,
            "phase": "effect",
            "protocol": protocol,
            "artifact_id": "denominator-effects",
            "provenance": DENOMINATOR_PROVENANCE,
        },
    ]
    coverage_refs = [
        {
            "anchor_id": decision_anchor,
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
    incident_id = f"incident-{uuid.uuid4()}"

    def denominator_checkpoint(
        *,
        signer: LocalEd25519Signer,
        issuer_role: str,
        anchor_id: str,
        phase: str,
        artifact_bytes: bytes,
    ) -> dict[str, Any]:
        artifact_hash = _sha256_bytes(artifact_bytes)
        checkpoint_envelope = operational_envelope(
            principal=signer.issuer,
            policy=f"policy://incident-{protocol}-{phase}-denominator",
            scope=f"{PROFILE}:incident.statement",
            forum_endpoint="local://incident-pilot/challenge",
            forum_root="team-operated:unanchored",
        )
        return build_profile_receipt(
            action_type="incident.statement",
            subject={
                "profile": PROFILE,
                "issuer_role": issuer_role,
                "statement_id": str(uuid.uuid4()),
                "incident_id": incident_id,
                "topic": denominator_checkpoint_topic(
                    anchor_id, phase, protocol
                ),
                "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
                "epistemic_status": "observed",
                "evidence_refs": [artifact_hash],
            },
            signer=signer,
            envelope=checkpoint_envelope,
            event_id=str(uuid.uuid4()),
            claimed_at=_now(),
            evidence_refs=(
                {
                    "name": "denominator:exact-bytes",
                    "hash": artifact_hash,
                    "grounding": "self_asserted",
                },
            ),
            producer={"bulla_version": "source", "pilot": protocol},
        )

    decision_checkpoint = denominator_checkpoint(
        signer=decision_observer,
        issuer_role="gateway" if protocol == "http" else "boundary",
        anchor_id=decision_anchor,
        phase="decision",
        artifact_bytes=decision_bytes,
    )
    effect_checkpoint = denominator_checkpoint(
        signer=effect_observer,
        issuer_role="target",
        anchor_id=effect_anchor,
        phase="effect",
        artifact_bytes=effect_bytes,
    )
    denominators[0]["checkpoint_attestation"] = decision_checkpoint[
        "hashes"
    ]["attestation"]
    denominators[1]["checkpoint_attestation"] = effect_checkpoint[
        "hashes"
    ]["attestation"]
    checkpoint_receipts = [
        (
            "receipts/04-denominator-decision-checkpoint.json",
            decision_checkpoint,
        ),
        (
            "receipts/05-denominator-effect-checkpoint.json",
            effect_checkpoint,
        ),
    ]
    statements = [
        {
            "receipt_path": path,
            "attestation_hash": receipt["hashes"]["attestation"],
        }
        for path, receipt in checkpoint_receipts
    ]
    envelope = operational_envelope(
        principal=publisher.issuer,
        policy=f"policy://incident-{protocol}-publisher",
        scope=f"{PROFILE}:incident.packet.publish",
        forum_endpoint="local://incident-pilot/challenge",
        forum_root="team-operated:unanchored",
    )
    packet = assemble_incident_packet(
        run.output_dir / "packet",
        packet_id=f"packet-{uuid.uuid4()}",
        incident_id=incident_id,
        classification="team-operated isolated localhost runtime pilot",
        role_issuers=run.role_issuers,
        timeline_receipts=[
            *_runtime_timeline(run.receipts),
            *checkpoint_receipts,
        ],
        artifact_entries=artifacts,
        released_artifacts=released,
        denominators=denominators,
        coverage=coverage_refs,
        statements=statements,
        publisher_signer=publisher,
        publisher_envelope=envelope,
        publish_event_id=str(uuid.uuid4()),
        publish_claimed_at=_now(),
        producer={"bulla_version": "source", "pilot": protocol},
    )
    context_document = {
        "accepted_issuers_by_role": {
            role: [issuer] for role, issuer in run.role_issuers.items()
        },
        "trusted_witness_roots": [],
        "team_controlled_roles": list(run.role_issuers),
    }
    context = IncidentVerificationContext.from_dict(context_document)
    verification = verify_incident_packet(packet, context)
    if verification.exit_code != 0:
        raise RuntimeError(
            f"assembled {protocol} runtime packet did not verify: {verification.errors}"
        )
    _write_json(run.output_dir / "context.json", context_document)
    report_document = verification.to_dict()
    _write_json(run.output_dir / "verification-report.json", report_document)
    return report_document


def _http_target_process(
    run_id: str,
    log_path: str,
    ready: Any,
    stop: Any,
) -> None:
    """Fixed loopback target. It accepts only ``{"operation":"append",...}``."""

    target_log = Path(log_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/effect":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length < 2 or length > 4096:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            try:
                document = _strict_json_loads(raw)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            if (
                not isinstance(document, dict)
                or document.get("operation") != "append"
                or set(document) != {"operation", "value"}
                or document.get("value") not in {"mediated", "direct-bypass"}
            ):
                self.send_error(422)
                return
            observation_id = str(uuid.uuid4())
            operation_ref = _sha256_bytes(raw)
            row = _observation(
                anchor_id="http-target-effects",
                observation_id=observation_id,
                run_id=run_id,
                protocol="http",
                operation_ref=operation_ref,
                phase="effect",
                evidence_hash=_sha256_json(
                    {"observation_id": observation_id, "request_bytes": operation_ref}
                ),
            )
            _append_jsonl(target_log, row)
            body = json.dumps(
                {
                    "observation_id": observation_id,
                    "operation_ref": operation_ref,
                    "evidence_hash": row["evidence_hash"],
                    "transport_status": "HTTP_200",
                },
                sort_keys=True,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.1
    ready.send(server.server_address[1])
    ready.close()
    while not stop.is_set():
        server.handle_request()
    server.server_close()


def _http_gateway_process(
    *,
    run_id: str,
    target_port: int,
    ingress_path: str,
    receipt_dir: str,
    signer_seed: bytes,
    mandate_ref: str,
    ready: Any,
    stop: Any,
) -> None:
    """Fixed loopback gateway. It permits ``allow`` and refuses ``deny``."""

    ingress_log = Path(ingress_path)
    receipts = Path(receipt_dir)
    signer = LocalEd25519Signer(seed=signer_seed)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/act":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length < 2 or length > 4096:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            try:
                document = _strict_json_loads(raw)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            if (
                not isinstance(document, dict)
                or document.get("operation") != "append"
                or document.get("mode") not in {"allow", "deny"}
                or set(document) != {"operation", "mode", "value"}
                or (document["mode"], document.get("value"))
                not in {("allow", "mediated"), ("deny", "refused")}
            ):
                self.send_error(422)
                return

            request_id = str(uuid.uuid4())
            operation_ref = _sha256_bytes(raw)
            ingress = _observation(
                anchor_id="http-gateway-ingress",
                observation_id=request_id,
                run_id=run_id,
                protocol="http",
                operation_ref=operation_ref,
                phase="decision",
                evidence_hash=_sha256_json(
                    {"request_id": request_id, "request_bytes": operation_ref}
                ),
            )
            _append_jsonl(ingress_log, ingress)

            decision = "PERMIT" if document["mode"] == "allow" else "REFUSE"
            decision_receipt = _make_receipt(
                action_type="capability.decide",
                subject={
                    "profile": PROFILE,
                    "issuer_role": "gateway",
                    "run_id": run_id,
                    "request_id": request_id,
                    "decision_event_id": request_id,
                    "request_hash": operation_ref,
                    "mandate_ref": mandate_ref,
                    "policy_digest": _sha256_json(
                        {"allow_mode": "allow", "operation": "append"}
                    ),
                    "decision": decision,
                    "rationale_codes": [
                        "FIXED_LOCALHOST_ALLOW"
                        if decision == "PERMIT"
                        else "FIXED_LOCALHOST_REFUSAL"
                    ],
                    "anchor_id": "http-gateway-ingress",
                    "protocol": "http",
                    "evidence_hash": ingress["evidence_hash"],
                },
                signer=signer,
                policy="policy://incident-http-gateway",
                event_id=str(uuid.uuid4()),
                evidence_refs=(
                    {
                        "name": "gateway_ingress",
                        "hash": ingress["evidence_hash"],
                        "grounding": "self_asserted",
                    },
                ),
            )
            _write_json(receipts / f"decision-{request_id}.json", decision_receipt)
            if decision == "REFUSE":
                self._json(
                    403,
                    {
                        "request_id": request_id,
                        "decision": decision,
                        "decision_attestation": decision_receipt["hashes"]["attestation"],
                    },
                )
                return

            try:
                target_status, target_result = _http_post(
                    f"http://127.0.0.1:{target_port}/effect",
                    {"operation": "append", "value": document["value"]},
                    allowed_path="/effect",
                )
                if target_status != 200:
                    raise RuntimeError(
                        f"fixed target returned HTTP {target_status}"
                    )
            except Exception as exc:
                self._json(
                    502,
                    {
                        "request_id": request_id,
                        "decision": decision,
                        "transport_status": f"ERROR_{type(exc).__name__}",
                    },
                )
                return

            observation_id = target_result["observation_id"]
            effect_hash = target_result["operation_ref"]
            observe_receipt = _make_receipt(
                action_type="capability.observe",
                subject={
                    "profile": PROFILE,
                    "issuer_role": "gateway",
                    "run_id": run_id,
                    "request_id": request_id,
                    "observation_id": observation_id,
                    "decision_attestation": decision_receipt["hashes"]["attestation"],
                    "anchor_id": "http-target-effects",
                    "protocol": "http",
                    "observation_class": "EFFECT_OBSERVED",
                    "transport_status": "HTTP_200",
                    "effect_hash": effect_hash,
                    "evidence_refs": [target_result["evidence_hash"]],
                    "mandate_ref": mandate_ref,
                    "evidence_hash": target_result["evidence_hash"],
                },
                signer=signer,
                policy="policy://incident-http-gateway",
                event_id=str(uuid.uuid4()),
                evidence_refs=(
                    {
                        "name": "target_observation",
                        "hash": target_result["evidence_hash"],
                        "grounding": "self_asserted",
                    },
                ),
            )
            _write_json(receipts / f"observe-{observation_id}.json", observe_receipt)
            self._json(
                200,
                {
                    "request_id": request_id,
                    "decision": decision,
                    "observation_id": observation_id,
                    "decision_attestation": decision_receipt["hashes"]["attestation"],
                    "observation_attestation": observe_receipt["hashes"]["attestation"],
                },
            )

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.1
    ready.send(server.server_address[1])
    ready.close()
    while not stop.is_set():
        server.handle_request()
    server.server_close()


def _http_post(
    url: str,
    document: dict[str, Any],
    *,
    allowed_path: str,
) -> tuple[int, dict[str, Any]]:
    _validate_http_loopback_url(url, allowed_path=allowed_path)
    raw = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _LOCAL_HTTP_OPENER.open(req, timeout=5) as response:
            return response.status, _strict_json_loads(response.read())
    except HTTPError as exc:
        return exc.code, _strict_json_loads(exc.read())


def run_http_pilot(output_dir: str | Path) -> PilotRun:
    """Run the fixed HTTP permit/refuse/bypass scenario on loopback."""

    root = _new_output_root(output_dir)
    receipt_dir = root / "receipts"
    receipt_dir.mkdir(exist_ok=True)
    target_log = root / "indexes" / "http-target-effects.jsonl"
    ingress_log = root / "indexes" / "http-gateway-ingress.jsonl"
    run_id = str(uuid.uuid4())
    evaluator = LocalEd25519Signer.generate()
    gateway_seed = secrets.token_bytes(32)
    gateway = LocalEd25519Signer(seed=gateway_seed)
    target_signer = LocalEd25519Signer.generate()
    publisher = LocalEd25519Signer.generate()
    valid_from, valid_until = _validity_window()
    policy_digest = _sha256_json(
        {"allow_mode": "allow", "operation": "append"}
    )

    mandate = _make_receipt(
        action_type="eval.run.authorize",
        subject={
            "profile": PROFILE,
            "issuer_role": "evaluation_authority",
            "run_id": run_id,
            "model_id": "fixture://agent",
            "harness_id": "bulla-http-localhost-pilot/1",
            "safeguards_id": "loopback-no-credentials-no-offensive-material/1",
            "authorized_targets": ["http://127.0.0.1:<ephemeral>/effect"],
            "budgets": {
                "compute_units": 0,
                "max_actions": 2,
                "duration_seconds": 300,
            },
            "prohibitions": [
                "external-network",
                "credentials",
                "arbitrary-destination",
                "offensive-payload",
            ],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "policy_digest": policy_digest,
            "role_issuers": {
                "evaluation_authority": evaluator.issuer,
                "gateway": gateway.issuer,
                "target": target_signer.issuer,
                "publisher": publisher.issuer,
            },
        },
        signer=evaluator,
        policy="policy://incident-http-evaluation",
        event_id=str(uuid.uuid4()),
    )
    _write_json(receipt_dir / "mandate.json", mandate)
    mandate_ref = mandate["hashes"]["attestation"]

    ctx = multiprocessing.get_context("spawn")
    target_parent, target_child = ctx.Pipe(duplex=False)
    target_stop = ctx.Event()
    target = ctx.Process(
        target=_http_target_process,
        args=(run_id, str(target_log), target_child, target_stop),
        daemon=True,
    )
    target.start()
    target_port = target_parent.recv()

    gateway_parent, gateway_child = ctx.Pipe(duplex=False)
    gateway_stop = ctx.Event()
    gateway_process = ctx.Process(
        target=_http_gateway_process,
        kwargs={
            "run_id": run_id,
            "target_port": target_port,
            "ingress_path": str(ingress_log),
            "receipt_dir": str(receipt_dir),
            "signer_seed": gateway_seed,
            "mandate_ref": mandate_ref,
            "ready": gateway_child,
            "stop": gateway_stop,
        },
        daemon=True,
    )
    gateway_process.start()
    gateway_port = gateway_parent.recv()

    try:
        allowed_status, allowed = _http_post(
            f"http://127.0.0.1:{gateway_port}/act",
            {"operation": "append", "mode": "allow", "value": "mediated"},
            allowed_path="/act",
        )
        refused_status, refused = _http_post(
            f"http://127.0.0.1:{gateway_port}/act",
            {"operation": "append", "mode": "deny", "value": "refused"},
            allowed_path="/act",
        )
        bypass_status, bypass = _http_post(
            f"http://127.0.0.1:{target_port}/effect",
            {"operation": "append", "value": "direct-bypass"},
            allowed_path="/effect",
        )
        if allowed_status != 200 or refused_status != 403 or bypass_status != 200:
            raise RuntimeError(
                "HTTP pilot did not produce expected permit/refuse/bypass statuses"
            )
    finally:
        gateway_stop.set()
        target_stop.set()
        gateway_process.join(timeout=5)
        target.join(timeout=5)
        if gateway_process.is_alive():
            gateway_process.terminate()
            gateway_process.join(timeout=2)
        if target.is_alive():
            target.terminate()
            target.join(timeout=2)

    decision_observations = tuple(_read_jsonl(ingress_log))
    effect_observations = tuple(_read_jsonl(target_log))
    receipts = tuple(
        _strict_json_loads(path.read_text(encoding="utf-8"))
        for path in sorted(receipt_dir.glob("*.json"))
    )
    run = PilotRun(
        protocol="http",
        output_dir=root,
        run_id=run_id,
        role_issuers={
            "evaluation_authority": evaluator.issuer,
            "gateway": gateway.issuer,
            "target": target_signer.issuer,
            "publisher": publisher.issuer,
        },
        decision_observations=decision_observations,
        effect_observations=effect_observations,
        receipts=receipts,
    )
    coverage = summarize_coverage(run)
    if coverage["decision"]["coverage"] != "2/2":
        raise RuntimeError(f"unexpected HTTP decision coverage: {coverage}")
    if coverage["effect"]["coverage"] != "1/2":
        raise RuntimeError(f"unexpected HTTP effect coverage: {coverage}")
    if bypass["observation_id"] not in coverage["effect"]["uncovered"]:
        raise RuntimeError("direct HTTP bypass was not reported as uncovered")
    _write_json(root / "coverage" / "http.json", coverage)
    verification = _finalize_runtime_packet(
        run,
        publisher=publisher,
        decision_observer=gateway,
        effect_observer=target_signer,
        path_results={
            "allowed": allowed,
            "refused": refused,
            "bypass": bypass,
        },
    )
    _write_json(
        root / "pilot-result.json",
        run.to_dict()
        | {
            "coverage": coverage,
            "packet_verification": verification,
            "paths": {
                "allowed": allowed,
                "refused": refused,
                "bypass": bypass,
            },
        },
    )
    return run


class _MCPBackendServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[socketserver.StreamRequestHandler],
        *,
        run_id: str,
        effect_log: Path,
        append_path: Path,
    ) -> None:
        super().__init__(server_address, handler)
        self.run_id = run_id
        self.effect_log = effect_log
        self.append_path = append_path


def _mcp_backend_process(
    run_id: str,
    effect_log_path: str,
    append_path: str,
    ready: Any,
    stop: Any,
) -> None:
    """Fixed MCP backend reachable only on an ephemeral loopback socket."""

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            raw = self.rfile.readline(8193)
            if not raw or len(raw) > 8192 or not raw.endswith(b"\n"):
                return
            try:
                document = _strict_json_loads(raw)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                return
            valid = (
                isinstance(document, dict)
                and document.get("jsonrpc") == "2.0"
                and isinstance(document.get("id"), (str, int))
                and document.get("method") == "tools/call"
                and isinstance(document.get("params"), dict)
                and set(document["params"]) == {"name", "arguments", "_meta"}
                and document["params"].get("name") == "append_fixed"
                and document["params"].get("arguments") == {}
                and isinstance(document["params"].get("_meta"), dict)
                and isinstance(
                    document["params"]["_meta"].get(_MCP_REQUEST_KEY), str
                )
            )
            if not valid:
                response = {
                    "jsonrpc": "2.0",
                    "id": document.get("id") if isinstance(document, dict) else None,
                    "error": {"code": -32602, "message": "fixed MCP request required"},
                }
                self.wfile.write(
                    json.dumps(response, sort_keys=True).encode("utf-8") + b"\n"
                )
                return

            server = self.server
            if not isinstance(server, _MCPBackendServer):
                return
            observation_id = str(uuid.uuid4())
            operation_ref = _sha256_bytes(raw[:-1])
            row = _observation(
                anchor_id="mcp-backend-effects",
                observation_id=observation_id,
                run_id=server.run_id,
                protocol="mcp",
                operation_ref=operation_ref,
                phase="effect",
                evidence_hash=_sha256_json(
                    {
                        "observation_id": observation_id,
                        "request_bytes": operation_ref,
                    }
                ),
            )
            _append_jsonl(server.effect_log, row)
            _append_bytes(server.append_path, b"incident-pilot-fixed-value\n")
            response = {
                "jsonrpc": "2.0",
                "id": document["id"],
                "result": {
                    "content": [{"type": "text", "text": "fixed value appended"}],
                    "isError": False,
                    "_meta": {
                        _MCP_REQUEST_KEY: document["params"]["_meta"][
                            _MCP_REQUEST_KEY
                        ],
                        _MCP_EVENT_KEY: observation_id,
                        "org.resagentica.bulla/operationRef": operation_ref,
                        "org.resagentica.bulla/evidenceHash": row["evidence_hash"],
                    },
                },
            }
            self.wfile.write(
                json.dumps(response, sort_keys=True).encode("utf-8") + b"\n"
            )

    server = _MCPBackendServer(
        ("127.0.0.1", 0),
        Handler,
        run_id=run_id,
        effect_log=Path(effect_log_path),
        append_path=Path(append_path),
    )
    server.timeout = 0.1
    ready.send(server.server_address[1])
    ready.close()
    while not stop.is_set():
        server.handle_request()
    server.server_close()


def _mcp_tcp_call(port: int, document: dict[str, Any]) -> dict[str, Any]:
    port = _validated_loopback_port(port, "MCP backend port")
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > 8192:
        raise ValueError("MCP request exceeds pilot limit")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(raw + b"\n")
        source = connection.makefile("rb")
        response = source.readline(8193)
    if not response or len(response) > 8192:
        raise RuntimeError("MCP backend returned no bounded response")
    return _strict_json_loads(response)


def _mcp_boundary_stdio(
    *,
    run_id: str,
    backend_port: int,
    ingress_path: Path,
    receipt_dir: Path,
    signer_seed: bytes,
    mandate_ref: str,
) -> int:
    """Serve the experimental boundary as newline-delimited JSON-RPC on stdio."""

    signer = LocalEd25519Signer(seed=signer_seed)
    while True:
        input_line = sys.stdin.buffer.readline(8193)
        if not input_line:
            break
        if len(input_line) > 8192 or not input_line.endswith(b"\n"):
            return 2
        try:
            document = _strict_json_loads(input_line)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return 2
        if (
            not isinstance(document, dict)
            or document.get("jsonrpc") != "2.0"
            or not isinstance(document.get("id"), (str, int))
            or document.get("method") != "tools/call"
            or not isinstance(document.get("params"), dict)
            or set(document["params"]) != {"name", "arguments", "_meta"}
            or document["params"].get("name") != "append_fixed"
            or document["params"].get("arguments") != {}
            or not isinstance(document["params"].get("_meta"), dict)
            or document["params"]["_meta"].get(
                "org.resagentica.bulla/mode"
            )
            not in {"allow", "deny"}
        ):
            return 2

        request_id = str(uuid.uuid4())
        operation_ref = _sha256_bytes(input_line.rstrip(b"\r\n"))
        ingress = _observation(
            anchor_id="mcp-boundary-ingress",
            observation_id=request_id,
            run_id=run_id,
            protocol="mcp",
            operation_ref=operation_ref,
            phase="decision",
            evidence_hash=_sha256_json(
                {"request_id": request_id, "request_bytes": operation_ref}
            ),
        )
        _append_jsonl(ingress_path, ingress)
        mode = document["params"]["_meta"]["org.resagentica.bulla/mode"]
        decision = "PERMIT" if mode == "allow" else "REFUSE"
        decision_receipt = _make_receipt(
            action_type="capability.decide",
            subject={
                "profile": PROFILE,
                "issuer_role": "boundary",
                "run_id": run_id,
                "request_id": request_id,
                "decision_event_id": request_id,
                "request_hash": operation_ref,
                "mandate_ref": mandate_ref,
                "policy_digest": _sha256_json(
                    {"name": "append_fixed", "permitted_mode": "allow"}
                ),
                "decision": decision,
                "rationale_codes": [
                    "FIXED_LOCAL_MCP_ALLOW"
                    if decision == "PERMIT"
                        else "FIXED_LOCAL_MCP_REFUSAL"
                ],
                "anchor_id": "mcp-boundary-ingress",
                "protocol": "mcp",
                "evidence_hash": ingress["evidence_hash"],
            },
            signer=signer,
            policy="policy://incident-mcp-boundary",
            event_id=str(uuid.uuid4()),
            evidence_refs=(
                {
                    "name": "boundary_ingress",
                    "hash": ingress["evidence_hash"],
                    "grounding": "self_asserted",
                },
            ),
        )
        _write_json(receipt_dir / f"decision-{request_id}.json", decision_receipt)
        if decision == "REFUSE":
            response = {
                "jsonrpc": "2.0",
                "id": document["id"],
                "error": {
                    "code": -32001,
                    "message": "fixed request refused by pilot policy",
                    "data": {
                        "request_id": request_id,
                        "decision_attestation": decision_receipt["hashes"][
                            "attestation"
                        ],
                    },
                },
            }
        else:
            forwarded = {
                "jsonrpc": "2.0",
                "id": document["id"],
                "method": "tools/call",
                "params": {
                    "name": "append_fixed",
                    "arguments": {},
                    "_meta": {
                        _MCP_REQUEST_KEY: request_id,
                    },
                },
            }
            target_result = _mcp_tcp_call(backend_port, forwarded)
            result = target_result.get("result") or {}
            metadata = result.get("_meta") or {}
            observation_id = metadata.get(_MCP_EVENT_KEY)
            effect_hash = metadata.get("org.resagentica.bulla/operationRef")
            evidence_hash = metadata.get("org.resagentica.bulla/evidenceHash")
            if (
                not isinstance(observation_id, str)
                or not isinstance(effect_hash, str)
                or not isinstance(evidence_hash, str)
            ):
                return 1
            observe_receipt = _make_receipt(
                action_type="capability.observe",
                subject={
                    "profile": PROFILE,
                    "issuer_role": "boundary",
                    "run_id": run_id,
                    "request_id": request_id,
                    "observation_id": observation_id,
                    "decision_attestation": decision_receipt["hashes"][
                        "attestation"
                    ],
                    "anchor_id": "mcp-backend-effects",
                    "protocol": "mcp",
                    "observation_class": "EFFECT_OBSERVED",
                    "transport_status": "MCP_IS_ERROR_FALSE",
                    "effect_hash": effect_hash,
                    "evidence_refs": [evidence_hash],
                    "mandate_ref": mandate_ref,
                    "evidence_hash": evidence_hash,
                },
                signer=signer,
                policy="policy://incident-mcp-boundary",
                event_id=str(uuid.uuid4()),
                evidence_refs=(
                    {
                        "name": "backend_observation",
                        "hash": evidence_hash,
                        "grounding": "self_asserted",
                    },
                ),
            )
            _write_json(
                receipt_dir / f"observe-{observation_id}.json", observe_receipt
            )
            response = target_result | {
                "result": result
                | {
                    "_meta": metadata
                    | {
                        "org.resagentica.bulla/decisionAttestation": (
                            decision_receipt["hashes"]["attestation"]
                        ),
                        "org.resagentica.bulla/observationAttestation": (
                            observe_receipt["hashes"]["attestation"]
                        ),
                    }
                }
            }
        sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0


def _mcp_boundary_command(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--ingress", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--mandate-ref", required=True)
    args = parser.parse_args(argv)
    bootstrap_line = sys.stdin.buffer.readline(4097)
    if (
        not bootstrap_line
        or len(bootstrap_line) > 4096
        or not bootstrap_line.endswith(b"\n")
    ):
        return 2
    try:
        bootstrap = _strict_json_loads(bootstrap_line)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return 2
    if (
        not isinstance(bootstrap, dict)
        or set(bootstrap) != {"signer_seed"}
        or not isinstance(bootstrap.get("signer_seed"), str)
        or len(bootstrap["signer_seed"]) != 64
    ):
        return 2
    try:
        signer_seed = bytes.fromhex(bootstrap["signer_seed"])
    except ValueError:
        return 2
    return _mcp_boundary_stdio(
        run_id=args.run_id,
        backend_port=args.backend_port,
        ingress_path=args.ingress,
        receipt_dir=args.receipts,
        signer_seed=signer_seed,
        mandate_ref=args.mandate_ref,
    )


def _mcp_stdio_call(
    process: subprocess.Popen[bytes], document: dict[str, Any]
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("MCP boundary stdio is unavailable")
    process.stdin.write(
        json.dumps(document, sort_keys=True).encode("utf-8") + b"\n"
    )
    process.stdin.flush()
    response = process.stdout.readline(8193)
    if not response or len(response) > 8192:
        detail = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr is not None
            else ""
        )
        raise RuntimeError(f"MCP boundary returned no response: {detail}")
    return _strict_json_loads(response)


def run_mcp_pilot(output_dir: str | Path) -> PilotRun:
    """Run the fixed MCP permit/refuse/bypass scenario across real processes."""

    root = _new_output_root(output_dir)
    receipt_dir = root / "receipts"
    receipt_dir.mkdir(exist_ok=True)
    effect_log = root / "indexes" / "mcp-backend-effects.jsonl"
    ingress_log = root / "indexes" / "mcp-boundary-ingress.jsonl"
    append_path = root / "runtime" / "fixed-append.txt"
    run_id = str(uuid.uuid4())
    evaluator = LocalEd25519Signer.generate()
    boundary_seed = secrets.token_bytes(32)
    boundary = LocalEd25519Signer(seed=boundary_seed)
    target_signer = LocalEd25519Signer.generate()
    publisher = LocalEd25519Signer.generate()
    valid_from, valid_until = _validity_window()
    policy_digest = _sha256_json(
        {"name": "append_fixed", "permitted_mode": "allow"}
    )
    mandate = _make_receipt(
        action_type="eval.run.authorize",
        subject={
            "profile": PROFILE,
            "issuer_role": "evaluation_authority",
            "run_id": run_id,
            "model_id": "fixture://agent",
            "harness_id": "bulla-mcp-localhost-pilot/1",
            "safeguards_id": "loopback-new-output-no-credentials/1",
            "authorized_targets": ["mcp://fixed-append-backend"],
            "budgets": {
                "compute_units": 0,
                "max_actions": 2,
                "duration_seconds": 300,
            },
            "prohibitions": [
                "external-network",
                "credentials",
                "arbitrary-path",
                "arbitrary-command",
                "environment-access",
            ],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "policy_digest": policy_digest,
            "role_issuers": {
                "evaluation_authority": evaluator.issuer,
                "boundary": boundary.issuer,
                "target": target_signer.issuer,
                "publisher": publisher.issuer,
            },
        },
        signer=evaluator,
        policy="policy://incident-mcp-evaluation",
        event_id=str(uuid.uuid4()),
    )
    _write_json(receipt_dir / "mandate.json", mandate)
    mandate_ref = mandate["hashes"]["attestation"]

    ctx = multiprocessing.get_context("spawn")
    backend_parent, backend_child = ctx.Pipe(duplex=False)
    backend_stop = ctx.Event()
    backend = ctx.Process(
        target=_mcp_backend_process,
        args=(
            run_id,
            str(effect_log),
            str(append_path),
            backend_child,
            backend_stop,
        ),
        daemon=True,
    )
    backend.start()
    backend_port = backend_parent.recv()
    source_root = str(Path(__file__).resolve().parents[2])
    environment = {
        "PYTHONPATH": source_root,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    boundary_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bulla.experimental.incident_pilots",
            "_mcp_boundary",
            "--run-id",
            run_id,
            "--backend-port",
            str(backend_port),
            "--ingress",
            str(ingress_log),
            "--receipts",
            str(receipt_dir),
            "--mandate-ref",
            mandate_ref,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if boundary_process.stdin is None:
        raise RuntimeError("MCP boundary stdin is unavailable")
    boundary_process.stdin.write(
        json.dumps({"signer_seed": boundary_seed.hex()}).encode("utf-8") + b"\n"
    )
    boundary_process.stdin.flush()
    allowed_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "append_fixed",
            "arguments": {},
            "_meta": {"org.resagentica.bulla/mode": "allow"},
        },
    }
    refused_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "append_fixed",
            "arguments": {},
            "_meta": {"org.resagentica.bulla/mode": "deny"},
        },
    }
    try:
        allowed = _mcp_stdio_call(boundary_process, allowed_request)
        refused = _mcp_stdio_call(boundary_process, refused_request)
        bypass = _mcp_tcp_call(
            backend_port,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "append_fixed",
                    "arguments": {},
                    "_meta": {_MCP_REQUEST_KEY: "direct-bypass"},
                },
            },
        )
        if (allowed.get("result") or {}).get("isError") is not False:
            raise RuntimeError("MCP mediated call did not complete")
        if (refused.get("error") or {}).get("code") != -32001:
            raise RuntimeError("MCP refusal did not occur before dispatch")
        if (bypass.get("result") or {}).get("isError") is not False:
            raise RuntimeError("MCP direct bypass did not reach the backend")
    finally:
        if boundary_process.stdin is not None:
            boundary_process.stdin.close()
        try:
            boundary_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            boundary_process.terminate()
            boundary_process.wait(timeout=2)
        backend_stop.set()
        backend.join(timeout=5)
        if backend.is_alive():
            backend.terminate()
            backend.join(timeout=2)

    if boundary_process.returncode != 0:
        detail = (
            boundary_process.stderr.read().decode("utf-8", errors="replace")
            if boundary_process.stderr is not None
            else ""
        )
        raise RuntimeError(f"MCP boundary exited {boundary_process.returncode}: {detail}")
    decision_observations = tuple(_read_jsonl(ingress_log))
    effect_observations = tuple(_read_jsonl(effect_log))
    receipts = tuple(
        _strict_json_loads(path.read_text(encoding="utf-8"))
        for path in sorted(receipt_dir.glob("*.json"))
    )
    run = PilotRun(
        protocol="mcp",
        output_dir=root,
        run_id=run_id,
        role_issuers={
            "evaluation_authority": evaluator.issuer,
            "boundary": boundary.issuer,
            "target": target_signer.issuer,
            "publisher": publisher.issuer,
        },
        decision_observations=decision_observations,
        effect_observations=effect_observations,
        receipts=receipts,
    )
    coverage = summarize_coverage(run)
    if coverage["decision"]["coverage"] != "2/2":
        raise RuntimeError(f"unexpected MCP decision coverage: {coverage}")
    if coverage["effect"]["coverage"] != "1/2":
        raise RuntimeError(f"unexpected MCP effect coverage: {coverage}")
    bypass_id = (bypass.get("result") or {}).get("_meta", {}).get(_MCP_EVENT_KEY)
    if bypass_id not in coverage["effect"]["uncovered"]:
        raise RuntimeError("direct MCP bypass was not reported as uncovered")
    appended = append_path.read_text(encoding="utf-8").splitlines()
    if appended != ["incident-pilot-fixed-value", "incident-pilot-fixed-value"]:
        raise RuntimeError("MCP backend did not record exactly two fixed effects")
    _write_json(root / "coverage" / "mcp.json", coverage)
    verification = _finalize_runtime_packet(
        run,
        publisher=publisher,
        decision_observer=boundary,
        effect_observer=target_signer,
        path_results={
            "allowed": allowed,
            "refused": refused,
            "bypass": bypass,
        },
        extra_trace=append_path.read_bytes(),
    )
    _write_json(
        root / "pilot-result.json",
        run.to_dict()
        | {
            "coverage": coverage,
            "packet_verification": verification,
            "paths": {
                "allowed": allowed,
                "refused": refused,
                "bypass": bypass,
            },
        },
    )
    return run


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "_mcp_boundary":
        raise SystemExit(_mcp_boundary_command(sys.argv[2:]))
    raise SystemExit(2)
