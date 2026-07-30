#!/usr/bin/env python3
"""Check release slots against release receipts and fail on omission.

A slot still open past its close deadline with no closing receipt is objective
evidence of omission — the release happened (or was committed to) and the
promised receipt did not follow. This check is wired into CI so the omission is
machine-visible rather than a quiet gap.

    python scripts/check_release_slots.py \
        --slots-dir releases/slots --receipts-dir releases \
        --context releases/release-trust-context.json

Exit codes: 0 all slots closed or still open within deadline; 1 any OMISSION;
2 any slot record fails verification (tampered or malformed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_slot import classify_slot, load_json, verify_slot  # noqa: E402
from release_slot import HISTORICAL_SCHEMA_VERSION  # noqa: E402


VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CONTEXT_FIELDS = {
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


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("invalid release version")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def issuer_record_for_version(context: dict, version: str) -> dict:
    if (
        set(context) != CONTEXT_FIELDS
        or context.get("schema_version") != "bulla.release-trust-context/0.2"
        or context.get("package") != "bulla"
        or context.get("expected_publisher") != "github:jkomkov/bulla"
        or context.get("proof_type") != "bulla/ed25519-2026"
        or not isinstance(context.get("issuers"), list)
    ):
        raise ValueError("release trust context is invalid")
    target = _version(version)
    intervals = []
    seen_ids = set()
    for record in context["issuers"]:
        if not isinstance(record, dict) or set(record) != ISSUER_FIELDS:
            raise ValueError("release issuer record is invalid")
        issuer_id = record["id"]
        issuer = record["issuer"]
        status = record["status"]
        if (
            not isinstance(issuer_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", issuer_id)
            or issuer_id in seen_ids
            or not isinstance(issuer, str)
            or not issuer.startswith("did:key:z")
            or status not in {"active", "retired", "revoked"}
        ):
            raise ValueError("release issuer identity is invalid")
        seen_ids.add(issuer_id)
        start = _version(record["valid_from"])
        retired = record["retired_after"]
        revoked = record["revoked_from"]
        if status == "active":
            if retired is not None or revoked is not None:
                raise ValueError("active release issuer has an end marker")
            end = None
        elif status == "retired":
            if revoked is not None:
                raise ValueError("retired release issuer has a revocation marker")
            retired_tuple = _version(retired)
            if retired_tuple < start:
                raise ValueError("release issuer retires before validity")
            end = (retired_tuple[0], retired_tuple[1], retired_tuple[2] + 1)
        else:
            if retired is not None:
                raise ValueError("revoked release issuer has a retirement marker")
            end = _version(revoked)
            if end <= start:
                raise ValueError("release issuer revocation has no valid interval")
        intervals.append((start, end, issuer_id))
    for index, (left_start, left_end, _) in enumerate(intervals):
        for right_start, right_end, _ in intervals[index + 1 :]:
            if (
                (right_end is None or left_start < right_end)
                and (left_end is None or right_start < left_end)
            ):
                raise ValueError("release issuer validity overlaps")
    matches = []
    for record in context["issuers"]:
        if not isinstance(record, dict) or set(record) != ISSUER_FIELDS:
            raise ValueError("release issuer record is invalid")
        start = _version(record["valid_from"])
        retired = record["retired_after"]
        revoked = record["revoked_from"]
        if target < start:
            continue
        if retired is not None and target > _version(retired):
            continue
        if revoked is not None and target >= _version(revoked):
            continue
        matches.append(record)
    if len(matches) != 1:
        raise ValueError("release version does not select one issuer")
    record = matches[0]
    identity = {
        "context_schema": context["schema_version"],
        "package": context["package"],
        "proof_type": context["proof_type"],
        "expected_publisher": context["expected_publisher"],
        "id": record["id"],
        "issuer": record["issuer"],
        "valid_from": record["valid_from"],
    }
    return {
        **record,
        "record_hash": "sha256:"
        + hashlib.sha256(_canonical(identity).encode()).hexdigest(),
        "trust_context_schema": context["schema_version"],
        "package": context["package"],
        "proof_type": context["proof_type"],
        "expected_publisher": context["expected_publisher"],
    }


def validate_release_inventory(
    releases: list[dict],
    pypi: dict,
    slot_refs: set[str],
    *,
    epoch: str,
) -> dict[str, list[str]]:
    """Reconcile protected slot tags, PyPI versions, and immutable releases."""
    epoch_tuple = _version(epoch)
    public = {
        item.get("tag_name"): item
        for item in releases
        if isinstance(item, dict)
        and item.get("draft") is False
        and isinstance(item.get("tag_name"), str)
    }
    normalized_slot_refs = {
        tag
        for tag in slot_refs
        if re.fullmatch(r"release-slot-v[0-9]+\.[0-9]+\.[0-9]+", tag)
    }
    pypi_versions = {
        version
        for version, files in (pypi.get("releases") or {}).items()
        if VERSION.fullmatch(version)
        and _version(version) >= epoch_tuple
        and isinstance(files, list)
        and files
    }
    required_slot_tags = normalized_slot_refs | {
        f"release-slot-v{version}" for version in pypi_versions
    }
    missing_refs = sorted(required_slot_tags - normalized_slot_refs)
    if missing_refs:
        raise ValueError(
            f"PyPI releases lack protected release-slot tags: {missing_refs}"
        )
    missing_releases = sorted(required_slot_tags - set(public))
    if missing_releases:
        raise ValueError(
            f"protected release-slot tags lack public releases: {missing_releases}"
        )
    required_immutable = required_slot_tags | {
        f"v{version}" for version in pypi_versions
    }
    mutable = sorted(
        tag
        for tag in required_immutable
        if (public.get(tag) or {}).get("immutable") is not True
    )
    if mutable:
        raise ValueError(f"required releases are not immutable: {mutable}")
    return {
        "slot_tags": sorted(required_slot_tags),
        "public_tags": sorted(public),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slots-dir", type=Path, required=True)
    ap.add_argument("--receipts-dir", type=Path, required=True)
    ap.add_argument("--context", type=Path, required=True)
    ap.add_argument("--enforcement-epoch", default="0.44.2")
    ap.add_argument(
        "--git-repository",
        type=Path,
        help="recompute each slot's committed Git tree before classifying closure",
    )
    ap.add_argument("--now", default=None,
                    help="ISO timestamp override for deterministic tests")
    args = ap.parse_args()
    context = load_json(args.context)
    if set(context) != CONTEXT_FIELDS:
        print("release trust context is invalid", file=sys.stderr)
        return 2

    now = (
        datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if args.now
        else datetime.now(timezone.utc)
    )

    if not args.slots_dir.exists():
        print("no slots directory; nothing to check")
        return 0

    receipts = []
    for path in sorted(args.receipts_dir.glob("*.json")):
        try:
            receipts.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue

    exit_code = 0
    checked = 0
    for path in sorted(args.slots_dir.glob("*.slot.json")):
        slot = load_json(path)
        try:
            if (
                slot.get("schema_version") == HISTORICAL_SCHEMA_VERSION
                and _version(slot.get("version")) >= _version(args.enforcement_epoch)
            ):
                raise ValueError("historical slot schema after enforcement epoch")
        except (TypeError, ValueError):
            print(
                f"SLOT INVALID {path.name}: historical slot schema is not allowed",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
            continue
        try:
            issuer_record = issuer_record_for_version(context, slot.get("version"))
        except (TypeError, ValueError):
            print(
                f"SLOT INVALID {path.name}: release trust context is invalid",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
            continue
        ok, reason = verify_slot(slot, issuer_record=issuer_record)
        if not ok:
            print(f"SLOT INVALID {path.name}: {reason}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue
        source_tree_sha256 = None
        if args.git_repository is not None:
            try:
                tree = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(args.git_repository),
                        "rev-parse",
                        f"{slot['source_commit']}^{{tree}}",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout.strip()
                payload = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(args.git_repository),
                        "cat-file",
                        "tree",
                        tree.decode("ascii"),
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                source_tree_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
            except (KeyError, UnicodeDecodeError, subprocess.CalledProcessError):
                print(
                    f"SLOT INVALID {path.name}: committed source tree is unavailable",
                    file=sys.stderr,
                )
                exit_code = max(exit_code, 2)
                continue
            if source_tree_sha256 != slot.get("source_tree_sha256"):
                print(
                    f"SLOT INVALID {path.name}: committed source tree differs",
                    file=sys.stderr,
                )
                exit_code = max(exit_code, 2)
                continue
        result = classify_slot(
            slot,
            receipts,
            now=now,
            source_tree_sha256=source_tree_sha256,
        )
        checked += 1
        if result["state"] == "OMISSION":
            print(
                f"OMISSION {result['version']}: {result['reason']} "
                f"(deadline {result['close_deadline']}, slot {result['slot_hash'][:23]}…)",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 1)
        else:
            print(f"{result['state']} {result['version']} slot {result['slot_hash'][:23]}…")

    print(f"release slots checked: {checked}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
