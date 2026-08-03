"""Rehearse ActionReceipt v0.2 verification from retained bytes only."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from bulla import __version__
from bulla.verification_kit import (
    MAX_ARCHIVE_CONTAINER_BYTES,
    VerificationKitError,
    extract_verification_kit,
    verification_kit_bytes,
)


MAX_RECEIPT_BYTES = 1_048_576
MAX_KEY_BYTES = 65_536
MAX_DIGEST_BYTES = 4_096


class ReceiptDrillError(ValueError):
    """The drill could not safely or consistently complete."""


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise ReceiptDrillError(f"{label} cannot be read: {exc}") from exc
    if len(payload) > limit:
        raise ReceiptDrillError(f"{label} exceeds {limit} bytes")
    return payload


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


def load_public_key(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        document = json.loads(_read_bounded(path, MAX_KEY_BYTES, "public-key file").decode("utf-8"))
        public_key = base64.b64decode(document["public_key_b64"], validate=True)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ReceiptDrillError(f"public-key file is malformed: {exc}") from exc
    if len(public_key) != 32:
        raise ReceiptDrillError("public_key_b64 must decode to 32 bytes")
    return public_key


def read_detached_digest(path: Path) -> str:
    try:
        line = _read_bounded(path, MAX_DIGEST_BYTES, "kit digest file").decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ReceiptDrillError(f"kit digest file cannot be read: {exc}") from exc
    digest = line.split()[0] if line else ""
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:"):]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ReceiptDrillError("kit digest must contain one lowercase SHA-256 value")
    return digest


def _integrity_ok(verdict) -> bool:
    return bool(verdict.checks.get("envelope_valid")) and all(
        verdict.checks.get(f"hash_{name}")
        for name in ("content", "event", "attestation", "log_leaf")
    )


def _bounds_result(receipt: dict, verdict, integrity_ok: bool) -> tuple[str, list[str]]:
    if not integrity_ok:
        return "NOT_EVALUATED", ["content-dependent results are suppressed after integrity failure"]
    statuses = list(verdict.conventions.values())
    bounds = verdict.bounds_conformance
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
        for name, status in sorted(verdict.conventions.items())
    )
    return result, basis


def package_report(receipt: dict, raw: bytes, public_key: bytes | None) -> dict:
    # This import occurs inside the guarded worker. Keeping it out of module
    # import makes the ordering mechanically testable.
    from bulla.action_receipt import verify_receipt

    verdict = verify_receipt(receipt, public_key=public_key)
    integrity_ok = _integrity_ok(verdict)
    integrity = "VERIFIED" if integrity_ok else "FAILED"
    bounds_result, bounds_basis = _bounds_result(receipt, verdict, integrity_ok)

    evidence = receipt.get("evidence_refs") or []
    grounding_result = (
        verdict.effective_grounding.upper()
        if verdict.effective_grounding
        else "NO_EVIDENCE_DECLARED"
    )
    signature = receipt.get("signature")
    issuer = signature.get("issuer") if isinstance(signature, dict) else None
    self_certifying = isinstance(issuer, str) and issuer.startswith("did:key:z")
    if not signature:
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "UNSIGNED",
            ["the retained receipt carries no content signature"],
            "a signed receipt and the issuer's verification key",
        )
    elif "signature" not in verdict.checks:
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "SKIPPED",
            ["the optional ed25519 library is unavailable"],
            "PyNaCl and a resolvable issuer key",
        )
    elif not self_certifying and public_key is None:
        authenticity = _dimension(
            "issuer_authenticity", "UNDETERMINED", "UNRESOLVED",
            ["the signature names a non-self-certifying issuer"],
            "a retained public key supplied with --key",
        )
    else:
        authenticity = _dimension(
            "issuer_authenticity", "RECHECKABLE",
            "VERIFIED" if verdict.checks["signature"] else "FAILED",
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
    dimensions = [
        _dimension(
            "record_integrity", "RECHECKABLE", integrity,
            ["four canonical hashes and the v0.2 modality law were recomputed"]
            + [f"{name}={str(value).upper()}" for name, value in sorted(verdict.checks.items()) if name == "envelope_valid" or name.startswith("hash_")],
        ),
        _dimension(
            "declared_bounds_or_convention_conformance",
            "RECHECKABLE" if integrity_ok else "UNDETERMINED",
            bounds_result,
            bounds_basis,
            None if integrity_ok else "an integrity-valid receipt",
        ),
        _dimension(
            "evidence_grounding_declaration",
            "RECHECKABLE" if integrity_ok else "UNDETERMINED",
            grounding_result if integrity_ok else "NOT_EVALUATED",
            [f"{len(evidence)} evidence reference(s) are carried by the receipt"],
            None if integrity_ok else "an integrity-valid receipt",
        ),
        authenticity,
        _dimension(
            "declared_authority", "RECHECKABLE" if integrity_ok else "UNDETERMINED",
            authority_result if integrity_ok else "NOT_EVALUATED",
            authority_basis,
            None if integrity_ok else "an integrity-valid receipt",
        ),
        _dimension(
            "recourse_declaration", "RECHECKABLE" if integrity_ok else "UNDETERMINED",
            ("NAMED" if forum or remedies else "ABSENT") if integrity_ok else "NOT_EVALUATED",
            [f"forum fields={len(forum)}", f"remedies={len(remedies)}"],
            None if integrity_ok else "an integrity-valid receipt",
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
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": receipt.get("schema_version"),
            "kind": receipt.get("kind"),
        },
        "verifier": {
            "implementation": "bulla",
            "bulla_version": __version__,
            "network_guard": "PYTHON_AUDIT_HOOK",
            "verified_to": verdict.verified_to,
        },
        "dimensions": dimensions,
    }


def _dimension_signature(report: dict) -> list[tuple]:
    return [
        (
            row.get("claim"),
            row.get("availability"),
            row.get("result"),
            row.get("required_input"),
        )
        for row in report.get("dimensions", [])
    ]


def _run_standalone(
    kit_root: Path,
    receipt_path: Path,
    key_path: Path | None,
) -> tuple[int, dict, str]:
    worker = Path(__file__).with_name("_receipt_drill_standalone_worker.py")
    checker_args = [
        str(kit_root / "verify.py"),
        "receipt",
        str(receipt_path),
        "--format",
        "json",
    ]
    if key_path is not None:
        checker_args.extend(("--key", str(key_path)))
    command = [
        sys.executable,
        "-I",
        "-B",
        str(worker),
        "--checker",
        checker_args[0],
        "--",
        *checker_args[1:],
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        report = json.loads(completed.stdout) if completed.stdout else {}
    except json.JSONDecodeError as exc:
        raise ReceiptDrillError(
            f"standalone checker returned malformed JSON: {completed.stdout!r}"
        ) from exc
    return completed.returncode, report, completed.stderr.strip()


def _run_bulla_checker(
    receipt_path: Path,
    key_path: Path | None,
) -> tuple[int, dict, str]:
    worker = Path(__file__).with_name("_receipt_drill_worker.py")
    command = [
        sys.executable,
        "-I",
        "-B",
        str(worker),
        str(receipt_path),
    ]
    if key_path is not None:
        command.extend(("--key", str(key_path)))
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        report = json.loads(completed.stdout) if completed.stdout else {}
    except json.JSONDecodeError as exc:
        raise ReceiptDrillError(
            f"Bulla checker returned malformed JSON: {completed.stdout!r}"
        ) from exc
    return completed.returncode, report, completed.stderr.strip()


def run_receipt_drill(
    receipt_path: Path,
    *,
    key_path: Path | None = None,
    kit_path: Path | None = None,
    kit_digest_path: Path | None = None,
) -> tuple[dict, int]:
    if (kit_path is None) != (kit_digest_path is None):
        raise ReceiptDrillError("--kit and --kit-digest must be supplied together")
    try:
        raw = _read_bounded(receipt_path, MAX_RECEIPT_BYTES, "receipt")
    except ReceiptDrillError as exc:
        raise ReceiptDrillError(f"receipt cannot be read safely: {exc}") from exc
    # Validate key bytes before passing the same path to both isolated checkers.
    load_public_key(key_path)

    embedded_payload = verification_kit_bytes()
    embedded_digest = hashlib.sha256(embedded_payload).hexdigest()
    if kit_path is None:
        payload = embedded_payload
        kit_source = "EMBEDDED_DISTRIBUTION"
        supplied_digest = embedded_digest
    else:
        try:
            payload = _read_bounded(
                kit_path, MAX_ARCHIVE_CONTAINER_BYTES, "verification kit"
            )
        except ReceiptDrillError as exc:
            raise ReceiptDrillError(str(exc)) from exc
        supplied_digest = read_detached_digest(kit_digest_path)
        kit_source = "CALLER_SUPPLIED_DIGEST"
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != supplied_digest:
        raise ReceiptDrillError(
            f"verification kit digest mismatch: computed {actual_digest}, expected {supplied_digest}"
        )
    if actual_digest != embedded_digest:
        raise ReceiptDrillError(
            "caller-supplied kit is not authenticated by the installed Bulla "
            "distribution; the detached digest establishes byte identity only"
        )

    package_code, package, package_error = _run_bulla_checker(
        receipt_path.resolve(), key_path.resolve() if key_path else None
    )
    if package_code not in (0, 1):
        raise ReceiptDrillError(
            "Bulla checker could not complete"
            + (f": {package_error}" if package_error else "")
        )
    with tempfile.TemporaryDirectory(prefix="bulla-receipt-drill-") as temporary:
        root = Path(temporary)
        kit_root = root / "kit"
        try:
            validated_digest = extract_verification_kit(payload, kit_root)
        except VerificationKitError as exc:
            raise ReceiptDrillError(str(exc)) from exc
        if validated_digest != actual_digest:
            raise ReceiptDrillError("validated kit digest changed unexpectedly")

        standalone_code, standalone, standalone_error = _run_standalone(
            kit_root, receipt_path.resolve(), key_path.resolve() if key_path else None
        )
        if standalone_code not in (0, 1):
            raise ReceiptDrillError(
                "standalone checker could not complete"
                + (f": {standalone_error}" if standalone_error else "")
            )
        if _dimension_signature(package) != _dimension_signature(standalone):
            raise ReceiptDrillError("verifier disagreement on dimensional results")

        try:
            tampered = copy.deepcopy(json.loads(raw))
        except (UnicodeError, json.JSONDecodeError) as exc:  # guarded worker already gives detail
            raise ReceiptDrillError(f"receipt cannot be read safely: {exc}") from exc
        action = tampered.get("action")
        if not isinstance(action, dict) or not isinstance(action.get("type"), str):
            raise ReceiptDrillError("receipt has no deterministic tamper-control field")
        action["type"] += ".tamper-control"
        tamper_path = root / "tamper-control.json"
        tamper_path.write_text(json.dumps(tampered, sort_keys=True), encoding="utf-8")
        tamper_internal_code, tamper_internal_report, tamper_internal_error = _run_bulla_checker(
            tamper_path, key_path.resolve() if key_path else None
        )
        tamper_code, tamper_report, tamper_error = _run_standalone(
            kit_root, tamper_path, key_path.resolve() if key_path else None
        )
        tamper_integrity = next(
            (row for row in tamper_report.get("dimensions", []) if row.get("claim") == "record_integrity"),
            None,
        )
        if (
            tamper_internal_code != 1
            or next(
                (row for row in tamper_internal_report.get("dimensions", []) if row.get("claim") == "record_integrity"),
                {},
            ).get("result") != "FAILED"
            or tamper_code != 1
            or not tamper_integrity
            or tamper_integrity.get("result") != "FAILED"
        ):
            raise ReceiptDrillError(
                "deterministic tamper control was not rejected"
                + (f": {tamper_internal_error or tamper_error}" if tamper_internal_error or tamper_error else "")
            )

    package["verifier"].update(
        {
            "kit_sha256": actual_digest,
            "kit_digest_source": kit_source,
            "kit_execution_trust": "INSTALLED_DISTRIBUTION_MATCH",
            "checker_agreement": "MATCH",
            "tamper_control": "REJECTED",
        }
    )
    return package, package_code


def print_receipt_drill(report: dict) -> None:
    print("RETAINED RECEIPT DRILL")
    print(f"receipt sha256        {report['receipt']['sha256']}")
    print(f"verification kit      {report['verifier']['kit_sha256']}")
    print(f"network guard         {report['verifier']['network_guard']}")
    print(f"checker agreement     {report['verifier']['checker_agreement']}")
    print(f"tamper control        {report['verifier']['tamper_control']}")
    groups = (
        ("RECHECKABLE", "RECHECKABLE FROM RETAINED BYTES"),
        ("EXTERNAL_EVIDENCE_REQUIRED", "EXTERNAL EVIDENCE REQUIRED"),
        ("UNDETERMINED", "UNDETERMINED OR NOT COMPUTED"),
    )
    for availability, heading in groups:
        print(f"\n{heading}")
        for row in report["dimensions"]:
            if row["availability"] != availability:
                continue
            print(f"{row['claim'].replace('_', ' '):42s} {row['result']}")
            if row.get("required_input"):
                print(f"  requires: {row['required_input']}")
