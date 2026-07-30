"""Hostile packet, standalone-checker parity, and pilot-boundary tests."""

from __future__ import annotations

import dataclasses
import base64
import hashlib
import json
import multiprocessing
import os
import re
import runpy
import shutil
import socket
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import bulla.experimental.incident_pilots as incident_pilots
from bulla.experimental.incident_packet import (
    IncidentPacketError,
    IncidentVerificationContext,
    assemble_incident_packet,
    build_profile_receipt,
    build_publish_receipt,
    canonical_hash,
    coverage_commitment,
    parse_incident_packet,
    released_artifact_entry,
    verify_incident_packet,
)
from bulla.action_receipt import ActionReceipt, sign_action_receipt_v04
from bulla.identity import LocalEd25519Signer
from bulla.experimental.incident_pilots import (
    PilotRun,
    run_http_pilot,
    run_mcp_pilot,
    summarize_coverage,
)

_ROOT = Path(__file__).resolve().parents[1]
_PROFILE = _ROOT / "spec" / "agent-incident-packet"
_PYTHON_CHECKER = _PROFILE / "verify_packet.py"
_NODE_CHECKER = _PROFILE / "verify_packet.mjs"
_EXPECTED = json.loads((_PROFILE / "expected-verdict.json").read_text())
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "session_token",
    "client_secret",
    "secret_key",
    "private_key",
    "cookie",
}
_LOW_ENTROPY_SECRET_DIGESTS = {
    "sha256:" + hashlib.sha256(value).hexdigest()
    for value in (
        b"password",
        b"secret",
        b"changeme",
        b"test",
        b"token",
        b"123456",
    )
}
_PERSONAL_DATA_PATTERNS = (
    re.compile(rb"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(rb"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(rb"(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    re.compile(rb"personal[-_ ]data[-_ ]sentinel", re.I),
)
_LEAKAGE_PATTERNS = (
    re.compile(rb"traceback \(most recent call last\)", re.I),
    re.compile(rb"\b(?:runtime|value|type|key|os|io)error:\s", re.I),
    re.compile(rb"(?:^|[\"' ])/(?:users|home)/[^/\\\s\"']+", re.I),
    re.compile(rb"\b[a-z]:\\users\\[^\\\s\"']+", re.I),
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(rb"\bbearer\s+[a-z0-9._~+/=-]{8,}", re.I),
    re.compile(rb"\bbasic\s+[a-z0-9+/=]{8,}", re.I),
    re.compile(rb"-----begin (?:rsa |ec |openssh )?private key-----", re.I),
    re.compile(rb"\bsk-[a-z0-9_-]{8,}", re.I),
)


def _context(protocol: str) -> IncidentVerificationContext:
    value = json.loads(
        (_PROFILE / "contexts" / f"{protocol}-context.json").read_text()
    )
    return IncidentVerificationContext.from_dict(value)


def _copy_packet(tmp_path: Path, protocol: str = "http") -> Path:
    target = tmp_path / f"{protocol}-packet"
    shutil.copytree(_PROFILE / "vectors" / f"{protocol}-clean", target)
    return target


def _fixture_signer(protocol: str, role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(
        seed=hashlib.sha256(
            f"glyph-incident-packet:{protocol}:{role}".encode()
        ).digest()
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def _rewrite_signed_receipt_and_republish(
    packet: Path,
    *,
    protocol: str,
    receipt_path: str,
    mutate_subject: Callable[[dict], None],
    mutate_evidence: Callable[[list[dict]], None] | None = None,
    signer_role: str | None = None,
    rebind_authority_to_signer: bool = False,
    claimed_at: str | None = None,
    rebind_witness: bool = False,
) -> None:
    """Create a semantically hostile but cryptographically coherent packet."""
    path = packet / receipt_path
    document = json.loads(path.read_text())
    parsed = ActionReceipt.from_dict(document)
    action = deepcopy(parsed.action)
    mutate_subject(action["subject"])
    evidence = [dict(item) for item in parsed.evidence_refs]
    if mutate_evidence is not None:
        mutate_evidence(evidence)
    role = signer_role or action["subject"]["issuer_role"]
    signer = _fixture_signer(protocol, role)
    envelope = parsed.envelope
    if rebind_authority_to_signer:
        envelope = dataclasses.replace(
            envelope,
            deed_schema="0.2",
            authority=dataclasses.replace(
                envelope.authority,
                principal=signer.issuer,
                delegation=(),
            ),
        )
    signed = sign_action_receipt_v04(
        dataclasses.replace(
            parsed,
            action=action,
            evidence_refs=tuple(evidence),
            envelope=envelope,
            claimed_at=claimed_at if claimed_at is not None else parsed.claimed_at,
        ),
        signer,
    ).to_dict()
    signed_bytes = _json_bytes(signed)
    path.write_bytes(signed_bytes)

    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    entry = next(
        item for item in core["timeline"] if item["receipt_path"] == receipt_path
    )
    old_attestation = entry["attestation_hash"]
    entry["byte_length"] = len(signed_bytes)
    entry["sha256"] = "sha256:" + hashlib.sha256(signed_bytes).hexdigest()
    entry["attestation_hash"] = signed["hashes"]["attestation"]
    for component in ("statements", "corrections"):
        for item in core[component]:
            if item["receipt_path"] == receipt_path:
                item["attestation_hash"] = signed["hashes"]["attestation"]
    for redaction in core["redactions"]:
        if redaction["reviewer_statement_ref"] == old_attestation:
            redaction["reviewer_statement_ref"] = signed["hashes"]["attestation"]
    if rebind_witness:
        for witness_ref in core["witnesses"]:
            witness_artifact = next(
                item
                for item in core["artifacts"]
                if item["artifact_id"] == witness_ref["artifact_id"]
            )
            witness_path = packet / witness_artifact["path"]
            witness = json.loads(witness_path.read_text())
            witness["included_attestation_hashes"] = [
                signed["hashes"]["attestation"]
                if item == old_attestation
                else item
                for item in witness["included_attestation_hashes"]
            ]
            witness["checkpoint_hash"] = canonical_hash(
                {
                    "witness_id": witness["witness_id"],
                    "root_ref": witness["root_ref"],
                    "received_at": witness["received_at"],
                    "witnessed_at": witness["witnessed_at"],
                    "included_attestation_hashes": witness[
                        "included_attestation_hashes"
                    ],
                }
            )
            witness["proof"] = _fixture_signer(
                protocol, "witness"
            ).sign_domain(
                "witness-checkpoint",
                witness["checkpoint_hash"],
                schema="0.4",
            )
            witness_bytes = _json_bytes(witness)
            witness_path.write_bytes(witness_bytes)
            witness_artifact["byte_length"] = len(witness_bytes)
            witness_artifact["sha256"] = (
                "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
            )
    core_path.write_bytes(_json_bytes(core))

    publish_path = packet / "publish-receipt.json"
    old_publish = ActionReceipt.from_dict(json.loads(publish_path.read_text()))
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer(protocol, "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    publish_path.write_bytes(_json_bytes(publish))


def _republish_core(packet: Path, protocol: str = "http") -> None:
    core = json.loads((packet / "packet-core.json").read_text())
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer(protocol, "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "publish-receipt.json").write_bytes(_json_bytes(publish))


def _resign_wire_receipt(
    document: dict,
    signer: LocalEd25519Signer,
) -> dict:
    """Re-sign a v0.4 wire receipt without constructing its semantic model.

    Hostile closed-shape fixtures cannot pass ``ActionReceipt.from_dict`` by
    design.  This helper still makes their hashes and detached proofs coherent
    over the wire fields that the v0.4 profile commits.
    """
    receipt = deepcopy(document)
    content_preimage = {
        "schema_version": receipt["schema_version"],
        "canonicalization": receipt["canonicalization"],
        "kind": receipt["kind"],
        "action": receipt["action"],
        "diagnostic_ref": receipt["diagnostic_ref"],
        "evidence_refs": receipt["evidence_refs"],
        "anchor_ref": receipt["anchor_ref"],
    }
    if receipt["conventions"]:
        content_preimage["conventions"] = receipt["conventions"]
    content_hash = canonical_hash(content_preimage)
    event_hash = canonical_hash(
        {
            "content_hash": content_hash,
            "event_id": receipt["event_id"],
            "claimed_at": receipt["claimed_at"],
        }
    )

    mandate = receipt["mandate"]
    remedy = receipt["remedy"]
    retention = receipt["retention"]
    envelope = {"deed_schema": mandate.get("deed_schema") or "0.2"}
    if mandate.get("authority"):
        envelope["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        envelope["bounds"] = mandate["bounds"]
    if remedy:
        envelope["recourse"] = {
            key: remedy[key]
            for key in ("challenge_window", "forum", "remedies")
            if key in remedy
        }
    if retention.get("record"):
        envelope["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        envelope["disclosure_class"] = retention["disclosure"]
    authorization_hash = canonical_hash(
        {
            "event_hash": event_hash,
            "envelope_hash": canonical_hash(envelope),
        }
    )

    receipt["signature"] = signer.sign_domain(
        "content", content_hash, schema="0.4"
    )
    receipt["occurrence"] = signer.sign_domain(
        "occurrence", event_hash, schema="0.4"
    )
    receipt["authorization"] = signer.sign_domain(
        "authorization", authorization_hash, schema="0.4"
    )
    attestation_hash = canonical_hash(
        {
            "content_hash": content_hash,
            "signature": receipt["signature"],
            "event_hash": event_hash,
            "occurrence": receipt["occurrence"],
            "recourse_envelope": envelope,
            "authorization": receipt["authorization"],
        }
    )
    receipt["hashes"] = {
        "content": content_hash,
        "event": event_hash,
        "attestation": attestation_hash,
        "log_leaf": "sha256:"
        + hashlib.sha256(b"\x00" + attestation_hash.encode()).hexdigest(),
    }
    return receipt


def _without_errors(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "errors"}


def _json_privacy_findings(value: object, location: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                findings.append(f"sensitive-key:{location}.{key}")
            findings.extend(_json_privacy_findings(item, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_json_privacy_findings(item, f"{location}[{index}]"))
    elif isinstance(value, str) and value.casefold() in _LOW_ENTROPY_SECRET_DIGESTS:
        findings.append(f"low-entropy-secret-digest:{location}")
    return findings


def _public_archive_privacy_findings(packet: Path) -> list[str]:
    """Return conservative heuristic findings; this does not compute safety."""
    findings: list[str] = []
    for path in packet.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        relative = path.relative_to(packet).as_posix()
        for label, patterns in (
            ("credential-value", _CREDENTIAL_VALUE_PATTERNS),
            ("personal-data", _PERSONAL_DATA_PATTERNS),
            ("exception-or-host-path", _LEAKAGE_PATTERNS),
        ):
            for pattern in patterns:
                if pattern.search(raw):
                    findings.append(f"{label}:{relative}")
        try:
            if path.suffix == ".jsonl":
                documents = [
                    json.loads(line)
                    for line in raw.decode("utf-8").splitlines()
                    if line
                ]
            elif path.suffix == ".json":
                documents = [json.loads(raw)]
            else:
                documents = []
        except (UnicodeDecodeError, json.JSONDecodeError):
            findings.append(f"unparseable-public-json:{relative}")
            continue
        for index, document in enumerate(documents):
            findings.extend(
                f"{finding}:{relative}#{index}"
                for finding in _json_privacy_findings(document)
            )
    return sorted(set(findings))


def _checker(
    checker: str,
    packet: Path,
    protocol: str = "http",
    *,
    context_path: Path | None = None,
) -> tuple[int, dict]:
    context = (
        context_path
        if context_path is not None
        else _PROFILE / "contexts" / f"{protocol}-context.json"
    )
    if checker == "python":
        command = [
            "python3",
            "-I",
            str(_PYTHON_CHECKER),
            str(packet),
            "--context",
            str(context),
        ]
    else:
        if shutil.which("node") is None:
            pytest.skip("Node is unavailable")
        command = [
            "node",
            str(_NODE_CHECKER),
            str(packet),
            "--context",
            str(context),
        ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.returncode, json.loads(completed.stdout)


@pytest.mark.parametrize("protocol", ["http", "mcp"])
def test_clean_packet_has_kernel_python_node_parity(protocol: str) -> None:
    packet = _PROFILE / "vectors" / f"{protocol}-clean"
    expected = _EXPECTED["vectors"][f"{protocol}-clean"]["result"]

    kernel = verify_incident_packet(packet, _context(protocol)).to_dict()
    python_exit, python = _checker("python", packet, protocol)
    node_exit, node = _checker("node", packet, protocol)

    assert kernel == expected
    assert python_exit == node_exit == 0
    assert python == node == expected


def test_untrusted_witness_root_suppresses_inclusion_and_time_with_parity(
    tmp_path: Path,
) -> None:
    protocol = "http"
    packet = _PROFILE / "vectors" / "http-clean"
    context_value = json.loads(
        (_PROFILE / "contexts" / "http-context.json").read_text()
    )
    context_value["trusted_witness_roots"] = []
    context_path = tmp_path / "untrusted-witness-context.json"
    context_path.write_bytes(_json_bytes(context_value))

    kernel = verify_incident_packet(
        packet,
        IncidentVerificationContext.from_dict(context_value),
    ).to_dict()
    python_exit, python = _checker(
        "python",
        packet,
        protocol,
        context_path=context_path,
    )
    node_exit, node = _checker(
        "node",
        packet,
        protocol,
        context_path=context_path,
    )

    assert python_exit == node_exit == 0
    assert kernel == python == node
    assert kernel["exit_code"] == 0
    assert kernel["witness_inclusion"] == "NOT_COMPUTED"
    assert kernel["witness_root_trust"] == "UNTRUSTED"
    assert kernel["temporal_evidence"]["received_at"] == []
    assert kernel["temporal_evidence"]["witnessed_at"] == []
    assert "witness-derived temporal labels" in kernel["suppressed_conclusions"]
    assert (
        "packet-carried witness root is not trusted out of band"
        in kernel["warnings"]
    )


@pytest.mark.parametrize(
    "failure",
    ["bad_proof", "wrong_issuer", "incomplete_inclusion"],
)
def test_untrusted_witness_root_does_not_mask_invalid_chain_with_parity(
    tmp_path: Path,
    failure: str,
) -> None:
    protocol = "http"
    packet = _copy_packet(tmp_path)
    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text())
    if failure == "bad_proof":
        proof_value = witness["proof"]["proofValue"]
        witness["proof"]["proofValue"] = (
            ("A" if proof_value[0] != "A" else "B") + proof_value[1:]
        )
    elif failure == "wrong_issuer":
        witness["proof"] = _fixture_signer(
            protocol,
            "mcp-attacker",
        ).sign_domain(
            "witness-checkpoint",
            witness["checkpoint_hash"],
            schema="0.4",
        )
    else:
        witness["included_attestation_hashes"].pop()
        witness["checkpoint_hash"] = canonical_hash(
            {
                "witness_id": witness["witness_id"],
                "root_ref": witness["root_ref"],
                "received_at": witness["received_at"],
                "witnessed_at": witness["witnessed_at"],
                "included_attestation_hashes": witness[
                    "included_attestation_hashes"
                ],
            }
        )
        witness["proof"] = _fixture_signer(
            protocol,
            "witness",
        ).sign_domain(
            "witness-checkpoint",
            witness["checkpoint_hash"],
            schema="0.4",
        )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)

    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    witness_artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == "witness-checkpoint"
    )
    witness_artifact["byte_length"] = len(witness_bytes)
    witness_artifact["sha256"] = (
        "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
    )
    core_path.write_bytes(_json_bytes(core))
    _republish_core(packet)

    context_value = json.loads(
        (_PROFILE / "contexts" / "http-context.json").read_text()
    )
    context_value["trusted_witness_roots"] = []
    context_path = tmp_path / "untrusted-witness-context.json"
    context_path.write_bytes(_json_bytes(context_value))
    kernel = verify_incident_packet(
        packet,
        IncidentVerificationContext.from_dict(context_value),
    ).to_dict()
    python_exit, python = _checker(
        "python",
        packet,
        protocol,
        context_path=context_path,
    )
    node_exit, node = _checker(
        "node",
        packet,
        protocol,
        context_path=context_path,
    )

    semantic_fields = (
        "exit_code",
        "witness_inclusion",
        "witness_root_trust",
        "temporal_evidence",
        "suppressed_conclusions",
    )
    assert python_exit == node_exit == 1
    assert {
        field: kernel[field] for field in semantic_fields
    } == {
        field: python[field] for field in semantic_fields
    } == {
        field: node[field] for field in semantic_fields
    }
    assert kernel["exit_code"] == 1
    assert kernel["witness_inclusion"] == "FAILED"
    assert kernel["witness_root_trust"] == "UNTRUSTED"
    assert kernel["temporal_evidence"]["received_at"] == []
    assert kernel["temporal_evidence"]["witnessed_at"] == []
    assert "witness-derived temporal labels" in kernel["suppressed_conclusions"]


@pytest.mark.parametrize("unavailable_first", [False, True])
def test_multiple_witnesses_are_malformed_before_verification_with_parity(
    tmp_path: Path,
    unavailable_first: bool,
) -> None:
    packet = _copy_packet(tmp_path)
    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text())
    proof_value = witness["proof"]["proofValue"]
    witness["proof"]["proofValue"] = (
        ("A" if proof_value[0] != "A" else "B") + proof_value[1:]
    )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)

    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    released = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == "witness-checkpoint"
    )
    released["byte_length"] = len(witness_bytes)
    released["sha256"] = (
        "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
    )
    controlled = deepcopy(released)
    controlled.update(
        {
            "artifact_id": "witness-controlled-2",
            "path": None,
            "withheld_ref": "controlled://witness/2",
            "disclosure_state": "controlled",
        }
    )
    core["artifacts"].append(controlled)
    unavailable = {
        "witness_id": "http-team-witness-002",
        "artifact_id": "witness-controlled-2",
        "root_ref": "team-witness://http/checkpoint-002",
    }
    if unavailable_first:
        core["witnesses"].insert(0, unavailable)
    else:
        core["witnesses"].append(unavailable)
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(IncidentPacketError, match="at most one witness"):
        verify_incident_packet(packet, _context("http"))
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True
        assert "at most one witness" in result["error"]


def test_python_standalone_path_fallback_parses_clean_packet() -> None:
    namespace = runpy.run_path(str(_PYTHON_CHECKER))
    read_packet = namespace["read_packet"]
    read_packet.__globals__["DESCRIPTOR_RELATIVE_WALK_SUPPORTED"] = False
    files = read_packet(_PROFILE / "vectors" / "http-clean")
    assert files["packet-core.json"]
    assert files["coverage/http-decisions.json"]


def test_python_standalone_rejects_windows_reparse_metadata() -> None:
    namespace = runpy.run_path(str(_PYTHON_CHECKER))
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=0x400,
    )
    assert namespace["is_symlink_or_reparse_point"](metadata)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda raw: raw.replace(
                b'"revision": 1', b'"revision": 1, "revision": 1', 1
            ),
            "duplicate JSON member",
        ),
        (
            lambda raw: raw.replace(b'"revision": 1', b'"revision": NaN', 1),
            "non-finite",
        ),
        (
            lambda raw: raw.replace(b'"revision": 1', b'"revision": true', 1),
            "positive integer",
        ),
        (
            lambda raw: raw.replace(
                b'"classification": "synthetic-public"',
                b'"classification": "synthetic-\\ud800public"',
                1,
            ),
            "surrogate",
        ),
        (
            lambda raw: raw.replace(b"{", b'{"ambient_trust":true,', 1),
            "unknown fields",
        ),
    ],
)
def test_packet_core_strict_ingestion_matches_standalone_checkers(
    tmp_path: Path,
    mutate: Callable[[bytes], bytes],
    match: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core = packet / "packet-core.json"
    core.write_bytes(mutate(core.read_bytes()))

    with pytest.raises(IncidentPacketError, match=match):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == 2
        assert result["malformed"] is True
        assert result["exit_code"] == 2


def test_packet_core_resource_limit_precedes_verification(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    (packet / "packet-core.json").write_bytes(b" " * 2_097_153)

    with pytest.raises(IncidentPacketError, match="exceeds 2097152 bytes"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert (exit_code, result["exit_code"]) == (2, 2)


@pytest.mark.parametrize("resource", ["depth", "nodes", "string"])
def test_packet_json_structural_resource_limits(
    tmp_path: Path,
    resource: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    if resource == "depth":
        value: object = "leaf"
        for _ in range(45):
            value = [value]
    elif resource == "nodes":
        value = [None] * 100_001
    else:
        value = "x" * 524_289
    core["hostile_resource"] = value
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(IncidentPacketError, match="depth|nodes|string"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


def test_packet_file_count_and_aggregate_limits(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    for index in range(513):
        (packet / f"extra-{index:03d}.json").write_text("{}")
    with pytest.raises(IncidentPacketError, match="exceeds 512 files"):
        parse_incident_packet(packet)

    packet = _copy_packet(tmp_path, "mcp")
    for index in range(9):
        (packet / f"large-{index:02d}.bin").write_bytes(b"x" * 2_000_000)
    with pytest.raises(IncidentPacketError, match="exceeds 16777216 total bytes"):
        parse_incident_packet(packet)


def test_symlink_and_undeclared_file_are_rejected(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (packet / "traces" / "escape.json").symlink_to(outside)

    with pytest.raises(IncidentPacketError, match="symlink|non-regular"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert (exit_code, result["exit_code"]) == (2, 2)

    (packet / "traces" / "escape.json").unlink()
    (packet / "undeclared.json").write_text("{}")
    with pytest.raises(IncidentPacketError, match="undeclared"):
        parse_incident_packet(packet)


def test_unsafe_manifest_path_is_rejected_before_file_access(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    core["artifacts"][0]["path"] = "../outside.json"
    core_path.write_text(json.dumps(core))

    with pytest.raises(
        IncidentPacketError, match="normalized relative|unsafe path separator"
    ):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert (exit_code, result["exit_code"]) == (2, 2)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/absolute/coverage.json",
        "coverage\\escape.json",
        "C:/outside/coverage.json",
    ],
)
def test_absolute_and_backslash_manifest_paths_are_rejected(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    core["artifacts"][0]["path"] = unsafe_path
    core_path.write_text(json.dumps(core))

    with pytest.raises(
        IncidentPacketError, match="normalized relative|unsafe path separator"
    ):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2


def test_duplicate_manifest_paths_are_rejected(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    core["artifacts"][1]["path"] = core["artifacts"][0]["path"]
    core_path.write_text(json.dumps(core))

    with pytest.raises(IncidentPacketError, match="duplicate artifact path"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2


def test_archive_input_is_rejected_without_decompression(tmp_path: Path) -> None:
    archive = tmp_path / "packet.zip"
    archive.write_bytes(
        b"PK\x03\x04" + b"\xff" * 1_000_000
    )

    with pytest.raises(IncidentPacketError, match="directory"):
        parse_incident_packet(archive)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, archive)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


@pytest.mark.parametrize(
    "relative",
    [
        "traces/http-released.jsonl",
        "receipts/01-decision-permit.json",
        "coverage/http-effects.json",
    ],
)
def test_tampering_fails_closed_and_suppresses_dependent_claims(
    tmp_path: Path,
    relative: str,
) -> None:
    packet = _copy_packet(tmp_path)
    target = packet / relative
    target.write_bytes(target.read_bytes() + b" ")

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 1
    assert (
        kernel.artifact_integrity == "FAILED"
        or kernel.receipt_integrity == "FAILED"
    )
    assert "reliance" in kernel.suppressed_conclusions
    if relative == "receipts/01-decision-permit.json":
        decision = next(
            item for item in kernel.coverage if item.phase == "decision"
        )
        assert decision.status == "FAILED"
        assert decision.receipted == 1
        assert decision.total == 2
        assert decision.uncovered_ids == ("http-decision-permit-001",)
        assert any(
            item["source"] == relative
            for item in decision.invalid_receipts
        )
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert (exit_code, result["exit_code"]) == (1, 1)
        assert "reliance" in result["suppressed_conclusions"]
        if relative == "receipts/01-decision-permit.json":
            decision = next(
                item
                for item in result["coverage"]
                if item["phase"] == "decision"
            )
            assert decision["status"] == "FAILED"
            assert decision["receipted"] == 1
            assert decision["total"] == 2
            assert decision["uncovered_ids"] == [
                "http-decision-permit-001"
            ]
            assert any(
                item["source"] == relative
                for item in decision["invalid_receipts"]
            )


def test_trailing_receipt_whitespace_has_deep_checker_parity(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    relative = "receipts/01-decision-permit.json"
    target = packet / relative
    target.write_bytes(target.read_bytes() + b" ")

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["receipt_integrity"] == "FAILED"
    assert kernel["timeline_integrity"] == "FAILED"
    assert kernel["signature_status"] == "VERIFIED"
    assert kernel["occurrence_binding"] == "VERIFIED"
    decision = next(
        item for item in kernel["coverage"] if item["phase"] == "decision"
    )
    assert decision["uncovered_ids"] == ["http-decision-permit-001"]
    assert decision["receipted"] == 1

    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == 1
        assert _without_errors(result) == _without_errors(kernel)


@pytest.mark.parametrize("component", ["denominators", "coverage"])
def test_controlled_coverage_inputs_are_not_computed_with_deep_parity(
    tmp_path: Path,
    component: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    reference = core[component][0]
    artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == reference["artifact_id"]
    )
    (packet / artifact["path"]).unlink()
    artifact.update(
        path=None,
        withheld_ref=f"controlled://{component}/test",
        disclosure_state="controlled",
    )
    core_path.write_bytes(_json_bytes(core))
    _republish_core(packet)

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 0
    assert kernel["coverage_status"] == "NOT_COMPUTED"
    assert reference["artifact_id"] in kernel["unavailable_artifacts"]
    affected = next(
        item
        for item in kernel["coverage"]
        if item["anchor_id"] == reference["anchor_id"]
        and item["phase"] == reference["phase"]
    )
    expected_reason = (
        "denominator artifact is unavailable"
        if component == "denominators"
        else "declared coverage artifact is unavailable"
    )
    assert affected["status"] == "NOT_COMPUTED"
    assert affected["reasons"] == [expected_reason]
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == 0
        assert result == kernel


def test_late_correction_invalid_utc_time_has_deep_checker_parity(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    invalid_time = "2026-07-26T12:11:00+00:00"
    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/11-correction.json",
        mutate_subject=lambda subject: None,
        claimed_at=invalid_time,
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    assert kernel["correction_status"] == "FAILED"
    assert invalid_time not in kernel["temporal_evidence"]["claimed_at"]
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == 1
        assert _without_errors(result) == _without_errors(kernel)


@pytest.mark.parametrize(
    ("receipt_path", "mutate_subject", "expected_dimension"),
    [
        (
            "receipts/01-decision-permit.json",
            lambda subject: subject.__setitem__("decision", "ALLOW"),
            "receipt_integrity",
        ),
        (
            "receipts/03-observation-mediated.json",
            lambda subject: subject.__setitem__("run_id", "cross-run-graft"),
            "lineage_integrity",
        ),
        (
            "receipts/03-observation-mediated.json",
            lambda subject: subject.__setitem__(
                "anchor_id", "http-gateway-ingress"
            ),
            "coverage_status",
        ),
        (
            "receipts/10-handoff.json",
            lambda subject: subject.__setitem__(
                "coverage_hash", "sha256:" + "44" * 32
            ),
            "timeline_integrity",
        ),
        (
            "receipts/09-statement-redaction-review.json",
            lambda subject: subject.__setitem__(
                "epistemic_status", "counterparty_confirmed"
            ),
            "receipt_integrity",
        ),
        (
            "receipts/11-correction.json",
            lambda subject: subject.__setitem__("supersedes_kind", "packet"),
            "correction_status",
        ),
        (
            "receipts/11-correction.json",
            lambda subject: subject.__setitem__(
                "replacement_ref", "sha256:" + "66" * 32
            ),
            "correction_status",
        ),
    ],
)
def test_resigned_semantic_and_binding_mutations_match_all_checkers(
    tmp_path: Path,
    receipt_path: str,
    mutate_subject: Callable[[dict], None],
    expected_dimension: str,
) -> None:
    packet = _copy_packet(tmp_path)
    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path=receipt_path,
        mutate_subject=mutate_subject,
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel[expected_dimension] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result[expected_dimension] == "FAILED"


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("statement_refs", "omit"),
        ("statement_refs", "reorder"),
        ("parent_refs", "omit"),
        ("parent_refs", "reorder"),
    ],
)
def test_handoff_requires_exact_ordered_reference_sets(
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)

    def mutate(subject: dict) -> None:
        values = list(subject[field])
        if mutation == "omit":
            values.pop()
        else:
            values[0], values[1] = values[1], values[0]
        subject[field] = values

    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/10-handoff.json",
        mutate_subject=mutate,
        rebind_witness=True,
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["packet_integrity"] == "VERIFIED"
    assert kernel["receipt_integrity"] == "VERIFIED"
    assert kernel["timeline_integrity"] == "FAILED"
    assert kernel["witness_inclusion"] == "VERIFIED"
    assert "timeline and lineage dependent conclusions" in (
        kernel["suppressed_conclusions"]
    )
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["packet_integrity"] == "VERIFIED"
        assert result["receipt_integrity"] == "VERIFIED"
        assert result["timeline_integrity"] == "FAILED"
        assert result["witness_inclusion"] == "VERIFIED"
        assert "timeline and lineage dependent conclusions" in (
            result["suppressed_conclusions"]
        )


def test_handoff_cannot_name_a_future_statement(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    template = ActionReceipt.from_dict(
        json.loads(
            (packet / "receipts" / "08-statement-unresolved.json").read_text()
        )
    )
    future = build_profile_receipt(
        action_type="incident.statement",
        subject={
            **template.action["subject"],
            "statement_id": "future-statement-001",
            "topic": "future-statement",
            "claim": "Recorded after the handoff.",
            "epistemic_status": "unresolved",
            "evidence_refs": [],
        },
        signer=_fixture_signer("http", "affected_party"),
        envelope=template.envelope,
        event_id="00000000-0000-4000-8000-000000000301",
        claimed_at="2026-07-26T12:59:30Z",
    )
    future_path = "receipts/12-statement-future.json"
    future_bytes = _json_bytes(future)
    (packet / future_path).write_bytes(future_bytes)
    core["timeline"].append(
        {
            "sequence": len(core["timeline"]) + 1,
            "receipt_path": future_path,
            "byte_length": len(future_bytes),
            "sha256": "sha256:" + hashlib.sha256(future_bytes).hexdigest(),
            "attestation_hash": future["hashes"]["attestation"],
            "action_type": "incident.statement",
        }
    )
    core["statements"].append(
        {
            "receipt_path": future_path,
            "attestation_hash": future["hashes"]["attestation"],
        }
    )
    witness_ref = core["witnesses"][0]
    witness_artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == witness_ref["artifact_id"]
    )
    witness_path = packet / witness_artifact["path"]
    witness = json.loads(witness_path.read_text())
    witness["included_attestation_hashes"].append(
        future["hashes"]["attestation"]
    )
    witness["checkpoint_hash"] = canonical_hash(
        {
            "witness_id": witness["witness_id"],
            "root_ref": witness["root_ref"],
            "received_at": witness["received_at"],
            "witnessed_at": witness["witnessed_at"],
            "included_attestation_hashes": witness[
                "included_attestation_hashes"
            ],
        }
    )
    witness["proof"] = _fixture_signer("http", "witness").sign_domain(
        "witness-checkpoint",
        witness["checkpoint_hash"],
        schema="0.4",
    )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)
    witness_artifact["byte_length"] = len(witness_bytes)
    witness_artifact["sha256"] = (
        "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
    )
    core_path.write_bytes(_json_bytes(core))

    statement_refs = [
        item["attestation_hash"] for item in core["statements"]
    ]
    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/10-handoff.json",
        mutate_subject=lambda subject: subject.__setitem__(
            "statement_refs", statement_refs
        ),
        rebind_witness=True,
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["packet_integrity"] == "VERIFIED"
    assert kernel["receipt_integrity"] == "VERIFIED"
    assert kernel["timeline_integrity"] == "FAILED"
    assert kernel["witness_inclusion"] == "VERIFIED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["packet_integrity"] == "VERIFIED"
        assert result["receipt_integrity"] == "VERIFIED"
        assert result["timeline_integrity"] == "FAILED"
        assert result["witness_inclusion"] == "VERIFIED"


def test_attacker_resigning_cannot_assume_a_declared_role(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)

    def retain_subject(_subject: dict) -> None:
        return

    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/01-decision-permit.json",
        mutate_subject=retain_subject,
        signer_role="mcp-attacker",
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["issuer_authenticity"] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["issuer_authenticity"] == "FAILED"


def test_agent_role_cannot_issue_boundary_decision(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)

    def assign_agent_role(subject: dict) -> None:
        subject["issuer_role"] = "affected_party"

    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/01-decision-permit.json",
        mutate_subject=assign_agent_role,
        signer_role="affected_party",
    )

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["receipt_integrity"] == "FAILED"


def test_cross_phase_action_type_transplant_fails_closed(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    path = packet / "receipts" / "03-observation-mediated.json"
    original = ActionReceipt.from_dict(json.loads(path.read_text()))
    action = deepcopy(original.action)
    action["type"] = "capability.decide"
    transplanted = sign_action_receipt_v04(
        dataclasses.replace(original, action=action),
        _fixture_signer("http", "gateway"),
    ).to_dict()
    transplanted_bytes = _json_bytes(transplanted)
    path.write_bytes(transplanted_bytes)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    entry = next(
        item
        for item in core["timeline"]
        if item["receipt_path"] == "receipts/03-observation-mediated.json"
    )
    entry.update(
        {
            "byte_length": len(transplanted_bytes),
            "sha256": (
                "sha256:" + hashlib.sha256(transplanted_bytes).hexdigest()
            ),
            "attestation_hash": transplanted["hashes"]["attestation"],
            "action_type": "capability.decide",
        }
    )
    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "publish-receipt.json").write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 1
    assert kernel.receipt_integrity == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["receipt_integrity"] == "FAILED"


@pytest.mark.parametrize("field", ["signature", "occurrence", "authorization"])
def test_unsigned_or_transplanted_boundary_proofs_fail(
    tmp_path: Path,
    field: str,
) -> None:
    packet = _copy_packet(tmp_path)
    path = packet / "receipts" / "01-decision-permit.json"
    original = ActionReceipt.from_dict(json.loads(path.read_text()))
    if field == "signature":
        changed = dataclasses.replace(original, signature=None)
    else:
        proof = dict(getattr(original, field))
        proof["purpose"] = "content"
        changed = dataclasses.replace(original, **{field: proof})
    receipt = changed.to_dict()
    receipt_bytes = _json_bytes(receipt)
    path.write_bytes(receipt_bytes)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    entry = next(
        item
        for item in core["timeline"]
        if item["receipt_path"] == "receipts/01-decision-permit.json"
    )
    entry["byte_length"] = len(receipt_bytes)
    entry["sha256"] = "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    entry["attestation_hash"] = receipt["hashes"]["attestation"]
    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "publish-receipt.json").write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 1
    assert kernel.receipt_integrity == "FAILED"
    assert kernel.signature_status == "FAILED"
    expected_dimensions = (
        kernel.signature_status,
        kernel.issuer_authenticity,
        kernel.authority_binding,
        kernel.occurrence_binding,
    )
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["receipt_integrity"] == "FAILED"
        assert (
            result["signature_status"],
            result["issuer_authenticity"],
            result["authority_binding"],
            result["occurrence_binding"],
        ) == expected_dimensions


def test_noncanonical_signature_pad_bits_fail_with_checker_parity(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    path = packet / "receipts" / "01-decision-permit.json"
    receipt = json.loads(path.read_text())
    canonical_proof = receipt["signature"]["proofValue"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    canonical_index = alphabet.index(canonical_proof[-3])
    assert canonical_index & 0x0F == 0
    receipt["signature"]["proofValue"] = (
        canonical_proof[:-3]
        + alphabet[canonical_index + 1]
        + canonical_proof[-2:]
    )
    assert base64.b64decode(
        receipt["signature"]["proofValue"], validate=True
    ) == base64.b64decode(canonical_proof, validate=True)

    envelope = {
        "deed_schema": receipt["mandate"].get("deed_schema") or "0.2",
        "authority": receipt["mandate"]["authority"],
        "bounds": receipt["mandate"]["bounds"],
        "recourse": receipt["remedy"],
        "retention_class": receipt["retention"]["record"],
        "disclosure_class": receipt["retention"]["disclosure"],
    }
    receipt["hashes"]["attestation"] = canonical_hash(
        {
            "content_hash": receipt["hashes"]["content"],
            "signature": receipt["signature"],
            "event_hash": receipt["hashes"]["event"],
            "occurrence": receipt["occurrence"],
            "recourse_envelope": envelope,
            "authorization": receipt["authorization"],
        }
    )
    receipt["hashes"]["log_leaf"] = "sha256:" + hashlib.sha256(
        b"\x00" + receipt["hashes"]["attestation"].encode()
    ).hexdigest()
    receipt_bytes = _json_bytes(receipt)
    path.write_bytes(receipt_bytes)

    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    entry = next(
        item
        for item in core["timeline"]
        if item["receipt_path"] == "receipts/01-decision-permit.json"
    )
    entry["byte_length"] = len(receipt_bytes)
    entry["sha256"] = "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    entry["attestation_hash"] = receipt["hashes"]["attestation"]
    core_path.write_bytes(_json_bytes(core))
    _republish_core(packet)

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["signature_status"] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["signature_status"] == "FAILED"


@pytest.mark.parametrize(
    ("case", "expected_receipt_status"),
    [
        ("decision_wrong_grounding", "FAILED"),
        ("gateway_observation_wrong_grounding", "FAILED"),
        ("target_observation_counterparty_signed", "VERIFIED"),
        ("team_third_party_anchored", "FAILED"),
        ("invalid_execution_verified_name", "FAILED"),
        ("observation_evidence_order", "FAILED"),
    ],
)
def test_resigned_evidence_grounding_rules_match_all_checkers(
    tmp_path: Path,
    case: str,
    expected_receipt_status: str,
) -> None:
    packet = _copy_packet(tmp_path)
    receipt_path = "receipts/03-observation-mediated.json"
    signer_role: str | None = None

    def mutate_subject(subject: dict) -> None:
        nonlocal signer_role
        if case == "target_observation_counterparty_signed":
            subject["issuer_role"] = "target"
            signer_role = "target"
        elif case == "observation_evidence_order":
            subject["evidence_refs"].append("sha256:" + "42" * 32)

    def mutate_evidence(evidence: list[dict]) -> None:
        if case in {
            "decision_wrong_grounding",
            "gateway_observation_wrong_grounding",
            "target_observation_counterparty_signed",
        }:
            evidence[0]["grounding"] = "counterparty_signed"
        elif case == "team_third_party_anchored":
            evidence[0]["grounding"] = "third_party_anchored"
        elif case == "invalid_execution_verified_name":
            evidence[0]["grounding"] = "execution_verified"
        elif case == "observation_evidence_order":
            evidence.append(
                {
                    "name": "second-evidence",
                    "hash": "sha256:" + "42" * 32,
                    "grounding": "self_asserted",
                }
            )
            evidence.reverse()

    if case == "decision_wrong_grounding":
        receipt_path = "receipts/01-decision-permit.json"
    elif case in {
        "team_third_party_anchored",
        "invalid_execution_verified_name",
    }:
        receipt_path = "receipts/05-denominator-decision-checkpoint.json"

    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path=receipt_path,
        mutate_subject=mutate_subject,
        mutate_evidence=mutate_evidence,
        signer_role=signer_role,
        rebind_authority_to_signer=(
            case == "target_observation_counterparty_signed"
        ),
    )
    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["receipt_integrity"] == expected_receipt_status
    expected_dimensions = tuple(
        kernel[name]
        for name in (
            "receipt_integrity",
            "signature_status",
            "issuer_authenticity",
            "authority_binding",
            "occurrence_binding",
        )
    )
    for checker in ("python", "node"):
        _, result = _checker(checker, packet)
        assert tuple(
            result[name]
            for name in (
                "receipt_integrity",
                "signature_status",
                "issuer_authenticity",
                "authority_binding",
                "occurrence_binding",
            )
        ) == expected_dimensions


@pytest.mark.parametrize("component", ["statements", "witnesses", "corrections"])
def test_nested_reference_unknown_fields_are_structurally_malformed(
    tmp_path: Path,
    component: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    core[component][0]["ambient_trust"] = True
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(IncidentPacketError, match="unknown fields"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


@pytest.mark.parametrize(
    ("component", "mutation"),
    [
        ("denominators", "unsupported_provenance"),
        ("denominators", "wrong_artifact_role"),
        ("coverage", "wrong_artifact_role"),
        ("coverage", "wrong_depth"),
    ],
)
def test_coverage_topology_errors_are_structurally_malformed(
    tmp_path: Path,
    component: str,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    reference = core[component][0]
    if mutation == "unsupported_provenance":
        reference["provenance"] = "AMBIENT"
    elif mutation == "wrong_depth":
        reference["minimum_verification_depth"] = "digest"
    else:
        artifact = next(
            item
            for item in core["artifacts"]
            if item["artifact_id"] == reference["artifact_id"]
        )
        artifact["role"] = "coverage" if component == "denominators" else "denominator"
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(IncidentPacketError):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


def test_duplicate_denominator_ids_make_coverage_not_computed(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    denominator_path = packet / "indexes" / "http-target-effects.json"
    denominator = json.loads(denominator_path.read_text())
    denominator["observations"].append(deepcopy(denominator["observations"][0]))
    denominator_path.write_text(json.dumps(denominator))

    kernel = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in kernel.coverage if item.phase == "effect")
    assert effect.status == "NOT_COMPUTED"
    assert effect.covered_ids == effect.uncovered_ids == ()
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        effect_result = next(
            item for item in result["coverage"] if item["phase"] == "effect"
        )
        assert exit_code == 1  # artifact bytes no longer match the signed manifest
        assert effect_result["status"] == "NOT_COMPUTED"


@pytest.mark.parametrize(
    "mutation",
    ["empty", "truncated", "fabricated", "wrong_anchor", "reordered", "shortened"],
)
def test_hostile_denominator_mutations_never_preserve_declared_coverage(
    tmp_path: Path,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    denominator_path = packet / "indexes" / "http-target-effects.json"
    denominator = json.loads(denominator_path.read_text())
    if mutation == "empty":
        denominator["observations"] = []
        denominator_path.write_text(json.dumps(denominator))
    elif mutation == "truncated":
        denominator_path.write_bytes(denominator_path.read_bytes()[:-9])
    elif mutation == "fabricated":
        denominator["observations"][0]["observation_id"] = "fabricated-effect"
        denominator_path.write_text(json.dumps(denominator))
    elif mutation == "wrong_anchor":
        denominator["observations"][0]["anchor_id"] = "wrong-anchor"
        denominator_path.write_text(json.dumps(denominator))
    elif mutation == "reordered":
        denominator["observations"].reverse()
        denominator_path.write_text(json.dumps(denominator))
    else:
        denominator["observations"].pop()
        denominator_path.write_text(json.dumps(denominator))

    kernel = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in kernel.coverage if item.phase == "effect")
    assert kernel.exit_code == 1
    assert kernel.artifact_integrity == "FAILED"
    expected_statuses = (
        {"COMPUTED"} if mutation == "reordered" else {"FAILED", "NOT_COMPUTED"}
    )
    assert effect.status in expected_statuses
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        effect_result = next(
            item for item in result["coverage"] if item["phase"] == "effect"
        )
        assert exit_code == result["exit_code"] == 1
        assert result["artifact_integrity"] == "FAILED"
        assert effect_result["status"] in expected_statuses


def test_missing_denominator_member_is_malformed_not_absence(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    (packet / "indexes" / "http-target-effects.json").unlink()

    with pytest.raises(IncidentPacketError, match="missing"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


def test_publisher_alone_cannot_republish_a_self_shortened_denominator(
    tmp_path: Path,
) -> None:
    """Publisher re-signing cannot replace the target's checkpoint statement."""
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    denominator_path = packet / "indexes" / "http-target-effects.json"
    denominator = json.loads(denominator_path.read_text())
    denominator["observations"] = denominator["observations"][:1]
    denominator_bytes = _json_bytes(denominator)
    denominator_path.write_bytes(denominator_bytes)
    report_path = packet / "coverage" / "http-effects.json"
    report = json.loads(report_path.read_text())
    report.update(
        {
            "denominator_sha256": (
                "sha256:" + hashlib.sha256(denominator_bytes).hexdigest()
            ),
            "total": 1,
            "receipted": 1,
            "covered_ids": ["http-effect-mediated-001"],
            "uncovered_ids": [],
        }
    )
    report_bytes = _json_bytes(report)
    report_path.write_bytes(report_bytes)
    for artifact_id, content in (
        ("denominator-effects", denominator_bytes),
        ("coverage-effects", report_bytes),
    ):
        artifact = next(
            item for item in core["artifacts"] if item["artifact_id"] == artifact_id
        )
        artifact["byte_length"] = len(content)
        artifact["sha256"] = "sha256:" + hashlib.sha256(content).hexdigest()

    handoff_path = packet / "receipts" / "10-handoff.json"
    old_handoff = ActionReceipt.from_dict(json.loads(handoff_path.read_text()))
    handoff_action = deepcopy(old_handoff.action)
    handoff_action["subject"]["coverage_hash"] = coverage_commitment(core)
    new_handoff = sign_action_receipt_v04(
        dataclasses.replace(old_handoff, action=handoff_action),
        _fixture_signer("http", "incident_commander"),
    ).to_dict()
    handoff_bytes = _json_bytes(new_handoff)
    handoff_path.write_bytes(handoff_bytes)
    timeline_entry = next(
        item
        for item in core["timeline"]
        if item["receipt_path"] == "receipts/10-handoff.json"
    )
    old_handoff_attestation = timeline_entry["attestation_hash"]
    timeline_entry.update(
        {
            "byte_length": len(handoff_bytes),
            "sha256": "sha256:" + hashlib.sha256(handoff_bytes).hexdigest(),
            "attestation_hash": new_handoff["hashes"]["attestation"],
        }
    )

    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text())
    witness["included_attestation_hashes"] = [
        new_handoff["hashes"]["attestation"]
        if item == old_handoff_attestation
        else item
        for item in witness["included_attestation_hashes"]
    ]
    witness["checkpoint_hash"] = canonical_hash(
        {
            "witness_id": witness["witness_id"],
            "root_ref": witness["root_ref"],
            "received_at": witness["received_at"],
            "witnessed_at": witness["witnessed_at"],
            "included_attestation_hashes": witness[
                "included_attestation_hashes"
            ],
        }
    )
    witness["proof"] = _fixture_signer("http", "witness").sign_domain(
        "witness-checkpoint", witness["checkpoint_hash"], schema="0.4"
    )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)
    witness_artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == "witness-checkpoint"
    )
    witness_artifact.update(
        {
            "byte_length": len(witness_bytes),
            "sha256": "sha256:" + hashlib.sha256(witness_bytes).hexdigest(),
        }
    )

    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "publish-receipt.json").write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in kernel.coverage if item.phase == "effect")
    assert kernel.exit_code == 1
    assert kernel.timeline_integrity == "FAILED"
    assert effect.status == "NOT_COMPUTED"
    assert any(
        "checkpoint evidence_refs must contain only" in item
        for item in kernel.errors
    )
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        effect_result = next(
            item for item in result["coverage"] if item["phase"] == "effect"
        )
        assert exit_code == result["exit_code"] == 1
        assert result["timeline_integrity"] == "FAILED"
        assert effect_result["status"] == "NOT_COMPUTED"


def test_phantom_claim_in_declared_coverage_is_rejected(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    report_path = packet / "coverage" / "http-effects.json"
    report = json.loads(report_path.read_text())
    report["phantom_receipt_ids"] = ["invented-receipt"]
    report_path.write_text(json.dumps(report))

    kernel = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in kernel.coverage if item.phase == "effect")
    assert kernel.exit_code == 1
    assert effect.status == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        effect_result = next(
            item for item in result["coverage"] if item["phase"] == "effect"
        )
        assert exit_code == result["exit_code"] == 1
        assert effect_result["status"] == "FAILED"


def test_conflicting_correction_fork_is_preserved_without_actor_time_selection(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    correction_template = ActionReceipt.from_dict(
        json.loads((packet / "receipts" / "11-correction.json").read_text())
    )
    first_subject = correction_template.action["subject"]
    alternate_replacement = core["statements"][-1]["attestation_hash"]
    fork = build_profile_receipt(
        action_type="incident.correct",
        subject={
            **first_subject,
            "correction_id": "http-correction-fork-002",
            "replacement_ref": alternate_replacement,
            "reason": "Conflicting signed correction retained for review.",
        },
        signer=_fixture_signer("http", "correction_authority"),
        envelope=correction_template.envelope,
        event_id="00000000-0000-4000-8000-000000000201",
        claimed_at="2026-07-26T12:59:00Z",
    )
    fork_path = "receipts/12-correction-fork.json"
    fork_bytes = _json_bytes(fork)
    (packet / fork_path).write_bytes(fork_bytes)
    core["timeline"].append(
        {
            "sequence": len(core["timeline"]) + 1,
            "receipt_path": fork_path,
            "byte_length": len(fork_bytes),
            "sha256": "sha256:" + hashlib.sha256(fork_bytes).hexdigest(),
            "attestation_hash": fork["hashes"]["attestation"],
            "action_type": "incident.correct",
        }
    )
    core["corrections"].append(
        {
            "receipt_path": fork_path,
            "attestation_hash": fork["hashes"]["attestation"],
        }
    )

    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text())
    witness["included_attestation_hashes"].append(
        fork["hashes"]["attestation"]
    )
    witness["checkpoint_hash"] = canonical_hash(
        {
            "witness_id": witness["witness_id"],
            "root_ref": witness["root_ref"],
            "received_at": witness["received_at"],
            "witnessed_at": witness["witnessed_at"],
            "included_attestation_hashes": witness[
                "included_attestation_hashes"
            ],
        }
    )
    witness["proof"] = _fixture_signer("http", "witness").sign_domain(
        "witness-checkpoint",
        witness["checkpoint_hash"],
        schema="0.4",
    )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)
    witness_artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == "witness-checkpoint"
    )
    witness_artifact["byte_length"] = len(witness_bytes)
    witness_artifact["sha256"] = (
        "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
    )

    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "publish-receipt.json").write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 0
    assert kernel.correction_status == "FORKED"
    assert any("does not select latest by actor time" in item for item in kernel.warnings)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 0
        assert result["correction_status"] == "FORKED"
        assert any(
            "does not select latest by actor time" in item
            for item in result["warnings"]
        )


@pytest.mark.parametrize(
    "include_observation,expected_status",
    [(True, "COMPUTED"), (False, "NOT_COMPUTED")],
    ids=["uncovered-effect", "declared-empty-group"],
)
@pytest.mark.parametrize(
    "authority_mutation",
    ["none", "cross_run", "cross_policy", "protocol_mismatch"],
)
def test_effect_anchor_distinguishes_uncovered_from_empty_denominator(
    tmp_path: Path,
    include_observation: bool,
    expected_status: str,
    authority_mutation: str,
) -> None:
    fixture = _PROFILE / "vectors" / "http-clean"
    mandate_template = ActionReceipt.from_dict(
        json.loads((fixture / "receipts" / "00-mandate.json").read_text())
    )
    decision_template = ActionReceipt.from_dict(
        json.loads(
            (fixture / "receipts" / "01-decision-permit.json").read_text()
        )
    )
    publish_template = ActionReceipt.from_dict(
        json.loads((fixture / "publish-receipt.json").read_text())
    )
    statement_template = ActionReceipt.from_dict(
        json.loads(
            (
                fixture
                / "receipts"
                / "06-denominator-effect-checkpoint.json"
            ).read_text()
        )
    )
    evaluator = _fixture_signer("http", "evaluation_authority")
    gateway = _fixture_signer("http", "gateway")
    target = _fixture_signer("http", "target")
    publisher = _fixture_signer("http", "publisher")
    role_issuers = {
        "evaluation_authority": evaluator.issuer,
        "gateway": gateway.issuer,
        "target": target.issuer,
        "publisher": publisher.issuer,
    }
    run_id = "post-effect-persistence-failure"
    incident_id = "incident-persistence-failure"
    mandate = build_profile_receipt(
        action_type="eval.run.authorize",
        subject={
            "profile": incident_pilots.PROFILE,
            "issuer_role": "evaluation_authority",
            "run_id": run_id,
            "model_id": "fixture://agent",
            "harness_id": "persistence-failure/1",
            "safeguards_id": "fixed-synthetic/1",
            "authorized_targets": ["http://127.0.0.1/fixed"],
            "budgets": {
                "compute_units": 0,
                "max_actions": 1,
                "duration_seconds": 60,
            },
            "prohibitions": ["external-network"],
            "valid_from": "2026-07-26T12:00:00Z",
            "valid_until": "2026-07-26T12:05:00Z",
            "policy_digest": "sha256:" + "10" * 32,
            "role_issuers": role_issuers,
        },
        signer=evaluator,
        envelope=mandate_template.envelope,
        event_id="00000000-0000-4000-8000-000000000101",
        claimed_at="2026-07-26T12:00:00Z",
    )
    decision = build_profile_receipt(
        action_type="capability.decide",
        subject={
            "profile": incident_pilots.PROFILE,
            "issuer_role": "gateway",
            "run_id": (
                "cross-run-graft"
                if authority_mutation == "cross_run"
                else run_id
            ),
            "request_id": "request-persist-001",
            "decision_event_id": "decision-persist-001",
            "request_hash": "sha256:" + "20" * 32,
            "mandate_ref": mandate["hashes"]["attestation"],
            "policy_digest": (
                "sha256:" + "99" * 32
                if authority_mutation == "cross_policy"
                else "sha256:" + "10" * 32
            ),
            "decision": "PERMIT",
            "rationale_codes": ["FIXED_ALLOW"],
            "anchor_id": "http-gateway-ingress",
            "protocol": (
                "mcp"
                if authority_mutation == "protocol_mismatch"
                else "http"
            ),
            "evidence_hash": "sha256:" + "30" * 32,
        },
        signer=gateway,
        envelope=decision_template.envelope,
        event_id="00000000-0000-4000-8000-000000000102",
        claimed_at="2026-07-26T12:01:00Z",
        evidence_refs=(
            {
                "name": "gateway_ingress",
                "hash": "sha256:" + "30" * 32,
                "grounding": "self_asserted",
            },
        ),
    )
    observation = {
        "anchor_id": "http-target-effects",
        "observation_id": "effect-persisted-before-receipt-failed",
        "run_id": run_id,
        "protocol": "http",
        "operation_ref": "sha256:" + "40" * 32,
        "phase": "effect",
        "evidence_hash": "sha256:" + "50" * 32,
    }
    decision_subject = decision["action"]["subject"]
    decision_observation = {
        "anchor_id": decision_subject["anchor_id"],
        "observation_id": decision_subject["decision_event_id"],
        "run_id": decision_subject["run_id"],
        "protocol": decision_subject["protocol"],
        "operation_ref": decision_subject["request_hash"],
        "phase": "decision",
        "evidence_hash": decision_subject["evidence_hash"],
    }
    decision_denominator = {
        "schema_version": 1,
        "anchor_id": "http-gateway-ingress",
        "phase": "decision",
        "protocol": "http",
        "observations": [decision_observation],
    }
    decision_denominator_bytes = _json_bytes(decision_denominator)
    decision_denominator_hash = (
        "sha256:" + hashlib.sha256(decision_denominator_bytes).hexdigest()
    )
    decision_coverage = {
        "schema_version": 1,
        "anchor_id": "http-gateway-ingress",
        "phase": "decision",
        "protocol": "http",
        "denominator_sha256": decision_denominator_hash,
        "minimum_verification_depth": "attestation",
        "total": 1,
        "receipted": 1,
        "covered_ids": [decision_subject["decision_event_id"]],
        "uncovered_ids": [],
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    decision_coverage_bytes = _json_bytes(decision_coverage)
    decision_checkpoint = build_profile_receipt(
        action_type="incident.statement",
        subject={
            "profile": incident_pilots.PROFILE,
            "issuer_role": "gateway",
            "statement_id": "decision-denominator-checkpoint",
            "incident_id": incident_id,
            "topic": "denominator:http-gateway-ingress:decision:http",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [decision_denominator_hash],
        },
        signer=gateway,
        envelope=decision_template.envelope,
        event_id="00000000-0000-4000-8000-000000000105",
        claimed_at="2026-07-26T12:01:30Z",
        evidence_refs=(
            {
                "name": "decision_denominator",
                "hash": decision_denominator_hash,
                "grounding": "self_asserted",
            },
        ),
    )
    denominator = {
        "schema_version": 1,
        "anchor_id": "http-target-effects",
        "phase": "effect",
        "protocol": "http",
        "observations": [observation] if include_observation else [],
    }
    denominator_bytes = _json_bytes(denominator)
    coverage_report = {
        "schema_version": 1,
        "anchor_id": "http-target-effects",
        "phase": "effect",
        "protocol": "http",
        "denominator_sha256": (
            "sha256:" + hashlib.sha256(denominator_bytes).hexdigest()
        ),
        "minimum_verification_depth": "attestation",
        "total": 1 if include_observation else 0,
        "receipted": 0,
        "covered_ids": [],
        "uncovered_ids": (
            [observation["observation_id"]] if include_observation else []
        ),
        "phantom_receipt_ids": [],
        "invalid_receipts": [],
    }
    coverage_bytes = _json_bytes(coverage_report)
    denominator_hash = "sha256:" + hashlib.sha256(denominator_bytes).hexdigest()
    checkpoint = build_profile_receipt(
        action_type="incident.statement",
        subject={
            "profile": incident_pilots.PROFILE,
            "issuer_role": "target",
            "statement_id": "effect-denominator-checkpoint",
            "incident_id": incident_id,
            "topic": "denominator:http-target-effects:effect:http",
            "claim": "ORDERED_DENOMINATOR_SNAPSHOT",
            "epistemic_status": "observed",
            "evidence_refs": [denominator_hash],
        },
        signer=target,
        envelope=statement_template.envelope,
        event_id="00000000-0000-4000-8000-000000000103",
        claimed_at="2026-07-26T12:02:00Z",
        evidence_refs=(
            {
                "name": "effect_denominator",
                "hash": denominator_hash,
                "grounding": "self_asserted",
            },
        ),
    )
    artifact_entries = [
        released_artifact_entry(
            artifact_id="decision-denominator",
            path="indexes/decisions.json",
            role="denominator",
            media_type="application/json",
            content=decision_denominator_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="decision-coverage",
            path="coverage/decisions.json",
            role="coverage",
            media_type="application/json",
            content=decision_coverage_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="effect-denominator",
            path="indexes/effects.json",
            role="denominator",
            media_type="application/json",
            content=denominator_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
        released_artifact_entry(
            artifact_id="effect-coverage",
            path="coverage/effects.json",
            role="coverage",
            media_type="application/json",
            content=coverage_bytes,
            retention="P7D",
            access_condition="pilot-operator",
        ),
    ]
    packet = assemble_incident_packet(
        tmp_path / "persistence-packet",
        packet_id="packet-persistence-failure",
        incident_id=incident_id,
        classification="synthetic-test",
        role_issuers=role_issuers,
            timeline_receipts=[
                ("receipts/00-mandate.json", mandate),
                ("receipts/01-decision.json", decision),
                (
                    "receipts/02-decision-denominator-checkpoint.json",
                    decision_checkpoint,
                ),
                ("receipts/03-effect-denominator-checkpoint.json", checkpoint),
            ],
        artifact_entries=artifact_entries,
            released_artifacts={
                "indexes/decisions.json": decision_denominator_bytes,
                "coverage/decisions.json": decision_coverage_bytes,
                "indexes/effects.json": denominator_bytes,
            "coverage/effects.json": coverage_bytes,
        },
            denominators=[
                {
                    "anchor_id": "http-gateway-ingress",
                    "phase": "decision",
                    "protocol": "http",
                    "artifact_id": "decision-denominator",
                    "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
                    "checkpoint_attestation": decision_checkpoint[
                        "hashes"
                    ]["attestation"],
                },
                {
                "anchor_id": "http-target-effects",
                "phase": "effect",
                "protocol": "http",
                "artifact_id": "effect-denominator",
                "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
                "checkpoint_attestation": checkpoint["hashes"]["attestation"],
            }
        ],
            coverage=[
                {
                    "anchor_id": "http-gateway-ingress",
                    "phase": "decision",
                    "protocol": "http",
                    "artifact_id": "decision-coverage",
                    "minimum_verification_depth": "attestation",
                },
                {
                "anchor_id": "http-target-effects",
                "phase": "effect",
                "protocol": "http",
                "artifact_id": "effect-coverage",
                "minimum_verification_depth": "attestation",
            }
        ],
        publisher_signer=publisher,
        publisher_envelope=publish_template.envelope,
        publish_event_id="00000000-0000-4000-8000-000000000104",
        publish_claimed_at="2026-07-26T12:03:00Z",
            statements=[
                {
                    "receipt_path": (
                        "receipts/02-decision-denominator-checkpoint.json"
                    ),
                    "attestation_hash": decision_checkpoint[
                        "hashes"
                    ]["attestation"],
                },
                {
                    "receipt_path": "receipts/03-effect-denominator-checkpoint.json",
                    "attestation_hash": checkpoint["hashes"]["attestation"],
            }
        ],
    )
    context_value = {
        "accepted_issuers_by_role": {
            role: [issuer] for role, issuer in role_issuers.items()
        },
        "trusted_witness_roots": [],
        "team_controlled_roles": list(role_issuers),
    }
    context = IncidentVerificationContext.from_dict(context_value)
    context_path = tmp_path / "persistence-context.json"
    context_path.write_text(json.dumps(context_value))

    kernel = verify_incident_packet(packet.root, context)
    effect = next(row for row in kernel.coverage if row.phase == "effect")
    expected_exit = 0 if authority_mutation == "none" else 1
    assert kernel.exit_code == expected_exit
    assert kernel.packet_integrity == "VERIFIED"
    assert kernel.receipt_integrity == "VERIFIED"
    assert kernel.authority_binding == (
        "FAILED"
        if authority_mutation in {"cross_run", "cross_policy"}
        else "VERIFIED"
    )
    assert kernel.timeline_integrity == (
        "FAILED"
        if authority_mutation == "protocol_mismatch"
        else "VERIFIED"
    )
    assert effect.status == expected_status
    assert effect.covered_ids == ()
    assert effect.uncovered_ids == (
        (observation["observation_id"],) if include_observation else ()
    )
    assert effect.reasons == (
        ()
        if include_observation
        else ("denominator.observations must contain at least one event",)
    )
    for checker in ("python", "node"):
        exit_code, result = _checker(
            checker,
            packet.root,
            context_path=context_path,
        )
        assert exit_code == result["exit_code"] == expected_exit
        assert result["packet_integrity"] == "VERIFIED"
        assert result["receipt_integrity"] == "VERIFIED"
        assert result["authority_binding"] == (
            "FAILED"
            if authority_mutation in {"cross_run", "cross_policy"}
            else "VERIFIED"
        )
        assert result["timeline_integrity"] == (
            "FAILED"
            if authority_mutation == "protocol_mismatch"
            else "VERIFIED"
        )
        effect_result = next(
            row for row in result["coverage"] if row["phase"] == "effect"
        )
        assert effect_result["status"] == expected_status
        assert effect_result["uncovered_ids"] == (
            [observation["observation_id"]] if include_observation else []
        )
        assert effect_result["reasons"] == (
            []
            if include_observation
            else ["denominator.observations must contain at least one event"]
        )


def test_packet_without_a_denominator_is_malformed(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    core["denominators"] = []
    core["coverage"] = []
    core_path.write_text(json.dumps(core))

    with pytest.raises(IncidentPacketError, match="at least one"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


@pytest.mark.parametrize(
    "mutation",
    ["missing_effect", "duplicate_decision_phase", "split_protocol_phases"],
)
def test_protocol_requires_one_decision_and_one_effect_anchor(
    tmp_path: Path,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    effect = next(
        item for item in core["denominators"] if item["phase"] == "effect"
    )
    if mutation == "missing_effect":
        effect_key = (
            effect["anchor_id"],
            effect["phase"],
            effect["protocol"],
        )
        core["denominators"] = [
            item for item in core["denominators"]
            if (
                item["anchor_id"],
                item["phase"],
                item["protocol"],
            )
            != effect_key
        ]
        core["coverage"] = [
            item for item in core["coverage"]
            if (
                item["anchor_id"],
                item["phase"],
                item["protocol"],
            )
            != effect_key
        ]
    elif mutation == "duplicate_decision_phase":
        effect["phase"] = "decision"
        next(
            item
            for item in core["coverage"]
            if item["artifact_id"] == "coverage-effects"
        )["phase"] = "decision"
    else:
        effect["protocol"] = "mcp"
        next(
            item
            for item in core["coverage"]
            if item["artifact_id"] == "coverage-effects"
        )["protocol"] = "mcp"
    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    publish_action = deepcopy(old_publish.action)
    publish_action["subject"]["packet_core_hash"] = canonical_hash(core)
    publish_action["subject"]["component_hashes"] = {
        name: canonical_hash(core[name])
        for name in (
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
    }
    (packet / "publish-receipt.json").write_bytes(
        _json_bytes(
            sign_action_receipt_v04(
                dataclasses.replace(old_publish, action=publish_action),
                _fixture_signer("http", "publisher"),
            ).to_dict()
        )
    )

    with pytest.raises(IncidentPacketError, match="exactly one decision"):
        parse_incident_packet(packet)
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


def test_coverage_boolean_cannot_coerce_to_integer(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path)
    coverage_path = packet / "coverage" / "http-effects.json"
    coverage = json.loads(coverage_path.read_text())
    coverage["schema_version"] = True
    coverage["receipted"] = True
    coverage_path.write_text(json.dumps(coverage))

    kernel = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in kernel.coverage if item.phase == "effect")
    assert effect.status == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        effect_result = next(
            item for item in result["coverage"] if item["phase"] == "effect"
        )
        assert exit_code == 1
        assert effect_result["status"] == "FAILED"


def test_packet_roles_do_not_bootstrap_trust(tmp_path: Path) -> None:
    packet = _PROFILE / "vectors" / "http-clean"
    empty_context_path = tmp_path / "context.json"
    empty_context_path.write_text(
        json.dumps(
            {
                "accepted_issuers_by_role": {},
                "trusted_witness_roots": [],
                "team_controlled_roles": [],
            }
        )
    )
    empty_context = IncidentVerificationContext.from_dict(
        json.loads(empty_context_path.read_text())
    )

    kernel = verify_incident_packet(packet, empty_context)
    assert kernel.exit_code == 1
    assert kernel.issuer_authenticity == "FAILED"
    for checker in ("python", "node"):
        if checker == "python":
            command = [
                "python3", "-I", str(_PYTHON_CHECKER), str(packet),
                "--context", str(empty_context_path),
            ]
        else:
            command = [
                "node", str(_NODE_CHECKER), str(packet),
                "--context", str(empty_context_path),
            ]
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=20
        )
        result = json.loads(completed.stdout)
        assert completed.returncode == result["exit_code"] == 1
        assert result["issuer_authenticity"] == "FAILED"


def test_context_rejects_cross_role_issuer_collision(tmp_path: Path) -> None:
    packet = _PROFILE / "vectors" / "http-clean"
    value = json.loads(
        (_PROFILE / "contexts" / "http-context.json").read_text()
    )
    publisher = value["accepted_issuers_by_role"]["publisher"][0]
    value["accepted_issuers_by_role"]["gateway"].append(publisher)
    context_path = tmp_path / "colliding-context.json"
    context_path.write_text(json.dumps(value))

    with pytest.raises(ValueError, match="assigned to both"):
        IncidentVerificationContext.from_dict(value)
    for checker in ("python", "node"):
        exit_code, result = _checker(
            checker, packet, context_path=context_path
        )
        assert exit_code == result["exit_code"] == 2
        assert result["malformed"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "checkpoint_hash",
        "proof_purpose",
        "chronology",
        "inclusion",
        "anchored_before",
    ],
)
def test_signed_witness_mutations_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text())
    if mutation == "checkpoint_hash":
        witness["checkpoint_hash"] = "sha256:" + "00" * 32
    elif mutation == "proof_purpose":
        witness["proof"]["purpose"] = "content"
    elif mutation == "chronology":
        witness["received_at"] = "2026-07-26T12:12:00Z"
        witness["witnessed_at"] = "2026-07-26T12:11:00Z"
    elif mutation == "anchored_before":
        witness["anchored_before"] = "2026-07-26T12:10:00Z"
    else:
        witness["included_attestation_hashes"].pop()
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)
    if mutation == "anchored_before":
        core_path = packet / "packet-core.json"
        core = json.loads(core_path.read_text())
        witness_artifact = next(
            item
            for item in core["artifacts"]
            if item["artifact_id"] == "witness-checkpoint"
        )
        witness_artifact["byte_length"] = len(witness_bytes)
        witness_artifact["sha256"] = (
            "sha256:" + hashlib.sha256(witness_bytes).hexdigest()
        )
        core_path.write_bytes(_json_bytes(core))
        old_publish = ActionReceipt.from_dict(
            json.loads((packet / "publish-receipt.json").read_text())
        )
        (packet / "publish-receipt.json").write_bytes(
            _json_bytes(
                build_publish_receipt(
                    core,
                    signer=_fixture_signer("http", "publisher"),
                    envelope=old_publish.envelope,
                    event_id=old_publish.event_id,
                    claimed_at=old_publish.claimed_at,
                    producer=old_publish.producer,
                )
            )
        )

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 1
    assert kernel.witness_inclusion == "FAILED"
    assert kernel.temporal_evidence["received_at"] == ()
    assert kernel.temporal_evidence["witnessed_at"] == ()
    assert kernel.temporal_evidence["anchored_before"] == ()
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["witness_inclusion"] == "FAILED"
        assert result["temporal_evidence"]["received_at"] == []
        assert result["temporal_evidence"]["witnessed_at"] == []
        assert result["temporal_evidence"]["anchored_before"] == []


def test_actor_claimed_time_cannot_promote_itself_to_witness_time(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    witness_artifact_ids = {
        item["artifact_id"] for item in core["witnesses"]
    }
    witness_paths = {
        item["path"]
        for item in core["artifacts"]
        if item["artifact_id"] in witness_artifact_ids
    }
    core["witnesses"] = []
    core["artifacts"] = [
        item
        for item in core["artifacts"]
        if item["artifact_id"] not in witness_artifact_ids
    ]
    for relative in witness_paths:
        (packet / relative).unlink()
    core_path.write_bytes(_json_bytes(core))
    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    (packet / "publish-receipt.json").write_bytes(
        _json_bytes(
            build_publish_receipt(
                core,
                signer=_fixture_signer("http", "publisher"),
                envelope=old_publish.envelope,
                event_id=old_publish.event_id,
                claimed_at=old_publish.claimed_at,
                producer=old_publish.producer,
            )
        )
    )

    before = verify_incident_packet(packet, _context("http"))
    promoted_actor_time = "2099-12-31T23:59:59Z"
    _rewrite_signed_receipt_and_republish(
        packet,
        protocol="http",
        receipt_path="receipts/11-correction.json",
        mutate_subject=lambda _subject: None,
        claimed_at=promoted_actor_time,
    )
    after = verify_incident_packet(packet, _context("http"))

    assert before.exit_code == after.exit_code == 0, (
        before.errors,
        after.errors,
    )
    assert promoted_actor_time not in before.temporal_evidence["claimed_at"]
    assert promoted_actor_time in after.temporal_evidence["claimed_at"]
    for label in ("received_at", "witnessed_at", "anchored_before"):
        assert before.temporal_evidence[label] == ()
        assert after.temporal_evidence[label] == ()
    assert after.witness_inclusion == "NOT_COMPUTED"
    assert after.witness_root_trust == "NOT_COMPUTED"

    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 0
        assert promoted_actor_time in result["temporal_evidence"]["claimed_at"]
        for label in ("received_at", "witnessed_at", "anchored_before"):
            assert result["temporal_evidence"][label] == []
        assert result["witness_inclusion"] == "NOT_COMPUTED"
        assert result["witness_root_trust"] == "NOT_COMPUTED"


def test_publisher_alone_cannot_rebind_a_redaction_record(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text())
    redaction = core["redactions"][0]
    record_artifact = next(
        item
        for item in core["artifacts"]
        if item["artifact_id"] == redaction["record_artifact_id"]
    )
    record_path = packet / record_artifact["path"]
    record = json.loads(record_path.read_text())
    record["released_sha256"] = "sha256:" + "00" * 32
    record_bytes = _json_bytes(record)
    record_path.write_bytes(record_bytes)
    record_artifact["byte_length"] = len(record_bytes)
    record_artifact["sha256"] = (
        "sha256:" + hashlib.sha256(record_bytes).hexdigest()
    )
    core_path.write_bytes(_json_bytes(core))

    old_publish = ActionReceipt.from_dict(
        json.loads((packet / "publish-receipt.json").read_text())
    )
    (packet / "publish-receipt.json").write_bytes(
        _json_bytes(
            build_publish_receipt(
                core,
                signer=_fixture_signer("http", "publisher"),
                envelope=old_publish.envelope,
                event_id=old_publish.event_id,
                claimed_at=old_publish.claimed_at,
                producer=old_publish.producer,
            )
        )
    )

    kernel = verify_incident_packet(packet, _context("http"))
    assert kernel.exit_code == 1
    assert kernel.packet_integrity == "VERIFIED"
    assert kernel.artifact_integrity == "VERIFIED"
    assert kernel.receipt_integrity == "VERIFIED"
    assert kernel.redaction_binding == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["packet_integrity"] == "VERIFIED"
        assert result["artifact_integrity"] == "VERIFIED"
        assert result["receipt_integrity"] == "VERIFIED"
        assert result["redaction_binding"] == "FAILED"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_executable_form",
        "unknown_remedy_field",
        "blank_semantic_forum_endpoint",
    ],
)
def test_signed_publish_receipt_nested_shapes_fail_closed_with_checker_parity(
    tmp_path: Path,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    publish_path = packet / "publish-receipt.json"
    publish = json.loads(publish_path.read_text())
    if mutation == "unknown_executable_form":
        definition = {
            "form": "benign-unknown-form",
            "schema": {},
        }
        publish["conventions"] = [
            {
                "name": "hostile-executable-form",
                "kind": "executable",
                "definition": definition,
                "definition_hash": canonical_hash(definition),
            }
        ]
    elif mutation == "unknown_remedy_field":
        publish["remedy"]["benign_note"] = "closed nested object"
    else:
        publish["conventions"] = [
            {
                "name": "semantic-boundary",
                "scope": "incident.packet.publish",
                "kind": "semantic",
                "definition_hash": "sha256:" + "11" * 32,
                "forum": {
                    "log_endpoint": "   ",
                    "trusted_root_ref": "did:key:trusted-root",
                },
            }
        ]
    publish = _resign_wire_receipt(
        publish, _fixture_signer("http", "publisher")
    )
    publish_path.write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == kernel["exit_code"]
        assert result["receipt_integrity"] == kernel["receipt_integrity"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_grant_member",
        "unknown_checkpoint_member",
        "missing_checkpoint_member",
        "unknown_grant_proof_member",
        "missing_grant_proof_member",
    ],
)
def test_signed_delegation_nested_shapes_fail_closed_with_checker_parity(
    tmp_path: Path,
    mutation: str,
) -> None:
    packet = _copy_packet(tmp_path)
    publish_path = packet / "publish-receipt.json"
    publish = json.loads(publish_path.read_text())
    publish["mandate"]["deed_schema"] = "0.3"
    issuer = publish["signature"]["issuer"]
    if mutation == "unknown_grant_member":
        grant = {"benign_note": "closed nested object"}
    else:
        grant = {
            "grantor": issuer,
            "grantee": issuer,
            "principal": issuer,
            "parent": None,
            "policy_digest": "sha256:" + "22" * 32,
            "scope_digest": "sha256:" + "33" * 32,
            "proof": dict(publish["signature"]),
        }
        if mutation in {
            "unknown_checkpoint_member",
            "missing_checkpoint_member",
        }:
            grant["not_before"] = {"domain": "sequence", "value": 0}
            if mutation == "unknown_checkpoint_member":
                grant["not_before"]["benign_note"] = "closed nested object"
            else:
                del grant["not_before"]["value"]
        elif mutation == "unknown_grant_proof_member":
            grant["proof"]["benign_note"] = "closed nested object"
        else:
            del grant["proof"]["proofValue"]
    publish["mandate"]["authority"]["delegation"] = [grant]
    publish = _resign_wire_receipt(
        publish, _fixture_signer("http", "publisher")
    )
    publish_path.write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == kernel["exit_code"]
        assert result["receipt_integrity"] == kernel["receipt_integrity"]


def test_signed_unrelated_authority_principal_fails_all_checkers(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    publish_path = packet / "publish-receipt.json"
    publish = json.loads(publish_path.read_text())
    publish["mandate"]["authority"]["principal"] = (
        _fixture_signer("http", "gateway").issuer
    )
    publish = _resign_wire_receipt(
        publish, _fixture_signer("http", "publisher")
    )
    publish_path.write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    assert kernel["signature_status"] == "VERIFIED"
    assert kernel["authority_binding"] == "FAILED"
    assert kernel["occurrence_binding"] == "VERIFIED"
    assert any(
        "incident profile requires direct deed_schema 0.2 authority" in error
        for error in kernel["errors"]
    )
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == 1
        assert result["receipt_integrity"] == "FAILED"
        assert result["signature_status"] == "VERIFIED"
        assert result["authority_binding"] == "FAILED"
        assert result["occurrence_binding"] == "VERIFIED"
        assert any(
            "incident profile requires direct deed_schema 0.2 authority" in error
            for error in result["errors"]
        )


@pytest.mark.parametrize("kind", ["python_named_group", "semantic"])
def test_profile_nonempty_conventions_have_cross_runtime_checker_parity(
    tmp_path: Path,
    kind: str,
) -> None:
    packet = _copy_packet(tmp_path)
    publish_path = packet / "publish-receipt.json"
    publish = json.loads(publish_path.read_text())
    if kind == "python_named_group":
        definition = {
            "form": "jsonschema+quantum/1",
            "schema": {
                "properties": {
                    "packet_id": {
                        "type": "string",
                        "pattern": "(?P<packet>a)",
                    }
                }
            },
        }
        convention = {
            "name": "host-runtime-regex",
            "scope": "incident.packet.publish",
            "kind": "executable",
            "definition": definition,
            "definition_hash": canonical_hash(definition),
        }
    else:
        convention = {
            "name": "semantic-control",
            "scope": "incident.packet.publish",
            "kind": "semantic",
            "definition_hash": "sha256:" + "44" * 32,
            "forum": {
                "log_endpoint": "https://appeals.example.test/log",
                "trusted_root_ref": "did:key:trusted-root",
            },
        }
    publish["conventions"] = [convention]
    publish = _resign_wire_receipt(
        publish, _fixture_signer("http", "publisher")
    )
    publish_path.write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    proof_dimensions = (
        kernel["signature_status"],
        kernel["authority_binding"],
        kernel["occurrence_binding"],
    )
    assert proof_dimensions == ("VERIFIED", "VERIFIED", "VERIFIED")
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == kernel["exit_code"]
        assert result["receipt_integrity"] == kernel["receipt_integrity"]
        assert (
            result["signature_status"],
            result["authority_binding"],
            result["occurrence_binding"],
        ) == proof_dimensions


def test_stored_hash_corruption_does_not_poison_independent_proof_dimensions(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path)
    publish_path = packet / "publish-receipt.json"
    publish = json.loads(publish_path.read_text())
    publish["hashes"]["content"] = "sha256:" + "00" * 32
    publish_path.write_bytes(_json_bytes(publish))

    kernel = verify_incident_packet(packet, _context("http")).to_dict()
    proof_dimensions = (
        kernel["signature_status"],
        kernel["authority_binding"],
        kernel["occurrence_binding"],
    )
    assert kernel["exit_code"] == 1
    assert kernel["receipt_integrity"] == "FAILED"
    assert proof_dimensions == ("VERIFIED", "VERIFIED", "VERIFIED")
    for checker in ("python", "node"):
        exit_code, result = _checker(checker, packet)
        assert exit_code == result["exit_code"] == kernel["exit_code"]
        assert result["receipt_integrity"] == kernel["receipt_integrity"]
        assert (
            result["signature_status"],
            result["authority_binding"],
            result["occurrence_binding"],
        ) == proof_dimensions


@pytest.mark.parametrize("protocol", ["http", "mcp"])
def test_verification_is_offline(
    protocol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_network(*_args, **_kwargs):
        raise AssertionError("packet verification attempted network access")

    monkeypatch.setattr(socket, "socket", refuse_network)
    result = verify_incident_packet(
        _PROFILE / "vectors" / f"{protocol}-clean",
        _context(protocol),
    )
    assert result.exit_code == 0


@pytest.mark.parametrize("protocol", ["http", "mcp"])
def test_public_packet_has_no_heuristic_privacy_findings(protocol: str) -> None:
    packet = _PROFILE / "vectors" / f"{protocol}-clean"
    assert _public_archive_privacy_findings(packet) == []


def test_privacy_detector_catches_nested_and_derived_material(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    packet.mkdir()
    low_entropy = "sha256:" + hashlib.sha256(b"password").hexdigest()
    (packet / "nested.json").write_text(
        json.dumps(
            {
                "outer": {
                    "client_secret": "not-for-release",
                    "authorization_value": "Bearer abcdefghijklmnop",
                    "derived": low_entropy,
                    "contact": "person@example.test",
                }
            }
        )
    )
    (packet / "failure.jsonl").write_text(
        json.dumps(
            {
                "message": (
                    'Traceback (most recent call last): File "/Users/alice/'
                    'private.py", line 1'
                ),
                "marker": "personal-data-sentinel",
            }
        )
        + "\n"
    )

    findings = _public_archive_privacy_findings(packet)

    assert any(item.startswith("sensitive-key:") for item in findings)
    assert any(item.startswith("credential-value:") for item in findings)
    assert any(item.startswith("low-entropy-secret-digest:") for item in findings)
    assert any(item.startswith("personal-data:") for item in findings)
    assert any(item.startswith("exception-or-host-path:") for item in findings)


def _pilot_run(
    receipt: dict,
    decision_observations: tuple[dict, ...],
    *,
    copies: int = 1,
) -> PilotRun:
    subject = receipt["action"]["subject"]
    return PilotRun(
        protocol="http",
        output_dir=Path("/tmp/not-used"),
        run_id=subject["run_id"],
        role_issuers={"gateway": receipt["signature"]["issuer"]},
        decision_observations=decision_observations,
        effect_observations=(),
        receipts=tuple(deepcopy(receipt) for _ in range(copies)),
    )


def test_pilot_coverage_uses_decision_event_id_and_exact_projection() -> None:
    packet = _PROFILE / "vectors" / "http-clean"
    receipt = json.loads(
        (packet / "receipts" / "01-decision-permit.json").read_text()
    )
    denominator = json.loads(
        (packet / "indexes" / "http-gateway-ingress.json").read_text()
    )
    observation = deepcopy(denominator["observations"][0])

    clean = summarize_coverage(_pilot_run(receipt, (observation,)))
    assert clean["decision"]["coverage"] == "1/1"

    observation["operation_ref"] = "sha256:" + "00" * 32
    mismatched = summarize_coverage(_pilot_run(receipt, (observation,)))
    assert mismatched["decision"]["coverage"] == "0/1"
    assert mismatched["decision"]["uncovered"] == [observation["observation_id"]]


@pytest.mark.parametrize("copies", [2, 3])
def test_pilot_duplicate_receipts_cannot_cover_an_observation(
    copies: int,
) -> None:
    packet = _PROFILE / "vectors" / "http-clean"
    receipt = json.loads(
        (packet / "receipts" / "01-decision-permit.json").read_text()
    )
    denominator = json.loads(
        (packet / "indexes" / "http-gateway-ingress.json").read_text()
    )
    observation = denominator["observations"][0]

    report = summarize_coverage(
        _pilot_run(receipt, (observation,), copies=copies)
    )

    assert report["decision"]["coverage"] == "0/1"
    assert report["decision"]["uncovered"] == [observation["observation_id"]]
    assert report["decision"]["invalid_receipts"]


def _raw_http_request(port: int, body: bytes, length: int | None = None) -> bytes:
    declared = len(body) if length is None else length
    request = (
        b"POST /act HTTP/1.1\r\n"
        + b"Host: 127.0.0.1\r\n"
        + f"Content-Length: {declared}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Connection: close\r\n\r\n"
        + body
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as stream:
        stream.sendall(request)
        stream.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while chunk := stream.recv(65_536):
            chunks.append(chunk)
    return b"".join(chunks)


def test_http_boundary_rejects_duplicate_and_oversized_json_without_records(
    tmp_path: Path,
) -> None:
    ctx = multiprocessing.get_context("spawn")
    ready_parent, ready_child = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    ingress = tmp_path / "ingress.jsonl"
    process = ctx.Process(
        target=incident_pilots._http_gateway_process,
        kwargs={
            "run_id": "hostile-http-run",
            "target_port": 1,
            "ingress_path": str(ingress),
            "receipt_dir": str(receipts),
            "signer_seed": b"g" * 32,
            "mandate_ref": "sha256:" + "11" * 32,
            "ready": ready_child,
            "stop": stop,
        },
    )
    process.start()
    assert ready_parent.poll(10)
    port = ready_parent.recv()
    try:
        duplicate = _raw_http_request(
            port,
            (
                b'{"operation":"append","operation":"append",'
                b'"mode":"deny","value":"refused"}'
            ),
        )
        oversized = _raw_http_request(port, b"{}", length=4097)
        assert b" 400 " in duplicate.split(b"\r\n", 1)[0]
        assert b" 413 " in oversized.split(b"\r\n", 1)[0]
        assert not ingress.exists()
        assert list(receipts.iterdir()) == []
    finally:
        stop.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


@pytest.mark.parametrize(
    "hostile_line",
    [
        b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n',
        b"x" * 8193,
    ],
)
def test_mcp_boundary_rejects_hostile_lines_without_records(
    tmp_path: Path,
    hostile_line: bytes,
) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    ingress = tmp_path / "ingress.jsonl"
    source_root = str(_ROOT / "src")
    environment = {
        "PYTHONPATH": source_root,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    command = [
        sys.executable,
        "-m",
        "bulla.experimental.incident_pilots",
        "_mcp_boundary",
        "--run-id",
        "hostile-mcp-run",
        "--backend-port",
        "1",
        "--ingress",
        str(ingress),
        "--receipts",
        str(receipts),
        "--mandate-ref",
        "sha256:" + "22" * 32,
    ]
    completed = subprocess.run(
        command,
        input=(
            json.dumps({"signer_seed": (b"b" * 32).hex()}).encode() + b"\n"
            + hostile_line
        ),
        capture_output=True,
        check=False,
        timeout=20,
        env=environment,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert not ingress.exists()
    assert list(receipts.iterdir()) == []


def test_mcp_pilot_excludes_seed_and_ambient_environment_from_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    real_popen = subprocess.Popen
    ambient_key = "BULLA_HOSTILE_AMBIENT_SENTINEL"
    ambient_value = "must-not-cross-boundary"
    monkeypatch.setenv(ambient_key, ambient_value)

    def inspecting_popen(*args, **kwargs):
        captured["args"] = args[0]
        captured["env"] = dict(kwargs.get("env") or {})
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(incident_pilots.subprocess, "Popen", inspecting_popen)
    run = run_mcp_pilot(tmp_path / "mcp-runtime")

    child_args = [str(item) for item in captured["args"]]
    child_env = captured["env"]
    assert run.expected_effect_coverage == "1/2"
    assert "--signer-seed" not in child_args
    assert "signer_seed" not in " ".join(child_args)
    assert ambient_key not in child_env
    assert ambient_value not in child_env.values()


@pytest.mark.parametrize("runner", [run_http_pilot, run_mcp_pilot])
def test_live_pilot_requires_a_new_output_directory(
    tmp_path: Path,
    runner: Callable[[Path], PilotRun],
) -> None:
    with pytest.raises(FileExistsError, match="must not already exist"):
        runner(tmp_path)
