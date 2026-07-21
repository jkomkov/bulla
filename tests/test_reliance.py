"""Reliance — RELY / REFUSE / ESCALATE, and why the split matters.

``decide`` is pure and crypto-free. The load-bearing distinction is the ROUTING of a
rejection: a CLEAR VIOLATION (forged, violates, broken, mismatch) forces REFUSE, while
an INCONCLUSIVE state (unresolved, unauthenticated, not_checkable) routes to ESCALATE —
the record is not adverse, it is merely undetermined, so it belongs in a forum queue,
not a rejection. That is forum-completeness on the consumer side.
"""

from __future__ import annotations

import pytest

from bulla.reliance import (
    ESCALATE,
    PRAGMATIC_RELIANCE_POLICY,
    REFUSE,
    RELY,
    RelianceError,
    ReliancePolicy,
    STRICT_RELIANCE_POLICY,
    decide,
)

# A fully-verified delegated, in-scope receipt view. temporal/revocation are unresolved
# BY CONSTRUCTION today (no checkpoint; transport unbuilt).
_GOOD = dict(
    ok=True, verified_to="attestation", authority_authentic="verified",
    effective_grounding=None, conventions={}, chain_integrity="verified",
    principal_binding="verified", policy_binding="verified", scope_binding="verified",
    temporal_status="unresolved", revocation_status="unresolved", bounds_conformance="conforms",
)


def test_pragmatic_relies_on_a_good_receipt():
    assert decide(_GOOD, PRAGMATIC_RELIANCE_POLICY).outcome == RELY


def test_strict_escalates_on_unresolved_not_refuses():
    """Unresolved temporal/revocation is inconclusive, not adverse — a forum question,
    so strict reliance ESCALATES rather than REFUSING."""
    d = decide(_GOOD, STRICT_RELIANCE_POLICY)
    assert d.outcome == ESCALATE
    assert {u["dimension"] for u in d.unmet} == {"temporal_status", "revocation_status"}
    assert all(u["routing"] == ESCALATE for u in d.unmet)


def test_over_scope_act_refuses():
    """bounds_conformance=violates is a CLEAR violation → REFUSE, even though the record
    is authentic and the chain conveyed the scope."""
    over = dict(_GOOD, bounds_conformance="violates")
    d = decide(over, PRAGMATIC_RELIANCE_POLICY)
    assert d.outcome == REFUSE
    assert any(u["dimension"] == "bounds_conformance" and u["routing"] == REFUSE for u in d.unmet)


def test_forged_authority_refuses():
    forged = dict(_GOOD, ok=False, authority_authentic="forged")
    assert decide(forged, PRAGMATIC_RELIANCE_POLICY).outcome == REFUSE


def test_wrong_principal_refuses():
    wp = dict(_GOOD, principal_binding="wrong_principal")
    assert decide(wp, PRAGMATIC_RELIANCE_POLICY).outcome == REFUSE


def test_scope_mismatch_refuses():
    sm = dict(_GOOD, scope_binding="mismatch")
    assert decide(sm, PRAGMATIC_RELIANCE_POLICY).outcome == REFUSE


def test_not_checkable_escalates():
    """A structured scope with no subject is undetermined, not adverse → ESCALATE."""
    nc = dict(_GOOD, bounds_conformance="not_checkable")
    d = decide(nc, PRAGMATIC_RELIANCE_POLICY)
    assert d.outcome == ESCALATE


def test_mixed_negative_and_ambiguous_refuses():
    """A single clear violation dominates: REFUSE wins over ESCALATE."""
    mixed = dict(_GOOD, bounds_conformance="violates", temporal_status="unresolved")
    assert decide(mixed, STRICT_RELIANCE_POLICY).outcome == REFUSE


def test_rung_floor_refuses_below_attestation():
    unsigned = dict(_GOOD, verified_to="digest", authority_authentic="unauthenticated")
    # min_verified_to defaults to "attestation"; a digest-only receipt is below the floor
    assert decide(unsigned, PRAGMATIC_RELIANCE_POLICY).outcome == REFUSE


def test_unauthenticated_escalates_when_rung_allows():
    view = dict(_GOOD, authority_authentic="unauthenticated")
    lax = ReliancePolicy(name="lax", min_verified_to="digest",
                         authority_authentic=None, temporal_status=None, revocation_status=None)
    # authority not enforced, but default authority set would flag it; here it's None → RELY
    assert decide(view, lax).outcome == RELY


def test_convention_violation_refuses():
    view = dict(_GOOD, conventions={"amount-in-usd-cents": "violates"})
    assert decide(view, PRAGMATIC_RELIANCE_POLICY).outcome == REFUSE


def test_accepting_unresolved_revocation_is_expressible_and_recorded():
    """The unbuilt-revocation gap becomes a declared, recorded choice — the policy that
    accepts it is a distinct, hashable object."""
    assert "unresolved" in PRAGMATIC_RELIANCE_POLICY.revocation_status
    assert "unresolved" not in STRICT_RELIANCE_POLICY.revocation_status
    assert PRAGMATIC_RELIANCE_POLICY.policy_hash != STRICT_RELIANCE_POLICY.policy_hash


def test_policy_hash_is_stable_and_pinnable():
    p = ReliancePolicy(name="payments.v1", bounds_conformance=("conforms",))
    assert p.policy_hash == p.policy_hash            # deterministic
    assert p.policy_hash.startswith("sha256:")
    # a different threshold → a different pin
    p2 = ReliancePolicy(name="payments.v1", bounds_conformance=("conforms", "not_applicable"))
    assert p.policy_hash != p2.policy_hash


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "line\nbreak"},
        {"name": "bad-rung", "min_verified_to": "maybe"},
        {"name": "empty-values", "scope_binding": ()},
        {"name": "unknown-value", "scope_binding": ("verified", "invented")},
        {"name": "non-string-value", "scope_binding": ("verified", 1)},
        {"name": "duplicates", "scope_binding": ("verified", "verified")},
    ],
)
def test_policy_validation_fails_closed(kwargs):
    with pytest.raises(RelianceError):
        ReliancePolicy(**kwargs)


def test_policy_is_mandatory():
    with pytest.raises(TypeError):
        decide(_GOOD)


def test_incomplete_view_is_malformed_not_not_applicable():
    truncated = dict(_GOOD)
    del truncated["scope_binding"]
    with pytest.raises(RelianceError, match="incomplete"):
        decide(truncated, PRAGMATIC_RELIANCE_POLICY)


def test_unknown_view_value_is_malformed():
    malformed = dict(_GOOD, revocation_status="probably-fine")
    with pytest.raises(RelianceError, match="unknown value"):
        decide(malformed, PRAGMATIC_RELIANCE_POLICY)


def test_non_string_view_value_is_malformed():
    malformed = dict(_GOOD, revocation_status={"looks": "valid"})
    with pytest.raises(RelianceError, match="unknown value"):
        decide(malformed, PRAGMATIC_RELIANCE_POLICY)


def test_decision_has_no_boolean_truth_value():
    d = decide(_GOOD, PRAGMATIC_RELIANCE_POLICY)
    assert d.outcome == RELY
    with pytest.raises(TypeError, match="outcome"):
        bool(d)


def test_decide_accepts_receipt_verification_object():
    """decide() takes either a view dict or a ReceiptVerification (via its to_dict)."""
    from bulla.action_receipt import verify_receipt
    bad = {"kind": "action_receipt", "schema_version": "0.2", "action": {"type": "x"},
           "diagnostic_ref": {"status": "not_applicable"}, "hashes": {}}
    v = verify_receipt(bad)              # ok is False
    d = decide(v, PRAGMATIC_RELIANCE_POLICY)   # must NOT evaluate bool(v) internally
    assert d.outcome == REFUSE
