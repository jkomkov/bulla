"""Verdict objects have NO boolean truth value — the R0 footgun, pinned shut.

A plain Python object is always truthy, so before this guard
``if verify_receipt(d): ship()`` was unconditionally taken — it *never consulted
``.ok``* and shipped receipts that had FAILED verification. The fix is numpy's:
make ``__bool__`` raise, so the ambiguous read becomes a loud error at first test
rather than a silent always-accept in production. These tests fail if any verdict
object regains an implicit truth value.
"""

from __future__ import annotations

import pytest

from bulla.action_receipt import verify_receipt
from bulla.delegation import verify_delegation
from bulla.identity import Authenticity
from bulla.recourse_gate import GateDecision
from bulla.reliance import RelianceVerification

_FAILING_RECEIPT = {
    "kind": "action_receipt",
    "schema_version": "0.2",
    "action": {"type": "x"},
    "diagnostic_ref": {"status": "not_applicable"},
    "hashes": {},  # missing hashes → verification fails outright
}


def test_receipt_verification_bool_raises_and_ok_still_false():
    v = verify_receipt(_FAILING_RECEIPT)
    assert v.ok is False                       # the honest answer is reachable…
    with pytest.raises(TypeError, match="ambiguous"):
        bool(v)                                # …but the ambiguous shortcut is refused
    with pytest.raises(TypeError):
        if v:                                  # the exact footgun: `if verify_receipt(d):`
            pass


def test_authenticity_bool_raises():
    a = Authenticity(authentic=False, method="did:key", issuer="did:key:zX", detail="nope")
    assert a.authentic is False
    with pytest.raises(TypeError, match="authentic"):
        bool(a)


def test_delegation_verdict_bool_raises():
    v = verify_delegation([], principal="github:x", policy_ref="p",
                          scope_ref="s", leaf_verification_method=None)
    # every dimension is not_applicable/unresolved here — definitely not "success"
    assert v.cryptographically_bound is False
    with pytest.raises(TypeError, match="independent"):
        bool(v)


def test_gate_decision_bool_raises():
    d = GateDecision(disposition="refuse_pending_disclosure", deficiency="INAUTHENTIC",
                     root_trust="none", fee=None, reason="test")
    assert d.proceed is False
    with pytest.raises(TypeError, match="proceed"):
        bool(d)


def test_reliance_verification_bool_raises():
    receipt = verify_receipt(_FAILING_RECEIPT)
    report = RelianceVerification(
        ok=False,
        claimed=None,
        recomputed=None,
        checks={},
        reasons=("test",),
        receipt_verification=receipt,
    )
    with pytest.raises(TypeError, match="ambiguous"):
        bool(report)
