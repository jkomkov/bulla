"""Release-slot lifecycle: open, close, tamper, and omission behavior."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from bulla import __version__
from bulla.action_receipt import build_release_receipt
from bulla.coverage import ENFORCEMENT_EPOCH
from bulla.envelope import (
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)
from bulla.identity import LocalEd25519Signer

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("release_slot", _SCRIPTS / "release_slot.py")
release_slot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_slot)
_check_spec = importlib.util.spec_from_file_location(
    "check_release_slots", _SCRIPTS / "check_release_slots.py"
)
check_release_slots = importlib.util.module_from_spec(_check_spec)
_check_spec.loader.exec_module(check_release_slots)
_mint_spec = importlib.util.spec_from_file_location(
    "mint_release_receipt", _SCRIPTS / "mint_release_receipt.py"
)
mint_release_receipt = importlib.util.module_from_spec(_mint_spec)
_mint_spec.loader.exec_module(mint_release_receipt)


def _issuer_record(slot: dict, signer: LocalEd25519Signer) -> dict:
    return {
        "id": slot["release_issuer_id"],
        "issuer": signer.issuer,
        "record_hash": slot["release_issuer_record_hash"],
        "trust_context_schema": slot["trust_context_schema"],
        "package": "bulla",
        "proof_type": "bulla/ed25519-2026",
        "expected_publisher": "github:jkomkov/bulla",
    }


def _slot(signer: LocalEd25519Signer, *, version: str = "0.99.0", opened_at: str | None = None) -> dict:
    identity = {
        "context_schema": "bulla.release-trust-context/0.2",
        "package": "bulla",
        "proof_type": "bulla/ed25519-2026",
        "expected_publisher": "github:jkomkov/bulla",
        "id": "release-issuer-test",
        "issuer": signer.issuer,
        "valid_from": "0.44.0",
    }
    issuer_record = {
        **identity,
        "record_hash": release_slot._canon_hash(identity),
        "trust_context_schema": "bulla.release-trust-context/0.2",
    }
    return release_slot.build_slot(
        version=version,
        source_commit="0" * 40,
        source_tree_sha256="sha256:" + "1" * 64,
        preflight_run_id=123456,
        preflight_manifest_sha256="sha256:" + "5" * 64,
        preflight_wheel_sha256="sha256:" + "2" * 64,
        preflight_sdist_sha256="sha256:" + "3" * 64,
        signer=signer,
        issuer_record=issuer_record,
        opened_at=opened_at,
    )


def _closing_receipt(
    slot: dict,
    signer: LocalEd25519Signer,
    *,
    signed_at: str | None = None,
    timestamp: str | None = None,
) -> dict:
    moment = signed_at or datetime.now(timezone.utc).isoformat()
    envelope = RecourseEnvelope(
        authority=Authority(
            principal="github:jkomkov",
            policy="policy://bulla/release",
            delegation=("pypi:project:bulla",),
        ),
        bounds=Bounds(scope=f"pypi:bulla version:{slot['version']}"),
        recourse=Recourse(
            challenge_window="P90D",
            forum=Forum(
                log_endpoint="https://pypi.org/project/bulla/",
                trusted_root_ref="rekor:sigstore-pep740",
            ),
            remedies=(
                Remedy(
                    rung="recompute",
                    verifier="pip download + sha256 vs PyPI",
                    anchor=f"pypi:bulla=={slot['version']}",
                ),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )
    kwargs = dict(
        package="bulla",
        version=slot["version"],
        git_commit=slot["source_commit"],
        git_tag=f"v{slot['version']}",
        wheel_sha256="sha256:" + "2" * 64,
        sdist_sha256="sha256:" + "3" * 64,
        tree_hash=slot["source_tree_sha256"],
        release_slot_hash=slot["slot_hash"],
        release_signed_at=moment,
        root_of_trust={
            "scheme": "sigstore-pep740",
            "publisher": slot["expected_publisher"],
            "integrity_api": [],
        },
        diagnostic_ref={
            "status": "reference",
            "ref": "sha256:" + "4" * 64,
        },
        envelope=envelope,
        timestamp=timestamp or moment,
        producer={"slot_ref": {"slot_hash": slot["slot_hash"]}},
    )
    unsigned = build_release_receipt(**kwargs)
    return build_release_receipt(
        **kwargs, signature=signer.sign(unsigned.content_hash)
    ).to_dict()


def test_slot_builds_and_verifies() -> None:
    signer = LocalEd25519Signer.generate()
    slot = _slot(signer)
    ok, reason = release_slot.verify_slot(
        slot, issuer_record=_issuer_record(slot, signer)
    )
    assert ok, reason
    assert slot["schema_version"] == "release-slot/0.3"
    assert slot["preflight_run_id"] == 123456
    assert slot["release_issuer_id"] == "release-issuer-test"
    assert slot["release_issuer_record_hash"].startswith("sha256:")
    without_context, reason = release_slot.verify_slot(slot)
    assert not without_context
    assert "external issuer record" in reason
    assert slot["slot_hash"].startswith("sha256:")
    assert slot["close_deadline"] > slot["opened_at"]


def test_tampered_slot_fails_closed() -> None:
    signer = LocalEd25519Signer.generate()
    slot = _slot(signer)
    tampered = dict(slot)
    tampered["version"] = "0.99.1"
    ok, reason = release_slot.verify_slot(
        tampered, issuer_record=_issuer_record(slot, signer)
    )
    assert not ok
    assert "slot_hash" in reason

    rehashed = dict(tampered)
    unsigned = {k: v for k, v in rehashed.items() if k not in ("slot_hash", "proof")}
    rehashed["slot_hash"] = release_slot._canon_hash(unsigned)
    ok, reason = release_slot.verify_slot(
        rehashed, issuer_record=_issuer_record(slot, signer)
    )
    assert not ok
    assert "proof rejected" in reason

    excessive = dict(slot)
    excessive["close_deadline"] = (
        datetime.fromisoformat(slot["opened_at"].replace("Z", "+00:00"))
        + timedelta(days=365)
    ).isoformat().replace("+00:00", "Z")
    unsigned = {
        key: value
        for key, value in excessive.items()
        if key not in ("slot_hash", "proof")
    }
    excessive["slot_hash"] = release_slot._canon_hash(unsigned)
    excessive["proof"] = signer.sign_domain(
        release_slot.PROOF_PURPOSE, excessive["slot_hash"]
    )
    ok, reason = release_slot.verify_slot(
        excessive, issuer_record=_issuer_record(slot, signer)
    )
    assert not ok
    assert "exactly 24 hours" in reason


def test_closing_receipt_matches_by_slot_hash() -> None:
    signer = LocalEd25519Signer.generate()
    slot = _slot(signer)
    receipt = _closing_receipt(slot, signer)
    assert release_slot.receipt_closes_slot(receipt, slot)
    assert not release_slot.receipt_closes_slot(
        receipt,
        slot,
        source_tree_sha256="sha256:" + "9" * 64,
    )
    assert not release_slot.receipt_closes_slot({"producer": {}}, slot)
    assert not release_slot.receipt_closes_slot(
        {"producer": {"slot_ref": {"slot_hash": slot["slot_hash"]}}},
        slot,
    )
    tampered = json.loads(json.dumps(receipt))
    tampered["action"]["subject"]["release_signed_at"] = "2020-01-01T00:00:00Z"
    assert not release_slot.receipt_closes_slot(tampered, slot)
    wrong_package = json.loads(json.dumps(receipt))
    wrong_package["action"]["subject"]["package"] = "other"
    assert not release_slot.receipt_closes_slot(wrong_package, slot)
    wrong_tag = json.loads(json.dumps(receipt))
    wrong_tag["action"]["subject"]["git_tag"] = "v9.9.9"
    assert not release_slot.receipt_closes_slot(wrong_tag, slot)
    wrong_tree = json.loads(json.dumps(receipt))
    wrong_tree["evidence_refs"][2]["hash"] = "sha256:" + "9" * 64
    assert not release_slot.receipt_closes_slot(wrong_tree, slot)
    backdated = _closing_receipt(
        slot,
        signer,
        signed_at="2020-01-01T00:00:00+00:00",
    )
    assert not release_slot.receipt_closes_slot(backdated, slot)
    mismatched_time = _closing_receipt(
        slot,
        signer,
        signed_at=datetime.now(timezone.utc).isoformat(),
        timestamp="2020-01-01T00:00:00+00:00",
    )
    assert not release_slot.receipt_closes_slot(mismatched_time, slot)

    result = release_slot.classify_slot(slot, [receipt])
    assert result["state"] == "CLOSED"


def test_open_within_deadline_and_omission_after() -> None:
    signer = LocalEd25519Signer.generate()
    opened = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    slot = _slot(signer, opened_at=opened)

    before = datetime.now(timezone.utc) + timedelta(hours=1)
    assert release_slot.classify_slot(slot, [], now=before)["state"] == "OPEN"

    after = datetime.now(timezone.utc) + timedelta(hours=48)
    result = release_slot.classify_slot(slot, [], now=after)
    assert result["state"] == "OMISSION"
    assert "no closing release receipt" in result["reason"]


def test_late_closure_is_stated_not_hidden() -> None:
    signer = LocalEd25519Signer.generate()
    opened = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat().replace("+00:00", "Z")
    slot = _slot(signer, opened_at=opened)
    late_receipt = _closing_receipt(slot, signer)
    result = release_slot.classify_slot(slot, [late_receipt])
    assert result["state"] == "CLOSED_LATE"


def test_check_script_fails_on_omission(tmp_path: Path) -> None:
    signer = LocalEd25519Signer.generate()
    opened = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat().replace("+00:00", "Z")
    slot = _slot(signer, opened_at=opened)
    slots_dir = tmp_path / "slots"
    receipts_dir = tmp_path / "receipts"
    slots_dir.mkdir()
    receipts_dir.mkdir()
    context = tmp_path / "release-trust-context.json"
    context.write_text(
        json.dumps(
            {
                "issuers": [
                    {
                        "id": "release-issuer-test",
                        "issuer": signer.issuer,
                        "valid_from": "0.44.0",
                        "retired_after": None,
                        "revoked_from": None,
                        "status": "active",
                    }
                ],
                "expected_publisher": "github:jkomkov/bulla",
                "package": "bulla",
                "proof_type": "bulla/ed25519-2026",
                "schema_version": "bulla.release-trust-context/0.2",
            }
        )
    )
    (slots_dir / "0.99.0.slot.json").write_text(json.dumps(slot))

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS / "check_release_slots.py"),
            "--slots-dir",
            str(slots_dir),
            "--receipts-dir",
            str(receipts_dir),
            "--context",
            str(context),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "OMISSION 0.99.0" in proc.stderr

    # Closing receipt flips the same invocation to success.
    (receipts_dir / "0.99.0.json").write_text(
        json.dumps(_closing_receipt(slot, signer))
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS / "check_release_slots.py"),
            "--slots-dir",
            str(slots_dir),
            "--receipts-dir",
            str(receipts_dir),
            "--context",
            str(context),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_coverage_reports_three_metrics_with_immutable_epoch() -> None:
    from bulla.coverage import pypi_coverage

    assert ENFORCEMENT_EPOCH == "0.44.0"
    repo = Path(__file__).resolve().parents[1]
    report = pypi_coverage(
        repo / "releases",
        project_doc=json.loads((repo / "releases" / "pypi-project.json").read_text()),
        verify_integrity=False,
    )
    metrics = report["metrics"]
    assert report["release_receipt_required_since"] == ENFORCEMENT_EPOCH
    forward = metrics["forward_contemporaneous_since_epoch"]
    # Every release at or after the epoch must be contemporaneous — the
    # forward invariant this sprint locks.
    assert forward["receipted"] == forward["total"]
    assert forward["total"] >= 2
    assert forward["coverage"] == 1.0
    all_contemp = metrics["all_time_contemporaneous"]
    availability = metrics["all_time_receipt_availability"]
    assert all_contemp["receipted"] <= availability["receipted"]
    assert availability["total"] == report["total_anchored"]


def test_public_release_inventory_rejects_a_mutable_actual_release() -> None:
    releases = [
        {
            "tag_name": "release-slot-v0.44.2",
            "draft": False,
            "immutable": True,
        },
        {"tag_name": "v0.44.2", "draft": False, "immutable": False},
    ]
    pypi = {"releases": {"0.44.2": [{"filename": "bulla-0.44.2.tar.gz"}]}}
    with pytest.raises(ValueError, match="required releases are not immutable"):
        check_release_slots.validate_release_inventory(
            releases,
            pypi,
            {"release-slot-v0.44.2"},
            epoch="0.44.2",
        )
    releases[1]["immutable"] = True
    result = check_release_slots.validate_release_inventory(
        releases,
        pypi,
        {"release-slot-v0.44.2"},
        epoch="0.44.2",
    )
    assert result["slot_tags"] == ["release-slot-v0.44.2"]


def test_repository_slot_wrapper_uses_the_current_external_context() -> None:
    source = (_SCRIPTS / "open_release_slot.py").read_text(encoding="utf-8")
    assert "release_issuer(args.context, args.version)" in source
    assert "issuer_record=issuer_record" in source
    assert "run by publish.yml" not in source
    assert 'add_argument("--package"' not in source
    assert 'add_argument("--expected-publisher"' not in source
    assert 'add_argument("--deadline-hours"' not in source


def test_slot_rejects_cross_package_and_publisher_context_transplants() -> None:
    signer = LocalEd25519Signer.generate()
    slot = _slot(signer)
    record = _issuer_record(slot, signer)

    wrong_package = {**record, "package": "other"}
    ok, reason = release_slot.verify_slot(slot, issuer_record=wrong_package)
    assert not ok
    assert "issuer record" in reason
    wrong_publisher = {
        **record,
        "expected_publisher": "github:other/project",
    }
    ok, reason = release_slot.verify_slot(slot, issuer_record=wrong_publisher)
    assert not ok
    assert "issuer record" in reason


def test_unsigned_receipt_minter_verifies_v02_slot_with_external_context(
    tmp_path: Path,
) -> None:
    signer = LocalEd25519Signer.generate()
    slot = _slot(signer, version="0.44.2")
    slot_path = tmp_path / "slot.json"
    slot_path.write_text(json.dumps(slot))
    context = {
        "issuers": [
            {
                "id": "release-issuer-test",
                "issuer": signer.issuer,
                "valid_from": "0.44.0",
                "retired_after": None,
                "revoked_from": None,
                "status": "active",
            }
        ],
        "expected_publisher": "github:jkomkov/bulla",
        "package": "bulla",
        "proof_type": "bulla/ed25519-2026",
        "schema_version": "bulla.release-trust-context/0.2",
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    verified = mint_release_receipt._verified_release_slot(
        slot_path,
        context_path,
        version="0.44.2",
        source_commit=slot["source_commit"],
    )
    assert verified["slot_hash"] == slot["slot_hash"]


def test_real_unsigned_mint_path_runs_with_v02_slot_and_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = LocalEd25519Signer.generate()
    version = __version__
    slot = _slot(signer, version=version)
    slot_path = tmp_path / "slot.json"
    slot_path.write_text(json.dumps(slot))
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "issuers": [
                    {
                        "id": "release-issuer-test",
                        "issuer": signer.issuer,
                        "valid_from": "0.44.0",
                        "retired_after": None,
                        "revoked_from": None,
                        "status": "active",
                    }
                ],
                "expected_publisher": "github:jkomkov/bulla",
                "package": "bulla",
                "proof_type": "bulla/ed25519-2026",
                "schema_version": "bulla.release-trust-context/0.2",
            }
        )
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / f"bulla-{version}-py3-none-any.whl"
    sdist = dist / f"bulla-{version}.tar.gz"
    verification_kit = dist / "action-receipt-v0.2-verification-kit.zip"
    verification_kit.write_bytes(
        (Path(__file__).resolve().parents[1] / "src" / "bulla" / "data" / verification_kit.name).read_bytes()
    )
    with ZipFile(wheel, "w", compression=ZIP_STORED) as archive:
        archive.writestr(
            "bulla/data/action-receipt-v0.2-verification-kit.zip",
            verification_kit.read_bytes(),
        )
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo(
            f"bulla-{version}/src/bulla/data/action-receipt-v0.2-verification-kit.zip"
        )
        member.size = verification_kit.stat().st_size
        with verification_kit.open("rb") as stream:
            archive.addfile(member, stream)
    records = [
        {
            "filename": path.name,
            "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        }
        for path in (wheel, sdist)
    ]
    monkeypatch.setattr(
        mint_release_receipt,
        "fetch_pypi_project",
        lambda project: {"releases": {version: records}},
    )
    monkeypatch.setattr(
        mint_release_receipt,
        "fetch_pypi_provenance",
        lambda project, version, filename: {
            "attestation_bundles": [
                {
                    "publisher": {
                        "kind": "GitHub",
                        "repository": "jkomkov/bulla",
                    },
                    "attestations": [{}],
                }
            ]
        },
    )
    monkeypatch.setattr(
        mint_release_receipt,
        "_git",
        lambda *args: slot["source_commit"],
    )
    monkeypatch.setattr(
        mint_release_receipt,
        "_git_tree_sha256",
        lambda: slot["source_tree_sha256"],
    )
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_WORKFLOW", "publish")
    out = tmp_path / f"{version}.unsigned.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mint_release_receipt.py",
            "--dist",
            str(dist),
            "--out",
            str(out),
            "--test-result",
            "199 passed",
            "--git-tag",
            f"v{version}",
            "--repository",
            "jkomkov/bulla",
            "--slot",
            str(slot_path),
            "--context",
            str(context_path),
            "--allow-unsigned",
        ],
    )
    assert mint_release_receipt.main() == 0
    document = json.loads(out.read_text())
    assert document["action"]["subject"]["release_slot_hash"] == slot["slot_hash"]
    assert document["evidence_refs"][2]["name"] == "verification-kit"
    assert document["evidence_refs"][3]["hash"] == slot["source_tree_sha256"]


def test_explicit_release_tag_must_resolve_to_source_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 40

    monkeypatch.setattr(mint_release_receipt, "_git", lambda *args: "")
    with pytest.raises(RuntimeError, match="does not resolve"):
        mint_release_receipt._verified_release_tag(
            "v0.44.4", version="0.44.4", commit=expected
        )

    monkeypatch.setattr(mint_release_receipt, "_git", lambda *args: "b" * 40)
    with pytest.raises(RuntimeError, match="not a{40}"):
        mint_release_receipt._verified_release_tag(
            "v0.44.4", version="0.44.4", commit=expected
        )

    monkeypatch.setattr(mint_release_receipt, "_git", lambda *args: expected)
    assert (
        mint_release_receipt._verified_release_tag(
            "v0.44.4", version="0.44.4", commit=expected
        )
        == "v0.44.4"
    )
