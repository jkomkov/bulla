from __future__ import annotations

import dataclasses

import pytest

from bulla.experimental.checkpoint import (
    CheckpointArchive,
    WitnessCheckpoint,
    issue_checkpoint,
    verify_checkpoint,
    verify_checkpoint_extension,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


def _deed(i: int) -> Deed:
    return Deed(
        issuer="did:key:zExample",
        content_hash=f"sha256:{i:064x}",
        attestation_hash=f"sha256:{(1000 + i):064x}",
    )


def test_signed_checkpoint_and_append_only_extension(tmp_path):
    signer = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "registry.jsonl")
    for i in range(3):
        log.append(_deed(i))
    old = issue_checkpoint(log, signer, log_id="log://pilot")
    for i in range(3, 7):
        log.append(_deed(i))
    new = issue_checkpoint(log, signer, log_id="log://pilot", previous=old)

    assert verify_checkpoint(old).ok
    assert verify_checkpoint(new).ok
    assert verify_checkpoint_extension(old, new, log.consistency(old.tree_size)).ok


def test_checkpoint_is_a_delegation_compatible_typed_position(tmp_path):
    signer = LocalEd25519Signer.generate()
    checkpoint = issue_checkpoint(DeedLog(tmp_path / "registry.jsonl"), signer, log_id="log://pilot")
    wire = checkpoint.to_dict()
    assert wire["ordering_domain"] == wire["ordering_domain"]
    assert {"domain": wire["ordering_domain"], "position": wire["position"]} == {
        "domain": checkpoint.ordering_domain,
        "position": 0,
    }


def test_cross_purpose_and_borrowed_operator_proofs_fail(tmp_path):
    signer = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "registry.jsonl")
    checkpoint = issue_checkpoint(log, signer, log_id="log://pilot")

    wrong_purpose = dataclasses.replace(
        checkpoint,
        proof=signer.sign_domain("authorization", checkpoint.checkpoint_hash),
    )
    assert verify_checkpoint(wrong_purpose).operator_authenticity == "invalid"

    borrowed = dataclasses.replace(
        checkpoint,
        proof=attacker.sign_domain("witness-checkpoint", checkpoint.checkpoint_hash),
    )
    assert verify_checkpoint(borrowed).operator_authenticity == "invalid"


def test_tamper_and_wrong_consistency_binding_fail(tmp_path):
    signer = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "registry.jsonl")
    log.append(_deed(0))
    old = issue_checkpoint(log, signer, log_id="log://pilot")
    log.append(_deed(1))
    new = issue_checkpoint(log, signer, log_id="log://pilot", previous=old)

    wire = new.to_dict()
    wire["root"] = "sha256:" + "00" * 32
    assert not verify_checkpoint(wire).ok

    consistency = log.consistency(old.tree_size)
    consistency["old_root"] = "sha256:" + "00" * 32
    assert not verify_checkpoint_extension(old, new, consistency).ok


def test_verification_dimensions_reject_boolean_coercion(tmp_path):
    signer = LocalEd25519Signer.generate()
    checkpoint = issue_checkpoint(DeedLog(tmp_path / "registry.jsonl"), signer, log_id="log://pilot")
    with pytest.raises(TypeError):
        bool(verify_checkpoint(checkpoint))


def test_checkpoint_archive_preserves_history_and_adjacent_proofs(tmp_path):
    signer = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "registry.jsonl")
    log.append(_deed(0))
    first = issue_checkpoint(log, signer, log_id="log://pilot")
    archive = CheckpointArchive(tmp_path / "checkpoints.jsonl")
    archive.append(first)
    log.append(_deed(1))
    second = issue_checkpoint(log, signer, log_id="log://pilot", previous=first)
    consistency = log.consistency(first.tree_size)
    archive.append(second, consistency_from_previous=consistency)

    reloaded = CheckpointArchive(tmp_path / "checkpoints.jsonl")
    assert reloaded.latest().checkpoint_hash == second.checkpoint_hash
    assert reloaded.get(first.checkpoint_hash).root == first.root
    assert reloaded.adjacent_consistency(first.checkpoint_hash, second.checkpoint_hash) == consistency

    attacker = LocalEd25519Signer.generate()
    bad = dataclasses.replace(
        second,
        proof=attacker.sign_domain("witness-checkpoint", second.checkpoint_hash),
    )
    with pytest.raises(ValueError, match="unauthenticated"):
        reloaded.append(bad, consistency_from_previous=consistency)
