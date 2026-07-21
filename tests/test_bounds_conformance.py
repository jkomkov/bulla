"""bounds_conformance — the missing half of authorization, without a lattice.

`scope_binding` (delegation) proves the chain CONVEYED scope S — hash agreement.
`bounds_conformance` proves the act was WITHIN S — a crypto-free recompute of
`action.subject` against a structured `jsonschema+quantum/1` `bounds.scope`
predicate, reusing the convention evaluator verbatim. The two are independent, and
authorization is the conjunction: conveyed AND obeyed.

The load-bearing test is `test_over_scope_act_is_surfaced_not_folded`: an act that
exceeds its scope must still produce a valid RECORD (`ok=True`, authority verified) —
the violation is surfaced for a relying party to act on, never folded into integrity.
"""

from __future__ import annotations

import pytest

from bulla.action_receipt import build_tool_call_receipt, sign_action_receipt, verify_receipt
from bulla.delegation import DelegationGrant, hash_ref, sign_grant
from bulla.envelope import (
    Authority, Bounds, EnvelopeError, Forum, Recourse, RecourseEnvelope, Remedy,
)
from bulla.executable_form import definition_hash

_HAS_NACL = True
try:
    from bulla.identity import LocalEd25519Signer
except Exception:  # pragma: no cover
    _HAS_NACL = False

pytestmark = pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")

POL = "policy://payments@sha256:aa"
STRUCTURED_SCOPE = {
    "form": "jsonschema+quantum/1",
    "schema": {
        "type": "object",
        "properties": {"amount": {"type": "integer", "minimum": 0, "maximum": 100000}},
    },
}


def _key(n):
    return LocalEd25519Signer(seed=bytes([n]) + bytes(31))


def _receipt(scope, subject, *, deed_schema="0.3", with_grant=True):
    P, L = _key(1), _key(3)
    pd = hash_ref(POL)
    sd = definition_hash(scope)   # polymorphic pin — str→UTF-8, dict→canonical
    delegation = ()
    if with_grant:
        g0 = sign_grant(DelegationGrant(P.verification_method, L.verification_method,
                                        P.verification_method, None, pd, sd), P)
        delegation = (g0.to_dict(),)
    env = RecourseEnvelope(
        authority=Authority(principal=P.verification_method, policy=POL, delegation=delegation),
        bounds=Bounds(scope=scope),
        recourse=Recourse(challenge_window="P7D",
                          forum=Forum(log_endpoint="https://l", trusted_root_ref="ots:r"),
                          remedies=(Remedy("recompute", "v", "the receipt"),
                                    Remedy("escalate", "r", P.verification_method))),
        deed_schema=deed_schema,
    )
    r = build_tool_call_receipt(tool="payments.charge", call_subject=subject,
                                diagnostic_ref={"status": "reference", "ref": "sha256:g"}, envelope=env)
    return sign_action_receipt(r, L).to_dict()


def test_in_scope_act_conforms():
    v = verify_receipt(_receipt(STRUCTURED_SCOPE, {"amount": 1250}))
    assert v.bounds_conformance == "conforms"
    assert v.scope_binding == "verified"   # chain conveyed the scope
    assert v.ok is True


def test_over_scope_act_is_surfaced_not_folded():
    """THE separability property. An act exceeding its scope is a valid RECORD; the
    violation is a surfaced verdict about the act, not a hit to record integrity."""
    v = verify_receipt(_receipt(STRUCTURED_SCOPE, {"amount": 999999}))
    assert v.bounds_conformance == "violates"
    assert v.ok is True                        # ← the record is still authentic…
    assert v.authority_authentic == "verified"
    assert v.scope_binding == "verified"       # ← and the chain still conveyed scope S…
    # …so authorization = scope_binding verified AND bounds_conformance conforms.
    # A relying party refuses on bounds_conformance, not on ok.


def test_prose_scope_is_not_applicable():
    """Backward compatibility: a prose scope has no predicate to recompute."""
    v = verify_receipt(_receipt("payments.charge amount<=100000", {"amount": 999999}))
    assert v.bounds_conformance == "not_applicable"
    assert v.ok is True


def test_type_mismatch_violates():
    v = verify_receipt(_receipt(STRUCTURED_SCOPE, {"amount": "not-an-int"}))
    assert v.bounds_conformance == "violates"


def test_structured_scope_requires_deed_schema_0_3():
    with pytest.raises(EnvelopeError, match="deed_schema"):
        RecourseEnvelope(
            authority=Authority(principal="did:key:zX", policy=POL),
            bounds=Bounds(scope=STRUCTURED_SCOPE),
            deed_schema="0.2",
        )


def test_malformed_structured_scope_rejected_at_construction():
    with pytest.raises(EnvelopeError):
        Bounds(scope={"form": "jsonschema+quantum/1", "schema": {"properties": {
            "amount": {"type": "not-a-type"}}}})
    with pytest.raises(EnvelopeError):
        Bounds(scope={"form": "wrong-form", "schema": {"type": "object"}})


def test_non_str_non_dict_scope_rejected():
    with pytest.raises(EnvelopeError, match="must be a non-empty string"):
        Bounds(scope=42)


def test_prose_scope_hashes_byte_identically():
    """The reuse guarantee: a prose scope's pin is unchanged, so the entire existing
    vector corpus survives the str|dict widening untouched."""
    from bulla.delegation import hash_ref as _hr
    assert definition_hash("payments.charge amount<=100000") == _hr("payments.charge amount<=100000")
