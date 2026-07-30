#!/usr/bin/env python3
"""No-Bulla verifier for ``glyph.agent-incident-packet/0.1-draft``.

The checker uses only the Python standard library.  It parses the packet from
its byte boundary, recomputes ActionReceipt v0.4 and packet commitments,
verifies self-certifying did:key Ed25519 proofs, reconciles each denominator
independently, and preserves unavailable evidence and coverage gaps as
separate results.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
import math
import os
import re
import stat as stat_module
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

PROFILE = "glyph.agent-incident-packet/0.1-draft"
CORE_FILE = "packet-core.json"
PUBLISH_FILE = "publish-receipt.json"
MAX_FILE_BYTES = 2_097_152
MAX_TOTAL_BYTES = 16_777_216
MAX_FILES = 512
MAX_DEPTH = 40
MAX_NODES = 100_000
MAX_STRING_BYTES = 524_288
DESCRIPTOR_RELATIVE_WALK_SUPPORTED = (
    os.name == "posix"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)
SAFE_INTEGER = (1 << 53) - 1
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
UTC_INSTANT_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
COMPONENTS = (
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
CORE_FIELDS = {
    "profile",
    "packet_id",
    "incident_id",
    "revision",
    "classification",
    "supersedes_core_hash",
    *COMPONENTS,
}
ACTION_FIELDS = {
    "eval.run.authorize": {
        "profile", "issuer_role", "run_id", "model_id", "harness_id",
        "safeguards_id", "authorized_targets", "budgets", "prohibitions",
        "valid_from", "valid_until", "policy_digest", "role_issuers",
    },
    "capability.decide": {
        "profile", "issuer_role", "run_id", "request_id", "decision_event_id",
        "request_hash", "mandate_ref", "policy_digest", "decision",
        "rationale_codes", "anchor_id", "protocol", "evidence_hash",
    },
    "capability.observe": {
        "profile", "issuer_role", "run_id", "request_id",
        "decision_attestation", "observation_id", "anchor_id", "protocol",
        "effect_hash", "evidence_refs", "observation_class",
        "transport_status", "mandate_ref", "evidence_hash",
    },
    "trajectory.decide": {
        "profile", "issuer_role", "run_id", "mandate_ref", "ordered_lineage",
        "policy_digest", "decision", "rationale_codes", "requested_controls",
    },
    "incident.statement": {
        "profile", "issuer_role", "statement_id", "incident_id", "topic",
        "claim", "epistemic_status", "evidence_refs",
    },
    "incident.handoff": {
        "profile", "issuer_role", "incident_id", "recipient", "mandate_ref",
        "timeline_hash", "coverage_hash", "statement_refs", "parent_refs",
        "requested_actions", "disclosure_conditions", "correction_channel",
        "challenge_channel",
    },
    "incident.packet.publish": {
        "profile", "issuer_role", "packet_id", "packet_core_hash",
        "component_hashes",
    },
    "incident.correct": {
        "profile", "issuer_role", "correction_id", "supersedes_kind",
        "supersedes_ref", "replacement_ref", "reason", "correction_channel",
    },
}
RECEIPT_FIELDS = {
    "schema_version", "canonicalization", "kind", "action", "diagnostic_ref",
    "evidence_refs", "anchor_ref", "mandate", "remedy", "retention", "stake",
    "conventions", "signature", "occurrence", "authorization", "event_id",
    "claimed_at", "producer", "hashes",
}
PROOF_FIELDS = {
    "type", "purpose", "issuer", "verificationMethod", "proofValue",
}
OBSERVATION_FIELDS = {
    "anchor_id", "observation_id", "run_id", "protocol", "operation_ref",
    "phase", "evidence_hash",
}
ACTION_ALLOWED_ROLES = {
    "eval.run.authorize": {"evaluation_authority"},
    "capability.decide": {"gateway", "boundary"},
    "capability.observe": {"gateway", "boundary", "target"},
    "trajectory.decide": {"trajectory_monitor"},
    "incident.statement": None,
    "incident.handoff": {"incident_commander"},
    "incident.packet.publish": {"publisher"},
    "incident.correct": {"correction_authority"},
}
PROTOCOLS = {"http", "mcp"}
DECISIONS = {"PERMIT", "REFUSE"}
OBSERVATION_CLASSES = {
    "EFFECT_OBSERVED", "NO_EFFECT_OBSERVED", "OUTCOME_UNKNOWN",
}
TRAJECTORY_DECISIONS = {"PROCEED", "ESCALATE", "REFUSE_AND_FREEZE"}
EPISTEMIC_STATUSES = {
    "observed", "inferred", "counterparty_confirmed", "unresolved",
}
GROUNDING_CLASSES = {
    "self_asserted", "counterparty_signed", "third_party_anchored",
    "execution_verified",
}


class Malformed(ValueError):
    """Unsafe, malformed, unsupported, or resource-exhausting input."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Malformed(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise Malformed(f"non-finite JSON number {value!r} is not permitted")


def _json_limits(value: Any, label: str) -> None:
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise Malformed(f"{label} exceeds {MAX_NODES} aggregate JSON nodes")
        if depth > MAX_DEPTH:
            raise Malformed(f"{label} exceeds maximum JSON depth {MAX_DEPTH}")
        if isinstance(current, str):
            try:
                size = len(current.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise Malformed(f"{label} contains a lone Unicode surrogate") from exc
            if size > MAX_STRING_BYTES:
                raise Malformed(
                    f"{label} contains a string over {MAX_STRING_BYTES} UTF-8 bytes"
                )
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def load_json(raw: bytes, label: str) -> Any:
    if len(raw) > MAX_FILE_BYTES:
        raise Malformed(f"{label} exceeds {MAX_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Malformed(f"{label} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
    except Malformed:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise Malformed(f"invalid JSON in {label}: {exc}") from exc
    _json_limits(value, label)
    return value


def exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Malformed(f"{label} must be an object")
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing:
        raise Malformed(f"{label} is missing required fields {sorted(missing)}")
    if unknown:
        raise Malformed(f"{label} has unknown fields {sorted(unknown)}")
    return value


def closed(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Malformed(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise Malformed(f"{label} has unknown fields {sorted(unknown)}")
    return value


def string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise Malformed(f"{label} must be a non-empty string")
    return value


def digest(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise Malformed(f"{label} must be a lowercase sha256 digest")
    return value


def integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Malformed(f"{label} must be an integer >= {minimum}")
    return value


def strings(value: Any, label: str, *, unique: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise Malformed(f"{label} must be an array")
    result = [string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if unique and len(set(result)) != len(result):
        raise Malformed(f"{label} must not contain duplicates")
    return result


def instant(value: Any, label: str) -> datetime:
    text = string(value, label)
    if UTC_INSTANT_RE.fullmatch(text) is None:
        raise Malformed(f"{label} must be an RFC 3339 UTC instant ending in Z")
    try:
        value = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise Malformed(f"{label} is not a valid RFC 3339 instant") from exc
    if value.utcoffset() is None:
        raise Malformed(f"{label} must carry an explicit UTC offset")
    return value


def objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise Malformed(f"{label} must be an array")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise Malformed(f"{label}[{index}] must be an object")
    return value


def safe_path(value: Any, label: str) -> str:
    value = string(value, label)
    if (
        "\\" in value
        or "\x00" in value
        or any(":" in part for part in value.split("/"))
    ):
        raise Malformed(f"{label} contains an unsafe separator or NUL")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise Malformed(f"{label} must be a normalized relative POSIX path")
    return value


def is_symlink_or_reparse_point(metadata: os.stat_result) -> bool:
    return stat_module.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def packet_read_open_flags(*, require_directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if require_directory:
        return flags | getattr(os, "O_DIRECTORY", 0)
    return flags | getattr(os, "O_BINARY", 0)


def read_packet_path_fallback(root: Path) -> dict[str, bytes]:
    """Read a packet where descriptor-relative directory APIs are unavailable."""
    files: dict[str, bytes] = {}
    total = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            path = current_path / name
            metadata = path.lstat()
            if is_symlink_or_reparse_point(metadata):
                raise Malformed(
                    "packet contains a symlink or reparse-point directory: "
                    f"{path.relative_to(root)}"
                )
        for name in sorted(filenames):
            path = current_path / name
            relative = safe_path(
                path.relative_to(root).as_posix(),
                "packet file",
            )
            metadata = path.lstat()
            if (
                is_symlink_or_reparse_point(metadata)
                or not stat_module.S_ISREG(metadata.st_mode)
            ):
                raise Malformed(
                    f"packet contains a non-regular file: {relative}"
                )
            if len(files) >= MAX_FILES:
                raise Malformed(f"packet exceeds {MAX_FILES} files")
            try:
                descriptor = os.open(
                    path,
                    packet_read_open_flags(require_directory=False),
                )
            except OSError as exc:
                raise Malformed(
                    f"unsafe or unreadable packet member {relative!r}: {exc}"
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat_module.S_ISREG(metadata.st_mode):
                    raise Malformed(
                        f"packet contains a non-regular file: {relative}"
                    )
                if metadata.st_size > MAX_FILE_BYTES:
                    raise Malformed(
                        f"{relative} exceeds {MAX_FILE_BYTES} bytes"
                    )
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
                raw = b"".join(chunks)
                if len(raw) != metadata.st_size:
                    raise Malformed(
                        f"packet member {relative!r} changed size while being read"
                    )
            finally:
                os.close(descriptor)
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise Malformed(
                    f"packet exceeds {MAX_TOTAL_BYTES} total bytes"
                )
            files[relative] = raw
    return files


def read_packet(root_value: str | Path) -> dict[str, bytes]:
    root = Path(root_value)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise Malformed(f"cannot inspect packet root: {exc}") from exc
    if is_symlink_or_reparse_point(root_stat) or not stat_module.S_ISDIR(
        root_stat.st_mode
    ):
        raise Malformed(
            "packet root must be a non-symlink, non-reparse-point directory"
        )
    if not DESCRIPTOR_RELATIVE_WALK_SUPPORTED:
        return read_packet_path_fallback(root)
    files: dict[str, bytes] = {}
    total = 0

    def read_regular(directory_fd: int, name: str, expected: os.stat_result) -> bytes:
        descriptor = os.open(
            name,
            packet_read_open_flags(require_directory=False),
            dir_fd=directory_fd,
        )
        try:
            actual = os.fstat(descriptor)
            if (
                not stat_module.S_ISREG(actual.st_mode)
                or (actual.st_dev, actual.st_ino)
                != (expected.st_dev, expected.st_ino)
            ):
                raise Malformed("packet member changed before descriptor open")
            chunks: list[bytes] = []
            remaining = MAX_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            final = os.fstat(descriptor)
            if (
                len(raw) != expected.st_size
                or (final.st_dev, final.st_ino, final.st_size)
                != (actual.st_dev, actual.st_ino, actual.st_size)
            ):
                raise Malformed("packet member changed while it was read")
            return raw
        finally:
            os.close(descriptor)

    def walk(directory_fd: int, prefix: str) -> None:
        nonlocal total
        for name in sorted(os.listdir(directory_fd)):
            relative = safe_path(
                f"{prefix}/{name}" if prefix else name,
                "packet file",
            )
            item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat_module.S_ISLNK(item_stat.st_mode):
                raise Malformed(f"packet contains a symlink: {relative}")
            if stat_module.S_ISDIR(item_stat.st_mode):
                child_fd = os.open(
                    name,
                    packet_read_open_flags(require_directory=True),
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child_fd)
                    if (
                        not stat_module.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino)
                        != (item_stat.st_dev, item_stat.st_ino)
                    ):
                        raise Malformed(
                            f"packet directory changed before open: {relative}"
                        )
                    walk(child_fd, relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat_module.S_ISREG(item_stat.st_mode):
                raise Malformed(f"packet contains a non-regular file: {relative}")
            if len(files) >= MAX_FILES:
                raise Malformed(f"packet exceeds {MAX_FILES} files")
            if item_stat.st_size > MAX_FILE_BYTES:
                raise Malformed(f"{relative} exceeds {MAX_FILE_BYTES} bytes")
            raw = read_regular(directory_fd, name, item_stat)
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise Malformed(f"packet exceeds {MAX_TOTAL_BYTES} total bytes")
            files[relative] = raw

    try:
        root_fd = os.open(
            root,
            packet_read_open_flags(require_directory=True),
        )
        try:
            opened_root = os.fstat(root_fd)
            if (
                not stat_module.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino)
                != (root_stat.st_dev, root_stat.st_ino)
            ):
                raise Malformed("packet root changed before descriptor open")
            walk(root_fd, "")
        finally:
            os.close(root_fd)
    except Malformed:
        raise
    except OSError as exc:
        raise Malformed(f"unsafe or unreadable packet member: {exc}") from exc
    return files


def _utf16_key(value: str) -> bytes:
    try:
        return value.encode("utf-16-be")
    except UnicodeEncodeError as exc:
        raise Malformed("lone Unicode surrogate is not canonical JSON") from exc


def canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > SAFE_INTEGER:
            raise Malformed("integer exceeds the portable safe range")
        return str(value)
    if isinstance(value, float):
        raise Malformed("floating-point values are not permitted")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise Malformed("lone Unicode surrogate is not canonical JSON") from exc
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise Malformed("canonical JSON keys must be strings")
        return (
            "{"
            + ",".join(
                f"{canonical(key)}:{canonical(value[key])}"
                for key in sorted(value, key=_utf16_key)
            )
            + "}"
        )
    raise Malformed(f"unsupported canonical JSON value {type(value).__name__}")


def hash_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def coverage_commitment(
    core: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> str:
    """Bind each coverage reference to the exact declared report bytes."""
    return hash_json(
        {
            name: [
                {
                    **item,
                    "artifact_sha256": artifacts[item["artifact_id"]]["sha256"],
                }
                for item in core[name]
            ]
            for name in ("denominators", "coverage")
        }
    )


def denominator_checkpoint_topic(
    anchor_id: str,
    phase: str,
    protocol: str,
) -> str:
    return f"denominator:{anchor_id}:{phase}:{protocol}"


# Minimal, strict Ed25519 verification for self-certifying did:key proofs.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_B_Y = (4 * pow(5, _Q - 2, _Q)) % _Q
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q:
        x = x * _I % _Q
    if (x * x - xx) % _Q:
        raise Malformed("invalid Ed25519 point")
    return _Q - x if x & 1 else x


_B_X = _xrecover(_B_Y)
_B = (_B_X, _B_Y, 1, _B_X * _B_Y % _Q)
_IDENTITY = (0, 1, 1, 0)


def _ed_add(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = (y1 - x1) * (y2 - x2) % _Q
    b = (y1 + x1) * (y2 + x2) % _Q
    c = 2 * _D * t1 * t2 % _Q
    d = 2 * z1 * z2 % _Q
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _Q, g * h % _Q, f * g % _Q, e * h % _Q


def _ed_mul(point: tuple[int, ...], scalar: int) -> tuple[int, ...]:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _ed_add(result, addend)
        addend = _ed_add(addend, addend)
        scalar >>= 1
    return result


def _ed_equal(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    return (
        (left[0] * right[2] - right[0] * left[2]) % _Q == 0
        and (left[1] * right[2] - right[1] * left[2]) % _Q == 0
    )


def _decode_point(raw: bytes) -> tuple[int, ...]:
    if len(raw) != 32:
        raise Malformed("Ed25519 point must be 32 bytes")
    encoded = int.from_bytes(raw, "little")
    sign = encoded >> 255
    y = encoded & ((1 << 255) - 1)
    if y >= _Q:
        raise Malformed("non-canonical Ed25519 point")
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    point = (x, y, 1, x * y % _Q)
    if (y * y - x * x - 1 - _D * x * x * y * y) % _Q:
        raise Malformed("Ed25519 point is not on curve")
    if _ed_equal(_ed_mul(point, 8), _IDENTITY):
        raise Malformed("small-order Ed25519 point")
    return point


def _b58decode(value: str) -> bytes:
    number = 0
    for character in value:
        index = _B58.find(character)
        if index < 0:
            raise Malformed("invalid base58btc did:key")
        number = number * 58 + index
    body = (
        number.to_bytes((number.bit_length() + 7) // 8, "big")
        if number
        else b""
    )
    padding = len(value) - len(value.lstrip("1"))
    return b"\0" * padding + body


def did_key_bytes(value: str) -> bytes:
    prefix = "did:key:z"
    if not value.startswith(prefix):
        raise Malformed("proof issuer must be a self-certifying did:key")
    raw = _b58decode(value[len(prefix):])
    if len(raw) != 34 or raw[:2] != b"\xed\x01":
        raise Malformed("did:key is not an Ed25519 public key")
    return raw[2:]


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    try:
        r = _decode_point(signature[:32])
        a = _decode_point(public_key)
    except Malformed:
        return False
    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= _L:
        return False
    challenge = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
    ) % _L
    return _ed_equal(_ed_mul(_B, scalar), _ed_add(r, _ed_mul(a, challenge)))


def envelope_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    mandate = closed(
        receipt["mandate"], {"deed_schema", "authority", "bounds"}, "mandate"
    )
    retention = closed(receipt["retention"], {"record", "disclosure"}, "retention")
    remedy = receipt["remedy"]
    if not isinstance(remedy, dict):
        raise Malformed("remedy must be an object")
    result: dict[str, Any] = {"deed_schema": mandate.get("deed_schema") or "0.2"}
    if mandate.get("authority"):
        result["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        result["bounds"] = mandate["bounds"]
    if remedy:
        result["recourse"] = remedy
    if retention.get("record"):
        result["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        result["disclosure_class"] = retention["disclosure"]
    return result


def receipt_hashes(receipt: dict[str, Any]) -> dict[str, str]:
    content_preimage: dict[str, Any] = {
        "schema_version": "0.4",
        "canonicalization": "bulla-jcs-int/1",
        "kind": "action_receipt",
        "action": receipt["action"],
        "diagnostic_ref": receipt["diagnostic_ref"],
        "evidence_refs": receipt["evidence_refs"],
        "anchor_ref": receipt["anchor_ref"],
    }
    if receipt["conventions"]:
        content_preimage["conventions"] = receipt["conventions"]
    content = hash_json(content_preimage)
    event = hash_json(
        {
            "content_hash": content,
            "event_id": receipt["event_id"],
            "claimed_at": receipt["claimed_at"],
        }
    )
    envelope = envelope_from_receipt(receipt)
    authorization = hash_json(
        {"event_hash": event, "envelope_hash": hash_json(envelope)}
    )
    attestation = hash_json(
        {
            "content_hash": content,
            "signature": receipt["signature"],
            "event_hash": event,
            "occurrence": receipt["occurrence"],
            "recourse_envelope": envelope,
            "authorization": receipt["authorization"],
        }
    )
    leaf = "sha256:" + hashlib.sha256(
        b"\0" + attestation.encode("utf-8")
    ).hexdigest()
    return {
        "content": content,
        "event": event,
        "authorization": authorization,
        "attestation": attestation,
        "log_leaf": leaf,
    }


def validate_proof(
    proof: Any,
    *,
    purpose: str,
    signed_digest: str,
    expected_issuer: str,
) -> None:
    proof = exact(proof, PROOF_FIELDS, f"{purpose} proof")
    if (
        proof["type"] != "bulla/ed25519-2026"
        or proof["purpose"] != purpose
        or proof["issuer"] != expected_issuer
        or proof["verificationMethod"] != expected_issuer
    ):
        raise Malformed(f"{purpose} proof identity or purpose mismatch")
    public_key = did_key_bytes(expected_issuer)
    try:
        signature = base64.b64decode(proof["proofValue"], validate=True)
    except (ValueError, TypeError) as exc:
        raise Malformed(f"{purpose} proofValue is not canonical base64") from exc
    if (
        len(signature) != 64
        or base64.b64encode(signature).decode("ascii") != proof["proofValue"]
    ):
        raise Malformed(f"{purpose} proofValue is not canonical base64")
    message = canonical(
        {
            "context": "bulla-proof",
            "schema": "0.4",
            "purpose": purpose,
            "digest": signed_digest,
        }
    ).encode("utf-8")
    if not verify_ed25519(public_key, message, signature):
        raise Malformed(f"{purpose} proof signature is not authentic")


def validate_executable_definition(value: Any, label: str) -> None:
    value = closed(value, {"form", "schema", "quantum"}, label)
    if value.get("form") != "jsonschema+quantum/1":
        raise Malformed(
            f"{label}.form must be 'jsonschema+quantum/1'"
        )
    schema = closed(
        value.get("schema"),
        {"type", "properties", "required", "additionalProperties"},
        f"{label}.schema",
    )
    if schema.get("type", "object") != "object":
        raise Malformed(f"{label}.schema.type must be 'object'")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise Malformed(f"{label}.schema.properties must be an object")
    required = schema.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) or not item.strip() for item in required)
        or len(required) != len(set(required))
    ):
        raise Malformed(
            f"{label}.schema.required must contain unique non-empty strings"
        )
    if not isinstance(schema.get("additionalProperties", True), bool):
        raise Malformed(f"{label}.schema.additionalProperties must be boolean")
    property_fields = {
        "type", "enum", "const", "minimum", "maximum", "pattern",
    }
    for name, property_schema in properties.items():
        if not isinstance(name, str) or not name.strip():
            raise Malformed(f"{label}.schema property name must not be blank")
        property_schema = closed(
            property_schema,
            property_fields,
            f"{label}.schema.properties[{name!r}]",
        )
        property_type = property_schema.get("type")
        if property_type is not None and property_type not in {
            "string", "integer", "number", "boolean",
        }:
            raise Malformed(f"{label} property type is unsupported")
        if "enum" in property_schema and (
            not isinstance(property_schema["enum"], list)
            or not property_schema["enum"]
        ):
            raise Malformed(f"{label} property enum must be a non-empty array")
        for keyword in ("minimum", "maximum"):
            if keyword not in property_schema:
                continue
            number = property_schema[keyword]
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or property_type not in {"integer", "number"}
            ):
                raise Malformed(f"{label} property {keyword} is invalid")
        if (
            "minimum" in property_schema
            and "maximum" in property_schema
            and property_schema["minimum"] > property_schema["maximum"]
        ):
            raise Malformed(f"{label} property minimum exceeds maximum")
        if "pattern" in property_schema:
            if (
                property_type != "string"
                or not isinstance(property_schema["pattern"], str)
            ):
                raise Malformed(f"{label} property pattern requires string type")
            try:
                re.compile(property_schema["pattern"])
            except re.error as exc:
                raise Malformed(f"{label} property pattern is invalid") from exc
    quantum = value.get("quantum")
    if quantum is not None:
        if not isinstance(quantum, dict):
            raise Malformed(f"{label}.quantum must be an object")
        for name, constraint in quantum.items():
            if not isinstance(name, str) or not name.strip():
                raise Malformed(f"{label}.quantum field must not be blank")
            constraint = closed(
                constraint, {"unit", "multipleOf"}, f"{label}.quantum[{name!r}]"
            )
            unit = constraint.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                raise Malformed(f"{label}.quantum unit is required")
            multiple = constraint.get("multipleOf", 1)
            if (
                isinstance(multiple, bool)
                or not isinstance(multiple, int)
                or multiple < 1
            ):
                raise Malformed(f"{label}.quantum multipleOf must be positive")
            if (
                name not in properties
                or properties[name].get("type") != "integer"
            ):
                raise Malformed(
                    f"{label}.quantum requires a declared integer property"
                )


def validate_convention(value: Any, label: str) -> None:
    value = closed(
        value,
        {"name", "scope", "kind", "definition", "definition_hash", "forum"},
        label,
    )
    name = string(value.get("name"), f"{label}.name")
    scope = string(value.get("scope"), f"{label}.scope")
    if not scope.strip():
        raise Malformed(f"{label}.scope must not be blank")
    if value.get("kind") not in {"executable", "semantic"}:
        raise Malformed(f"{label}.kind is unsupported")
    definition_hash = digest(value.get("definition_hash"), f"{label}.definition_hash")
    if value["kind"] == "executable":
        if "definition" not in value:
            raise Malformed(f"{label} executable convention requires definition")
        validate_executable_definition(
            value["definition"], f"{label}.definition"
        )
        expected = (
            hash_bytes(value["definition"].encode("utf-8"))
            if isinstance(value["definition"], str)
            else hash_json(value["definition"])
        )
        if definition_hash != expected:
            raise Malformed(f"{label} definition_hash does not match definition")
    else:
        forum = exact(
            value.get("forum"),
            {"log_endpoint", "trusted_root_ref"},
            f"{label}.forum",
        )
        log_endpoint = string(
            forum["log_endpoint"], f"{label}.forum.log_endpoint"
        )
        trusted_root_ref = string(
            forum["trusted_root_ref"], f"{label}.forum.trusted_root_ref"
        )
        if not log_endpoint.strip():
            raise Malformed(f"{label}.forum.log_endpoint must not be blank")
        if not trusted_root_ref.strip():
            raise Malformed(f"{label}.forum.trusted_root_ref must not be blank")
        if "definition" in value:
            definition = string(value["definition"], f"{label}.definition")
            if hash_bytes(definition.encode("utf-8")) != definition_hash:
                raise Malformed(
                    f"{label} definition_hash does not match definition"
                )
    if not name.strip():
        raise Malformed(f"{label}.name must not be blank")


def validate_envelope_views(receipt: dict[str, Any], label: str) -> None:
    mandate = closed(
        receipt["mandate"],
        {"deed_schema", "authority", "bounds"},
        f"{label}.mandate",
    )
    retention = closed(
        receipt["retention"],
        {"record", "disclosure"},
        f"{label}.retention",
    )
    deed_schema = mandate.get("deed_schema") or "0.2"
    if deed_schema not in {"0.2", "0.3"}:
        raise Malformed(f"{label}.mandate.deed_schema is unsupported")
    authority = mandate.get("authority")
    if authority is not None:
        authority = exact(
            authority,
            {"principal", "policy", "delegation"},
            f"{label}.mandate.authority",
        )
        principal = string(
            authority["principal"], f"{label}.mandate.authority.principal"
        )
        policy = string(
            authority["policy"], f"{label}.mandate.authority.policy"
        )
        if not principal.strip() or not policy.strip():
            raise Malformed(f"{label}.mandate.authority fields must not be blank")
        delegation = authority["delegation"]
        if not isinstance(delegation, list):
            raise Malformed(f"{label}.mandate.authority.delegation must be an array")
        if deed_schema == "0.2" and any(
            not isinstance(item, str) for item in delegation
        ):
            raise Malformed(f"{label} deed_schema 0.2 delegation must use strings")
        if deed_schema == "0.3" and any(
            not isinstance(item, dict) for item in delegation
        ):
            raise Malformed(f"{label} deed_schema 0.3 delegation must use objects")
        if deed_schema == "0.3":
            grant_fields = {
                "grantor", "grantee", "principal", "parent",
                "policy_digest", "scope_digest", "not_before", "not_after",
                "proof",
            }
            proof_fields = {
                "type", "purpose", "issuer", "verificationMethod", "proofValue",
            }
            for index, grant in enumerate(delegation):
                grant_label = (
                    f"{label}.mandate.authority.delegation[{index}]"
                )
                grant = closed(grant, grant_fields, grant_label)
                for checkpoint_name in ("not_before", "not_after"):
                    if checkpoint_name in grant:
                        exact(
                            grant[checkpoint_name],
                            {"domain", "value"},
                            f"{grant_label}.{checkpoint_name}",
                        )
                exact(grant.get("proof"), proof_fields, f"{grant_label}.proof")
    bounds = mandate.get("bounds")
    if bounds is not None:
        bounds = closed(
            bounds,
            {"scope", "expires", "rollback_window"},
            f"{label}.mandate.bounds",
        )
        scope = bounds.get("scope")
        if isinstance(scope, dict):
            validate_executable_definition(
                scope, f"{label}.mandate.bounds.scope"
            )
            if deed_schema != "0.3":
                raise Malformed(
                    f"{label} structured bounds.scope requires deed_schema 0.3"
                )
        elif not isinstance(scope, str) or not scope.strip():
            raise Malformed(
                f"{label}.mandate.bounds.scope must be a non-empty string "
                "or executable definition"
            )
    remedy = receipt["remedy"]
    if not isinstance(remedy, dict):
        raise Malformed(f"{label}.remedy must be an object")
    if remedy:
        remedy = exact(
            remedy,
            {"challenge_window", "forum", "remedies"},
            f"{label}.remedy",
        )
        challenge_window = string(
            remedy["challenge_window"], f"{label}.remedy.challenge_window"
        )
        if not challenge_window.strip():
            raise Malformed(f"{label}.remedy.challenge_window must not be blank")
        forum = exact(
            remedy["forum"],
            {"log_endpoint", "trusted_root_ref"},
            f"{label}.remedy.forum",
        )
        for name in ("log_endpoint", "trusted_root_ref"):
            value = string(forum[name], f"{label}.remedy.forum.{name}")
            if not value.strip():
                raise Malformed(f"{label}.remedy.forum.{name} must not be blank")
        remedies = objects(remedy["remedies"], f"{label}.remedy.remedies")
        if not remedies:
            raise Malformed(f"{label}.remedy.remedies must not be empty")
        for index, item in enumerate(remedies):
            item_label = f"{label}.remedy.remedies[{index}]"
            item = exact(item, {"rung", "verifier", "anchor"}, item_label)
            if item["rung"] not in {
                "recompute", "challenge", "cure", "revert", "slash", "escalate",
            }:
                raise Malformed(f"{item_label}.rung is unsupported")
            for name in ("verifier", "anchor"):
                value = string(item[name], f"{item_label}.{name}")
                if not value.strip():
                    raise Malformed(f"{item_label}.{name} must not be blank")
            if item["rung"] == "escalate" and authority is None:
                raise Malformed(f"{label} escalate remedy requires authority")
    record = retention.get("record")
    disclosure = retention.get("disclosure")
    if record is not None and record not in {
        "authority-permanent", "operational", "personal-expiring",
    }:
        raise Malformed(f"{label}.retention.record is unsupported")
    if disclosure is not None and disclosure not in {
        "public", "party", "auditor",
    }:
        raise Malformed(f"{label}.retention.disclosure is unsupported")
    if (
        authority is None
        and bounds is None
        and not remedy
        and record is None
        and disclosure is None
    ):
        raise Malformed(f"{label} envelope must not be empty")


def validate_receipt_base(receipt: dict[str, Any], label: str) -> None:
    diagnostic = closed(
        receipt["diagnostic_ref"], {"status", "ref"}, f"{label}.diagnostic_ref"
    )
    status = diagnostic.get("status")
    if status not in {"reference", "not_applicable", "deferred"}:
        raise Malformed(f"{label}.diagnostic_ref.status is unsupported")
    if status == "reference":
        ref = string(diagnostic.get("ref"), f"{label}.diagnostic_ref.ref")
        if not ref.strip():
            raise Malformed(f"{label}.diagnostic_ref.ref must not be blank")
    evidence = objects(receipt["evidence_refs"], f"{label}.evidence_refs")
    for index, item in enumerate(evidence):
        item_label = f"{label}.evidence_refs[{index}]"
        item = exact(item, {"name", "hash", "grounding"}, item_label)
        name = string(item["name"], f"{item_label}.name")
        evidence_hash = string(item["hash"], f"{item_label}.hash")
        if not name.strip() or not evidence_hash.strip():
            raise Malformed(f"{item_label} name and hash must not be blank")
        if item["grounding"] not in GROUNDING_CLASSES:
            raise Malformed(f"{item_label}.grounding is unsupported")
    if receipt["stake"] is not None:
        raise Malformed(f"{label}.stake is reserved and must be null")
    validate_envelope_views(receipt, label)
    conventions = objects(receipt["conventions"], f"{label}.conventions")
    for index, item in enumerate(conventions):
        validate_convention(item, f"{label}.conventions[{index}]")
    if conventions:
        raise Malformed(
            f"{label} glyph.agent-incident-packet/0.1-draft "
            "requires conventions to be empty"
        )
    try:
        event_id = uuid.UUID(receipt["event_id"])
    except (ValueError, AttributeError, TypeError) as exc:
        raise Malformed(
            f"{label}.event_id must be a canonical lowercase UUIDv4"
        ) from exc
    if (
        event_id.version != 4
        or str(event_id) != receipt["event_id"]
        or receipt["event_id"] != receipt["event_id"].lower()
    ):
        raise Malformed(f"{label}.event_id must be a canonical lowercase UUIDv4")
    instant(receipt["claimed_at"], f"{label}.claimed_at")


def validate_evidence_rules(
    receipt: dict[str, Any],
    *,
    label: str,
    team_roles: set[str],
) -> None:
    action = receipt["action"]
    subject = action["subject"]
    evidence = receipt["evidence_refs"]
    if action["type"] == "capability.decide":
        if any(item["grounding"] != "self_asserted" for item in evidence):
            raise Malformed(f"{label} gateway decision evidence must be self_asserted")
        if [item["hash"] for item in evidence] != [subject["evidence_hash"]]:
            raise Malformed(
                f"{label} decision evidence_hash must equal the sole "
                "ActionReceipt evidence hash"
            )
    elif action["type"] == "capability.observe":
        expected = (
            "counterparty_signed"
            if subject["issuer_role"] == "target"
            else "self_asserted"
        )
        if any(item["grounding"] != expected for item in evidence):
            raise Malformed(
                f"{label} {subject['issuer_role']} observation evidence "
                f"must be {expected}"
            )
        if [item["hash"] for item in evidence] != subject["evidence_refs"]:
            raise Malformed(
                f"{label} observation evidence_refs must equal the ordered "
                "ActionReceipt evidence hashes"
            )
    if subject["issuer_role"] in team_roles and any(
        item["grounding"] == "third_party_anchored" for item in evidence
    ):
        raise Malformed(
            f"{label} team-controlled infrastructure cannot claim "
            "third_party_anchored"
        )
    if any(
        item["grounding"] == "execution_verified"
        and not item["name"].startswith("recomputation:")
        for item in evidence
    ):
        raise Malformed(
            f"{label} execution_verified is limited to named deterministic "
            "recomputations"
        )


def validate_subject(action_type: str, subject: Any) -> dict[str, Any]:
    if action_type not in ACTION_FIELDS:
        raise Malformed(f"unsupported profile action {action_type!r}")
    subject = exact(subject, ACTION_FIELDS[action_type], f"{action_type}.subject")
    if subject["profile"] != PROFILE:
        raise Malformed(f"{action_type}.subject.profile mismatch")
    role = string(subject["issuer_role"], f"{action_type}.subject.issuer_role")
    allowed_roles = ACTION_ALLOWED_ROLES[action_type]
    if allowed_roles is not None and role not in allowed_roles:
        raise Malformed(
            f"{action_type}.subject.issuer_role must be one of "
            f"{sorted(allowed_roles)!r}"
        )
    label = f"{action_type}.subject"
    if action_type == "eval.run.authorize":
        for name in (
            "run_id", "model_id", "harness_id", "safeguards_id",
            "valid_from", "valid_until",
        ):
            string(subject[name], f"{label}.{name}")
        digest(subject["policy_digest"], f"{label}.policy_digest")
        strings(subject["authorized_targets"], f"{label}.authorized_targets", unique=True)
        strings(subject["prohibitions"], f"{label}.prohibitions", unique=True)
        budgets = exact(
            subject["budgets"],
            {"compute_units", "max_actions", "duration_seconds"},
            f"{label}.budgets",
        )
        for name, value in budgets.items():
            integer(value, f"{label}.budgets.{name}")
        if not isinstance(subject["role_issuers"], dict) or not subject["role_issuers"]:
            raise Malformed(f"{label}.role_issuers must be a non-empty object")
        for declared_role, issuer in subject["role_issuers"].items():
            string(declared_role, f"{label}.role_issuers role")
            string(issuer, f"{label}.role_issuers.{declared_role}")
        try:
            valid_from = datetime.fromisoformat(
                subject["valid_from"].replace("Z", "+00:00")
            )
            valid_until = datetime.fromisoformat(
                subject["valid_until"].replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise Malformed(f"{label} validity interval is not RFC 3339") from exc
        if valid_from.tzinfo is None or valid_until.tzinfo is None:
            raise Malformed(f"{label} validity interval requires a timezone")
        if valid_from >= valid_until:
            raise Malformed(f"{label}.valid_from must precede valid_until")
    elif action_type == "capability.decide":
        for name in ("run_id", "request_id", "decision_event_id", "anchor_id"):
            string(subject[name], f"{label}.{name}")
        for name in ("request_hash", "mandate_ref", "policy_digest", "evidence_hash"):
            digest(subject[name], f"{label}.{name}")
        if subject["decision"] not in DECISIONS:
            raise Malformed(f"{label}.decision is unsupported")
        if subject["protocol"] not in PROTOCOLS:
            raise Malformed(f"{label}.protocol is unsupported")
        strings(subject["rationale_codes"], f"{label}.rationale_codes", unique=True)
    elif action_type == "capability.observe":
        for name in (
            "run_id", "request_id", "observation_id", "anchor_id",
            "transport_status",
        ):
            string(subject[name], f"{label}.{name}")
        for name in (
            "decision_attestation", "effect_hash", "mandate_ref", "evidence_hash",
        ):
            digest(subject[name], f"{label}.{name}")
        refs = strings(subject["evidence_refs"], f"{label}.evidence_refs", unique=True)
        for index, value in enumerate(refs):
            digest(value, f"{label}.evidence_refs[{index}]")
        if subject["observation_class"] not in OBSERVATION_CLASSES:
            raise Malformed(f"{label}.observation_class is unsupported")
        if subject["protocol"] not in PROTOCOLS:
            raise Malformed(f"{label}.protocol is unsupported")
    elif action_type == "trajectory.decide":
        string(subject["run_id"], f"{label}.run_id")
        for name in ("mandate_ref", "policy_digest"):
            digest(subject[name], f"{label}.{name}")
        lineage = strings(
            subject["ordered_lineage"], f"{label}.ordered_lineage", unique=True
        )
        for index, value in enumerate(lineage):
            digest(value, f"{label}.ordered_lineage[{index}]")
        if subject["decision"] not in TRAJECTORY_DECISIONS:
            raise Malformed(f"{label}.decision is unsupported")
        strings(subject["rationale_codes"], f"{label}.rationale_codes", unique=True)
        strings(
            subject["requested_controls"],
            f"{label}.requested_controls",
            unique=True,
        )
    elif action_type == "incident.statement":
        for name in ("statement_id", "incident_id", "topic", "claim"):
            string(subject[name], f"{label}.{name}")
        if subject["epistemic_status"] not in EPISTEMIC_STATUSES:
            raise Malformed(f"{label}.epistemic_status is unsupported")
        if (
            subject["epistemic_status"] == "counterparty_confirmed"
            and role != "affected_party"
        ):
            raise Malformed(
                f"{label}.counterparty_confirmed requires affected_party issuer"
            )
        refs = strings(subject["evidence_refs"], f"{label}.evidence_refs", unique=True)
        for index, value in enumerate(refs):
            digest(value, f"{label}.evidence_refs[{index}]")
    elif action_type == "incident.handoff":
        for name in (
            "incident_id", "recipient", "correction_channel", "challenge_channel",
        ):
            string(subject[name], f"{label}.{name}")
        for name in ("mandate_ref", "timeline_hash", "coverage_hash"):
            digest(subject[name], f"{label}.{name}")
        for name in ("statement_refs", "parent_refs"):
            refs = strings(subject[name], f"{label}.{name}", unique=True)
            for index, value in enumerate(refs):
                digest(value, f"{label}.{name}[{index}]")
        strings(
            subject["requested_actions"], f"{label}.requested_actions", unique=True
        )
        strings(
            subject["disclosure_conditions"],
            f"{label}.disclosure_conditions",
            unique=True,
        )
    elif action_type == "incident.packet.publish":
        string(subject["packet_id"], f"{label}.packet_id")
        digest(subject["packet_core_hash"], f"{label}.packet_core_hash")
        components = exact(
            subject["component_hashes"], set(COMPONENTS), f"{label}.component_hashes"
        )
        for name, value in components.items():
            digest(value, f"{label}.component_hashes.{name}")
    elif action_type == "incident.correct":
        for name in ("correction_id", "reason", "correction_channel"):
            string(subject[name], f"{label}.{name}")
        if subject["supersedes_kind"] not in {"packet", "statement"}:
            raise Malformed(f"{label}.supersedes_kind is unsupported")
        digest(subject["supersedes_ref"], f"{label}.supersedes_ref")
        digest(subject["replacement_ref"], f"{label}.replacement_ref")
    return subject


def validate_receipt(
    raw: bytes,
    *,
    label: str,
    context: dict[str, set[str]],
    team_roles: set[str],
    expected_action: str | None = None,
) -> dict[str, Any]:
    receipt = exact(load_json(raw, label), RECEIPT_FIELDS, label)
    if (
        receipt["schema_version"] != "0.4"
        or receipt["canonicalization"] != "bulla-jcs-int/1"
        or receipt["kind"] != "action_receipt"
    ):
        raise Malformed(f"{label} is not an ActionReceipt v0.4 profile record")
    validate_receipt_base(receipt, label)
    action = exact(receipt["action"], {"type", "subject"}, f"{label}.action")
    if expected_action is not None and action["type"] != expected_action:
        raise Malformed(
            f"{label} expected action {expected_action!r}, got {action['type']!r}"
        )
    subject = validate_subject(action["type"], action["subject"])
    role = subject["issuer_role"]
    signature = receipt["signature"]
    issuer = signature.get("issuer") if isinstance(signature, dict) else None
    if issuer is not None and issuer not in context.get(role, set()):
        raise Malformed(f"{label} issuer is not accepted for role {role!r}")
    authority = receipt["mandate"].get("authority")
    if (
        not isinstance(authority, dict)
        or (receipt["mandate"].get("deed_schema") or "0.2") != "0.2"
        or authority.get("delegation") != []
        or authority.get("principal") != issuer
    ):
        raise Malformed(
            f"{label} incident profile requires direct deed_schema 0.2 "
            "authority with an empty delegation and principal equal to the "
            "accepted signer"
        )
    hashes = receipt_hashes(receipt)
    exact(
        receipt["hashes"],
        {"content", "event", "attestation", "log_leaf"},
        f"{label}.hashes",
    )
    for name in ("content", "event", "attestation", "log_leaf"):
        if receipt["hashes"][name] != hashes[name]:
            raise Malformed(f"{label} {name} hash mismatch")
    validate_proof(
        receipt["signature"],
        purpose="content",
        signed_digest=hashes["content"],
        expected_issuer=issuer,
    )
    validate_proof(
        receipt["occurrence"],
        purpose="occurrence",
        signed_digest=hashes["event"],
        expected_issuer=issuer,
    )
    validate_proof(
        receipt["authorization"],
        purpose="authorization",
        signed_digest=hashes["authorization"],
        expected_issuer=issuer,
    )
    validate_evidence_rules(
        receipt,
        label=label,
        team_roles=team_roles,
    )
    return receipt


def validate_core(core: Any) -> dict[str, Any]:
    core = exact(core, CORE_FIELDS, "packet-core")
    if core["profile"] != PROFILE:
        raise Malformed(f"unsupported packet profile {core['profile']!r}")
    string(core["packet_id"], "packet-core.packet_id")
    string(core["incident_id"], "packet-core.incident_id")
    integer(core["revision"], "packet-core.revision", minimum=1)
    string(core["classification"], "packet-core.classification")
    digest(
        core["supersedes_core_hash"],
        "packet-core.supersedes_core_hash",
        nullable=True,
    )
    for name in COMPONENTS:
        objects(core[name], f"packet-core.{name}")
    return core


def validate_core_components(core: dict[str, Any]) -> dict[str, Any]:
    """Validate packet-core component topology without reading packet members."""
    role_names: set[str] = set()
    role_issuers: set[str] = set()
    for index, item in enumerate(core["roles"]):
        label = f"packet-core.roles[{index}]"
        item = exact(item, {"role", "issuer"}, label)
        role = string(item["role"], f"{label}.role")
        issuer = string(item["issuer"], f"{label}.issuer")
        if role in role_names or issuer in role_issuers:
            raise Malformed("duplicate role or cross-role issuer")
        role_names.add(role)
        role_issuers.add(issuer)

    timeline_paths: set[str] = set()
    timeline_hashes: set[str] = set()
    timeline_hash_by_path: dict[str, str] = {}
    timeline_type_by_path: dict[str, str] = {}
    for index, item in enumerate(core["timeline"]):
        label = f"packet-core.timeline[{index}]"
        item = exact(
            item,
            {
                "sequence", "receipt_path", "byte_length", "sha256",
                "attestation_hash", "action_type",
            },
            label,
        )
        if integer(item["sequence"], f"{label}.sequence", minimum=1) != index + 1:
            raise Malformed("timeline sequence must be contiguous from 1")
        path = safe_path(item["receipt_path"], f"{label}.receipt_path")
        if not path.startswith("receipts/"):
            raise Malformed("timeline receipts must be under receipts/")
        integer(item["byte_length"], f"{label}.byte_length")
        digest(item["sha256"], f"{label}.sha256")
        attestation = digest(item["attestation_hash"], f"{label}.attestation_hash")
        action_type = string(item["action_type"], f"{label}.action_type")
        if action_type not in set(ACTION_FIELDS) - {"incident.packet.publish"}:
            raise Malformed("unsupported timeline action type")
        if path in timeline_paths or attestation in timeline_hashes:
            raise Malformed("timeline paths and attestation hashes must be unique")
        timeline_paths.add(path)
        timeline_hashes.add(attestation)
        timeline_hash_by_path[path] = attestation
        timeline_type_by_path[path] = action_type

    artifacts: dict[str, dict[str, Any]] = {}
    artifact_paths: set[str] = set()
    for index, item in enumerate(core["artifacts"]):
        label = f"packet-core.artifacts[{index}]"
        item = exact(
            item,
            {
                "artifact_id", "path", "withheld_ref", "role", "media_type",
                "byte_length", "sha256", "disclosure_state", "retention",
                "access_condition",
            },
            label,
        )
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id in artifacts:
            raise Malformed("duplicate artifact id")
        artifacts[artifact_id] = item
        integer(item["byte_length"], f"{label}.byte_length")
        digest(item["sha256"], f"{label}.sha256")
        for name in ("role", "media_type", "retention", "access_condition"):
            string(item[name], f"{label}.{name}")
        if item["disclosure_state"] == "released":
            path = safe_path(item["path"], f"{label}.path")
            if (
                path in {CORE_FILE, PUBLISH_FILE}
                or path.startswith("receipts/")
                or path in artifact_paths
                or item["withheld_ref"] is not None
            ):
                raise Malformed("released artifact path or withheld_ref is invalid")
            artifact_paths.add(path)
        elif item["disclosure_state"] in {"controlled", "withheld"}:
            if item["path"] is not None:
                raise Malformed("unreleased artifact path must be null")
            string(item["withheld_ref"], f"{label}.withheld_ref")
        else:
            raise Malformed("unsupported artifact disclosure state")

    denominator_keys: set[tuple[str, str, str]] = set()
    denominator_ids: set[str] = set()
    checkpoints: set[str] = set()
    phase_counts: dict[str, dict[str, int]] = {}
    for index, item in enumerate(core["denominators"]):
        label = f"packet-core.denominators[{index}]"
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id", "provenance",
                "checkpoint_attestation",
            },
            label,
        )
        key = (
            string(item["anchor_id"], f"{label}.anchor_id"),
            item["phase"],
            item["protocol"],
        )
        if item["phase"] not in {"decision", "effect"}:
            raise Malformed(f"{label}.phase is unsupported")
        if item["protocol"] not in {"http", "mcp"}:
            raise Malformed(f"{label}.protocol is unsupported")
        if key in denominator_keys:
            raise Malformed("duplicate denominator key")
        denominator_keys.add(key)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts or artifacts[artifact_id]["role"] != "denominator":
            raise Malformed(f"{label} must reference a denominator artifact")
        if artifact_id in denominator_ids:
            raise Malformed("denominator artifact is reused")
        denominator_ids.add(artifact_id)
        if item["provenance"] not in {
            "PATH_SEPARATE_TEAM_CONTROLLED", "SEPARATELY_CONTROLLED", "UNKNOWN",
        }:
            raise Malformed(f"{label}.provenance is unsupported")
        checkpoint = digest(
            item["checkpoint_attestation"], f"{label}.checkpoint_attestation"
        )
        if checkpoint in checkpoints:
            raise Malformed("denominator checkpoints must be unique")
        checkpoints.add(checkpoint)
        counts = phase_counts.setdefault(
            item["protocol"], {"decision": 0, "effect": 0}
        )
        counts[item["phase"]] += 1
    if not denominator_keys or any(
        counts != {"decision": 1, "effect": 1}
        for counts in phase_counts.values()
    ):
        raise Malformed(
            "each represented protocol must declare one decision and effect denominator"
        )

    coverage_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(core["coverage"]):
        label = f"packet-core.coverage[{index}]"
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id",
                "minimum_verification_depth",
            },
            label,
        )
        key = (
            string(item["anchor_id"], f"{label}.anchor_id"),
            item["phase"],
            item["protocol"],
        )
        if item["phase"] not in {"decision", "effect"}:
            raise Malformed(f"{label}.phase is unsupported")
        if item["protocol"] not in {"http", "mcp"}:
            raise Malformed(f"{label}.protocol is unsupported")
        if key in coverage_keys:
            raise Malformed("duplicate coverage key")
        coverage_keys.add(key)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts or artifacts[artifact_id]["role"] != "coverage":
            raise Malformed(f"{label} must reference a coverage artifact")
        if artifact_id in denominator_ids:
            raise Malformed("coverage and denominator artifacts must be distinct")
        if item["minimum_verification_depth"] != "attestation":
            raise Malformed(
                f"{label}.minimum_verification_depth must be 'attestation'"
            )
    if coverage_keys != denominator_keys:
        raise Malformed("denominator and coverage keys differ")

    for name, expected_type in (
        ("statements", "incident.statement"),
        ("corrections", "incident.correct"),
    ):
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(core[name]):
            label = f"packet-core.{name}[{index}]"
            item = exact(item, {"receipt_path", "attestation_hash"}, label)
            path = safe_path(item["receipt_path"], f"{label}.receipt_path")
            attestation = digest(
                item["attestation_hash"], f"{label}.attestation_hash"
            )
            if (
                timeline_hash_by_path.get(path) != attestation
                or timeline_type_by_path.get(path) != expected_type
            ):
                raise Malformed(f"{label} is not bound to the expected timeline row")
            if (path, attestation) in seen:
                raise Malformed(f"duplicate {name[:-1]} reference")
            seen.add((path, attestation))

    if len(core["witnesses"]) > 1:
        raise Malformed(
            "packet-core.witnesses supports at most one witness reference "
            "in this draft"
        )
    witness_ids: set[str] = set()
    for index, item in enumerate(core["witnesses"]):
        label = f"packet-core.witnesses[{index}]"
        item = exact(item, {"witness_id", "artifact_id", "root_ref"}, label)
        witness_id = string(item["witness_id"], f"{label}.witness_id")
        if witness_id in witness_ids:
            raise Malformed("duplicate witness id")
        witness_ids.add(witness_id)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts or artifacts[artifact_id]["role"] != "witness":
            raise Malformed(f"{label} must reference a witness artifact")
        string(item["root_ref"], f"{label}.root_ref")

    # Redaction topology is also part of structural ingestion.
    redaction_ids: set[str] = set()
    for index, item in enumerate(core["redactions"]):
        label = f"packet-core.redactions[{index}]"
        item = exact(
            item,
            {
                "redaction_id", "record_artifact_id", "source_artifact_id",
                "released_artifact_id", "tool", "tool_version", "rules_hash",
                "reviewer_statement_ref",
            },
            label,
        )
        redaction_id = string(item["redaction_id"], f"{label}.redaction_id")
        if redaction_id in redaction_ids:
            raise Malformed("duplicate redaction id")
        redaction_ids.add(redaction_id)
        for name in (
            "record_artifact_id", "source_artifact_id", "released_artifact_id",
        ):
            if item[name] not in artifacts:
                raise Malformed(f"{label}.{name} references an unknown artifact")
        if artifacts[item["record_artifact_id"]]["role"] != "redaction":
            raise Malformed("redaction record artifact role must be 'redaction'")
        if artifacts[item["record_artifact_id"]]["disclosure_state"] != "released":
            raise Malformed("redaction record artifact must be released")
        if item["source_artifact_id"] == item["released_artifact_id"]:
            raise Malformed("redaction source and release must differ")
        if item["record_artifact_id"] in {
            item["source_artifact_id"], item["released_artifact_id"],
        }:
            raise Malformed("redaction record artifact must differ from source and release")
        if artifacts[item["released_artifact_id"]]["disclosure_state"] != "released":
            raise Malformed("redaction released artifact must be released")
        string(item["tool"], f"{label}.tool")
        string(item["tool_version"], f"{label}.tool_version")
        digest(item["rules_hash"], f"{label}.rules_hash")
        digest(item["reviewer_statement_ref"], f"{label}.reviewer_statement_ref")
    return core


def parse_context(path: Path) -> tuple[dict[str, set[str]], set[str], set[str]]:
    value = exact(
        load_json(path.read_bytes(), "context"),
        {
            "accepted_issuers_by_role",
            "trusted_witness_roots",
            "team_controlled_roles",
        },
        "context",
    )
    if not isinstance(value["accepted_issuers_by_role"], dict):
        raise Malformed("context.accepted_issuers_by_role must be an object")
    accepted = {
        string(role, "context role"): set(
            strings(issuers, f"context.accepted_issuers_by_role.{role}", unique=True)
        )
        for role, issuers in value["accepted_issuers_by_role"].items()
    }
    issuer_roles: dict[str, str] = {}
    for role, issuers in accepted.items():
        for issuer in issuers:
            prior = issuer_roles.setdefault(issuer, role)
            if prior != role:
                raise Malformed(
                    "context issuer is accepted for more than one incident role"
                )
    return (
        accepted,
        set(strings(value["trusted_witness_roots"], "context.trusted_witness_roots", unique=True)),
        set(strings(value["team_controlled_roles"], "context.team_controlled_roles", unique=True)),
    )


def projection(receipt: dict[str, Any]) -> tuple[tuple[str, str, str], dict[str, str]] | None:
    subject = receipt["action"]["subject"]
    if receipt["action"]["type"] == "capability.decide":
        return (
            subject["anchor_id"], "decision", subject["protocol"],
        ), {
            "anchor_id": subject["anchor_id"],
            "observation_id": subject["decision_event_id"],
            "run_id": subject["run_id"],
            "protocol": subject["protocol"],
            "operation_ref": subject["request_hash"],
            "phase": "decision",
            "evidence_hash": subject["evidence_hash"],
        }
    if receipt["action"]["type"] == "capability.observe":
        return (
            subject["anchor_id"], "effect", subject["protocol"],
        ), {
            "anchor_id": subject["anchor_id"],
            "observation_id": subject["observation_id"],
            "run_id": subject["run_id"],
            "protocol": subject["protocol"],
            "operation_ref": subject["effect_hash"],
            "phase": "effect",
            "evidence_hash": subject["evidence_hash"],
        }
    return None


def proof_failure_dimensions(
    raw: bytes, problem: str
) -> tuple[bool, bool, bool, bool]:
    """Classify the proof dimensions without conflating byte/semantic errors."""
    try:
        receipt = load_json(raw, "receipt proof classification")
    except Malformed:
        return False, False, False, False
    signature = receipt.get("signature") if isinstance(receipt, dict) else None
    occurrence = receipt.get("occurrence") if isinstance(receipt, dict) else None
    authorization = receipt.get("authorization") if isinstance(receipt, dict) else None
    missing_signature = not isinstance(signature, dict)
    content_failed = missing_signature
    occurrence_proof_failed = not isinstance(occurrence, dict)
    authorization_proof_failed = not isinstance(authorization, dict)
    try:
        hashes = receipt_hashes(receipt)
        issuer = signature.get("issuer") if isinstance(signature, dict) else None
        for proof, purpose, signed_digest, target in (
            (signature, "content", hashes["content"], "content"),
            (occurrence, "occurrence", hashes["event"], "occurrence"),
            (
                authorization,
                "authorization",
                hashes["authorization"],
                "authorization",
            ),
        ):
            try:
                validate_proof(
                    proof,
                    purpose=purpose,
                    signed_digest=signed_digest,
                    expected_issuer=issuer,
                )
            except Malformed:
                if target == "content":
                    content_failed = True
                elif target == "occurrence":
                    occurrence_proof_failed = True
                else:
                    authorization_proof_failed = True
    except (Malformed, KeyError, TypeError):
        # A non-proof structural error is not itself evidence that a proof failed.
        pass
    signature_failed = (
        content_failed or occurrence_proof_failed or authorization_proof_failed
    )
    occurrence_failed = content_failed or occurrence_proof_failed
    authority_failed = (
        content_failed or occurrence_proof_failed or authorization_proof_failed
    )
    return (
        signature_failed,
        authority_failed,
        occurrence_failed,
        missing_signature,
    )


def verify(packet_dir: Path, context_path: Path) -> dict[str, Any]:
    files = read_packet(packet_dir)
    if CORE_FILE not in files or PUBLISH_FILE not in files:
        raise Malformed("packet is missing packet-core.json or publish-receipt.json")
    core = validate_core_components(
        validate_core(load_json(files[CORE_FILE], CORE_FILE))
    )
    accepted, trusted_roots, team_roles = parse_context(context_path)

    declared = {CORE_FILE, PUBLISH_FILE}
    timeline_paths: set[str] = set()
    timeline_hashes: set[str] = set()
    timeline_types_by_path: dict[str, str] = {}
    timeline_hash_by_path: dict[str, str] = {}
    for index, item in enumerate(core["timeline"]):
        item = exact(
            item,
            {
                "sequence", "receipt_path", "byte_length", "sha256",
                "attestation_hash", "action_type",
            },
            f"packet-core.timeline[{index}]",
        )
        if integer(item["sequence"], "timeline.sequence", minimum=1) != index + 1:
            raise Malformed("timeline sequence must be contiguous from 1")
        path = safe_path(item["receipt_path"], "timeline.receipt_path")
        if not path.startswith("receipts/"):
            raise Malformed("timeline receipts must be under receipts/")
        if path in timeline_paths:
            raise Malformed("duplicate timeline receipt path")
        timeline_paths.add(path)
        integer(item["byte_length"], "timeline.byte_length")
        digest(item["sha256"], "timeline.sha256")
        attestation = digest(item["attestation_hash"], "timeline.attestation_hash")
        if attestation in timeline_hashes:
            raise Malformed("duplicate timeline attestation hash")
        timeline_hashes.add(attestation)
        if item["action_type"] not in set(ACTION_FIELDS) - {"incident.packet.publish"}:
            raise Malformed("unsupported timeline action type")
        timeline_types_by_path[path] = item["action_type"]
        timeline_hash_by_path[path] = attestation
        declared.add(path)

    artifacts: dict[str, dict[str, Any]] = {}
    artifact_paths: set[str] = set()
    unavailable: list[str] = []
    for index, item in enumerate(core["artifacts"]):
        item = exact(
            item,
            {
                "artifact_id", "path", "withheld_ref", "role", "media_type",
                "byte_length", "sha256", "disclosure_state", "retention",
                "access_condition",
            },
            f"packet-core.artifacts[{index}]",
        )
        artifact_id = string(item["artifact_id"], "artifact.artifact_id")
        if artifact_id in artifacts:
            raise Malformed("duplicate artifact id")
        artifacts[artifact_id] = item
        integer(item["byte_length"], "artifact.byte_length")
        digest(item["sha256"], "artifact.sha256")
        for name in ("role", "media_type", "retention", "access_condition"):
            string(item[name], f"artifact.{name}")
        if item["disclosure_state"] == "released":
            path = safe_path(item["path"], "artifact.path")
            if (
                path in {CORE_FILE, PUBLISH_FILE}
                or path.startswith("receipts/")
                or path in artifact_paths
            ):
                raise Malformed("artifact path is duplicate or reserved")
            if item["withheld_ref"] is not None:
                raise Malformed("released artifact withheld_ref must be null")
            artifact_paths.add(path)
            declared.add(path)
        elif item["disclosure_state"] in {"controlled", "withheld"}:
            if item["path"] is not None:
                raise Malformed("unreleased artifact path must be null")
            string(item["withheld_ref"], "artifact.withheld_ref")
            unavailable.append(artifact_id)
        else:
            raise Malformed("unsupported artifact disclosure state")

    denominator_keys: set[tuple[str, str, str]] = set()
    denominator_artifact_ids: set[str] = set()
    denominator_checkpoints: set[str] = set()
    for index, item in enumerate(core["denominators"]):
        label = f"packet-core.denominators[{index}]"
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id", "provenance",
                "checkpoint_attestation",
            },
            label,
        )
        key = (
            string(item["anchor_id"], f"{label}.anchor_id"),
            item["phase"],
            item["protocol"],
        )
        if item["phase"] not in {"decision", "effect"}:
            raise Malformed(f"{label}.phase is unsupported")
        if item["protocol"] not in {"http", "mcp"}:
            raise Malformed(f"{label}.protocol is unsupported")
        if key in denominator_keys:
            raise Malformed("duplicate denominator key")
        denominator_keys.add(key)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts:
            raise Malformed(f"{label} references an unknown artifact")
        if artifacts[artifact_id]["role"] != "denominator":
            raise Malformed(f"{label} artifact role must be 'denominator'")
        if artifact_id in denominator_artifact_ids:
            raise Malformed("denominator artifact is reused")
        denominator_artifact_ids.add(artifact_id)
        if item["provenance"] not in {
            "SEPARATELY_CONTROLLED", "PATH_SEPARATE_TEAM_CONTROLLED", "UNKNOWN",
        }:
            raise Malformed(f"{label}.provenance is unsupported")
        checkpoint = digest(
            item["checkpoint_attestation"], f"{label}.checkpoint_attestation"
        )
        if checkpoint in denominator_checkpoints:
            raise Malformed(
                "each denominator must use a distinct checkpoint attestation"
            )
        denominator_checkpoints.add(checkpoint)

    coverage_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(core["coverage"]):
        label = f"packet-core.coverage[{index}]"
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id",
                "minimum_verification_depth",
            },
            label,
        )
        key = (
            string(item["anchor_id"], f"{label}.anchor_id"),
            item["phase"],
            item["protocol"],
        )
        if item["phase"] not in {"decision", "effect"}:
            raise Malformed(f"{label}.phase is unsupported")
        if item["protocol"] not in {"http", "mcp"}:
            raise Malformed(f"{label}.protocol is unsupported")
        if key in coverage_keys:
            raise Malformed("duplicate coverage key")
        coverage_keys.add(key)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts:
            raise Malformed(f"{label} references an unknown artifact")
        if artifacts[artifact_id]["role"] != "coverage":
            raise Malformed(f"{label} artifact role must be 'coverage'")
        if artifact_id in denominator_artifact_ids:
            raise Malformed("coverage and denominator must use distinct artifacts")
        if item["minimum_verification_depth"] != "attestation":
            raise Malformed(
                f"{label}.minimum_verification_depth must be 'attestation'"
            )
    if denominator_keys != coverage_keys or not denominator_keys:
        raise Malformed("denominator and coverage keys differ")

    redaction_ids: set[str] = set()
    for index, item in enumerate(core["redactions"]):
        label = f"packet-core.redactions[{index}]"
        item = exact(
            item,
            {
                "redaction_id", "record_artifact_id", "source_artifact_id",
                "released_artifact_id", "tool", "tool_version", "rules_hash",
                "reviewer_statement_ref",
            },
            label,
        )
        redaction_id = string(item["redaction_id"], f"{label}.redaction_id")
        if redaction_id in redaction_ids:
            raise Malformed("duplicate redaction id")
        redaction_ids.add(redaction_id)
        for name in (
            "record_artifact_id",
            "source_artifact_id",
            "released_artifact_id",
        ):
            if item[name] not in artifacts:
                raise Malformed(f"{label}.{name} references an unknown artifact")
        record = artifacts[item["record_artifact_id"]]
        released = artifacts[item["released_artifact_id"]]
        if record["role"] != "redaction":
            raise Malformed("redaction record artifact role must be 'redaction'")
        if record["disclosure_state"] != "released":
            raise Malformed("redaction record artifact must be released")
        if item["source_artifact_id"] == item["released_artifact_id"]:
            raise Malformed("redaction source and released artifacts must differ")
        if item["record_artifact_id"] in {
            item["source_artifact_id"],
            item["released_artifact_id"],
        }:
            raise Malformed(
                "redaction record artifact must differ from source and release"
            )
        if released["disclosure_state"] != "released":
            raise Malformed("redaction released artifact must be released")
        string(item["tool"], f"{label}.tool")
        string(item["tool_version"], f"{label}.tool_version")
        digest(item["rules_hash"], f"{label}.rules_hash")
        digest(item["reviewer_statement_ref"], f"{label}.reviewer_statement_ref")

    for name, expected_type in (
        ("statements", "incident.statement"),
        ("corrections", "incident.correct"),
    ):
        references: set[tuple[str, str]] = set()
        for index, item in enumerate(core[name]):
            label = f"packet-core.{name}[{index}]"
            item = exact(item, {"receipt_path", "attestation_hash"}, label)
            path = safe_path(item["receipt_path"], f"{label}.receipt_path")
            attestation = digest(
                item["attestation_hash"], f"{label}.attestation_hash"
            )
            if (
                path not in timeline_hash_by_path
                or attestation not in timeline_hashes
                or timeline_hash_by_path[path] != attestation
            ):
                raise Malformed(f"{label} must reference one timeline receipt")
            if timeline_types_by_path[path] != expected_type:
                raise Malformed(
                    f"{label} must reference an {expected_type} timeline receipt"
                )
            reference = (path, attestation)
            if reference in references:
                raise Malformed(f"duplicate {name[:-1]} reference")
            references.add(reference)

    if len(core["witnesses"]) > 1:
        raise Malformed(
            "packet-core.witnesses supports at most one witness reference "
            "in this draft"
        )
    witness_ids: set[str] = set()
    for index, item in enumerate(core["witnesses"]):
        label = f"packet-core.witnesses[{index}]"
        item = exact(item, {"witness_id", "artifact_id", "root_ref"}, label)
        witness_id = string(item["witness_id"], f"{label}.witness_id")
        if witness_id in witness_ids:
            raise Malformed("duplicate witness id")
        witness_ids.add(witness_id)
        artifact_id = string(item["artifact_id"], f"{label}.artifact_id")
        if artifact_id not in artifacts:
            raise Malformed(f"{label}.artifact_id references an unknown artifact")
        if artifacts[artifact_id]["role"] != "witness":
            raise Malformed(f"{label} artifact role must be 'witness'")
        string(item["root_ref"], f"{label}.root_ref")

    if set(files) != declared:
        raise Malformed(
            f"packet declared-file mismatch: undeclared={sorted(set(files)-declared)}, "
            f"missing={sorted(declared-set(files))}"
        )

    errors: list[str] = []
    warnings: list[str] = []
    artifact_failed = False
    trace_failed = False
    for artifact_id, item in artifacts.items():
        if item["disclosure_state"] != "released":
            continue
        raw = files[item["path"]]
        if len(raw) != item["byte_length"] or hash_bytes(raw) != item["sha256"]:
            artifact_failed = True
            trace_failed |= item["role"] == "trace"
            errors.append(f"artifact {artifact_id!r} byte length or digest mismatch")

    roles: dict[str, str] = {}
    role_issuers: set[str] = set()
    role_failed = False
    for index, item in enumerate(core["roles"]):
        item = exact(item, {"role", "issuer"}, f"packet-core.roles[{index}]")
        role, issuer = string(item["role"], "role"), string(item["issuer"], "issuer")
        if role in roles or issuer in role_issuers:
            raise Malformed("duplicate role or cross-role issuer")
        roles[role] = issuer
        role_issuers.add(issuer)
        if issuer not in accepted.get(role, set()):
            role_failed = True
            errors.append(f"packet role {role!r} is not accepted out of band")

    receipts: list[dict[str, Any] | None] = []
    receipt_failed = False
    signature_failed = False
    authority_failed = False
    occurrence_failed = False
    timeline_byte_failed = False
    receipt_error_paths: set[str] = set()
    receipt_errors_by_path: dict[str, list[str]] = {}
    for item in core["timeline"]:
        raw = files[item["receipt_path"]]
        byte_problem = (
            len(raw) != item["byte_length"]
            or hash_bytes(raw) != item["sha256"]
        )
        if byte_problem:
            timeline_byte_failed = True
            receipt_failed = True
            receipt_error_paths.add(item["receipt_path"])
            problem = (
                f"{item['receipt_path']}: exact bytes do not match packet "
                "timeline commitment"
            )
            receipt_errors_by_path.setdefault(
                item["receipt_path"], []
            ).append(problem)
            errors.append(problem)
        try:
            receipt = validate_receipt(
                raw,
                label=item["receipt_path"],
                context=accepted,
                team_roles=team_roles,
                expected_action=item["action_type"],
            )
            if receipt["hashes"]["attestation"] != item["attestation_hash"]:
                raise Malformed("timeline attestation hash mismatch")
            if (
                receipt["action"]["subject"]["issuer_role"] not in roles
                or receipt["signature"]["issuer"]
                != roles[receipt["action"]["subject"]["issuer_role"]]
            ):
                raise Malformed("receipt signer does not equal packet-declared role issuer")
            receipts.append(receipt)
        except Malformed as exc:
            receipt_failed = True
            receipt_error_paths.add(item["receipt_path"])
            proof_signature, proof_authority, proof_occurrence, proof_role = (
                proof_failure_dimensions(raw, str(exc))
            )
            signature_failed |= proof_signature
            authority_failed |= proof_authority
            occurrence_failed |= proof_occurrence
            role_failed |= proof_role
            if "authority" in str(exc):
                authority_failed = True
            if "issuer" in str(exc) or "role" in str(exc):
                role_failed = True
            receipts.append(None)
            problem = f"{item['receipt_path']}: {exc}"
            receipt_errors_by_path.setdefault(
                item["receipt_path"], []
            ).append(problem)
            errors.append(problem)

    publish_failed = False
    try:
        publish = validate_receipt(
            files[PUBLISH_FILE],
            label=PUBLISH_FILE,
            context=accepted,
            team_roles=team_roles,
            expected_action="incident.packet.publish",
        )
        publish_subject = publish["action"]["subject"]
        if publish_subject["packet_id"] != core["packet_id"]:
            raise Malformed("publish packet_id mismatch")
        if publish_subject["packet_core_hash"] != hash_json(core):
            raise Malformed("publish packet_core_hash mismatch")
        expected_components = {name: hash_json(core[name]) for name in COMPONENTS}
        if publish_subject["component_hashes"] != expected_components:
            raise Malformed("publish component_hashes mismatch")
        publisher = publish_subject["issuer_role"]
        if publisher not in roles or publish["signature"]["issuer"] != roles[publisher]:
            raise Malformed("publish signer does not equal packet-declared publisher")
    except Malformed as exc:
        publish_failed = True
        receipt_failed = True
        proof_signature, proof_authority, proof_occurrence, proof_role = (
            proof_failure_dimensions(files[PUBLISH_FILE], str(exc))
        )
        signature_failed |= proof_signature
        authority_failed |= proof_authority
        occurrence_failed |= proof_occurrence
        role_failed |= proof_role
        if "authority" in str(exc):
            authority_failed = True
        if "issuer" in str(exc) or "role" in str(exc):
            role_failed = True
        publish = None
        errors.append(f"{PUBLISH_FILE}: {exc}")

    timeline_failed = timeline_byte_failed
    lineage_failed = False
    attestations = {
        receipt["hashes"]["attestation"]: (index, receipt)
        for index, receipt in enumerate(receipts)
        if receipt is not None
    }
    mandates = {
        receipt["hashes"]["attestation"]: (index, receipt)
        for index, receipt in enumerate(receipts)
        if receipt is not None
        and receipt["action"]["type"] == "eval.run.authorize"
        and core["timeline"][index]["receipt_path"]
        not in receipt_error_paths
    }
    for mandate_index, mandate in mandates.values():
        if mandate["action"]["subject"]["role_issuers"] != roles:
            timeline_failed = True
            authority_failed = True
            errors.append(
                "eval.run.authorize role_issuers does not equal packet-core roles"
            )
        if core["timeline"][mandate_index]["receipt_path"] in receipt_error_paths:
            authority_failed = True
    for index, receipt in enumerate(receipts):
        if receipt is None:
            continue
        if core["timeline"][index]["receipt_path"] in receipt_error_paths:
            continue
        action_type = receipt["action"]["type"]
        subject = receipt["action"]["subject"]
        mandate: dict[str, Any] | None = None
        if action_type in {
            "capability.decide", "capability.observe", "trajectory.decide",
            "incident.handoff",
        }:
            resolved = mandates.get(subject["mandate_ref"])
            if resolved is None or resolved[0] >= index:
                timeline_failed = True
                authority_failed = True
                errors.append("mandate_ref does not resolve")
            else:
                mandate_index, mandate = resolved
                if (
                    core["timeline"][mandate_index]["receipt_path"]
                    in receipt_error_paths
                ):
                    authority_failed = True
                    errors.append(
                        "mandate_ref does not resolve to an accepted attestation"
                    )
        if (
            action_type in {"incident.statement", "incident.handoff"}
            and subject["incident_id"] != core["incident_id"]
        ):
            timeline_failed = True
            errors.append("incident_id does not match packet-core")
        if action_type == "capability.observe":
            if (
                mandate is not None
                and subject["run_id"] != mandate["action"]["subject"]["run_id"]
            ):
                authority_failed = True
                errors.append(
                    "capability.observe run_id does not equal the referenced "
                    "mandate run_id"
                )
            parent = attestations.get(subject["decision_attestation"])
            if (
                parent is None
                or parent[0] >= index
                or core["timeline"][parent[0]]["receipt_path"]
                in receipt_error_paths
                or parent[1]["action"]["type"] != "capability.decide"
                or parent[1]["action"]["subject"]["run_id"] != subject["run_id"]
                or parent[1]["action"]["subject"]["request_id"] != subject["request_id"]
                or parent[1]["action"]["subject"]["decision"] != "PERMIT"
                or parent[1]["action"]["subject"]["protocol"] != subject["protocol"]
                or parent[1]["action"]["subject"]["mandate_ref"]
                != subject["mandate_ref"]
            ):
                lineage_failed = True
                if (
                    parent is not None
                    and (
                        parent[1]["action"]["subject"].get("mandate_ref")
                        != subject["mandate_ref"]
                        or core["timeline"][parent[0]]["receipt_path"]
                        in receipt_error_paths
                    )
                ):
                    authority_failed = True
                errors.append("observation decision parent is missing or mismatched")
        elif action_type == "capability.decide" and mandate is not None:
            mandate_subject = mandate["action"]["subject"]
            if subject["run_id"] != mandate_subject["run_id"]:
                authority_failed = True
                errors.append(
                    "capability.decide run_id does not equal the referenced "
                    "mandate run_id"
                )
            if subject["policy_digest"] != mandate_subject["policy_digest"]:
                authority_failed = True
                errors.append(
                    "capability.decide policy_digest does not equal the "
                    "referenced mandate policy_digest"
                )
            claimed = instant(receipt["claimed_at"], "capability.decide.claimed_at")
            valid_from = instant(
                mandate_subject["valid_from"], "eval.run.authorize.valid_from"
            )
            valid_until = instant(
                mandate_subject["valid_until"], "eval.run.authorize.valid_until"
            )
            if not valid_from <= claimed <= valid_until:
                authority_failed = True
                errors.append(
                    "capability.decide claimed_at is outside the mandate's "
                    "claimed validity interval"
                )
        elif action_type == "trajectory.decide":
            expected = [
                prior["hashes"]["attestation"]
                for prior_index, prior in enumerate(receipts[:index])
                if prior is not None
                and prior["action"]["type"]
                in {"capability.decide", "capability.observe"}
                and prior["action"]["subject"]["run_id"] == subject["run_id"]
                and core["timeline"][prior_index]["receipt_path"]
                not in receipt_error_paths
            ]
            invalid_same_run = any(
                prior is not None
                and prior["action"]["type"]
                in {"capability.decide", "capability.observe"}
                and prior["action"]["subject"]["run_id"] == subject["run_id"]
                and core["timeline"][prior_index]["receipt_path"]
                in receipt_error_paths
                for prior_index, prior in enumerate(receipts[:index])
            )
            if subject["ordered_lineage"] != expected:
                lineage_failed = True
                errors.append("trajectory ordered_lineage mismatch")
            if invalid_same_run:
                lineage_failed = True
                errors.append(
                    "trajectory lineage contains a same-run receipt that did "
                    "not pass accepted attestation verification"
                )
            if mandate is not None:
                mandate_subject = mandate["action"]["subject"]
                if subject["run_id"] != mandate_subject["run_id"]:
                    authority_failed = True
                    errors.append(
                        "trajectory.decide run_id does not equal the referenced "
                        "mandate run_id"
                    )
                if subject["policy_digest"] != mandate_subject["policy_digest"]:
                    authority_failed = True
                    errors.append(
                        "trajectory.decide policy_digest does not equal the "
                        "referenced mandate policy_digest"
                    )
        elif action_type == "incident.handoff":
            expected_statements = [
                item["attestation_hash"] for item in core["statements"]
            ]
            statement_indexes = [
                attestations[attestation][0]
                for attestation in expected_statements
                if attestation in attestations
            ]
            if (
                subject["statement_refs"] != expected_statements
                or len(statement_indexes) != len(expected_statements)
                or any(statement_index >= index for statement_index in statement_indexes)
                or any(
                    core["timeline"][statement_index]["receipt_path"]
                    in receipt_error_paths
                    for statement_index in statement_indexes
                )
            ):
                timeline_failed = True
                errors.append(
                    "incident.handoff statement_refs do not equal the ordered "
                    "named prior statement attestations"
                )
            if subject["timeline_hash"] != hash_json(core["timeline"][:index]):
                timeline_failed = True
                errors.append("incident.handoff timeline_hash does not recompute")
            if subject["coverage_hash"] != coverage_commitment(core, artifacts):
                timeline_failed = True
                errors.append("incident.handoff coverage_hash does not recompute")
            prior = [
                item["attestation_hash"] for item in core["timeline"][:index]
            ]
            if subject["parent_refs"] != prior:
                timeline_failed = True
                errors.append(
                    "incident.handoff parent_refs do not equal the exact ordered "
                    "prior timeline attestations"
                )

    projections: dict[
        tuple[str, str, str],
        dict[str, tuple[dict[str, str], str]],
    ] = {}
    duplicate_projections: set[tuple[tuple[str, str, str], str]] = set()
    projection_invalid: dict[
        tuple[str, str, str], list[dict[str, str]]
    ] = {}
    reported_invalid_paths: set[str] = set()
    for index, receipt in enumerate(receipts):
        if receipt is None:
            continue
        item = projection(receipt)
        if item is None:
            continue
        key, projected = item
        path = core["timeline"][index]["receipt_path"]
        if path in receipt_error_paths:
            reported_invalid_paths.add(path)
            projection_invalid.setdefault(key, []).append(
                {
                    "source": path,
                    "reason": "; ".join(receipt_errors_by_path[path]),
                }
            )
            continue
        bucket = projections.setdefault(key, {})
        observation_id = projected["observation_id"]
        if observation_id in bucket:
            previous = bucket.pop(observation_id)
            duplicate_projections.add((key, observation_id))
            projection_invalid.setdefault(key, []).extend(
                [
                    {
                        "source": previous[1],
                        "reason": (
                            "duplicate accepted receipt projection for "
                            f"{observation_id!r}"
                        ),
                    },
                    {
                        "source": path,
                        "reason": (
                            "duplicate accepted receipt projection for "
                            f"{observation_id!r}"
                        ),
                    },
                ]
            )
        elif (key, observation_id) not in duplicate_projections:
            bucket[observation_id] = (projected, path)
        else:
            projection_invalid.setdefault(key, []).append(
                {
                    "source": path,
                    "reason": (
                        "duplicate accepted receipt projection for "
                        f"{observation_id!r}"
                    ),
                }
            )

    for path in receipt_error_paths - reported_invalid_paths:
        try:
            candidate = load_json(files[path], path)
            item = projection(candidate)
        except (Malformed, KeyError, TypeError):
            item = None
        if item is not None:
            key, _ = item
            projection_invalid.setdefault(key, []).append(
                {
                    "source": path,
                    "reason": "; ".join(receipt_errors_by_path[path]),
                }
            )

    denominator_refs: dict[tuple[str, str, str], dict[str, Any]] = {}
    denominator_phase_counts: dict[str, dict[str, int]] = {}
    for item in core["denominators"]:
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id", "provenance",
                "checkpoint_attestation",
            },
            "denominator ref",
        )
        string(item["anchor_id"], "denominator.anchor_id")
        if item["phase"] not in {"decision", "effect"}:
            raise Malformed("denominator.phase is unsupported")
        if item["protocol"] not in {"http", "mcp"}:
            raise Malformed("denominator.protocol is unsupported")
        key = (item["anchor_id"], item["phase"], item["protocol"])
        if key in denominator_refs:
            raise Malformed("duplicate denominator key")
        digest(
            item["checkpoint_attestation"],
            "denominator.checkpoint_attestation",
        )
        denominator_refs[key] = item
        phases = denominator_phase_counts.setdefault(
            item["protocol"], {"decision": 0, "effect": 0}
        )
        phases[item["phase"]] += 1
    if any(
        counts != {"decision": 1, "effect": 1}
        for counts in denominator_phase_counts.values()
    ):
        raise Malformed(
            "each represented protocol must declare exactly one decision "
            "denominator and one effect denominator"
        )
    capability_protocols = {
        receipt["action"]["subject"]["protocol"]
        for receipt in receipts
        if receipt is not None
        and receipt["action"]["type"]
        in {"capability.decide", "capability.observe"}
    }
    denominator_protocols = {
        item["protocol"] for item in denominator_refs.values()
    }
    if capability_protocols != denominator_protocols:
        timeline_failed = True
        errors.append(
            "capability receipt protocols and denominator protocols must match"
        )
    named_statement_hashes = {
        item["attestation_hash"] for item in core["statements"]
    }
    handoff_indexes = [
        index
        for index, receipt in enumerate(receipts)
        if receipt is not None
        and receipt["action"]["type"] == "incident.handoff"
    ]
    invalid_denominator_checkpoints: dict[
        tuple[str, str, str], list[str]
    ] = {}
    for key, item in denominator_refs.items():
        checkpoint_ref = item["checkpoint_attestation"]
        resolved = attestations.get(checkpoint_ref)
        problems: list[str] = []
        if (
            resolved is None
            or checkpoint_ref not in named_statement_hashes
            or resolved[1]["action"]["type"] != "incident.statement"
        ):
            problems.append(
                "checkpoint_attestation must resolve to a named incident.statement"
            )
        else:
            checkpoint_index, checkpoint = resolved
            checkpoint_path = core["timeline"][checkpoint_index][
                "receipt_path"
            ]
            if checkpoint_path in receipt_error_paths:
                problems.append(
                    "denominator checkpoint receipt did not pass accepted "
                    "attestation verification"
                )
            subject = checkpoint["action"]["subject"]
            expected_role = (
                "target"
                if key[1] == "effect"
                else "gateway"
                if key[2] == "http"
                else "boundary"
            )
            if subject["issuer_role"] != expected_role:
                problems.append(
                    "denominator checkpoint issuer_role must match phase topology "
                    f"({expected_role!r})"
                )
            expected_topic = denominator_checkpoint_topic(*key)
            if subject["topic"] != expected_topic:
                problems.append(
                    "denominator checkpoint topic must equal "
                    f"{expected_topic!r}"
                )
            if subject["epistemic_status"] != "observed":
                problems.append(
                    "denominator checkpoint epistemic_status must be 'observed'"
                )
            if subject["claim"] != "ORDERED_DENOMINATOR_SNAPSHOT":
                problems.append(
                    "denominator checkpoint claim must be "
                    "'ORDERED_DENOMINATOR_SNAPSHOT'"
                )
            artifact_hash = artifacts[item["artifact_id"]]["sha256"]
            if subject["evidence_refs"] != [artifact_hash]:
                problems.append(
                    "denominator checkpoint evidence_refs must contain only "
                    "the exact denominator artifact byte SHA"
                )
            carried_evidence = checkpoint["evidence_refs"]
            if (
                not isinstance(carried_evidence, list)
                or len(carried_evidence) != 1
                or not isinstance(carried_evidence[0], dict)
                or carried_evidence[0].get("hash") != artifact_hash
                or carried_evidence[0].get("grounding") != "self_asserted"
            ):
                problems.append(
                    "denominator checkpoint receipt evidence must contain "
                    "exactly one self_asserted reference to the denominator bytes"
                )
            if any(checkpoint_index >= value for value in handoff_indexes):
                problems.append(
                    "denominator checkpoint must occur before every incident.handoff"
                )
        if problems:
            timeline_failed = True
            invalid_denominator_checkpoints[key] = problems
            errors.extend(
                f"denominator checkpoint {key!r}: {problem}"
                for problem in problems
            )
    coverage_refs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in core["coverage"]:
        item = exact(
            item,
            {
                "anchor_id", "phase", "protocol", "artifact_id",
                "minimum_verification_depth",
            },
            "coverage ref",
        )
        key = (item["anchor_id"], item["phase"], item["protocol"])
        if key in coverage_refs:
            raise Malformed("duplicate coverage key")
        coverage_refs[key] = item
    if set(denominator_refs) != set(coverage_refs):
        raise Malformed("denominator and coverage keys differ")
    if not denominator_refs:
        raise Malformed(
            "packet-core must declare at least one denominator and coverage anchor"
        )

    coverage_results: list[dict[str, Any]] = []
    coverage_failed = False
    coverage_not_computed = False
    for key in sorted(denominator_refs):
        denominator_ref = denominator_refs[key]
        coverage_ref = coverage_refs[key]
        provenance = denominator_ref["provenance"]
        base = {
            "anchor_id": key[0], "phase": key[1], "protocol": key[2],
            "provenance": provenance,
        }
        group_invalid = list(projection_invalid.get(key, []))
        if key in invalid_denominator_checkpoints:
            coverage_not_computed = True
            coverage_results.append(
                base
                | {
                    "status": "NOT_COMPUTED", "total": 0, "receipted": 0,
                    "covered_ids": [], "uncovered_ids": [],
                    "phantom_receipt_ids": [],
                    "invalid_receipts": group_invalid,
                    "reasons": invalid_denominator_checkpoints[key],
                }
            )
            continue
        try:
            denominator_artifact = artifacts[denominator_ref["artifact_id"]]
            if denominator_artifact["disclosure_state"] != "released":
                raise Malformed("denominator artifact is unavailable")
            denominator = exact(
                load_json(files[denominator_artifact["path"]], denominator_artifact["path"]),
                {"schema_version", "anchor_id", "phase", "protocol", "observations"},
                "denominator",
            )
            if integer(denominator["schema_version"], "denominator.schema_version", minimum=1) != 1:
                raise Malformed("denominator.schema_version must be 1")
            if (denominator["anchor_id"], denominator["phase"], denominator["protocol"]) != key:
                raise Malformed("denominator identity mismatch")
            observation_rows = objects(denominator["observations"], "denominator.observations")
            if not observation_rows:
                coverage_not_computed = True
                coverage_results.append(
                    base
                    | {
                        "status": "NOT_COMPUTED", "total": 0, "receipted": 0,
                        "covered_ids": [], "uncovered_ids": [],
                        "phantom_receipt_ids": [],
                        "invalid_receipts": group_invalid,
                        "reasons": [
                            "denominator.observations must contain at least one event"
                        ],
                    }
                )
                continue
            observation_ids: set[str] = set()
            observations: list[dict[str, str]] = []
            for observation in observation_rows:
                observation = exact(observation, OBSERVATION_FIELDS, "observation")
                observation_id = string(observation["observation_id"], "observation_id")
                if observation_id in observation_ids:
                    raise Malformed("duplicate denominator observation id")
                observation_ids.add(observation_id)
                if (
                    observation["anchor_id"], observation["phase"], observation["protocol"]
                ) != key:
                    raise Malformed("observation belongs to another coverage group")
                digest(observation["operation_ref"], "observation.operation_ref")
                digest(observation["evidence_hash"], "observation.evidence_hash")
                observations.append(observation)
        except (Malformed, KeyError) as exc:
            coverage_not_computed = True
            coverage_results.append(
                base
                | {
                    "status": "NOT_COMPUTED", "total": 0, "receipted": 0,
                    "covered_ids": [], "uncovered_ids": [],
                    "phantom_receipt_ids": [],
                    "invalid_receipts": group_invalid,
                    "reasons": [str(exc)],
                }
            )
            continue

        by_id = projections.get(key, {})
        invalid = list(projection_invalid.get(key, []))
        covered: list[str] = []
        for row in observations:
            candidate = by_id.get(row["observation_id"])
            if candidate is None:
                continue
            projected, source = candidate
            if projected == row:
                covered.append(row["observation_id"])
            else:
                invalid.append(
                    {
                        "source": source,
                        "reason": (
                            "receipt projection does not equal the "
                            "denominator observation"
                        ),
                    }
                )
        uncovered = [
            row["observation_id"]
            for row in observations
            if row["observation_id"] not in set(covered)
        ]
        phantom = sorted(set(by_id) - observation_ids)
        recomputed = {
            "schema_version": 1,
            "anchor_id": key[0],
            "phase": key[1],
            "protocol": key[2],
            "denominator_sha256": denominator_artifact["sha256"],
            "minimum_verification_depth": "attestation",
            "total": len(observations),
            "receipted": len(covered),
            "covered_ids": covered,
            "uncovered_ids": uncovered,
            "phantom_receipt_ids": phantom,
            "invalid_receipts": invalid,
        }
        coverage_artifact = artifacts[coverage_ref["artifact_id"]]
        if coverage_artifact["disclosure_state"] != "released":
            coverage_not_computed = True
            coverage_results.append(
                base
                | {
                    "status": "NOT_COMPUTED",
                    "total": len(observations),
                    "receipted": len(covered),
                    "covered_ids": covered,
                    "uncovered_ids": uncovered,
                    "phantom_receipt_ids": phantom,
                    "invalid_receipts": invalid,
                    "reasons": ["declared coverage artifact is unavailable"],
                }
            )
            continue
        try:
            declared_coverage = exact(
                load_json(
                    files[coverage_artifact["path"]], coverage_artifact["path"]
                ),
                set(recomputed),
                "coverage report",
            )
            if (
                integer(
                    declared_coverage["schema_version"],
                    "coverage.schema_version",
                    minimum=1,
                )
                != 1
            ):
                raise Malformed("coverage.schema_version must be 1")
            integer(declared_coverage["total"], "coverage.total")
            integer(declared_coverage["receipted"], "coverage.receipted")
            if declared_coverage != recomputed:
                raise Malformed(
                    "declared coverage report does not equal the recomputed report"
                )
            status, reasons = "COMPUTED", []
        except (Malformed, KeyError) as exc:
            coverage_failed = True
            status, reasons = "FAILED", [str(exc)]
            errors.append(f"coverage {key!r}: {exc}")
        coverage_results.append(
            base
            | {
                "status": status,
                "total": len(observations),
                "receipted": len(covered),
                "covered_ids": covered,
                "uncovered_ids": uncovered,
                "phantom_receipt_ids": phantom,
                "invalid_receipts": invalid,
                "reasons": reasons,
            }
        )
        if uncovered:
            warning = (
                "one or more denominator observations have no accepted covering receipt"
            )
            if warning not in warnings:
                warnings.append(warning)
    if any(item["status"] == "COMPUTED" for item in coverage_results):
        warnings.append(
            "denominator checkpoints authenticate what the path-separated "
            "observer reported; they do not establish completeness or "
            "organizational independence"
        )

    statement_receipts = {
        item["attestation_hash"]: (
            None
            if item["receipt_path"] in receipt_error_paths
            else next(
                (
                    receipt for receipt in receipts
                    if receipt is not None
                    and receipt["hashes"]["attestation"]
                    == item["attestation_hash"]
                ),
                None,
            )
        )
        for item in core["statements"]
    }
    redaction_failed = False
    for redaction in core["redactions"]:
        try:
            record_artifact = artifacts[redaction["record_artifact_id"]]
            source_artifact = artifacts[redaction["source_artifact_id"]]
            released_artifact = artifacts[redaction["released_artifact_id"]]
            record = exact(
                load_json(
                    files[record_artifact["path"]],
                    record_artifact["path"],
                ),
                {
                    "schema_version", "redaction_id", "source_sha256",
                    "released_sha256", "tool", "tool_version", "rules_hash",
                    "disclosure_safety",
                },
                "redaction record",
            )
            if (
                integer(
                    record["schema_version"],
                    "redaction record.schema_version",
                    minimum=1,
                )
                != 1
            ):
                raise Malformed("redaction record.schema_version must be 1")
            expected_record = {
                "schema_version": 1,
                "redaction_id": redaction["redaction_id"],
                "source_sha256": source_artifact["sha256"],
                "released_sha256": released_artifact["sha256"],
                "tool": redaction["tool"],
                "tool_version": redaction["tool_version"],
                "rules_hash": redaction["rules_hash"],
                "disclosure_safety": "NOT_COMPUTED",
            }
            if record != expected_record:
                raise Malformed(
                    "redaction record does not equal core and artifact bindings"
                )
            reviewer_ref = redaction["reviewer_statement_ref"]
            if reviewer_ref not in statement_receipts:
                raise Malformed("redaction reviewer statement does not resolve")
            reviewer = statement_receipts[reviewer_ref]
            reviewer_item = next(
                item
                for item in core["statements"]
                if item["attestation_hash"] == reviewer_ref
            )
            if (
                reviewer is None
                or reviewer_item["receipt_path"] in receipt_error_paths
            ):
                raise Malformed(
                    "redaction reviewer statement is not an accepted receipt"
                )
            subject = reviewer["action"]["subject"]
            record_hash = record_artifact["sha256"]
            evidence = reviewer["evidence_refs"]
            if (
                subject["issuer_role"] != "reviewer"
                or subject["topic"] != f"redaction:{redaction['redaction_id']}"
                or subject["epistemic_status"] != "observed"
                or subject["claim"] != "REDACTION_BINDING_REVIEWED"
                or subject["evidence_refs"] != [record_hash]
                or len(evidence) != 1
                or evidence[0].get("hash") != record_hash
                or evidence[0].get("grounding") != "self_asserted"
            ):
                raise Malformed(
                    "redaction reviewer statement must bind only the exact "
                    "redaction record"
                )
        except (Malformed, KeyError) as exc:
            redaction_failed = True
            errors.append(str(exc))

    topics: dict[str, set[tuple[str, str]]] = {}
    for receipt in statement_receipts.values():
        if receipt is None:
            continue
        subject = receipt["action"]["subject"]
        topics.setdefault(subject["topic"], set()).add(
            (subject["claim"], subject["epistemic_status"])
        )
    conflicts = sorted(topic for topic, claims in topics.items() if len(claims) > 1)
    if conflicts:
        warnings.append(
            "conflicting party statements are preserved without adjudication"
        )

    temporal = {
        "claimed_at": [
            receipt["claimed_at"]
            for index, receipt in enumerate(receipts)
            if receipt is not None
            and core["timeline"][index]["receipt_path"]
            not in receipt_error_paths
        ] + ([publish["claimed_at"]] if publish is not None else []),
        "received_at": [],
        "witnessed_at": [],
        "anchored_before": [],
    }
    witness_inclusion = "NOT_COMPUTED" if not core["witnesses"] else "VERIFIED"
    witness_trust = "NOT_COMPUTED" if not core["witnesses"] else "TRUSTED"
    for witness_ref in core["witnesses"]:
        artifact = artifacts[witness_ref["artifact_id"]]
        if artifact["disclosure_state"] != "released":
            witness_inclusion = witness_trust = "UNAVAILABLE"
            continue
        try:
            witness = exact(
                load_json(files[artifact["path"]], artifact["path"]),
                {
                    "schema_version", "witness_id", "root_ref", "received_at",
                    "witnessed_at", "anchored_before",
                    "included_attestation_hashes", "checkpoint_hash", "proof",
                },
                "witness",
            )
            if (
                integer(
                    witness["schema_version"],
                    "witness.schema_version",
                    minimum=1,
                )
                != 1
            ):
                raise Malformed("witness.schema_version must be 1")
            if (
                witness["witness_id"] != witness_ref["witness_id"]
                or witness["root_ref"] != witness_ref["root_ref"]
            ):
                raise Malformed("witness identity does not match packet-core")
            included_values = strings(
                witness["included_attestation_hashes"],
                "witness.included_attestation_hashes",
                unique=True,
            )
            included = set(included_values)
            for value in included:
                digest(value, "witness.included_attestation_hashes[]")
            root_trusted = witness["root_ref"] in trusted_roots
            if not root_trusted:
                witness_trust = "UNTRUSTED"
            received_at = string(witness["received_at"], "witness.received_at")
            witnessed_at = string(witness["witnessed_at"], "witness.witnessed_at")
            received_instant = instant(received_at, "witness.received_at")
            witnessed_instant = instant(witnessed_at, "witness.witnessed_at")
            if received_instant > witnessed_instant:
                raise Malformed("witness.received_at must not follow witnessed_at")
            checkpoint_preimage = {
                "witness_id": witness["witness_id"],
                "root_ref": witness["root_ref"],
                "received_at": received_at,
                "witnessed_at": witnessed_at,
                "included_attestation_hashes": included_values,
            }
            expected_checkpoint = hash_json(checkpoint_preimage)
            if witness["checkpoint_hash"] != expected_checkpoint:
                raise Malformed("witness checkpoint_hash does not recompute")
            proof = exact(witness["proof"], PROOF_FIELDS, "witness proof")
            witness_issuer = proof["issuer"]
            validate_proof(
                proof,
                purpose="witness-checkpoint",
                signed_digest=expected_checkpoint,
                expected_issuer=witness_issuer,
            )
            if witness_issuer != roles.get("witness"):
                raise Malformed(
                    "witness checkpoint issuer does not equal packet-core "
                    "witness role"
                )
            if witness_issuer not in accepted.get("witness", set()):
                raise Malformed(
                    "witness checkpoint issuer is not accepted out of band"
                )
            if not timeline_hashes <= included:
                raise Malformed(
                    "witness does not include every timeline attestation"
                )
            if witness["anchored_before"] is not None:
                raise Malformed(
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
        except Malformed as exc:
            witness_inclusion = "FAILED"
            errors.append(str(exc))

    statement_indexes = {
        item["attestation_hash"]: attestations[item["attestation_hash"]][0]
        for item in core["statements"]
        if item["attestation_hash"] in attestations
        and item["receipt_path"] not in receipt_error_paths
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
            referenced_core = validate_core_components(
                validate_core(load_json(files[artifact["path"]], artifact["path"]))
            )
            released_packet_refs.add(hash_json(referenced_core))
        except Malformed as exc:
            packet_reference_invalid = True
            errors.append(
                f"released packet-core artifact {artifact['artifact_id']!r} "
                f"is not a conforming packet core: {exc}"
            )

    correction_status = "NONE"
    correction_invalid = packet_reference_invalid
    corrections: dict[tuple[str, str], set[str]] = {}
    for item in core["corrections"]:
        resolved = attestations.get(item["attestation_hash"])
        receipt = resolved[1] if resolved is not None else None
        if (
            receipt is None
            or core["timeline"][resolved[0]]["receipt_path"]
            in receipt_error_paths
        ):
            correction_invalid = True
            errors.append("correction receipt does not resolve")
            continue
        subject = receipt["action"]["subject"]
        correction_index = resolved[0]
        kind = subject["supersedes_kind"]
        supersedes = subject["supersedes_ref"]
        replacement = subject["replacement_ref"]
        if kind == "statement":
            supersedes_index = statement_indexes.get(supersedes)
            replacement_index = statement_indexes.get(replacement)
            if supersedes_index is None or supersedes_index >= correction_index:
                correction_invalid = True
                errors.append(
                    "incident.correct statement supersedes_ref must resolve "
                    "to a prior named statement"
                )
            if replacement_index is None or replacement_index >= correction_index:
                correction_invalid = True
                errors.append(
                    "incident.correct statement replacement_ref must resolve "
                    "to a prior named statement"
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
        corrections.setdefault((kind, supersedes), set()).add(replacement)
    correction_status = (
        "FAILED"
        if correction_invalid
        else "FORKED"
        if any(len(values) > 1 for values in corrections.values())
        else "PRESENT"
        if corrections
        else "NONE"
    )
    if correction_status == "FORKED":
        warnings.append(
            "correction fork preserved; verifier does not select latest by actor time"
        )

    coverage_status = (
        "FAILED"
        if coverage_failed
        else "NOT_COMPUTED"
        if coverage_not_computed
        else "COMPUTED"
    )
    hard_content_failure = publish_failed or artifact_failed or receipt_failed
    hard_failure = any(
        (
            hard_content_failure,
            role_failed,
            authority_failed,
            timeline_failed,
            lineage_failed,
            redaction_failed,
            coverage_failed,
            witness_inclusion == "FAILED",
            correction_status == "FAILED",
        )
    )
    suppressed: list[str] = []
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
    return {
        "profile": PROFILE,
        "packet_integrity": "FAILED" if publish_failed else "VERIFIED",
        "artifact_integrity": "FAILED" if artifact_failed else "VERIFIED",
        "receipt_integrity": "FAILED" if receipt_failed else "VERIFIED",
        "signature_status": "FAILED" if signature_failed else "VERIFIED",
        "issuer_authenticity": "FAILED" if role_failed else "VERIFIED",
        "authority_binding": "FAILED" if authority_failed else "VERIFIED",
        "occurrence_binding": "FAILED" if occurrence_failed else "VERIFIED",
        "timeline_integrity": "FAILED" if timeline_failed else "VERIFIED",
        "lineage_integrity": "FAILED" if lineage_failed else "VERIFIED",
        "trace_integrity": (
            "FAILED"
            if trace_failed
            else "VERIFIED"
            if any(
                item["role"] == "trace"
                and item["disclosure_state"] == "released"
                for item in core["artifacts"]
            )
            else "NOT_COMPUTED"
        ),
        "redaction_binding": "FAILED" if redaction_failed else "VERIFIED",
        "coverage_status": coverage_status,
        "coverage": coverage_results,
        "denominator_provenance": (
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
        "witness_inclusion": witness_inclusion,
        "witness_root_trust": witness_trust,
        "temporal_evidence": temporal,
        "party_conflicts": conflicts,
        "correction_status": correction_status,
        "unavailable_artifacts": unavailable,
        "suppressed_conclusions": suppressed,
        "disclosure_safety": "NOT_COMPUTED",
        "reliance": "NOT_COMPUTED",
        "errors": errors,
        "warnings": warnings,
        "exit_code": 1 if hard_failure else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.packet, args.context)
    except (Malformed, OSError, KeyError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "profile": PROFILE,
                    "malformed": True,
                    "error": str(exc),
                    "exit_code": 2,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
