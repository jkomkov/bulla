from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from bulla.action_receipt import build_release_receipt, verify_receipt
from bulla.envelope import (
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)
from bulla.identity import LocalEd25519Signer
from bulla.diagnostic import diagnose
from bulla.parser import load_composition
from bulla.witness import witness


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/trusted_release_signer.py"
SPEC = importlib.util.spec_from_file_location("trusted_release_signer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SIGNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIGNER)


def _context(signer: LocalEd25519Signer, **overrides: object) -> dict:
    record = {
        "id": "release-issuer-test",
        "issuer": signer.issuer,
        "valid_from": "0.44.0",
        "retired_after": None,
        "revoked_from": None,
        "status": "active",
        **overrides,
    }
    return {
        "issuers": [record],
        "expected_publisher": "github:jkomkov/bulla",
        "package": "bulla",
        "proof_type": "bulla/ed25519-2026",
        "schema_version": "bulla.release-trust-context/0.2",
    }


def _key(monkeypatch: pytest.MonkeyPatch) -> LocalEd25519Signer:
    signer = LocalEd25519Signer.generate()
    monkeypatch.setenv(
        "BULLA_RELEASE_KEY",
        json.dumps(signer.to_keyfile_dict()),
    )
    return signer


def _envelope(version: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal="github:jkomkov",
            policy="policy://bulla/release",
            delegation=("pypi:project:bulla",),
        ),
        bounds=Bounds(scope=f"pypi:bulla version:{version}"),
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
                    anchor=f"pypi:bulla=={version}",
                ),
                Remedy(
                    rung="revert",
                    verifier="pypi yank",
                    anchor=f"pypi:bulla=={version}",
                ),
                Remedy(
                    rung="escalate",
                    verifier="maintainer review",
                    anchor="github:jkomkov",
                ),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    signer = _key(monkeypatch)
    version = "0.44.2"
    commit = "a" * 40
    tree = "sha256:" + "b" * 64
    context = tmp_path / "release-trust-context.json"
    context.write_text(
        json.dumps(_context(signer)) + "\n"
    )
    slot_path = tmp_path / "slot.json"
    SIGNER.build_slot(
        type(
            "Args",
            (),
            {
                "version": version,
                "source_commit": commit,
                "source_tree_sha256": tree,
                "context": context,
                "out": slot_path,
            },
        )()
    )
    slot = SIGNER.read_json(slot_path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    wheel = candidate / f"bulla-{version}-py3-none-any.whl"
    sdist = candidate / f"bulla-{version}.tar.gz"
    summary = candidate / "pytest-summary.txt"
    wheel.write_bytes(b"reviewed wheel")
    sdist.write_bytes(b"reviewed sdist")
    summary.write_text("106 passed in 3.00s\n")
    witness = tmp_path / "witness.json"
    witness_document = {
        "canon_version": 2,
        "receipt_version": "0.1.0",
        "kernel_version": version,
        "composition_hash": "d" * 64,
        "diagnostic_hash": "e" * 64,
        "policy_profile": {
            "name": "witness.default.v1",
            "max_blind_spots": 0,
            "max_fee": 0,
            "max_unknown": -1,
            "require_bridge": True,
            "max_unmet_obligations": -1,
            "max_contradictions": -1,
            "max_structural_contradictions": -1,
        },
        "fee": 2,
        "blind_spots_count": 2,
        "bridges_required": 2,
        "unknown_dimensions": 0,
        "disposition": "refuse_pending_disclosure",
        "timestamp": "2026-07-29T12:00:00+00:00",
        "patches": [],
        "active_packs": [],
        "witness_basis": None,
        "anchor_ref": None,
    }
    witness_hash = SIGNER.digest_json(
        {
            key: value
            for key, value in witness_document.items()
            if key != "anchor_ref"
        }
    ).removeprefix("sha256:")
    witness_document["receipt_hash"] = witness_hash
    witness.write_text(json.dumps(witness_document) + "\n")
    producer = {
        "bulla_version": version,
        "minted": "post-publication",
        "workflow": "publish",
        "pypi_project": "bulla",
        "note": "UNSIGNED — no release key configured at mint time (stated, not hidden)",
        "slot_ref": {
            "slot_hash": slot["slot_hash"],
            "opened_at": slot["opened_at"],
            "close_deadline": slot["close_deadline"],
        },
    }
    receipt = build_release_receipt(
        package="bulla",
        version=version,
        git_commit=commit,
        git_tag=f"v{version}",
        wheel_sha256=SIGNER.digest_file(wheel),
        sdist_sha256=SIGNER.digest_file(sdist),
        tree_hash=tree,
        test_result="106 passed in 3.00s",
        release_slot_hash=slot["slot_hash"],
        diagnostic_ref={"status": "reference", "ref": f"sha256:{witness_hash}"},
        envelope=_envelope(version),
        root_of_trust={
            "scheme": "sigstore-pep740",
            "publisher": "github:jkomkov/bulla",
            "integrity_api": [
                (
                    f"https://pypi.org/integrity/bulla/{version}/"
                    f"bulla-{version}-py3-none-any.whl/provenance"
                ),
                (
                    f"https://pypi.org/integrity/bulla/{version}/"
                    f"bulla-{version}.tar.gz/provenance"
                ),
            ],
        },
        timestamp="2026-07-29T12:00:00+00:00",
        producer=producer,
    )
    unsigned = tmp_path / "unsigned.json"
    unsigned.write_text(receipt.to_json() + "\n")
    return {
        "version": version,
        "commit": commit,
        "tree": tree,
        "context": context,
        "slot": slot_path,
        "candidate": candidate,
        "summary": summary,
        "witness": witness,
        "unsigned": unsigned,
    }


def test_minimal_signer_never_imports_candidate_package() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import bulla" not in source
    assert "from bulla" not in source


def test_release_witness_contract_matches_the_actual_gate() -> None:
    composition = load_composition(
        ROOT / "examples/two-manifest-quickstart/example_fetch_memory_joint.yaml"
    )
    document = witness(diagnose(composition), composition).to_dict()
    assert set(document) == SIGNER.WITNESS_FIELDS
    preimage = {
        key: value
        for key, value in document.items()
        if key not in {"receipt_hash", "anchor_ref"}
    }
    assert (
        SIGNER.digest_json(preimage).removeprefix("sha256:")
        == document["receipt_hash"]
    )


def test_minimal_signer_binds_slot_summary_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "signed.json"
    SIGNER.sign_receipt(
        type(
            "Args",
            (),
            {
                **fixture,
                "receipt": fixture["unsigned"],
                "dist": fixture["candidate"],
                "out": output,
                "source_commit": fixture["commit"],
                "source_tree_sha256": fixture["tree"],
            },
        )()
    )
    signed = json.loads(output.read_text())
    assert signed["action"]["subject"]["release_slot_hash"]
    assert (
        signed["action"]["subject"]["release_signed_at"]
        == signed["timestamp"]
        != "2026-07-29T12:00:00+00:00"
    )
    assert "note" not in signed["producer"]
    verification = verify_receipt(signed)
    assert verification.ok
    assert verification.verified_to == "attestation"

    replay = tmp_path / "replayed.json"
    SIGNER.sign_receipt(
        type(
            "Args",
            (),
            {
                **fixture,
                "receipt": fixture["unsigned"],
                "dist": fixture["candidate"],
                "existing": output,
                "out": replay,
                "source_commit": fixture["commit"],
                "source_tree_sha256": fixture["tree"],
            },
        )()
    )
    assert replay.read_bytes() == output.read_bytes()

    tampered = json.loads(output.read_text())
    tampered["action"]["subject"]["release_signed_at"] = (
        "2026-07-29T12:00:00+00:00"
    )
    tampered_path = tmp_path / "tampered-existing.json"
    tampered_path.write_text(json.dumps(tampered))
    with pytest.raises(SIGNER.ReleaseSigningError):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "existing": tampered_path,
                    "out": tmp_path / "tampered-replay.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": fixture["tree"],
                },
            )()
        )

    witness_bytes = fixture["witness"].read_bytes()  # type: ignore[union-attr]
    witness = json.loads(witness_bytes)
    witness["fee"] += 1
    fixture["witness"].write_text(json.dumps(witness))  # type: ignore[union-attr]
    with pytest.raises(
        SIGNER.ReleaseSigningError,
        match="release diagnostic reference does not bind its sidecar",
    ):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "out": tmp_path / "tampered-witness.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": fixture["tree"],
                },
            )()
        )
    fixture["witness"].write_bytes(witness_bytes)  # type: ignore[union-attr]

    fixture["summary"].write_text("999999 passed\n")  # type: ignore[union-attr]
    with pytest.raises(SIGNER.ReleaseSigningError, match="action subject differs"):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "out": tmp_path / "forged.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": fixture["tree"],
                },
            )()
        )

    fixture["summary"].write_text("106 passed in 3.00s\n")  # type: ignore[union-attr]
    with pytest.raises(
        SIGNER.ReleaseSigningError,
        match="source tree digest differs from the signed release slot",
    ):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "out": tmp_path / "wrong-tree.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": "sha256:" + "c" * 64,
                },
            )()
        )


def test_minimal_signer_rejects_key_rotation_after_slot_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _key(monkeypatch)
    with pytest.raises(
        SIGNER.ReleaseSigningError,
        match="release signing key differs from release slot issuer",
    ):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "out": tmp_path / "wrong-key.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": fixture["tree"],
                },
            )()
        )


def test_minimal_signer_rejects_a_new_receipt_before_slot_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    slot = SIGNER.read_json(fixture["slot"])  # type: ignore[arg-type]
    opened = SIGNER.parse_time(slot["opened_at"], "opened_at")
    real_datetime = SIGNER.datetime

    class BehindSlotClock:
        @classmethod
        def now(cls, tz: object) -> object:
            return opened - timedelta(hours=1)

        fromisoformat = staticmethod(real_datetime.fromisoformat)

    monkeypatch.setattr(SIGNER, "datetime", BehindSlotClock)
    with pytest.raises(
        SIGNER.ReleaseSigningError,
        match="release receipt predates its signed slot",
    ):
        SIGNER.sign_receipt(
            type(
                "Args",
                (),
                {
                    **fixture,
                    "receipt": fixture["unsigned"],
                    "dist": fixture["candidate"],
                    "out": tmp_path / "predated.json",
                    "source_commit": fixture["commit"],
                    "source_tree_sha256": fixture["tree"],
                },
            )()
        )


def test_trust_registry_rotates_without_invalidating_historical_versions(
    tmp_path: Path,
) -> None:
    old = LocalEd25519Signer.generate()
    new = LocalEd25519Signer.generate()
    context = _context(
        old,
        status="retired",
        retired_after="0.44.2",
    )
    context["issuers"].append(
        {
            "id": "release-issuer-next",
            "issuer": new.issuer,
            "valid_from": "0.44.3",
            "retired_after": None,
            "revoked_from": None,
            "status": "active",
        }
    )
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    assert SIGNER.release_issuer(path, "0.44.2")["issuer"] == old.issuer
    assert SIGNER.release_issuer(path, "0.44.3")["issuer"] == new.issuer

    context["issuers"][1]["valid_from"] = "0.44.2"
    path.write_text(json.dumps(context))
    with pytest.raises(SIGNER.ReleaseSigningError, match="validity overlaps"):
        SIGNER.release_trust_context(path)


def test_trust_registry_revocation_preserves_prior_versions(
    tmp_path: Path,
) -> None:
    signer = LocalEd25519Signer.generate()
    context = _context(
        signer,
        status="revoked",
        revoked_from="0.44.3",
    )
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    assert SIGNER.release_issuer(path, "0.44.2")["issuer"] == signer.issuer
    with pytest.raises(SIGNER.ReleaseSigningError, match="0 accepted issuers"):
        SIGNER.release_issuer(path, "0.44.3")
