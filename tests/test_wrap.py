"""wrap_action: the ergonomic receipt wrapper (context manager + decorator)."""

from __future__ import annotations

from copy import deepcopy

import pytest

from bulla.action_receipt import verify_receipt
from bulla.wrap import operational_envelope, receipt_for, wrap_action


def test_context_manager_emits_verifiable_receipt() -> None:
    with wrap_action("payments.charge", {"amount": 200, "currency": "USD"}) as act:
        act.set_result("sha256:" + "a" * 64)
        act.add_evidence("counterparty_ack", "sha256:" + "b" * 64, "counterparty_signed")
    assert act.receipt is not None
    verdict = verify_receipt(act.receipt)
    assert verdict.ok
    assert act.receipt["action"]["type"] == "payments.charge"
    assert act.receipt["producer"]["outcome"] == "ok"
    assert act.receipt["action"]["outcome"] == {
        "status": "ok",
        "result_hash": "sha256:" + "a" * 64,
    }


def test_one_call_convenience() -> None:
    receipt = receipt_for("tool.call", {"tool": "github.create_file"},
                          principal="did:web:acme#agent")
    assert verify_receipt(receipt).ok
    assert receipt["mandate"]["authority"]["principal"] == "did:web:acme#agent"


def test_decorator_emits_receipt_per_call() -> None:
    scope = wrap_action("tool.call", {"tool": "search"})

    @scope
    def do_search(q: str) -> str:
        return f"results:{q}"

    assert do_search("cats") == "results:cats"
    assert scope.last_receipt is not None
    assert do_search.last_receipt == scope.last_receipt
    assert verify_receipt(scope.last_receipt).ok
    assert scope.last_receipt["producer"]["outcome"] == "ok"


def test_receipt_emitted_even_when_body_raises() -> None:
    with pytest.raises(ValueError):
        with wrap_action("db.write", {"table": "accounts"}) as act:
            raise ValueError("boom")
    assert act.receipt is not None
    assert act.receipt["producer"]["outcome"] == "error"
    assert act.receipt["producer"]["error_type"] == "ValueError"
    assert "boom" not in str(act.receipt)
    assert act.receipt["action"]["outcome"] == {
        "status": "error",
        "error_type": "ValueError",
    }
    assert verify_receipt(act.receipt).ok  # a failed action still leaves a valid receipt


def test_decorator_surfaces_error_receipt_on_callable() -> None:
    scope = wrap_action("db.write", {"table": "accounts"})

    @scope
    def fail() -> None:
        raise RuntimeError("credential=do-not-serialize")

    with pytest.raises(RuntimeError):
        fail()
    assert fail.last_receipt is not None
    assert fail.last_receipt == scope.last_receipt
    assert fail.last_receipt["action"]["outcome"]["status"] == "error"
    assert "do-not-serialize" not in str(fail.last_receipt)


def test_outcome_and_result_are_content_bound() -> None:
    with wrap_action("tool.call", {"tool": "search"}) as act:
        act.set_result("sha256:" + "c" * 64)
    assert act.receipt is not None

    tampered_outcome = deepcopy(act.receipt)
    tampered_outcome["action"]["outcome"]["status"] = "error"
    assert not verify_receipt(tampered_outcome).ok

    tampered_result = deepcopy(act.receipt)
    tampered_result["action"]["outcome"]["result_hash"] = (
        "sha256:" + "d" * 64
    )
    assert not verify_receipt(tampered_result).ok


def test_result_hash_is_validated() -> None:
    with wrap_action("tool.call") as act:
        with pytest.raises(ValueError, match="sha256"):
            act.set_result("not-a-digest")


def test_wrapper_outcome_does_not_pollute_closed_bounds_subject() -> None:
    scope = {
        "form": "jsonschema+quantum/1",
        "schema": {
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
        "quantum": {},
    }
    receipt = receipt_for("payments.charge", {"amount": 200}, scope=scope)
    verdict = verify_receipt(receipt)
    assert verdict.bounds_conformance == "conforms"
    assert receipt["action"]["subject"] == {"amount": 200}
    assert receipt["action"]["outcome"] == {"status": "ok"}


def test_preset_defaults_are_overridable() -> None:
    env = operational_envelope(
        principal="did:key:zP",
        policy="policy://strict@sha256:aa",
        scope="payments.charge amount<=100000",
        forum_endpoint="https://log.example",
        forum_root="ots:root",
    )
    d = env.to_dict()
    assert d["authority"]["principal"] == "did:key:zP"
    assert d["recourse"]["forum"]["log_endpoint"] == "https://log.example"


def test_signed_receipt_reaches_attestation_rung() -> None:
    identity = pytest.importorskip("bulla.identity")
    signer = identity.LocalEd25519Signer.generate()
    receipt = receipt_for("network.egress", {"destination": "internal-cache"},
                          principal="did:key:zP", policy="policy://eval@sha256:aa",
                          signer=signer)
    verdict = verify_receipt(receipt)
    assert verdict.ok
    assert verdict.verified_to == "attestation"
