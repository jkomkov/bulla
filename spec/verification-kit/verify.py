#!/usr/bin/env python3
"""Verify the extracted kit and reproduce its receipt-vector verdicts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import subprocess
import sys


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
CONTROL = {"MANIFEST.json", "MANIFEST.sha256"}
MAX_RECEIPT_BYTES = 1_048_576
MAX_RECEIPT_DEPTH = 32
MAX_RECEIPT_NODES = 50_000
MAX_STRING_BYTES = 262_144


class ReceiptInputError(ValueError):
    """The supplied receipt cannot safely reach semantic verification."""


def _reject_constant(value: str):
    raise ReceiptInputError(f"non-finite JSON number {value!r}")


def _pairs_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ReceiptInputError(f"duplicate JSON member {key!r}")
        value[key] = item
    return value


def _check_string(value: str) -> int:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ReceiptInputError("lone Unicode surrogates are forbidden")
    size = len(value.encode("utf-8"))
    if size > MAX_STRING_BYTES:
        raise ReceiptInputError(f"JSON string exceeds {MAX_STRING_BYTES} UTF-8 bytes")
    return size


def strict_receipt_json(raw: bytes) -> dict:
    """Parse one v0.2 receipt without erasing hostile byte-level structure."""
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ReceiptInputError(f"receipt exceeds {MAX_RECEIPT_BYTES} bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReceiptInputError("receipt is not valid UTF-8") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except ReceiptInputError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ReceiptInputError(f"invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReceiptInputError("receipt must be a JSON object")

    nodes = 0
    stack = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_RECEIPT_NODES:
            raise ReceiptInputError(f"receipt exceeds {MAX_RECEIPT_NODES} JSON nodes")
        if depth > MAX_RECEIPT_DEPTH:
            raise ReceiptInputError(f"receipt exceeds JSON depth {MAX_RECEIPT_DEPTH}")
        if isinstance(value, dict):
            for key, item in value.items():
                _check_string(key)
                stack.append((item, depth + 1))
        elif isinstance(value, list):
            for item in value:
                stack.append((item, depth + 1))
        elif isinstance(value, str):
            _check_string(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ReceiptInputError("non-finite JSON numbers are forbidden")
    if document.get("kind") != "action_receipt":
        raise ReceiptInputError("expected kind 'action_receipt'")
    if document.get("schema_version") != "0.2":
        raise ReceiptInputError("retained-kit verification supports ActionReceipt v0.2 only")
    return document


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise ReceiptInputError(f"{label} cannot be read: {exc}") from exc
    if len(payload) > limit:
        raise ReceiptInputError(f"{label} exceeds {limit} bytes")
    return payload


def _load_public_key(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        document = json.loads(_read_bounded(path, 65_536, "public-key file").decode("utf-8"))
        encoded = document["public_key_b64"]
        public_key = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ReceiptInputError(f"public-key file is malformed: {exc}") from exc
    if len(public_key) != 32:
        raise ReceiptInputError("public_key_b64 must decode to 32 bytes")
    return public_key


def _install_network_guard() -> None:
    """Deny network and process escape before the independent checker loads."""
    denied = (
        "socket.",
        "http.client.",
        "urllib.",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
    )

    def guard(event, _args):
        if event.startswith(denied):
            raise RuntimeError(f"network guard denied audit event {event}")

    sys.addaudithook(guard)


def _load_checker():
    module_spec = importlib.util.spec_from_file_location(
        "retained_action_receipt_checker", ROOT / "vectors" / "independent_check.py"
    )
    if module_spec is None or module_spec.loader is None:
        raise ReceiptInputError("independent checker could not be loaded")
    checker = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(checker)
    return checker


def _dimension(claim, availability, result, basis, required_input=None):
    row = {
        "claim": claim,
        "availability": availability,
        "result": result,
        "basis": basis,
    }
    if required_input is not None:
        row["required_input"] = required_input
    return row


def _bounds_result(receipt: dict, verdict: dict) -> tuple[str, list[str]]:
    if not verdict.get("ok"):
        return "NOT_EVALUATED", ["content-dependent results are suppressed after integrity failure"]
    statuses = list((verdict.get("conventions") or {}).values())
    bounds = str(verdict.get("bounds_conformance") or "not_applicable")
    if "violates" in statuses or bounds == "violates":
        result = "VIOLATES"
    elif "pinned" in statuses:
        result = "PINNED"
    elif "conforms" in statuses or bounds == "conforms":
        result = "CONFORMS"
    elif bounds == "not_checkable":
        result = "NOT_CHECKABLE"
    else:
        result = "NOT_APPLICABLE"
    basis = [f"bounds_conformance={bounds}"]
    basis.extend(
        f"convention {name}={status}"
        for name, status in sorted((verdict.get("conventions") or {}).items())
    )
    return result, basis


def receipt_report(receipt: dict, raw: bytes, checker, public_key: bytes | None) -> dict:
    digest_verdict = checker.verify_action_receipt(receipt)
    identity = checker.verify_identity_rung(receipt, public_key=public_key)
    integrity = "VERIFIED" if digest_verdict.get("ok") else "FAILED"
    bounds_result, bounds_basis = _bounds_result(receipt, digest_verdict)

    evidence = receipt.get("evidence_refs") or []
    grounding = digest_verdict.get("effective_grounding")
    grounding_result = str(grounding).upper() if grounding else "NO_EVIDENCE_DECLARED"

    signature = receipt.get("signature")
    signature_result = identity.get("signature_authentic") if identity.get("available") else None
    if not signature:
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "UNSIGNED",
            ["the retained receipt carries no content signature"],
            "a signed receipt and the issuer's verification key",
        )
    elif not identity.get("available"):
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "SKIPPED",
            ["the optional ed25519 library is unavailable"],
            "PyNaCl and a resolvable issuer key",
        )
    elif signature_result == "unresolved":
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "UNRESOLVED",
            ["the signature names a non-self-certifying issuer"],
            "a retained public key supplied with --key",
        )
    else:
        authenticity = _dimension(
            "issuer_authenticity", "RECHECKABLE",
            "VERIFIED" if signature_result is True else "FAILED",
            ["ed25519 verification over the retained content hash"],
        )

    mandate = receipt.get("mandate") or {}
    authority = mandate.get("authority")
    authority_basis = ["authority is a declaration retained inside the receipt envelope"]
    if isinstance(authority, dict) and authority:
        authority_result = "DECLARED"
        for name in ("principal", "policy"):
            if authority.get(name):
                authority_basis.append(f"{name}={authority[name]}")
    else:
        authority_result = "ABSENT"

    remedy = receipt.get("remedy") or {}
    forum = remedy.get("forum") if isinstance(remedy.get("forum"), dict) else {}
    remedies = remedy.get("remedies") if isinstance(remedy.get("remedies"), list) else []
    recourse_named = bool(forum or remedies)

    dimensions = [
        _dimension(
            "record_integrity", "RECHECKABLE", integrity,
            ["four canonical hashes and the v0.2 modality law were recomputed"]
            + list(digest_verdict.get("reasons") or []),
        ),
        _dimension(
            "declared_bounds_or_convention_conformance",
            "RECHECKABLE" if digest_verdict.get("ok") else "UNDETERMINED",
            bounds_result,
            bounds_basis,
            None if digest_verdict.get("ok") else "an integrity-valid receipt",
        ),
        _dimension(
            "evidence_grounding_declaration",
            "RECHECKABLE" if digest_verdict.get("ok") else "UNDETERMINED",
            grounding_result if digest_verdict.get("ok") else "NOT_EVALUATED",
            [f"{len(evidence)} evidence reference(s) are carried by the receipt"],
            None if digest_verdict.get("ok") else "an integrity-valid receipt",
        ),
        authenticity,
        _dimension(
            "declared_authority", "RECHECKABLE" if digest_verdict.get("ok") else "UNDETERMINED",
            authority_result if digest_verdict.get("ok") else "NOT_EVALUATED",
            authority_basis,
            None if digest_verdict.get("ok") else "an integrity-valid receipt",
        ),
        _dimension(
            "recourse_declaration", "RECHECKABLE" if digest_verdict.get("ok") else "UNDETERMINED",
            ("NAMED" if recourse_named else "ABSENT") if digest_verdict.get("ok") else "NOT_EVALUATED",
            [f"forum fields={len(forum)}", f"remedies={len(remedies)}"],
            None if digest_verdict.get("ok") else "an integrity-valid receipt",
        ),
        _dimension(
            "recourse_reachability", "EXTERNAL_EVIDENCE_REQUIRED", "NOT_CHECKED",
            ["the drill performs no network request"],
            "a current observation from the named dispute forum",
        ),
        _dimension(
            "underlying_event_occurrence", "EXTERNAL_EVIDENCE_REQUIRED", "NOT_ESTABLISHED",
            ["a receipt records a claim; its hashes do not establish the worldly event"],
            "an independently retained execution, payment-rail, or equivalent event record",
        ),
        _dimension(
            "receipt_coverage", "UNDETERMINED", "NOT_COMPUTED",
            ["no independent action denominator is accepted by this command"],
            "a separate record of consequential actions",
        ),
        _dimension(
            "reliance_decision", "UNDETERMINED", "NOT_COMPUTED",
            ["verification facts are not an acceptance policy"],
            "a relying party's explicit policy",
        ),
    ]
    return {
        "report_version": "1",
        "receipt": {
            "sha256": digest(raw),
            "schema_version": receipt.get("schema_version"),
            "kind": receipt.get("kind"),
        },
        "verifier": {
            "implementation": "action-receipt-v0.2-verification-kit",
            "network_guard": "PYTHON_AUDIT_HOOK",
            "verified_to": digest_verdict.get("verified_to"),
        },
        "dimensions": dimensions,
    }


def print_receipt_report(report: dict) -> None:
    groups = (
        ("RECHECKABLE", "RECHECKABLE FROM RETAINED BYTES"),
        ("EXTERNAL_EVIDENCE_REQUIRED", "EXTERNAL EVIDENCE REQUIRED"),
        ("UNDETERMINED", "UNDETERMINED OR NOT COMPUTED"),
    )
    print("RETAINED RECEIPT DRILL")
    print(f"receipt sha256        {report['receipt']['sha256']}")
    print("network guard         PYTHON_AUDIT_HOOK")
    for availability, heading in groups:
        print(f"\n{heading}")
        for row in report["dimensions"]:
            if row["availability"] == availability:
                print(f"{row['claim'].replace('_', ' '):42s} {row['result']}")
                if row.get("required_input"):
                    print(f"  requires: {row['required_input']}")


def verify_external_receipt(args: argparse.Namespace) -> int:
    if verify_manifest(quiet=args.format == "json") != 0:
        return 2
    try:
        raw = _read_bounded(args.receipt, MAX_RECEIPT_BYTES, "receipt")
        receipt = strict_receipt_json(raw)
        public_key = _load_public_key(args.key)
        _install_network_guard()
        checker = _load_checker()
        report = receipt_report(receipt, raw, checker, public_key)
    except (OSError, ReceiptInputError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print_receipt_report(report)
    failed = {"FAILED", "VIOLATES", "NOT_CHECKABLE", "NOT_EVALUATED"}
    return int(any(
        row.get("availability") == "RECHECKABLE" and row.get("result") in failed
        for row in report["dimensions"]
    ))


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


def verify_manifest(*, quiet: bool = False) -> int:
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
    if not quiet:
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


def verify_kit() -> int:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify this retained kit or one ActionReceipt v0.2."
    )
    subparsers = parser.add_subparsers(dest="command")
    receipt = subparsers.add_parser(
        "receipt", help="Verify one retained ActionReceipt v0.2 with no network access"
    )
    receipt.add_argument("receipt", type=Path)
    receipt.add_argument("--key", type=Path, default=None, metavar="PUBLIC_KEY.json")
    receipt.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.command == "receipt":
        return verify_external_receipt(args)
    return verify_kit()


if __name__ == "__main__":
    raise SystemExit(main())
