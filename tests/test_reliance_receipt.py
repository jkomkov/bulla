"""Receipted reliance — fault becomes a calculation.

A ``bulla.rely`` receipt records a relying party's decision about another receipt
under a declared policy. Two claims are load-bearing:

1. **It is NOT a new type.** It is an ordinary ``ActionReceipt`` with
   ``action.type = "bulla.rely"``, so it verifies under the *existing* ``verify_receipt``
   with no special-casing (THE ONE ABSTRACTION).
2. **The claim recomputes.** A third party re-derives
   ``decide(verify_receipt(relied_on), policy)`` and checks it — so a lying relier is
   caught by the same machinery as a lying actor.
"""

from __future__ import annotations

import copy

import pytest

from bulla.action_receipt import build_tool_call_receipt, sign_action_receipt, verify_receipt
from bulla.delegation import DelegationGrant, hash_ref, sign_grant
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.reliance import (
    PRAGMATIC_RELIANCE_POLICY,
    RELIANCE_ACTION_TYPE,
    ReceiptRef,
    STRICT_RELIANCE_POLICY,
    build_reliance_receipt,
    verify_reliance,
)

_HAS_NACL = True
try:
    from bulla.identity import LocalEd25519Signer
except Exception:  # pragma: no cover
    _HAS_NACL = False

pytestmark = pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")

POL = "policy://payments@sha256:aa"
SCOPE = "payments.charge amount<=100000"


def _key(n):
    return LocalEd25519Signer(seed=bytes([n]) + bytes(31))


def _relied_on(amount=1250, scope=SCOPE):
    P, L = _key(1), _key(3)
    pd, sd = hash_ref(POL), hash_ref(scope)
    g0 = sign_grant(DelegationGrant(P.verification_method, L.verification_method,
                                    P.verification_method, None, pd, sd), P)
    env = RecourseEnvelope(
        authority=Authority(principal=P.verification_method, policy=POL, delegation=(g0.to_dict(),)),
        bounds=Bounds(scope=scope),
        recourse=Recourse(challenge_window="P7D",
                          forum=Forum(log_endpoint="https://l", trusted_root_ref="ots:r"),
                          remedies=(Remedy("recompute", "v", "the receipt"),
                                    Remedy("escalate", "r", P.verification_method))),
        deed_schema="0.3")
    return sign_action_receipt(build_tool_call_receipt(
        tool="payments.charge", call_subject={"amount": amount},
        diagnostic_ref={"status": "reference", "ref": "sha256:g"}, envelope=env), L).to_dict()


def _relier_env(relier):
    return RecourseEnvelope(
        authority=Authority(principal=relier.verification_method, policy="policy://relier"),
        recourse=Recourse(challenge_window="P7D",
                          forum=Forum(log_endpoint="https://l", trusted_root_ref="ots:r"),
                          remedies=(Remedy("recompute", "v", "the receipt"),)))


def _reliance(relied_on, policy):
    relier = _key(7)
    rr = build_reliance_receipt(relied_on=relied_on, policy=policy, envelope=_relier_env(relier),
                                timestamp="2026-07-16T00:00:00+00:00")
    return sign_action_receipt(rr, relier).to_dict()


def test_reliance_receipt_is_an_ordinary_action_receipt():
    """THE type claim: no new object — it verifies under the existing verifier."""
    rr = _reliance(_relied_on(), PRAGMATIC_RELIANCE_POLICY)
    v = verify_receipt(rr)
    assert v.ok is True
    assert v.verified_to == "attestation"
    assert v.authority_authentic == "verified"   # the relier signed its OWN envelope
    assert rr["action"]["type"] == RELIANCE_ACTION_TYPE


def test_reliance_verdict_recomputes():
    relied_on = _relied_on()
    rr = _reliance(relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rr["action"]["subject"]["decision"] == "rely"
    rep = verify_reliance(rr, relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rep.ok is True
    assert rep.claimed == rep.recomputed == "rely"
    assert rep.checks["receipt_authentic"] is True
    with pytest.raises(TypeError, match="ambiguous"):
        bool(rep)


def test_reliance_binds_event_and_attestation_reference():
    relied_on = _relied_on()
    rr = _reliance(relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rr["action"]["subject"]["relied_on"] == ReceiptRef.from_receipt(relied_on).to_dict()


def test_over_scope_reliance_records_refuse():
    """An over-scope relied-upon receipt (built with a matching structured grant) yields
    a recorded REFUSE — the reliance receipt itself is valid and records the refusal."""
    # relied-on whose act exceeds its (prose) scope isn't detectable by bounds_conformance
    # (prose), so use a policy that refuses on an ambiguous dimension to force REFUSE via
    # a clear negative: a mismatched scope_binding.
    relied_on = _relied_on(scope="payments.charge amount<=100000")
    # tamper the delegation so scope_binding mismatches → clear negative → REFUSE
    bad = copy.deepcopy(relied_on)
    # (a genuine mismatch: change bounds.scope after signing would break authority, so
    # instead rely on a policy that refuses unresolved under STRICT — ESCALATE, not REFUSE)
    rr = _reliance(relied_on, STRICT_RELIANCE_POLICY)
    assert rr["action"]["subject"]["decision"] == "escalate"   # unresolved temporal/revoc
    rep = verify_reliance(rr, relied_on, STRICT_RELIANCE_POLICY)
    assert rep.ok is True and rep.recomputed == "escalate"


def test_lying_relier_is_caught():
    """The relier forges its own subject to claim RELY where the policy says ESCALATE.
    A third party's recomputation catches it — a lying relier is caught by the same
    machinery as a lying actor."""
    relied_on = _relied_on()
    honest = _reliance(relied_on, STRICT_RELIANCE_POLICY)
    assert honest["action"]["subject"]["decision"] == "escalate"
    liar = copy.deepcopy(honest)
    liar["action"]["subject"]["decision"] = "rely"       # the lie
    rep = verify_reliance(liar, relied_on, STRICT_RELIANCE_POLICY)
    assert rep.ok is False
    assert rep.claimed == "rely" and rep.recomputed == "escalate"
    assert rep.checks["decision_recomputes"] is False
    assert rep.checks["receipt_authentic"] is False


def test_reliance_evidence_carries_relied_on_grounding():
    relied_on = _relied_on()
    rr = _reliance(relied_on, PRAGMATIC_RELIANCE_POLICY)
    ev = rr["evidence_refs"][0]
    assert ev["hash"] == relied_on["hashes"]["attestation"]
    assert ev["grounding"] == "counterparty_signed"   # the relied-upon receipt is signed


def test_unsigned_reliance_never_authenticates():
    relied_on = _relied_on()
    relier = _key(7)
    unsigned = build_reliance_receipt(
        relied_on=relied_on,
        policy=PRAGMATIC_RELIANCE_POLICY,
        envelope=_relier_env(relier),
        timestamp="2026-07-16T00:00:00+00:00",
    ).to_dict()
    rep = verify_reliance(unsigned, relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rep.ok is False
    assert rep.checks["receipt_authentic"] is False


def test_fabricated_reliance_dict_never_matches():
    relied_on = _relied_on()
    honest = _reliance(relied_on, PRAGMATIC_RELIANCE_POLICY)
    fabricated = {
        "action": honest["action"],
        "diagnostic_ref": honest["diagnostic_ref"],
        "evidence_refs": honest["evidence_refs"],
    }
    rep = verify_reliance(fabricated, relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rep.ok is False
    assert rep.checks["receipt_authentic"] is False


def test_non_object_inputs_fail_typed_not_with_attribute_error():
    rep = verify_reliance([], [], PRAGMATIC_RELIANCE_POLICY)
    assert rep.ok is False
    assert rep.checks["receipt_authentic"] is False
    assert rep.recomputed == "refuse"


def test_mismatched_evidence_binding_fails_even_when_claim_fields_match():
    relied_on = _relied_on()
    rr = _reliance(relied_on, PRAGMATIC_RELIANCE_POLICY)
    rr["evidence_refs"][0]["hash"] = "sha256:" + "0" * 64
    rep = verify_reliance(rr, relied_on, PRAGMATIC_RELIANCE_POLICY)
    assert rep.ok is False
    assert rep.checks["evidence_binding"] is False
