"""Rigor for the deed registry: RFC 6962 Merkle correctness (hand vectors +
exhaustive self-verification + adversarial rejection) and DeedLog behavior."""

from __future__ import annotations

import hashlib

import pytest

from bulla.registry import (
    Deed,
    DeedLog,
    _lpo2,
    consistency_proof,
    inclusion_proof,
    leaf_hash,
    merkle_root,
    verify_consistency,
    verify_consistency_record,
    verify_inclusion,
    verify_inclusion_record,
)


def _leaves(n: int) -> list[bytes]:
    return [leaf_hash(bytes([i])) for i in range(n)]


# Independent reference hashing (must match the module, by definition).
def _H(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _L(d: bytes) -> bytes:
    return _H(b"\x00" + d)


def _N(left: bytes, right: bytes) -> bytes:
    return _H(b"\x01" + left + right)


# ── Hand-computed small trees (absolute correctness anchor) ──────────────────

def test_root_hand_computed():
    a, b, c, d = bytes([0]), bytes([1]), bytes([2]), bytes([3])
    assert merkle_root([_L(a)]) == _L(a)
    assert merkle_root([_L(a), _L(b)]) == _N(_L(a), _L(b))
    # n=3 splits 2|1: node(node(a,b), c)
    assert merkle_root([_L(a), _L(b), _L(c)]) == _N(_N(_L(a), _L(b)), _L(c))
    # n=4 splits 2|2
    assert merkle_root([_L(a), _L(b), _L(c), _L(d)]) == _N(_N(_L(a), _L(b)), _N(_L(c), _L(d)))


def test_lpo2():
    assert [_lpo2(n) for n in (2, 3, 4, 5, 7, 8, 9)] == [1, 2, 2, 4, 4, 4, 8]


def test_domain_separation_blocks_second_preimage():
    # A leaf hash must never collide with an internal-node hash of the same bytes.
    assert _L(b"x") != _H(b"x")
    assert _N(_L(b"a"), _L(b"b")) != _H(_L(b"a") + _L(b"b"))


# ── Inclusion: exhaustive self-verification + rejection ──────────────────────

def test_inclusion_verifies_for_every_size_and_index():
    for n in range(1, 33):
        leaves = _leaves(n)
        root = merkle_root(leaves)
        for m in range(n):
            proof = inclusion_proof(leaves, m)
            assert verify_inclusion(leaves[m], m, n, proof, root), (n, m)


def test_inclusion_rejects_tampering():
    leaves = _leaves(7)
    root = merkle_root(leaves)
    proof = inclusion_proof(leaves, 3)
    assert verify_inclusion(leaves[3], 3, 7, proof, root)
    assert not verify_inclusion(leaves[4], 3, 7, proof, root)          # wrong leaf
    assert not verify_inclusion(leaves[3], 3, 7, proof, _L(b"\xff"))   # wrong root
    assert not verify_inclusion(leaves[3], 2, 7, proof, root)          # wrong index
    assert not verify_inclusion(leaves[3], 3, 7, proof + [bytes(32)], root)  # too long


# ── Consistency: exhaustive self-verification + cross-check + rejection ──────

def test_consistency_verifies_for_every_prefix_pair():
    for n in range(1, 25):
        leaves = _leaves(n)
        root_n = merkle_root(leaves)
        for m in range(0, n + 1):
            proof = consistency_proof(leaves, m, n)
            root_m = merkle_root(leaves[:m])
            assert verify_consistency(m, n, proof, root_m, root_n), (m, n)


def test_consistency_rejects_nonprefix_old_root():
    leaves = _leaves(8)
    root8 = merkle_root(leaves)
    other_root4 = merkle_root([leaf_hash(bytes([100 + i])) for i in range(4)])
    proof = consistency_proof(leaves, 4, 8)
    assert not verify_consistency(4, 8, proof, other_root4, root8)


def test_consistency_detects_tampered_proof():
    leaves = _leaves(11)
    root4 = merkle_root(leaves[:4])
    root11 = merkle_root(leaves)
    proof = consistency_proof(leaves, 4, 11)
    assert proof  # 4 -> 11 is a non-trivial proof
    tampered = list(proof)
    tampered[0] = bytes(32)
    assert not verify_consistency(4, 11, tampered, root4, root11)


def test_consistency_empty_and_equal_edge_cases():
    leaves = _leaves(5)
    root5 = merkle_root(leaves)
    assert verify_consistency(0, 5, [], _H(b""), root5)   # consistent with empty
    assert verify_consistency(5, 5, [], root5, root5)     # equal sizes
    assert not verify_consistency(5, 5, [], root5, _L(b"\x00"))  # equal but different root
    assert not verify_consistency(6, 5, [], root5, root5)        # m > n


# ── DeedLog: persistence, enumeration, dedup, append-only ────────────────────

def _deed(issuer: str, i: int) -> Deed:
    return Deed(
        issuer=issuer,
        content_hash=f"sha256:{i:064x}",
        attestation_hash=f"sha256:{(1000 + i):064x}",
    )


def test_deedlog_append_enumerate_dedup(tmp_path):
    log = DeedLog(tmp_path / "reg.jsonl")
    assert (log.append(_deed("did:key:zA", 0)),
            log.append(_deed("did:key:zB", 1)),
            log.append(_deed("did:key:zA", 2))) == (0, 1, 2)
    # dedup: re-appending the same deed returns its index, no new leaf
    assert log.append(_deed("did:key:zA", 0)) == 0
    assert len(log) == 3
    # the completeness query: full set under an issuer
    assert len(log.deeds("did:key:zA")) == 2
    assert [d.issuer for _, d in log.deeds("did:key:zB")] == ["did:key:zB"]
    assert len(log.deeds()) == 3


def test_deedlog_reload_is_append_only_source_of_truth(tmp_path):
    p = tmp_path / "reg.jsonl"
    log = DeedLog(p)
    for i in range(5):
        log.append(_deed("did:key:zA", i))
    root5 = log.root()
    reloaded = DeedLog(p)
    assert reloaded.root() == root5
    assert len(reloaded) == 5


def test_deedlog_inclusion_and_consistency_records(tmp_path):
    log = DeedLog(tmp_path / "reg.jsonl")
    for i in range(6):
        log.append(_deed("did:key:zA", i))
    old_root = log.root()
    for i in range(6, 10):
        log.append(_deed("did:key:zA", i))
    rec = log.inclusion(2)
    assert verify_inclusion_record(rec)
    crec = log.consistency(6)
    assert crec["old_root"] == old_root
    assert verify_consistency_record(crec)


def test_deed_from_signed_certificate(tmp_path):
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    a = ToolSpec("a", ("amount",), ("amount",))
    b = ToolSpec("b", ("amount",), ("amount",))
    comp = Composition(
        "demo", (a, b), (Edge("a", "b", (SemanticDimension("amount_unit", "amount", "amount"),)),)
    )
    signed = to_dict(sign_certificate(certify(comp), LocalEd25519Signer.generate()))
    deed = Deed.from_certificate(signed)
    assert deed.issuer.startswith("did:key:")
    assert deed.attestation_hash == signed["attestation_hash"]
    # an unsigned certificate cannot be a deed
    with pytest.raises(ValueError):
        Deed.from_certificate(to_dict(certify(comp)))


def _signing_comp():
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    a = ToolSpec("a", ("amount",), ("amount",))
    b = ToolSpec("b", ("amount",), ("amount",))
    return Composition(
        "demo", (a, b), (Edge("a", "b", (SemanticDimension("amount_unit", "amount", "amount"),)),)
    )


def test_registry_rejects_forged_deed_under_victim_issuer():
    # QA: the submission boundary (`from_certificate` / `append_certificate`) must
    # verify the issuer's signature, or an attacker pollutes a victim's enumerable
    # history with deeds the victim never signed. (This guards `from_certificate`;
    # `test_append_certificate_is_the_submission_boundary` guards the writer's path.)
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer

    comp = _signing_comp()
    victim = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()

    forged = to_dict(sign_certificate(certify(comp), attacker))
    forged["issuer"] = {"type": "did:key", "id": victim.verification_method}  # claim the victim
    with pytest.raises(ValueError):
        Deed.from_certificate(forged)  # integrity (issuer is in the hash) + authenticity reject it

    # the attacker's OWN genuine deed is fine — under the attacker's own id
    deed = Deed.from_certificate(to_dict(sign_certificate(certify(comp), attacker)))
    assert deed.issuer == attacker.verification_method


def test_registry_refuses_unresolved_external_issuer_without_key():
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer

    signer = LocalEd25519Signer.generate(issuer_override="did:web:example.com:agent")
    signed = to_dict(sign_certificate(certify(_signing_comp()), signer))
    with pytest.raises(ValueError):
        Deed.from_certificate(signed)  # external issuer, unresolved → refused
    # supplying the issuer's key authenticates it
    deed = Deed.from_certificate(signed, public_key=signer.public_key)
    assert deed.issuer == "did:web:example.com:agent"


def test_append_certificate_is_the_submission_boundary(tmp_path):
    """The verified submission boundary rejects forgery — the test that IS the
    'cannot pollute a victim's history' property, on the path a writer actually
    calls (the prior test guards `from_certificate` directly)."""
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer

    comp = _signing_comp()
    victim = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "reg.jsonl")

    forged = to_dict(sign_certificate(certify(comp), attacker))
    forged["issuer"] = {"type": "did:key", "id": victim.verification_method}  # claim the victim
    with pytest.raises(ValueError):
        log.append_certificate(forged)                       # rejected at the boundary
    assert len(log) == 0                                     # nothing logged
    assert log.deeds(victim.verification_method) == []       # victim's history un-polluted

    # a genuine cert submits and enumerates under its TRUE issuer
    idx = log.append_certificate(to_dict(sign_certificate(certify(comp), attacker)))
    assert idx == 0
    assert [d.issuer for _, d in log.deeds(attacker.verification_method)] \
        == [attacker.verification_method]


def test_raw_append_is_post_verification_by_design(tmp_path):
    """Raw `append` is the Merkle primitive — it does NOT re-verify authenticity.
    The submission boundary is `append_certificate`; routing untrusted input to raw
    `append` would pollute enumeration. Asserted so the door is not mistaken for
    guarded — in-process raw-append access ⊇ direct JSONL write, which is the
    operator-trust / anchoring boundary, not the authenticity one."""
    log = DeedLog(tmp_path / "reg.jsonl")
    log.append(Deed(issuer="did:key:zVICTIM",
                    content_hash="sha256:" + "0" * 64,
                    attestation_hash="sha256:" + "f" * 64))   # unsigned, hand-built
    assert len(log) == 1
    assert [d.issuer for _, d in log.deeds("did:key:zVICTIM")] == ["did:key:zVICTIM"]


def test_from_certificate_rejects_empty_content_hash():
    """A deed with an empty content_hash is malformed — `from_certificate` refuses
    it even with require_authentic=False (guards the `content or ''` foot-gun)."""
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer

    signed = to_dict(sign_certificate(certify(_signing_comp()), LocalEd25519Signer.generate()))
    signed["certificate_content_hash"] = ""
    with pytest.raises(ValueError):
        Deed.from_certificate(signed, require_authentic=False)


def test_served_enumeration_is_self_auditable_against_a_polluting_operator(tmp_path):
    """THE read-side property (where the adversary actually is). A malicious OPERATOR
    serves a polluted enumeration; an independent consumer rejects the forged entry
    using ONLY served data + a pinned root — no certificate corpus. This is the test
    whose absence let the read-side over-reach ship."""
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer
    from bulla.registry import verify_deed_record, verify_inclusion_record

    comp = _signing_comp()
    honest = LocalEd25519Signer.generate()
    victim = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()
    log = DeedLog(tmp_path / "reg.jsonl")

    # an honest deed, submitted through the verified boundary
    genuine = to_dict(sign_certificate(certify(comp), honest))
    log.append_certificate(genuine)
    comp_hash = genuine["subject"]["composition_sha256"]

    # the operator POLLUTES: a raw forged leaf claiming the victim's issuer, carrying
    # the attacker's OWN signature (the best they can do — they lack the victim's key)
    atk = to_dict(sign_certificate(certify(comp), attacker))
    log.append(Deed(
        issuer=victim.verification_method,           # claim the victim
        content_hash=atk["certificate_content_hash"],
        attestation_hash=atk["attestation_hash"],
        composition_hash=comp_hash,
        signature=atk["signature"],                  # attacker's sig, not the victim's
    ))

    served = log.by_composition(comp_hash)           # what a remote consumer receives
    assert len(served) == 2
    root = log.root()                                # the consumer pins this (anchored)

    authentic = [
        e for e in served
        if verify_deed_record(e)
        and verify_inclusion_record(log.inclusion(e["index"]), trusted_root=root)
    ]
    issuers = {e["issuer"] for e in authentic}
    assert honest.verification_method in issuers      # the genuine deed survives
    assert victim.verification_method not in issuers  # the forged victim entry is rejected
    assert len(authentic) == 1


def test_verify_deed_record_rejects_tampered_signature(tmp_path):
    """A served entry whose signature does not hash to its attestation_hash (an
    operator swapped the signature) is rejected by the binding check alone."""
    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.identity import LocalEd25519Signer
    from bulla.registry import verify_deed_record

    signed = to_dict(sign_certificate(certify(_signing_comp()), LocalEd25519Signer.generate()))
    deed = Deed.from_certificate(signed)
    good = {"issuer": deed.issuer, "content_hash": deed.content_hash,
            "attestation_hash": deed.attestation_hash, "signature": deed.signature}
    assert verify_deed_record(good) is True
    tampered = dict(good, signature=dict(deed.signature, proofValue="AAAA"))
    assert verify_deed_record(tampered) is False      # H(content, sig) != attestation_hash


def test_verify_inclusion_record_binds_to_expected_leaf(tmp_path):
    """A proof that verifies under the root but covers a DIFFERENT leaf than the deed's
    is rejected once `expected_leaf` is supplied (closes borrowed inclusion)."""
    from bulla.registry import deed_leaf

    log = DeedLog(tmp_path / "reg.jsonl")
    log.append(_deed("did:key:zA", 0))
    log.append(_deed("did:key:zB", 1))
    root = log.root()
    proof_for_0 = log.inclusion(0)
    leaf_of_0 = deed_leaf({"issuer": "did:key:zA",
                           "content_hash": _deed("did:key:zA", 0).content_hash,
                           "attestation_hash": _deed("did:key:zA", 0).attestation_hash})
    leaf_of_1 = deed_leaf({"issuer": "did:key:zB",
                           "content_hash": _deed("did:key:zB", 1).content_hash,
                           "attestation_hash": _deed("did:key:zB", 1).attestation_hash})
    assert verify_inclusion_record(proof_for_0, trusted_root=root, expected_leaf=leaf_of_0)
    # the proof for leaf 0 must NOT satisfy a query expecting leaf 1's deed
    assert not verify_inclusion_record(proof_for_0, trusted_root=root, expected_leaf=leaf_of_1)


def test_verify_served_deed_rejects_borrowed_inclusion_over_http(tmp_path):
    """THE inclusion-binding property, against an adversarial host that controls the
    inclusion channel: it serves an authentic deed record R but answers R's inclusion
    query with a valid proof for a DIFFERENT real leaf under the same root. A consumer
    using `verify_served_deed` (served data + pinned root) rejects it; the honest host
    is accepted."""
    import threading

    from bulla.certificate import certify, sign_certificate, to_dict
    from bulla.http_registry import HttpRegistry, make_server
    from bulla.identity import LocalEd25519Signer
    from bulla.registry import verify_served_deed

    comp = _signing_comp()
    log = DeedLog(tmp_path / "reg.jsonl")
    log.append_certificate(to_dict(sign_certificate(certify(comp), LocalEd25519Signer.generate())))
    # a SECOND real deed (distinct issuer -> distinct content/leaf) to borrow a proof from
    log.append_certificate(to_dict(sign_certificate(certify(comp), LocalEd25519Signer.generate())))
    comp_hash = comp.canonical_hash()
    root = log.root()
    a_att = log.by_composition(comp_hash)[0]["attestation_hash"]
    b_att = log.by_composition(comp_hash)[1]["attestation_hash"]

    class _BorrowingOperator:                 # honest root/by_composition, lying inclusion
        is_remote = False
        def __init__(self, real, borrowed_att):
            self._real, self._borrowed = real, real.inclusion_by_attestation(borrowed_att)
        def __len__(self): return len(self._real)
        def root(self): return self._real.root()
        def by_composition(self, h): return self._real.by_composition(h)
        def inclusion_by_attestation(self, att): return self._borrowed  # always B's proof

    def _run(server_log, pin):
        srv = make_server(server_log, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            remote = HttpRegistry(f"http://127.0.0.1:{srv.server_address[1]}")
            a_rec = next(e for e in remote.by_composition(comp_hash)
                         if e["attestation_hash"] == a_att)
            return verify_served_deed(a_rec, remote.inclusion_by_attestation(a_att),
                                      trusted_root=pin)
        finally:
            srv.shutdown()

    assert _run(log, root) is True                          # honest host: A is genuinely included
    assert _run(_BorrowingOperator(log, b_att), root) is False  # borrowed proof for B → rejected
