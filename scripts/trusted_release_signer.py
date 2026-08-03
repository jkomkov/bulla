#!/usr/bin/env python3
"""Minimal release signer for default-branch, manually approved workflows.

This tool deliberately does not import ``bulla`` or install the candidate
wheel. Candidate code prepares an unsigned receipt; this script treats that
receipt, the PyPI artifacts, the archived test summary, and the signed slot as
untrusted data and validates their closed release contract before signing.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


SLOT_SCHEMA = "release-slot/0.2"
SLOT_KIND = "bulla.release-slot"
PROOF_TYPE = "bulla/ed25519-2026"
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERIFICATION_KIT_NAME = "action-receipt-v0.2-verification-kit.zip"
MAX_VERIFICATION_KIT_BYTES = 16 * 1024 * 1024
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SLOT_FIELDS = {
    "schema_version",
    "kind",
    "package",
    "version",
    "source_commit",
    "source_tree_sha256",
    "release_issuer_id",
    "release_issuer_record_hash",
    "trust_context_schema",
    "expected_publisher",
    "opened_at",
    "close_deadline",
    "slot_hash",
    "proof",
}
RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "action",
    "diagnostic_ref",
    "evidence_refs",
    "anchor_ref",
    "mandate",
    "remedy",
    "retention",
    "stake",
    "conventions",
    "signature",
    "timestamp",
    "producer",
    "hashes",
}
TRUST_CONTEXT_FIELDS = {
    "schema_version",
    "package",
    "expected_publisher",
    "proof_type",
    "issuers",
}
ISSUER_FIELDS = {
    "id",
    "issuer",
    "valid_from",
    "retired_after",
    "revoked_from",
    "status",
}
WITNESS_FIELDS = {
    "canon_version",
    "receipt_version",
    "kernel_version",
    "composition_hash",
    "diagnostic_hash",
    "policy_profile",
    "fee",
    "blind_spots_count",
    "bridges_required",
    "unknown_dimensions",
    "disposition",
    "timestamp",
    "patches",
    "active_packs",
    "witness_basis",
    "receipt_hash",
    "anchor_ref",
}


class ReleaseSigningError(ValueError):
    """The release input is not the one closed contract this signer accepts."""


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def leaf_hash(attestation: str) -> str:
    return "sha256:" + hashlib.sha256(
        b"\x00" + attestation.encode("utf-8")
    ).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseSigningError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def read_json(
    path: Path, maximum: int = 262_144, *, allow_float: bool = False
) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise ReleaseSigningError(f"{path} exceeds {maximum} bytes")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_float=(
                float
                if allow_float
                else lambda value: (_ for _ in ()).throw(
                    ReleaseSigningError(
                        f"floating-point value is forbidden: {value}"
                    )
                )
            ),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReleaseSigningError(f"non-finite value is forbidden: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseSigningError(f"{path} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseSigningError(f"{path} must contain one JSON object")
    return value


def require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ReleaseSigningError(
            f"{label} fields differ: missing={sorted(expected - set(value))} "
            f"unknown={sorted(set(value) - expected)}"
        )


def _b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58[remainder] + encoded
    zeroes = len(data) - len(data.lstrip(b"\x00"))
    return "1" * zeroes + encoded


def _b58decode(value: str) -> bytes:
    number = 0
    for character in value:
        position = BASE58.find(character)
        if position < 0:
            raise ReleaseSigningError("did:key contains invalid base58")
        number = number * 58 + position
    body = (
        number.to_bytes((number.bit_length() + 7) // 8, "big")
        if number
        else b""
    )
    zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * zeroes + body


def did_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ReleaseSigningError("Ed25519 public key must contain 32 bytes")
    return "did:key:z" + _b58encode(b"\xed\x01" + public_key)


def did_public_key(value: str) -> bytes:
    prefix = "did:key:z"
    if not value.startswith(prefix):
        raise ReleaseSigningError("proof issuer must be an Ed25519 did:key")
    decoded = _b58decode(value[len(prefix) :])
    if decoded[:2] != b"\xed\x01" or len(decoded[2:]) != 32:
        raise ReleaseSigningError("proof issuer is not an Ed25519 did:key")
    return decoded[2:]


def signing_key() -> SigningKey:
    raw = os.environ.get("BULLA_RELEASE_KEY")
    if not raw:
        raise ReleaseSigningError("BULLA_RELEASE_KEY is required")
    try:
        document = json.loads(raw, object_pairs_hook=_pairs)
        require_keys(
            document,
            {
                "version",
                "alg",
                "did",
                "issuer",
                "secret_key_b64",
                "public_key_b64",
            },
            "release key",
        )
        if document["version"] != 1 or document["alg"] != "ed25519":
            raise ReleaseSigningError("unsupported release key")
        seed = base64.b64decode(document["secret_key_b64"], validate=True)
        public = base64.b64decode(document["public_key_b64"], validate=True)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ReleaseSigningError("BULLA_RELEASE_KEY is not a closed keyfile") from exc
    key = SigningKey(seed)
    if bytes(key.verify_key) != public or did_key(public) != document["did"]:
        raise ReleaseSigningError("release key public material is inconsistent")
    if document["issuer"] != document["did"]:
        raise ReleaseSigningError("release signer requires a self-certifying did:key")
    return key


def proof(key: SigningKey, message: bytes, *, purpose: str | None = None) -> dict:
    signature = key.sign(message).signature
    issuer = did_key(bytes(key.verify_key))
    result = {
        "type": PROOF_TYPE,
        "issuer": issuer,
        "verificationMethod": issuer,
        "proofValue": base64.b64encode(signature).decode("ascii"),
    }
    if purpose is not None:
        result["purpose"] = purpose
    return result


def verify_proof(
    value: dict[str, Any], message: bytes, *, purpose: str | None = None
) -> None:
    expected = {"type", "issuer", "verificationMethod", "proofValue"}
    if purpose is not None:
        expected.add("purpose")
    require_keys(value, expected, "proof")
    if (
        value["type"] != PROOF_TYPE
        or value["issuer"] != value["verificationMethod"]
        or value.get("purpose") != purpose
    ):
        raise ReleaseSigningError("proof metadata does not match the contract")
    try:
        signature = base64.b64decode(value["proofValue"], validate=True)
        VerifyKey(did_public_key(value["issuer"])).verify(message, signature)
    except (ValueError, BadSignatureError) as exc:
        raise ReleaseSigningError("proof signature does not verify") from exc


def domain_message(purpose: str, digest: str) -> bytes:
    return canonical(
        {
            "context": "bulla-proof",
            "schema": "0.3",
            "purpose": purpose,
            "digest": digest,
        }
    ).encode("utf-8")


def parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseSigningError(f"{label} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseSigningError(f"{label} is not an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReleaseSigningError(f"{label} must include an offset")
    return parsed


def _version_tuple(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ReleaseSigningError(f"{label} must be a three-part release version")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def issuer_record_hash(
    context: dict[str, Any], record: dict[str, Any]
) -> str:
    return digest_json(
        {
            "context_schema": context["schema_version"],
            "package": context["package"],
            "proof_type": context["proof_type"],
            "expected_publisher": context["expected_publisher"],
            "id": record["id"],
            "issuer": record["issuer"],
            "valid_from": record["valid_from"],
        }
    )


def release_trust_context(path: Path) -> dict[str, Any]:
    context = read_json(path)
    require_keys(context, TRUST_CONTEXT_FIELDS, "release trust context")
    if (
        context["schema_version"] != "bulla.release-trust-context/0.2"
        or context["package"] != "bulla"
        or context["expected_publisher"] != "github:jkomkov/bulla"
        or context["proof_type"] != PROOF_TYPE
        or not isinstance(context["issuers"], list)
        or not context["issuers"]
    ):
        raise ReleaseSigningError("release trust context does not match")
    intervals: list[tuple[tuple[int, int, int], tuple[int, int, int] | None, str]] = []
    seen_ids: set[str] = set()
    for record in context["issuers"]:
        if not isinstance(record, dict):
            raise ReleaseSigningError("release issuer record must be an object")
        require_keys(record, ISSUER_FIELDS, "release issuer record")
        issuer_id = record["id"]
        issuer = record["issuer"]
        status = record["status"]
        if (
            not isinstance(issuer_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", issuer_id)
            or issuer_id in seen_ids
            or not isinstance(issuer, str)
            or status not in {"active", "retired", "revoked"}
        ):
            raise ReleaseSigningError("release issuer identity is invalid")
        seen_ids.add(issuer_id)
        did_public_key(issuer)
        start = _version_tuple(record["valid_from"], "valid_from")
        retired = record["retired_after"]
        revoked = record["revoked_from"]
        if status == "active":
            if retired is not None or revoked is not None:
                raise ReleaseSigningError("active release issuer has an end marker")
            end = None
        elif status == "retired":
            if revoked is not None:
                raise ReleaseSigningError("retired release issuer has a revocation marker")
            retired_tuple = _version_tuple(retired, "retired_after")
            if retired_tuple < start:
                raise ReleaseSigningError("release issuer retires before validity")
            end = (retired_tuple[0], retired_tuple[1], retired_tuple[2] + 1)
        else:
            if retired is not None:
                raise ReleaseSigningError("revoked release issuer has a retirement marker")
            end = _version_tuple(revoked, "revoked_from")
            if end <= start:
                raise ReleaseSigningError("release issuer revocation has no valid interval")
        intervals.append((start, end, issuer_id))
    for index, (left_start, left_end, left_id) in enumerate(intervals):
        for right_start, right_end, right_id in intervals[index + 1 :]:
            left_before_right_end = right_end is None or left_start < right_end
            right_before_left_end = left_end is None or right_start < left_end
            if left_before_right_end and right_before_left_end:
                raise ReleaseSigningError(
                    f"release issuer validity overlaps: {left_id}, {right_id}"
                )
    return context


def release_issuer(path: Path, version: str) -> dict[str, Any]:
    context = release_trust_context(path)
    target = _version_tuple(version, "release version")
    matches: list[dict[str, Any]] = []
    for record in context["issuers"]:
        start = _version_tuple(record["valid_from"], "valid_from")
        retired = record["retired_after"]
        revoked = record["revoked_from"]
        if target < start:
            continue
        if retired is not None and target > _version_tuple(retired, "retired_after"):
            continue
        if revoked is not None and target >= _version_tuple(revoked, "revoked_from"):
            continue
        matches.append(record)
    if len(matches) != 1:
        raise ReleaseSigningError(
            f"release version {version} has {len(matches)} accepted issuers"
        )
    return {
        **matches[0],
        "record_hash": issuer_record_hash(context, matches[0]),
        "trust_context_schema": context["schema_version"],
        "package": context["package"],
        "proof_type": context["proof_type"],
        "expected_publisher": context["expected_publisher"],
    }


def build_slot(args: argparse.Namespace) -> None:
    if not VERSION.fullmatch(args.version) or not COMMIT.fullmatch(args.source_commit):
        raise ReleaseSigningError("slot version or source commit is malformed")
    if not SHA256.fullmatch(args.source_tree_sha256):
        raise ReleaseSigningError("slot source tree digest is malformed")
    issuer_record = release_issuer(args.context, args.version)
    expected_issuer = issuer_record["issuer"]
    key = signing_key()
    if did_key(bytes(key.verify_key)) != expected_issuer:
        raise ReleaseSigningError(
            "release signing key is not the accepted release issuer"
        )
    opened = datetime.now(timezone.utc)
    unsigned = {
        "schema_version": SLOT_SCHEMA,
        "kind": SLOT_KIND,
        "package": "bulla",
        "version": args.version,
        "source_commit": args.source_commit,
        "source_tree_sha256": args.source_tree_sha256,
        "release_issuer_id": issuer_record["id"],
        "release_issuer_record_hash": issuer_record["record_hash"],
        "trust_context_schema": issuer_record["trust_context_schema"],
        "expected_publisher": "github:jkomkov/bulla",
        "opened_at": opened.isoformat().replace("+00:00", "Z"),
        "close_deadline": (opened + timedelta(hours=24))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    slot_hash = digest_json(unsigned)
    document = {
        **unsigned,
        "slot_hash": slot_hash,
        "proof": proof(
            key,
            domain_message("release-slot", slot_hash),
            purpose="release-slot",
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise ReleaseSigningError(f"refusing to replace {args.out}")
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    verify_slot(document, args.version, args.source_commit, issuer_record)


def verify_slot(
    slot: dict[str, Any],
    version: str,
    source_commit: str,
    issuer_record: dict[str, Any],
) -> dict[str, Any]:
    require_keys(slot, SLOT_FIELDS, "release slot")
    if (
        slot["schema_version"] != SLOT_SCHEMA
        or slot["kind"] != SLOT_KIND
        or slot["package"] != "bulla"
        or slot["version"] != version
        or slot["source_commit"] != source_commit
        or slot["release_issuer_id"] != issuer_record["id"]
        or slot["release_issuer_record_hash"] != issuer_record["record_hash"]
        or slot["trust_context_schema"] != issuer_record["trust_context_schema"]
        or slot["expected_publisher"] != "github:jkomkov/bulla"
        or not SHA256.fullmatch(str(slot["source_tree_sha256"]))
        or not SHA256.fullmatch(str(slot["slot_hash"]))
    ):
        raise ReleaseSigningError("release slot identity does not match")
    opened = parse_time(slot["opened_at"], "opened_at")
    deadline = parse_time(slot["close_deadline"], "close_deadline")
    if deadline - opened != timedelta(hours=24):
        raise ReleaseSigningError("release slot deadline is not exactly 24 hours")
    unsigned = {key: slot[key] for key in SLOT_FIELDS - {"slot_hash", "proof"}}
    if digest_json(unsigned) != slot["slot_hash"]:
        raise ReleaseSigningError("release slot hash does not match")
    verify_proof(
        slot["proof"],
        domain_message("release-slot", slot["slot_hash"]),
        purpose="release-slot",
    )
    if slot["proof"]["issuer"] != issuer_record["issuer"]:
        raise ReleaseSigningError(
            "release slot issuer is not accepted by the external context"
        )
    return slot


def envelope_from_views(receipt: dict[str, Any]) -> dict[str, Any]:
    mandate = receipt["mandate"]
    remedy = receipt["remedy"]
    retention = receipt["retention"]
    require_keys(mandate, {"authority", "bounds"}, "mandate")
    require_keys(retention, {"record", "disclosure"}, "retention")
    result = {
        "deed_schema": "0.2",
        "authority": mandate["authority"],
        "bounds": mandate["bounds"],
        "recourse": remedy,
        "retention_class": retention["record"],
        "disclosure_class": retention["disclosure"],
    }
    return result


def expected_release_contract(
    receipt: dict[str, Any],
    *,
    version: str,
    source_commit: str,
    slot: dict[str, Any],
    summary: str,
    wheel: Path,
    sdist: Path,
    verification_kit: Path | None,
    witness: Path,
    tree_sha256: str,
) -> None:
    require_keys(receipt, RECEIPT_FIELDS, "release receipt")
    if (
        receipt["schema_version"] != "0.2"
        or receipt["kind"] != "action_receipt"
        or receipt["stake"] is not None
        or receipt["conventions"] != []
        or receipt["signature"] is not None
    ):
        raise ReleaseSigningError("release receipt is not an unsigned v0.2 preimage")
    expected_subject = {
        "package": "bulla",
        "version": version,
        "git_commit": source_commit,
        "git_tag": f"v{version}",
        "test_result": summary,
        "release_slot_hash": slot["slot_hash"],
    }
    if receipt["action"] != {
        "type": "package.release",
        "subject": expected_subject,
    }:
        raise ReleaseSigningError("release receipt action subject differs")
    witness_document = read_json(witness, 1_048_576)
    require_keys(witness_document, WITNESS_FIELDS, "release witness")
    witness_hash = witness_document.get("receipt_hash")
    witness_preimage = {
        key: value
        for key, value in witness_document.items()
        if key not in {"receipt_hash", "anchor_ref"}
    }
    if (
        witness_document.get("canon_version") != 2
        or witness_document.get("receipt_version") != "0.1.0"
        or witness_document.get("kernel_version") != version
        or witness_document.get("anchor_ref") is not None
        or not isinstance(witness_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", witness_hash)
        or hashlib.sha256(canonical(witness_preimage).encode("utf-8")).hexdigest()
        != witness_hash
        or receipt["diagnostic_ref"] != {
        "status": "reference",
        "ref": f"sha256:{witness_hash}",
        }
    ):
        raise ReleaseSigningError("release diagnostic reference does not bind its sidecar")
    expected_evidence = [
        {
            "name": "wheel",
            "hash": digest_file(wheel),
            "grounding": "third_party_anchored",
        },
        {
            "name": "sdist",
            "hash": digest_file(sdist),
            "grounding": "third_party_anchored",
        },
    ]
    if verification_kit is not None:
        expected_evidence.append(
            {
                "name": "verification-kit",
                "hash": digest_file(verification_kit),
                "grounding": "third_party_anchored",
            }
        )
    expected_evidence.append(
        {
            "name": "tree",
            "hash": tree_sha256,
            "grounding": "third_party_anchored",
        }
    )
    if receipt["evidence_refs"] != expected_evidence:
        raise ReleaseSigningError("release evidence does not bind exact artifacts and tree")
    expected_integrity = [
        (
            f"https://pypi.org/integrity/bulla/{version}/"
            f"bulla-{version}-py3-none-any.whl/provenance"
        ),
        (
            f"https://pypi.org/integrity/bulla/{version}/"
            f"bulla-{version}.tar.gz/provenance"
        ),
    ]
    if receipt["anchor_ref"] != {
        "kind": "pypi",
        "ref": f"bulla {version}",
        "root_of_trust": {
            "scheme": "sigstore-pep740",
            "publisher": "github:jkomkov/bulla",
            "integrity_api": expected_integrity,
        },
    }:
        raise ReleaseSigningError("release root of trust differs")
    if receipt["mandate"] != {
        "authority": {
            "principal": "github:jkomkov",
            "policy": "policy://bulla/release",
            "delegation": ["pypi:project:bulla"],
        },
        "bounds": {"scope": f"pypi:bulla version:{version}"},
    }:
        raise ReleaseSigningError("release mandate differs")
    if receipt["remedy"] != {
        "challenge_window": "P90D",
        "forum": {
            "log_endpoint": "https://pypi.org/project/bulla/",
            "trusted_root_ref": "rekor:sigstore-pep740",
        },
        "remedies": [
            {
                "rung": "recompute",
                "verifier": "pip download + sha256 vs PyPI",
                "anchor": f"pypi:bulla=={version}",
            },
            {
                "rung": "revert",
                "verifier": "pypi yank",
                "anchor": f"pypi:bulla=={version}",
            },
            {
                "rung": "escalate",
                "verifier": "maintainer review",
                "anchor": "github:jkomkov",
            },
        ],
    }:
        raise ReleaseSigningError("release remedy differs")
    if receipt["retention"] != {
        "record": "authority-permanent",
        "disclosure": "public",
    }:
        raise ReleaseSigningError("release retention differs")
    expected_slot_ref: dict[str, Any] = {
        "slot_hash": slot["slot_hash"],
        "opened_at": slot["opened_at"],
        "close_deadline": slot["close_deadline"],
    }
    actual_slot_ref = receipt["producer"].get("slot_ref")
    if isinstance(actual_slot_ref, dict) and actual_slot_ref.get("closed_late") is True:
        expected_slot_ref["closed_late"] = True
    producer = receipt["producer"]
    allowed_producer = {
        "bulla_version",
        "minted",
        "workflow",
        "pypi_project",
        "note",
        "slot_ref",
    }
    require_keys(producer, allowed_producer, "release producer")
    if (
        producer["bulla_version"] != version
        or producer["minted"] != "post-publication"
        or producer["workflow"] != "publish"
        or producer["pypi_project"] != "bulla"
        or producer["note"]
        != "UNSIGNED — no release key configured at mint time (stated, not hidden)"
        or producer["slot_ref"] != expected_slot_ref
    ):
        raise ReleaseSigningError("release producer preimage differs")
    parse_time(receipt["timestamp"], "receipt timestamp")
    content_preimage = {
        "schema_version": "0.2",
        "kind": "action_receipt",
        "action": receipt["action"],
        "diagnostic_ref": receipt["diagnostic_ref"],
        "evidence_refs": receipt["evidence_refs"],
        "anchor_ref": receipt["anchor_ref"],
    }
    content_hash = digest_json(content_preimage)
    event_hash = digest_json(
        {"content_hash": content_hash, "timestamp": receipt["timestamp"]}
    )
    envelope = envelope_from_views(receipt)
    unsigned_attestation = digest_json(
        {
            "content_hash": content_hash,
            "signature": None,
            "recourse_envelope": envelope,
        }
    )
    expected_hashes = {
        "content": content_hash,
        "event": event_hash,
        "attestation": unsigned_attestation,
        "log_leaf": leaf_hash(unsigned_attestation),
    }
    if receipt["hashes"] != expected_hashes:
        raise ReleaseSigningError("unsigned receipt hashes do not recompute")


def _signed_receipt_hashes(receipt: dict[str, Any]) -> dict[str, str]:
    content_preimage = {
        "schema_version": "0.2",
        "kind": "action_receipt",
        "action": receipt["action"],
        "diagnostic_ref": receipt["diagnostic_ref"],
        "evidence_refs": receipt["evidence_refs"],
        "anchor_ref": receipt["anchor_ref"],
    }
    content_hash = digest_json(content_preimage)
    event_hash = digest_json(
        {"content_hash": content_hash, "timestamp": receipt["timestamp"]}
    )
    envelope = envelope_from_views(receipt)
    attestation = digest_json(
        {
            "content_hash": content_hash,
            "signature": receipt["signature"],
            "recourse_envelope": envelope,
        }
    )
    return {
        "content": content_hash,
        "event": event_hash,
        "attestation": attestation,
        "log_leaf": leaf_hash(attestation),
    }


def verify_existing_signed_receipt(
    existing: dict[str, Any],
    unsigned: dict[str, Any],
    slot: dict[str, Any],
) -> None:
    require_keys(existing, RECEIPT_FIELDS, "signed release receipt")
    action = existing.get("action")
    if not isinstance(action, dict):
        raise ReleaseSigningError("signed receipt action must be an object")
    subject = action.get("subject")
    if not isinstance(subject, dict):
        raise ReleaseSigningError("signed receipt subject must be an object")
    signed_at = subject.get("release_signed_at")
    signed_moment = parse_time(signed_at, "release_signed_at")
    if existing["timestamp"] != signed_at:
        raise ReleaseSigningError(
            "signed receipt timestamp differs from release_signed_at"
        )
    if signed_moment < parse_time(slot["opened_at"], "slot opened_at"):
        raise ReleaseSigningError("release receipt predates its signed slot")

    expected_action = json.loads(json.dumps(unsigned["action"]))
    expected_action["subject"]["release_signed_at"] = signed_at
    expected_producer = json.loads(json.dumps(unsigned["producer"]))
    expected_producer.pop("note")
    if signed_moment > parse_time(slot["close_deadline"], "slot close_deadline"):
        expected_producer["slot_ref"]["closed_late"] = True
    else:
        expected_producer["slot_ref"].pop("closed_late", None)

    for field in (
        "schema_version",
        "kind",
        "diagnostic_ref",
        "evidence_refs",
        "anchor_ref",
        "mandate",
        "remedy",
        "retention",
        "stake",
        "conventions",
    ):
        if existing[field] != unsigned[field]:
            raise ReleaseSigningError(
                f"signed receipt {field} differs from its verified preimage"
            )
    if existing["action"] != expected_action:
        raise ReleaseSigningError(
            "signed receipt action differs from its verified preimage"
        )
    if existing["producer"] != expected_producer:
        raise ReleaseSigningError(
            "signed receipt producer differs from its verified preimage"
        )
    if not isinstance(existing["signature"], dict):
        raise ReleaseSigningError("signed receipt has no signature proof")
    if existing["hashes"] != _signed_receipt_hashes(existing):
        raise ReleaseSigningError("signed receipt hashes do not recompute")
    content_hash = existing["hashes"]["content"]
    verify_proof(existing["signature"], content_hash.encode("utf-8"))
    if existing["signature"]["issuer"] != slot["proof"]["issuer"]:
        raise ReleaseSigningError(
            "signed receipt issuer differs from release slot issuer"
        )


def verify_embedded_verification_kit(
    wheel: Path,
    sdist: Path,
    version: str,
    verification_kit: Path,
) -> None:
    try:
        expected = verification_kit.read_bytes()
    except OSError as exc:
        raise ReleaseSigningError("verification kit is unreadable") from exc
    if len(expected) > MAX_VERIFICATION_KIT_BYTES:
        raise ReleaseSigningError("verification kit exceeds the signer size limit")

    wheel_member = f"bulla/data/{VERIFICATION_KIT_NAME}"
    try:
        with ZipFile(wheel) as archive:
            matches = [info for info in archive.infolist() if info.filename == wheel_member]
            if len(matches) != 1 or not stat.S_ISREG(matches[0].external_attr >> 16):
                raise ReleaseSigningError(
                    "wheel must contain exactly one regular verification-kit member"
                )
            if matches[0].file_size > MAX_VERIFICATION_KIT_BYTES:
                raise ReleaseSigningError("wheel verification-kit member exceeds the size limit")
            wheel_kit = archive.read(matches[0])
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ReleaseSigningError("wheel verification-kit member is unreadable") from exc

    sdist_member = f"bulla-{version}/src/bulla/data/{VERIFICATION_KIT_NAME}"
    try:
        with tarfile.open(sdist, mode="r:gz") as archive:
            matches = [member for member in archive.getmembers() if member.name == sdist_member]
            if len(matches) != 1 or not matches[0].isfile():
                raise ReleaseSigningError(
                    "sdist must contain exactly one regular verification-kit member"
                )
            if matches[0].size > MAX_VERIFICATION_KIT_BYTES:
                raise ReleaseSigningError("sdist verification-kit member exceeds the size limit")
            extracted = archive.extractfile(matches[0])
            if extracted is None:
                raise ReleaseSigningError("sdist verification-kit member is unreadable")
            sdist_kit = extracted.read(MAX_VERIFICATION_KIT_BYTES + 1)
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseSigningError("sdist verification-kit member is unreadable") from exc

    if wheel_kit != expected:
        raise ReleaseSigningError("wheel verification-kit bytes differ from the candidate kit")
    if sdist_kit != expected:
        raise ReleaseSigningError("sdist verification-kit bytes differ from the candidate kit")


def sign_receipt(args: argparse.Namespace) -> None:
    if not VERSION.fullmatch(args.version) or not COMMIT.fullmatch(args.source_commit):
        raise ReleaseSigningError("release version or source commit is malformed")
    if not SHA256.fullmatch(args.source_tree_sha256):
        raise ReleaseSigningError("source tree digest is malformed")
    issuer_record = release_issuer(args.context, args.version)
    slot = verify_slot(
        read_json(args.slot),
        args.version,
        args.source_commit,
        issuer_record,
    )
    if slot["source_tree_sha256"] != args.source_tree_sha256:
        raise ReleaseSigningError(
            "source tree digest differs from the signed release slot"
        )
    summary_lines = [
        line for line in args.summary.read_text(encoding="utf-8").splitlines() if line
    ]
    if not summary_lines:
        raise ReleaseSigningError("archived pytest summary is empty")
    summary = summary_lines[-1]
    wheel = args.dist / f"bulla-{args.version}-py3-none-any.whl"
    sdist = args.dist / f"bulla-{args.version}.tar.gz"
    kit_required = tuple(int(part) for part in args.version.split(".")) >= (0, 44, 5)
    verification_kit = (
        args.dist / "action-receipt-v0.2-verification-kit.zip"
        if kit_required
        else None
    )
    verification_kit_checksum = (
        args.dist / "action-receipt-v0.2-verification-kit.zip.sha256"
        if kit_required
        else None
    )
    expected_inventory = [wheel.name, sdist.name, args.summary.name]
    if verification_kit is not None:
        expected_inventory.extend(
            [verification_kit.name, verification_kit_checksum.name]
        )
    if (
        not wheel.is_file()
        or not sdist.is_file()
        or (verification_kit is not None and not verification_kit.is_file())
        or (
            verification_kit_checksum is not None
            and not verification_kit_checksum.is_file()
        )
        or sorted(path.name for path in args.dist.iterdir() if path.is_file())
        != sorted(expected_inventory)
    ):
        raise ReleaseSigningError("release candidate inventory differs")
    if verification_kit is not None and verification_kit_checksum is not None:
        expected_checksum = (
            f"{digest_file(verification_kit).removeprefix('sha256:')}  "
            f"{verification_kit.name}\n"
        )
        try:
            observed_checksum = verification_kit_checksum.read_text(encoding="ascii")
        except (OSError, UnicodeError) as exc:
            raise ReleaseSigningError("verification-kit checksum is unreadable") from exc
        if observed_checksum != expected_checksum:
            raise ReleaseSigningError("verification-kit checksum differs")
        verify_embedded_verification_kit(
            wheel,
            sdist,
            args.version,
            verification_kit,
        )
    receipt = read_json(args.receipt, 1_048_576)
    expected_release_contract(
        receipt,
        version=args.version,
        source_commit=args.source_commit,
        slot=slot,
        summary=summary,
        wheel=wheel,
        sdist=sdist,
        verification_kit=verification_kit,
        witness=args.witness,
        tree_sha256=args.source_tree_sha256,
    )
    existing = getattr(args, "existing", None)
    if existing is not None:
        signed_receipt = read_json(existing, 1_048_576)
        verify_existing_signed_receipt(signed_receipt, receipt, slot)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.out.exists():
            raise ReleaseSigningError(f"refusing to replace {args.out}")
        shutil.copyfile(existing, args.out)
        return

    signed_at = datetime.now(timezone.utc).isoformat()
    receipt["action"]["subject"]["release_signed_at"] = signed_at
    receipt["timestamp"] = signed_at
    signed_moment = parse_time(signed_at, "release_signed_at")
    slot_opened = parse_time(slot["opened_at"], "slot opened_at")
    slot_deadline = parse_time(slot["close_deadline"], "slot close_deadline")
    if signed_moment < slot_opened:
        raise ReleaseSigningError("release receipt predates its signed slot")
    if signed_moment > slot_deadline:
        receipt["producer"]["slot_ref"]["closed_late"] = True
    else:
        receipt["producer"]["slot_ref"].pop("closed_late", None)
    content_hash = _signed_receipt_hashes(receipt)["content"]
    key = signing_key()
    receipt["signature"] = proof(key, content_hash.encode("utf-8"))
    if receipt["signature"]["issuer"] != slot["proof"]["issuer"]:
        raise ReleaseSigningError(
            "release signing key differs from release slot issuer"
        )
    receipt["producer"].pop("note")
    receipt["hashes"] = _signed_receipt_hashes(receipt)
    verify_proof(receipt["signature"], content_hash.encode("utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise ReleaseSigningError(f"refusing to replace {args.out}")
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    slot = commands.add_parser("open-slot")
    slot.add_argument("--version", required=True)
    slot.add_argument("--source-commit", required=True)
    slot.add_argument("--source-tree-sha256", required=True)
    slot.add_argument("--context", type=Path, required=True)
    slot.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify-slot")
    verify.add_argument("--version", required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--slot", type=Path, required=True)
    verify.add_argument("--context", type=Path, required=True)
    receipt = commands.add_parser("sign-receipt")
    receipt.add_argument("--version", required=True)
    receipt.add_argument("--source-commit", required=True)
    receipt.add_argument("--source-tree-sha256", required=True)
    receipt.add_argument("--receipt", type=Path, required=True)
    receipt.add_argument("--witness", type=Path, required=True)
    receipt.add_argument("--slot", type=Path, required=True)
    receipt.add_argument("--summary", type=Path, required=True)
    receipt.add_argument("--dist", type=Path, required=True)
    receipt.add_argument("--context", type=Path, required=True)
    receipt.add_argument("--existing", type=Path)
    receipt.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "open-slot":
            build_slot(args)
        elif args.command == "verify-slot":
            verify_slot(
                read_json(args.slot),
                args.version,
                args.source_commit,
                release_issuer(args.context, args.version),
            )
        else:
            sign_receipt(args)
    except (OSError, ReleaseSigningError) as exc:
        print(f"release signer rejected input: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
