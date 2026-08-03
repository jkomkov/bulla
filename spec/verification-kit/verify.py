#!/usr/bin/env python3
"""Verify the extracted kit and reproduce its receipt-vector verdicts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
CONTROL = {"MANIFEST.json", "MANIFEST.sha256"}


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_relative(name: str) -> bool:
    pure = PurePosixPath(name)
    windows = PureWindowsPath(name)
    return (
        bool(name)
        and "\\" not in name
        and not pure.is_absolute()
        and not windows.drive
        and not windows.root
        and not windows.is_absolute()
        and pure.as_posix() == name
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def verify_manifest() -> int:
    try:
        manifest_bytes = (ROOT / "MANIFEST.json").read_bytes()
        digest_line = (ROOT / "MANIFEST.sha256").read_text(encoding="ascii")
        claimed, marker = digest_line.rstrip("\n").split("  ", 1)
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"manifest controls are malformed: {exc}")
    if marker != "MANIFEST.json" or claimed != digest(manifest_bytes):
        return fail("MANIFEST.sha256 does not authenticate MANIFEST.json")
    if set(manifest) != {"format", "authenticates", "members"}:
        return fail("manifest top-level shape differs")
    if manifest["format"] != "bulla.action-receipt-v0.2-verification-kit/1":
        return fail("manifest format differs")
    if manifest["authenticates"] != "payload-members-only":
        return fail("manifest authenticity boundary differs")
    rows = manifest["members"]
    if not isinstance(rows, list):
        return fail("manifest members must be a list")
    expected_paths = []
    folded = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "role"}:
            return fail("manifest member shape differs")
        name = row["path"]
        if not isinstance(name, str) or not safe_relative(name) or name in CONTROL:
            return fail(f"unsafe manifest path: {name!r}")
        if name.casefold() in folded:
            return fail(f"case-folding path collision: {name}")
        folded.add(name.casefold())
        expected_paths.append(name)
        path = ROOT / name
        try:
            mode = path.lstat().st_mode
            payload = path.read_bytes()
        except OSError as exc:
            return fail(f"missing payload {name}: {exc}")
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            return fail(f"payload is not one regular file: {name}")
        if row["bytes"] != len(payload) or row["sha256"] != digest(payload):
            return fail(f"payload digest or length differs: {name}")
        if not isinstance(row["role"], str) or not row["role"]:
            return fail(f"payload role is absent: {name}")
    if expected_paths != sorted(expected_paths) or len(expected_paths) != len(set(expected_paths)):
        return fail("manifest paths are not unique and lexicographically ordered")
    actual = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        actual.append(path.relative_to(ROOT).as_posix())
    if sorted(actual) != sorted(expected_paths + list(CONTROL)):
        return fail("extracted member set differs from the manifest")
    print(f"OK: manifest authenticates {len(rows)} payload members")
    return 0


def verify_claims_file() -> int:
    try:
        expected = json.loads(
            (ROOT / "claims-file" / "expected-local-verdict.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = json.loads(
            (ROOT / "claims-file" / expected["receipt"]).read_text(encoding="utf-8")
        )
        module_spec = importlib.util.spec_from_file_location(
            "retained_action_receipt_checker", ROOT / "vectors" / "independent_check.py"
        )
        if module_spec is None or module_spec.loader is None:
            return fail("independent checker could not be loaded")
        checker = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(checker)
    except (KeyError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"constructed claims file is malformed: {exc}")

    verdict = checker.verify_action_receipt(receipt)
    limit_status = verdict.get("bounds_conformance")
    if limit_status == "not_applicable":
        limit_status = (verdict.get("conventions") or {}).get(
            "payment-within-declared-limit"
        )
    local_checks = {
        "record_integrity": "VERIFIED" if verdict.get("ok") else "FAILED",
        "declared_bounds": str(limit_status or "NOT_EVALUATED").upper(),
        "evidence_grounding": str(
            verdict.get("effective_grounding") or "NOT_EVALUATED"
        ).upper(),
        "authority": "UNAUTHENTICATED" if receipt.get("signature") is None else "NOT_COMPUTED",
        "reliance": "NOT_COMPUTED",
    }
    if expected.get("fixture") != "CONSTRUCTED":
        return fail("claims-file fixture is not labeled CONSTRUCTED")
    if local_checks != expected.get("local_checks"):
        return fail(
            "claims-file dimensional verdict differs: "
            f"computed {local_checks!r}, expected {expected.get('local_checks')!r}"
        )
    boundaries = expected.get("not_established_by_this_check")
    if not isinstance(boundaries, list) or not boundaries or not all(
        isinstance(item, str) and item for item in boundaries
    ):
        return fail("claims-file interpretation boundary is malformed")

    subject = (receipt.get("action") or {}).get("subject") or {}
    print("\nCONSTRUCTED CLAIMS FILE")
    print(subject.get("payment_id", "(payment id missing)"))
    print("\nLOCAL CHECKS")
    for key in (
        "record_integrity", "declared_bounds", "evidence_grounding", "authority", "reliance"
    ):
        print(f"{key.replace('_', ' '):22s} {local_checks[key]}")
    print("\nINTERPRETATION BOUNDARY")
    print("NOT ESTABLISHED BY THIS CHECK")
    for item in boundaries:
        print(f"- {item}")
    print("\nNo issuer connection was used. A payment-rail record is required to check whether funds moved.")
    return 0


def main() -> int:
    if verify_manifest() != 0:
        return 1
    result = subprocess.run(
        [sys.executable, "vectors/independent_check.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return fail("included receipt-vector reproduction failed")
    for line in result.stdout.splitlines():
        if "occurrence-binding boundary" in line or line.startswith("OK:"):
            print(line)
    return verify_claims_file()


if __name__ == "__main__":
    raise SystemExit(main())
