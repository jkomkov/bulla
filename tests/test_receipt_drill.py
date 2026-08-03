"""Stable retained-receipt drill and standalone-kit contract."""

from __future__ import annotations

import json
import dataclasses
import hashlib
import io
from pathlib import Path
import stat
import subprocess
import sys
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest

from bulla.receipt_drill import ReceiptDrillError, run_receipt_drill
from bulla.verification_kit import (
    ARCHIVE_NAME,
    VerificationKitError,
    validate_verification_kit,
    verification_kit_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
PAYMENT = ROOT / "spec" / "vectors" / "payment-authorization.json"


def test_embedded_drill_reports_typed_boundaries() -> None:
    report, code = run_receipt_drill(PAYMENT)

    assert code == 0
    assert report["report_version"] == "1"
    assert "ok" not in report
    assert report["verifier"]["checker_agreement"] == "MATCH"
    assert report["verifier"]["tamper_control"] == "REJECTED"
    assert report["verifier"]["network_guard"] == "PYTHON_AUDIT_HOOK"
    rows = {row["claim"]: row for row in report["dimensions"]}
    assert rows["record_integrity"]["availability"] == "RECHECKABLE"
    assert rows["record_integrity"]["result"] == "VERIFIED"
    assert rows["underlying_event_occurrence"]["availability"] == "EXTERNAL_EVIDENCE_REQUIRED"
    assert rows["underlying_event_occurrence"]["result"] == "NOT_ESTABLISHED"
    assert rows["receipt_coverage"]["availability"] == "UNDETERMINED"
    assert rows["reliance_decision"]["result"] == "NOT_COMPUTED"


def test_tampered_receipt_is_a_completed_failing_drill(tmp_path: Path) -> None:
    document = json.loads(PAYMENT.read_text(encoding="utf-8"))
    document["action"]["subject"]["amount_minor"] += 1
    receipt = tmp_path / "tampered.json"
    receipt.write_text(json.dumps(document), encoding="utf-8")

    report, code = run_receipt_drill(receipt)

    assert code == 1
    rows = {row["claim"]: row for row in report["dimensions"]}
    assert rows["record_integrity"]["result"] == "FAILED"
    assert rows["declared_authority"]["result"] == "NOT_EVALUATED"


def test_supplied_kit_requires_and_matches_detached_digest(tmp_path: Path) -> None:
    kit = tmp_path / ARCHIVE_NAME
    digest = tmp_path / f"{ARCHIVE_NAME}.sha256"
    payload = verification_kit_bytes()
    kit.write_bytes(payload)
    digest.write_text(f"{hashlib.sha256(payload).hexdigest()}  {ARCHIVE_NAME}\n", encoding="ascii")
    report, code = run_receipt_drill(PAYMENT, kit_path=kit, kit_digest_path=digest)
    assert code == 0
    assert report["verifier"]["kit_digest_source"] == "CALLER_SUPPLIED_DIGEST"
    assert report["verifier"]["kit_execution_trust"] == "INSTALLED_DISTRIBUTION_MATCH"

    digest.write_text(f"{'0' * 64}  {ARCHIVE_NAME}\n", encoding="ascii")
    with pytest.raises(ReceiptDrillError, match="digest mismatch"):
        run_receipt_drill(PAYMENT, kit_path=kit, kit_digest_path=digest)


def test_caller_digest_cannot_authorize_a_foreign_kit(tmp_path: Path) -> None:
    with ZipFile(io.BytesIO(verification_kit_bytes()), "r") as source:
        members = {info.filename: source.read(info) for info in source.infolist()}
    members["README.md"] += b"\nForeign retained copy.\n"
    manifest = json.loads(members["MANIFEST.json"])
    for row in manifest["members"]:
        if row["path"] == "README.md":
            row["bytes"] = len(members["README.md"])
            row["sha256"] = hashlib.sha256(members["README.md"]).hexdigest()
    members["MANIFEST.json"] = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    members["MANIFEST.sha256"] = (
        f"{hashlib.sha256(members['MANIFEST.json']).hexdigest()}  MANIFEST.json\n"
    ).encode("ascii")

    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
        for name, value in sorted(members.items()):
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    payload = stream.getvalue()
    kit = tmp_path / ARCHIVE_NAME
    digest = tmp_path / f"{ARCHIVE_NAME}.sha256"
    kit.write_bytes(payload)
    digest.write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {ARCHIVE_NAME}\n", encoding="ascii"
    )

    with pytest.raises(ReceiptDrillError, match="detached digest establishes byte identity only"):
        run_receipt_drill(PAYMENT, kit_path=kit, kit_digest_path=digest)


@pytest.mark.parametrize("which", ["kit", "digest"])
def test_supplied_kit_pair_is_atomic(tmp_path: Path, which: str) -> None:
    kwargs = {"kit_path": tmp_path / "kit.zip"} if which == "kit" else {"kit_digest_path": tmp_path / "kit.sha256"}
    with pytest.raises(ReceiptDrillError, match="supplied together"):
        run_receipt_drill(PAYMENT, **kwargs)


def test_drill_rejects_non_v02_before_checker_execution(tmp_path: Path) -> None:
    document = json.loads(PAYMENT.read_text(encoding="utf-8"))
    document["schema_version"] = "0.3"
    receipt = tmp_path / "wrong-version.json"
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReceiptDrillError):
        run_receipt_drill(receipt)


def test_cli_rejects_duplicate_member_as_malformed(tmp_path: Path) -> None:
    raw = PAYMENT.read_text(encoding="utf-8").replace(
        '"schema_version": "0.2"',
        '"schema_version": "0.2", "schema_version": "0.2"',
        1,
    )
    receipt = tmp_path / "duplicate.json"
    receipt.write_text(raw, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from bulla.cli import main; main()",
            "receipt",
            "drill",
            str(receipt),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "duplicate JSON member" in completed.stderr


def test_standalone_receipt_mode_uses_no_bulla_import(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    from bulla.verification_kit import extract_verification_kit

    extract_verification_kit(verification_kit_bytes(), kit)
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(kit / "verify.py"),
            "receipt",
            str(PAYMENT),
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["verifier"]["network_guard"] == "PYTHON_AUDIT_HOOK"
    assert report["dimensions"][0]["result"] == "VERIFIED"


def test_standalone_kit_is_rerunnable_without_extra_files(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    from bulla.verification_kit import extract_verification_kit

    extract_verification_kit(verification_kit_bytes(), kit)
    command = [sys.executable, "-I", "-B", str(kit / "verify.py")]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr


def test_archive_validation_rejects_changed_bytes() -> None:
    payload = verification_kit_bytes()[:-20]
    with pytest.raises(VerificationKitError):
        validate_verification_kit(payload)


def test_signature_failure_does_not_become_integrity_failure(tmp_path: Path) -> None:
    pytest.importorskip("nacl")
    from bulla.action_receipt import ActionReceipt
    from bulla.identity import LocalEd25519Signer

    signer = LocalEd25519Signer(seed=bytes(range(32)))
    unsigned = ActionReceipt.from_dict(json.loads(PAYMENT.read_text(encoding="utf-8")))
    signature = signer.sign(unsigned.content_hash)
    signature["proofValue"] = "A" * len(signature["proofValue"])
    receipt = dataclasses.replace(unsigned, signature=signature).to_dict()
    path = tmp_path / "bad-signature.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    report, code = run_receipt_drill(path)

    rows = {row["claim"]: row for row in report["dimensions"]}
    assert code == 1
    assert rows["record_integrity"]["result"] == "VERIFIED"
    assert rows["issuer_authenticity"]["availability"] == "RECHECKABLE"
    assert rows["issuer_authenticity"]["result"] == "FAILED"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"0.2","value":1e999}',
        b'{"schema_version":"0.2","mandate":null}',
    ],
)
def test_malformed_numbers_and_shapes_return_cli_misuse(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "malformed.json"
    path.write_bytes(raw)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from bulla.cli import main; main()",
            "receipt",
            "drill",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "could not complete" in completed.stderr


def test_guard_is_installed_before_bulla_checker_import() -> None:
    worker = (ROOT / "src" / "bulla" / "_receipt_drill_worker.py").read_text(
        encoding="utf-8"
    )
    main = worker[worker.index("def main()") :]
    assert main.index("_install_network_guard()") < main.index(
        "from bulla.receipt_drill import"
    )


def test_outer_guard_denies_network_even_if_retained_checker_does_not(tmp_path: Path) -> None:
    checker = tmp_path / "checker.py"
    checker.write_text(
        "import socket\nsocket.getaddrinfo('example.com', 443)\n",
        encoding="utf-8",
    )
    worker = ROOT / "src" / "bulla" / "_receipt_drill_standalone_worker.py"
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(worker), "--checker", str(checker)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "network guard denied audit event socket.getaddrinfo" in completed.stderr
