"""did:key delegation — the attack matrix as first-class tests.

The six verdict dimensions (chain_integrity, principal_binding, policy_binding,
scope_binding, temporal_status, revocation_status) are INDEPENDENT: a chain can be structurally broken while its
endpoints are correct, and vice versa. Each test pins one hostile construction to
the dimension that must catch it. `policy_binding=verified` is hash agreement,
never authorization — a distinct test pins that boundary.
"""

from __future__ import annotations

import dataclasses

import pytest

from bulla.delegation import (
    DelegationGrant,
    DelegationError,
    MAX_DEPTH,
    hash_ref,
    sign_grant,
    verify_delegation,
)

_HAS_NACL = True
try:
    from bulla.identity import LocalEd25519Signer
except Exception:  # pragma: no cover
    _HAS_NACL = False

pytestmark = pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")

POL = "policy://payments@sha256:aa"
PD = hash_ref(POL)
SCOPE = "scope:charge<=100000"
SD = hash_ref(SCOPE)


def _key(n: int) -> "LocalEd25519Signer":
    return LocalEd25519Signer(seed=bytes([n]) + bytes(31))


def _chain(P, M, L):
    g0 = sign_grant(DelegationGrant(P.verification_method, M.verification_method, P.verification_method, None, PD, SD), P)
    g1 = sign_grant(DelegationGrant(M.verification_method, L.verification_method, P.verification_method, g0.grant_hash, PD, SD), M)
    return g0, g1


def _verify(grants, *, principal, leaf, policy=POL, scope=SCOPE, checkpoint=None):
    return verify_delegation(
        [g.to_dict() if isinstance(g, DelegationGrant) else g for g in grants],
        principal=principal, policy_ref=policy, scope_ref=scope,
        leaf_verification_method=leaf, checkpoint=checkpoint,
    )


def test_happy_path_two_link_chain():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify([g0, g1], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "verified"
    assert v.principal_binding == "verified"
    assert v.policy_binding == "verified"
    assert v.scope_binding == "verified"
    assert v.cryptographically_bound is True
    assert v.temporal_status == "unresolved"
    assert v.revocation_status == "unresolved"
    assert v.fully_delegated is False


def test_wrong_principal_root_grantor():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify([g0, g1], principal=M.verification_method, leaf=L.verification_method)
    assert v.principal_binding == "wrong_principal"
    assert v.chain_integrity == "verified"      # independent: the chain itself is sound
    assert v.fully_delegated is False


def test_leaf_not_signer():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify([g0, g1], principal=P.verification_method, leaf=M.verification_method)
    assert v.principal_binding == "wrong_principal"


def test_policy_mismatch_is_not_authorization():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify([g0, g1], principal=P.verification_method, leaf=L.verification_method, policy="policy://OTHER")
    assert v.policy_binding == "mismatch"
    assert v.chain_integrity == "verified"      # a valid chain that simply conveys a different policy


def test_receipt_scope_mismatch_is_caught_independently():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify(
        [g0, g1], principal=P.verification_method, leaf=L.verification_method,
        scope="admin:*",
    )
    assert v.chain_integrity == "verified"
    assert v.principal_binding == "verified"
    assert v.policy_binding == "verified"
    assert v.scope_binding == "mismatch"
    assert v.cryptographically_bound is False


def test_stripped_root_breaks_chain():
    P, M, L = _key(1), _key(2), _key(3)
    _, g1 = _chain(P, M, L)
    v = _verify([g1], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"        # g1.parent points at a hash not present


def test_spliced_parent_hash_breaks_chain_but_not_principal():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    bad = dataclasses.replace(g1, parent="sha256:" + "0" * 64)
    v = _verify([g0, bad], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"
    assert v.principal_binding == "verified"    # THE independence property: endpoints still correct


def test_cross_domain_replay_of_a_content_proof_as_a_grant():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    d1 = g1.to_dict()
    d1["proof"] = dict(d1["proof"], purpose="content")   # relabel a grant proof to a content proof
    v = _verify([g0.to_dict(), d1], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"        # domain separation rejects the wrong-purpose proof


def test_cycle_detected():
    P, M = _key(1), _key(2)
    # P -> M -> P : continuity holds, but the identity path revisits P
    g0 = sign_grant(DelegationGrant(P.verification_method, M.verification_method, P.verification_method, None, PD, SD), P)
    g1 = sign_grant(DelegationGrant(M.verification_method, P.verification_method, P.verification_method, g0.grant_hash, PD, SD), M)
    v = _verify([g0, g1], principal=P.verification_method, leaf=P.verification_method)
    assert v.chain_integrity == "cycle"


def test_over_depth_refused():
    P, L = _key(1), _key(9)
    g0 = sign_grant(DelegationGrant(P.verification_method, L.verification_method, P.verification_method, None, PD, SD), P)
    v = _verify([g0.to_dict()] * (MAX_DEPTH + 1), principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "over_depth"


def test_non_did_key_principal_is_unresolved():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify([g0, g1], principal="github:jkomkov", leaf=L.verification_method)
    assert v.principal_binding == "unresolved"


def test_grant_signed_by_non_grantor_is_broken():
    P, M, L, X = _key(1), _key(2), _key(3), _key(8)
    g0, _ = _chain(P, M, L)
    # X (not M) signs the second grant, but it claims grantor M
    g1 = DelegationGrant(M.verification_method, L.verification_method, P.verification_method, g0.grant_hash, PD, SD)
    proof = X.sign_domain("delegation-grant", g1.grant_hash)
    g1_forged = dataclasses.replace(g1, proof=proof)
    v = _verify([g0, g1_forged], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"


def test_no_delegation_principal_signs_directly():
    P = _key(1)
    v = _verify([], principal=P.verification_method, leaf=P.verification_method)
    assert v.chain_integrity == "not_applicable"
    assert v.principal_binding == "verified"


def test_no_delegation_non_principal_signer_is_wrong():
    P, X = _key(1), _key(8)
    v = _verify([], principal=P.verification_method, leaf=X.verification_method)
    assert v.principal_binding == "wrong_principal"


def test_malformed_did_key_is_never_treated_as_a_bound_principal():
    v = _verify([], principal="did:key:zNOT_A_REAL_ED25519_KEY", leaf="did:key:zNOT_A_REAL_ED25519_KEY")
    assert v.principal_binding == "unresolved"


def test_missing_policy_reference_fails_closed_without_raising():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = _verify(
        [g0, g1], principal=P.verification_method, leaf=L.verification_method,
        policy=None,
    )
    assert v.policy_binding == "mismatch"


def test_revocation_window_evaluated_against_checkpoint():
    P, L = _key(1), _key(3)
    g0 = sign_grant(
        DelegationGrant(
            P.verification_method, L.verification_method, P.verification_method,
            None, PD, SD,
            not_before={"domain": "log-size", "value": 100},
            not_after={"domain": "log-size", "value": 200},
        ),
        P,
    )
    within = _verify(
        [g0], principal=P.verification_method, leaf=L.verification_method,
        checkpoint={"domain": "log-size", "value": 150},
    )
    assert within.temporal_status == "within_window"
    assert within.revocation_status == "unresolved"
    assert within.fully_delegated is False
    expired = _verify(
        [g0], principal=P.verification_method, leaf=L.verification_method,
        checkpoint={"domain": "log-size", "value": 250},
    )
    assert expired.temporal_status == "expired"


def test_incomparable_or_untyped_checkpoint_is_unresolved():
    P, L = _key(1), _key(3)
    g0 = sign_grant(
        DelegationGrant(
            P.verification_method, L.verification_method, P.verification_method,
            None, PD, SD,
            not_before={"domain": "log-size", "value": 100},
            not_after={"domain": "log-size", "value": 200},
        ),
        P,
    )
    assert _verify(
        [g0], principal=P.verification_method, leaf=L.verification_method,
        checkpoint={"domain": "block-height", "value": 150},
    ).temporal_status == "unresolved"
    assert _verify(
        [g0], principal=P.verification_method, leaf=L.verification_method,
        checkpoint=150,
    ).temporal_status == "unresolved"


def test_no_window_is_named_unresolved_not_valid_forever():
    P, L = _key(1), _key(3)
    g0 = sign_grant(DelegationGrant(P.verification_method, L.verification_method, P.verification_method, None, PD, SD), P)
    v = _verify([g0], principal=P.verification_method, leaf=L.verification_method)
    assert v.temporal_status == "unresolved"
    assert v.revocation_status == "unresolved"
    assert v.fully_delegated is False


def test_malformed_grant_rejected_at_construction():
    with pytest.raises(DelegationError):
        DelegationGrant("not-a-did-key", "did:key:zABC", "did:key:zABC", None, PD, SD)
    with pytest.raises(DelegationError):
        DelegationGrant("did:key:zABC", "did:key:zABC", "did:key:zABC", None, "not-a-hash", SD)
    with pytest.raises(DelegationError):
        DelegationGrant(
            "did:key:zABC", "did:key:zABC", "did:key:zABC", None,
            "sha256:", SD,
        )
    with pytest.raises(DelegationError):
        DelegationGrant(
            "did:key:zABC", "did:key:zABC", "did:key:zABC", None,
            PD, SD, not_before=100,
        )


def test_unknown_grant_members_fail_closed():
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    hostile = dict(g1.to_dict(), role="admin")
    v = _verify([g0, hostile], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"
    assert "unknown grant members" in " ".join(v.reasons)


def test_attacker_key_cannot_authenticate_an_upstream_grantor():
    """A grant claiming grantor P but signed by attacker A must never verify: the
    grantor is self-certifying, so its key derives from `grant.grantor` and no proof
    claim can stand in for an upstream principal."""
    P, L, A = _key(1), _key(3), _key(9)
    forged = DelegationGrant(P.verification_method, L.verification_method,
                             P.verification_method, None, PD, SD)
    forged = dataclasses.replace(forged, proof=A.sign_domain("delegation-grant", forged.grant_hash))
    v = _verify([forged], principal=P.verification_method, leaf=L.verification_method)
    assert v.chain_integrity == "broken"


# ── receipt-level wiring (the layer both P0 holes actually lived in) ──────────
#
# The unit tests above call verify_delegation directly. Both reproduced attacks
# arrived through verify_receipt, so the wiring gets its own regressions: the
# receipt's real bounds.scope must reach the scope check, and the receipt-signer
# key override must NOT reach the grant checks.

def _envelope(grants, *, principal, scope=SCOPE):
    from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
    return RecourseEnvelope(
        authority=Authority(principal=principal.verification_method, policy=POL,
                            delegation=tuple(g.to_dict() for g in grants)),
        bounds=Bounds(scope=scope),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(Remedy("recompute", "bulla receipt verify", "hashes.content"),
                      Remedy("escalate", "maintainer review", principal.verification_method)),
        ),
        deed_schema="0.3",
    )


def _signed_receipt(grants, *, principal, leaf, scope=SCOPE):
    from bulla.action_receipt import build_tool_call_receipt, sign_action_receipt
    r = build_tool_call_receipt(
        tool="payments.charge", call_subject={"amount": 1250},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "d" * 64},
        envelope=_envelope(grants, principal=principal, scope=scope),
    )
    return sign_action_receipt(r, leaf).to_dict()


def test_receipt_level_scope_widening_is_caught():
    """End-to-end: the leaf HONESTLY signs a receipt declaring `admin:*` while its
    grant only ever conveyed the narrow scope. Every proof is valid and nothing is
    mutated — so the RECORD is authentic, but the chain does not convey the act."""
    from bulla.action_receipt import verify_receipt
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = verify_receipt(_signed_receipt([g0, g1], principal=P, leaf=L, scope="admin:*"))
    assert v.ok is True                        # the record IS authentically signed…
    assert v.authority_authentic == "verified"
    assert v.scope_binding == "mismatch"       # …but delegation does not back admin:*
    assert v.chain_integrity == "verified"


def test_receipt_scope_binding_verifies_on_the_honest_path():
    from bulla.action_receipt import verify_receipt
    P, M, L = _key(1), _key(2), _key(3)
    g0, g1 = _chain(P, M, L)
    v = verify_receipt(_signed_receipt([g0, g1], principal=P, leaf=L))
    assert v.scope_binding == "verified"
    assert v.chain_integrity == "verified"
    assert v.principal_binding == "verified"


def test_receipt_public_key_override_does_not_authenticate_grants():
    """`verify_receipt(public_key=…)` overrides the key for THIS receipt's signer
    only. Forwarding it to the grant checks would let one attacker key validate a
    grant claiming ANY upstream grantor.

    The strongest form: attacker A signs the receipt (so a supplied A key does
    authenticate its content/authorization proofs) AND forges a grant that *claims*
    principal P as issuer and verificationMethod while A's key made the signature.
    Only deriving the grant key from `grant.grantor` catches this."""
    from bulla.action_receipt import verify_receipt
    P, A = _key(1), _key(9)
    forged = DelegationGrant(P.verification_method, A.verification_method,
                             P.verification_method, None, PD, SD)
    proof = A.sign_domain("delegation-grant", forged.grant_hash)
    proof = dict(proof, issuer=P.verification_method,
                 verificationMethod=P.verification_method)   # lie about who signed
    forged = dataclasses.replace(forged, proof=proof)

    d = _signed_receipt([forged], principal=P, leaf=A)
    v = verify_receipt(d, public_key=A.public_key)
    assert v.checks["signature"] is True      # A's key authenticates A's own receipt…
    assert v.chain_integrity == "broken"      # …but never a grant claiming grantor P
    assert verify_receipt(d).chain_integrity == "broken"
