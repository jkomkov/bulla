"""ActionReceipt witness pilot: verified intake, proofs, heads, and the
injected split-view equivocation drill."""

from __future__ import annotations

import copy
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from bulla.experimental.checkpoint import (
    verify_checkpoint,
    verify_checkpoint_extension,
)
from bulla.experimental.receipt_witness import (
    IntakeRefused,
    ReceiptWitness,
    make_witness_server,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import classify_root_trust, verify_inclusion_record

_VECTORS = Path(__file__).resolve().parents[1] / "spec" / "vectors"


def _witness(tmp_path: Path, name: str) -> ReceiptWitness:
    return ReceiptWitness(
        operator_signer=LocalEd25519Signer(seed=name.encode().ljust(32, b"\0")),
        log_id=f"receipt-witness-{name}",
        log_path=tmp_path / name / "log.jsonl",
        store_path=tmp_path / name / "store.jsonl",
    )


def _vector(name: str) -> bytes:
    return (_VECTORS / name).read_bytes()


def test_intake_verifies_and_returns_received_at(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    result = w.intake(_vector("signed-authorized.json"))
    assert result["verified_to"] in ("digest", "attestation")
    assert result["received_at"].endswith("Z")
    assert result["tree_size"] == 1
    assert result["attestation_hash"].startswith("sha256:")


def test_intake_fails_closed_on_tampered_receipt(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    doc = json.loads(_vector("signed-authorized.json"))
    doc["action"]["type"] = "payments.charge"  # break the content hash
    with pytest.raises(IntakeRefused):
        w.intake(json.dumps(doc).encode())
    assert len(w) == 0  # nothing entered the log


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda raw: raw.replace(
                b'"schema_version": "0.3"',
                b'"schema_version": "0.3", "schema_version": "0.3"',
                1,
            ),
            "duplicate JSON member",
        ),
        (
            lambda raw: raw.rstrip()[:-1] + b', "ambient_authority": true}',
            "unknown fields",
        ),
        (
            lambda raw: raw.rstrip()[:-1] + b', "ambient_authority": NaN}',
            "non-finite JSON number",
        ),
        (
            lambda raw: raw.replace(
                b'"github.create_file"', b'"github.\\ud800create_file"', 1
            ),
            "lone Unicode surrogates",
        ),
    ],
)
def test_intake_uses_strict_byte_parser(
    tmp_path: Path,
    mutate,
    reason: str,
) -> None:
    w = _witness(tmp_path, "a")
    raw = mutate(_vector("signed-authorized.json"))

    with pytest.raises(IntakeRefused, match=reason):
        w.intake(raw)

    assert len(w) == 0


def test_intake_enforces_resource_limits(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")

    with pytest.raises(IntakeRefused, match="exceeds 1048576 bytes"):
        w.intake(b" " * 1_048_577)

    assert len(w) == 0


def test_intake_is_idempotent_and_preserves_exact_bytes(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    raw = _vector("signed-authorized.json")
    first = w.intake(raw)
    second = w.intake(raw)
    assert second.get("duplicate") is True
    assert len(w) == 1
    assert w.receipt_bytes(first["attestation_hash"]) == raw


def test_inclusion_proof_verifies_against_local_root(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    result = w.intake(_vector("signed-authorized.json"))
    w.intake(_vector("reliance-rely.json"))
    record = w.inclusion(result["attestation_hash"])
    assert record is not None
    assert record["received_at"] == result["received_at"]
    assert verify_inclusion_record(record, trusted_root=w.root())


def test_checkpoint_chain_and_extension(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    w.intake(_vector("signed-authorized.json"))
    head1 = w.checkpoint()
    assert verify_checkpoint(head1).ok
    w.intake(_vector("reliance-rely.json"))
    head2 = w.checkpoint()
    consistency = w.consistency(head1.tree_size)
    extension = verify_checkpoint_extension(head1, head2, consistency)
    assert extension.ok


def test_two_instances_same_cargo_agree(tmp_path: Path) -> None:
    a = _witness(tmp_path, "a")
    b = _witness(tmp_path, "b")
    for name in ("signed-authorized.json", "reliance-rely.json"):
        a.intake(_vector(name))
        b.intake(_vector(name))
    assert a.root() == b.root()
    # Different operators sign different heads over the same tree.
    assert a.checkpoint().root == b.checkpoint().root
    assert a.checkpoint().operator != b.checkpoint().operator


def test_split_view_drill_exposes_equivocation(tmp_path: Path) -> None:
    """Injected split view: two instances share a prefix, then diverge at the
    same size. The mismatch must be detectable from served data."""
    a = _witness(tmp_path, "a")
    b = _witness(tmp_path, "b")
    a.intake(_vector("signed-authorized.json"))
    b.intake(_vector("signed-authorized.json"))
    # Divergence: each appends a different second receipt.
    a.intake(_vector("reliance-rely.json"))
    b.intake(_vector("delegated-receipt.json"))
    assert len(a) == len(b)
    assert a.root() != b.root()

    # A relier pinning A's root classifies B's served root as a mismatch.
    label, trusted = classify_root_trust(True, b.root(), a.root(), None)
    assert label == "mismatch"
    assert not trusted

    # And the checkpoint layer cannot present B's head as an extension of A's.
    head_a = a.checkpoint()
    head_b = b.checkpoint()
    assert head_a.root != head_b.root and head_a.tree_size == head_b.tree_size
    extension = verify_checkpoint_extension(head_a, head_b, b.consistency(head_a.tree_size))
    assert not extension.ok


def test_release_receipts_as_first_cargo(tmp_path: Path) -> None:
    """The pilot's first real cargo: the project's own contemporaneous
    release receipts."""
    releases = Path(__file__).resolve().parents[1] / "releases"
    w = _witness(tmp_path, "a")
    for version in ("0.44.0", "0.44.1"):
        result = w.intake((releases / f"{version}.json").read_bytes())
        assert result["verified_to"] == "attestation"
    assert len(w) == 2
    head = w.checkpoint()
    assert head.tree_size == 2 and verify_checkpoint(head).ok


def test_http_surface_intake_and_proofs(tmp_path: Path) -> None:
    w = _witness(tmp_path, "a")
    server = make_witness_server(w)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw = _vector("signed-authorized.json")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/intake", data=raw, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            accepted = json.loads(resp.read())
        assert accepted["tree_size"] == 1

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/inclusion?attestation={accepted['attestation_hash']}"
        ) as resp:
            record = json.loads(resp.read())
        assert verify_inclusion_record(record, trusted_root=w.root())

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest") as resp:
            manifest = json.loads(resp.read())
        assert "NOT an independent witness" in manifest["control_domain"]

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/intake", data=b"{}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        assert err.value.code == 422
    finally:
        server.shutdown()
