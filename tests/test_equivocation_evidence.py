"""The one 0.44 witness-adjacent primitive: objective equivocation evidence."""

from __future__ import annotations

from datetime import datetime, timezone

from bulla.experimental.equivocation import log_head_hash, verify_equivocation_evidence
from bulla.identity import LocalEd25519Signer


def _head(signer: LocalEd25519Signer, *, root: str, size: int = 7, log_id: str = "log:alpha") -> dict:
    head = {
        "operator_id": signer.issuer,
        "log_id": log_id,
        "tree_size": size,
        "root": root,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    head["signature"] = signer.sign(log_head_hash({**head, "signature": {}}))
    return head


def _evidence(a: dict, b: dict) -> dict:
    return {
        "kind": "equivocation_evidence",
        "version": "0.1-experimental",
        "head_a": a,
        "head_b": b,
    }


def test_authentic_same_size_different_roots_establishes_equivocation():
    signer = LocalEd25519Signer.generate()
    result = verify_equivocation_evidence(
        _evidence(_head(signer, root="sha256:" + "a" * 64), _head(signer, root="sha256:" + "b" * 64))
    )
    assert result["equivocation"] is True
    assert all(result["checks"].values())


def test_different_size_is_not_equivocation_evidence():
    signer = LocalEd25519Signer.generate()
    result = verify_equivocation_evidence(
        _evidence(
            _head(signer, root="sha256:" + "a" * 64, size=7),
            _head(signer, root="sha256:" + "b" * 64, size=8),
        )
    )
    assert result["equivocation"] is False
    assert result["checks"]["same_tree_size"] is False


def test_cross_operator_heads_do_not_convict():
    a = LocalEd25519Signer.generate()
    b = LocalEd25519Signer.generate()
    result = verify_equivocation_evidence(
        _evidence(_head(a, root="sha256:" + "a" * 64), _head(b, root="sha256:" + "b" * 64))
    )
    assert result["equivocation"] is False
    assert result["checks"]["same_operator"] is False


def test_cross_log_heads_do_not_convict():
    signer = LocalEd25519Signer.generate()
    result = verify_equivocation_evidence(
        _evidence(
            _head(signer, root="sha256:" + "a" * 64, log_id="log:alpha"),
            _head(signer, root="sha256:" + "b" * 64, log_id="log:beta"),
        )
    )
    assert result["equivocation"] is False
    assert result["checks"]["same_log"] is False


def test_same_root_heads_do_not_convict():
    signer = LocalEd25519Signer.generate()
    root = "sha256:" + "a" * 64
    result = verify_equivocation_evidence(
        _evidence(_head(signer, root=root), _head(signer, root=root))
    )
    assert result["equivocation"] is False
    assert result["checks"]["different_roots"] is False


def test_forged_signature_does_not_convict():
    signer = LocalEd25519Signer.generate()
    head_a = _head(signer, root="sha256:" + "a" * 64)
    head_b = _head(signer, root="sha256:" + "b" * 64)
    head_b["root"] = "sha256:" + "c" * 64
    result = verify_equivocation_evidence(_evidence(head_a, head_b))
    assert result["equivocation"] is False
    assert result["checks"]["head_b_signature"] is False


def test_malformed_or_unsigned_input_does_not_convict():
    result = verify_equivocation_evidence(
        {"kind": "equivocation_evidence", "version": "0.1-experimental", "head_a": {}, "head_b": {}}
    )
    assert result["ok"] is False
    assert result["equivocation"] is False
