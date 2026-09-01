#!/usr/bin/env python3
"""Pre-publication release slots: the commitment half of the release invariant.

``publish.yml`` mints the release receipt only AFTER PyPI accepts the upload,
so the immutable publication and its receipt are not atomic. A release slot
closes that gap the way ``spec/commitment-slot-v0.1-draft.md`` closes it for
acts generally: open a signed commitment BEFORE the irreversible step, close it
with the post-publication receipt, and treat a slot still open past its
deadline as objective evidence of omission.

A v0.3 slot record is a small canonical JSON document:

  {schema_version, kind, package, version, source_commit, source_tree_sha256,
   preflight_run_id, preflight_manifest_sha256, preflight_wheel_sha256,
   preflight_sdist_sha256,
   release_issuer_id, release_issuer_record_hash, trust_context_schema,
   expected_publisher, opened_at, close_deadline, slot_hash, proof}

``slot_hash`` is the canonical hash of everything above it; ``proof`` is an
ed25519 domain-separated signature (purpose ``release-slot``) over the hash.
The closing link is ``producer.slot_ref`` on the release receipt: a receipt
closes a slot when ``slot_ref.slot_hash`` equals the slot's hash and the
receipt verifies for the same version.

This module is release tooling, not package API. It imports bulla only for
canonical hashing and the ed25519 identity primitives.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bulla._canonical import canonical_json
from bulla.action_receipt import verify_receipt
from bulla.identity import LocalEd25519Signer, verify_proof_domain

HISTORICAL_SCHEMA_VERSION = "release-slot/0.1"
PREVIOUS_SCHEMA_VERSION = "release-slot/0.2"
SCHEMA_VERSION = "release-slot/0.3"
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION
KIND = "bulla.release-slot"
PROOF_PURPOSE = "release-slot"
DEFAULT_DEADLINE_HOURS = 24


def _canon_hash(obj: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_slot(
    *,
    version: str,
    source_commit: str,
    source_tree_sha256: str,
    signer: LocalEd25519Signer,
    issuer_record: dict,
    preflight_run_id: int,
    preflight_manifest_sha256: str,
    preflight_wheel_sha256: str,
    preflight_sdist_sha256: str,
    opened_at: str | None = None,
) -> dict:
    """Build and sign a pre-publication release slot record."""
    opened = opened_at or _now().isoformat().replace("+00:00", "Z")
    deadline = (
        (_parse_ts(opened) + timedelta(hours=DEFAULT_DEADLINE_HOURS))
        .isoformat()
        .replace("+00:00", "Z")
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "package": issuer_record["package"],
        "version": version,
        "source_commit": source_commit,
        "source_tree_sha256": source_tree_sha256,
        "preflight_run_id": preflight_run_id,
        "preflight_manifest_sha256": preflight_manifest_sha256,
        "preflight_wheel_sha256": preflight_wheel_sha256,
        "preflight_sdist_sha256": preflight_sdist_sha256,
        "release_issuer_id": issuer_record["id"],
        "release_issuer_record_hash": issuer_record["record_hash"],
        "trust_context_schema": issuer_record["trust_context_schema"],
        "expected_publisher": issuer_record["expected_publisher"],
        "opened_at": opened,
        "close_deadline": deadline,
    }
    slot_hash = _canon_hash(unsigned)
    return {
        **unsigned,
        "slot_hash": slot_hash,
        "proof": signer.sign_domain(PROOF_PURPOSE, slot_hash),
    }


def verify_slot(
    slot: dict,
    *,
    accepted_issuer: str | None = None,
    issuer_record: dict | None = None,
) -> tuple[bool, str]:
    """Structural + hash + signature verification of a slot record."""
    required = (
        "schema_version",
        "kind",
        "package",
        "version",
        "source_commit",
        "source_tree_sha256",
        "expected_publisher",
        "opened_at",
        "close_deadline",
        "slot_hash",
        "proof",
    )
    for field in required:
        if field not in slot:
            return False, f"missing field {field}"
    schema = slot["schema_version"]
    if (
        schema
        not in {
            HISTORICAL_SCHEMA_VERSION,
            PREVIOUS_SCHEMA_VERSION,
            CURRENT_SCHEMA_VERSION,
        }
        or slot["kind"] != KIND
    ):
        return False, "unexpected schema_version or kind"
    if schema in {PREVIOUS_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION}:
        for field in (
            "source_tree_sha256",
            "release_issuer_id",
            "release_issuer_record_hash",
            "trust_context_schema",
        ):
            if field not in slot:
                return False, f"missing field {field}"
        if issuer_record is None:
            return False, "current lineage slot requires an external issuer record"
        if (
            slot["release_issuer_id"] != issuer_record.get("id")
            or slot["release_issuer_record_hash"] != issuer_record.get("record_hash")
            or slot["trust_context_schema"]
            != issuer_record.get("trust_context_schema")
            or slot["package"] != issuer_record.get("package")
            or slot["expected_publisher"]
            != issuer_record.get("expected_publisher")
            or issuer_record.get("proof_type") != "bulla/ed25519-2026"
        ):
            return False, "slot issuer record differs from the external context"
    if schema == CURRENT_SCHEMA_VERSION:
        digest_fields = (
            "preflight_manifest_sha256",
            "preflight_wheel_sha256",
            "preflight_sdist_sha256",
        )
        if (
            isinstance(slot.get("preflight_run_id"), bool)
            or not isinstance(slot.get("preflight_run_id"), int)
            or slot["preflight_run_id"] <= 0
            or any(
                not isinstance(slot.get(field), str)
                or len(slot[field]) != 71
                or not slot[field].startswith("sha256:")
                or any(character not in "0123456789abcdef" for character in slot[field][7:])
                for field in digest_fields
            )
        ):
            return False, "v0.3 slot preflight binding is invalid"
    if issuer_record is not None:
        accepted_issuer = issuer_record.get("issuer")
    try:
        opened = _parse_ts(slot["opened_at"])
        deadline = _parse_ts(slot["close_deadline"])
    except (AttributeError, TypeError, ValueError):
        return False, "slot timestamps are invalid"
    if opened.tzinfo is None or deadline.tzinfo is None or deadline <= opened:
        return False, "slot timestamps are not ordered timezone-aware values"
    if (
        schema in {PREVIOUS_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION}
        and deadline - opened != timedelta(hours=DEFAULT_DEADLINE_HOURS)
    ):
        return False, "current release slot deadline is not exactly 24 hours"
    unsigned = {k: v for k, v in slot.items() if k not in ("slot_hash", "proof")}
    if _canon_hash(unsigned) != slot["slot_hash"]:
        return False, "slot_hash does not match canonical content"
    verdict = verify_proof_domain(PROOF_PURPOSE, slot["slot_hash"], slot["proof"])
    if not verdict.authentic:
        return False, f"proof rejected: {verdict.detail or verdict.method}"
    if accepted_issuer is not None and slot["proof"].get("issuer") != accepted_issuer:
        return False, "proof issuer is not accepted by the external context"
    return True, "ok"


def receipt_closes_slot(
    receipt_doc: dict,
    slot: dict,
    *,
    source_tree_sha256: str | None = None,
) -> bool:
    """A release receipt closes a slot only when signed content binds the hash.

    ``producer.slot_ref`` remains readable provenance, but producer metadata is
    not part of the v0.2 content hash. The action subject carries the same slot
    hash and is the cryptographic closing link.
    """
    producer = receipt_doc.get("producer") or {}
    slot_ref = producer.get("slot_ref") or {}
    action = receipt_doc.get("action") or {}
    subject = action.get("subject") or {}
    anchor = receipt_doc.get("anchor_ref") or {}
    root_of_trust = anchor.get("root_of_trust") or {}
    evidence = receipt_doc.get("evidence_refs") or []
    tree_evidence = [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("name") == "tree"
    ]
    expected = slot.get("slot_hash")
    try:
        verification = verify_receipt(receipt_doc)
        signed_at = _parse_ts(subject.get("release_signed_at", ""))
        opened_at = _parse_ts(slot.get("opened_at", ""))
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        verification.ok
        and verification.verified_to == "attestation"
        and action.get("type") == "package.release"
        and subject.get("package") == slot.get("package")
        and subject.get("version") == slot.get("version")
        and subject.get("git_commit") == slot.get("source_commit")
        and subject.get("git_tag") == f"v{slot.get('version')}"
        and signed_at.tzinfo is not None
        and opened_at.tzinfo is not None
        and signed_at >= opened_at
        and receipt_doc.get("timestamp") == subject.get("release_signed_at")
        and len(tree_evidence) == 1
        and tree_evidence[0].get("hash") == slot.get("source_tree_sha256")
        and (
            source_tree_sha256 is None
            or slot.get("source_tree_sha256") == source_tree_sha256
        )
        and slot_ref.get("slot_hash") == expected
        and subject.get("release_slot_hash") == expected
        and anchor.get("kind") == "pypi"
        and anchor.get("ref")
        == f"{slot.get('package')} {slot.get('version')}"
        and root_of_trust.get("publisher") == slot.get("expected_publisher")
        and (receipt_doc.get("signature") or {}).get("issuer")
        == (slot.get("proof") or {}).get("issuer")
        and signed_at.tzinfo is not None
    )


def classify_slot(
    slot: dict,
    receipts: list[dict],
    *,
    now: datetime | None = None,
    source_tree_sha256: str | None = None,
) -> dict:
    """OPEN / CLOSED / CLOSED_LATE / OMISSION for one slot against release receipts."""
    moment = now or _now()
    deadline = _parse_ts(slot["close_deadline"])
    closing = next(
        (
            doc
            for doc in receipts
            if receipt_closes_slot(
                doc,
                slot,
                source_tree_sha256=source_tree_sha256,
            )
        ),
        None,
    )
    if closing is not None:
        signed_at = closing["action"]["subject"]["release_signed_at"]
        closed_late = _parse_ts(signed_at) > deadline
        return {
            "version": slot["version"],
            "state": "CLOSED_LATE" if closed_late else "CLOSED",
            "slot_hash": slot["slot_hash"],
        }
    if moment <= deadline:
        return {
            "version": slot["version"],
            "state": "OPEN",
            "slot_hash": slot["slot_hash"],
            "close_deadline": slot["close_deadline"],
        }
    return {
        "version": slot["version"],
        "state": "OMISSION",
        "slot_hash": slot["slot_hash"],
        "close_deadline": slot["close_deadline"],
        "reason": "slot open past its close deadline with no closing release receipt",
    }


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())
