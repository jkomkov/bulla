"""Crypto core: ed25519 signing, did:key self-certification, forgery-proofness."""

from __future__ import annotations

from bulla.identity import (
    LocalEd25519Signer,
    did_key_from_pubkey,
    pubkey_from_did_key,
    verify_proof,
)

_HASH = "sha256:" + "ab" * 32


def test_did_key_roundtrip_and_prefix():
    signer = LocalEd25519Signer.generate()
    did = signer.verification_method
    # ed25519 did:key is the well-known z6Mk… form.
    assert did.startswith("did:key:z6Mk")
    assert pubkey_from_did_key(did) == signer.public_key


def test_sign_verify_roundtrip_is_did_key_method():
    signer = LocalEd25519Signer.generate()
    proof = signer.sign(_HASH)
    assert proof["type"] == "bulla/ed25519-2026"
    assert proof["issuer"].startswith("did:key:")
    res = verify_proof(_HASH, proof)
    assert res.authentic is True
    assert res.method == "did:key"


def test_tampered_content_hash_fails():
    proof = LocalEd25519Signer.generate().sign(_HASH)
    res = verify_proof("sha256:" + "cd" * 32, proof)
    assert res.authentic is False


def test_forgery_by_issuer_swap_fails_by_construction():
    # Sign with Alice's key, then claim issuer = Bob's did:key. Because the
    # verifier DERIVES the key from the (did:key) issuer, it checks Alice's
    # signature against Bob's key -> fails. No resolution needed.
    alice = LocalEd25519Signer.generate()
    bob = LocalEd25519Signer.generate()
    forged = dict(alice.sign(_HASH))
    forged["issuer"] = bob.verification_method
    res = verify_proof(_HASH, forged)
    assert res.method == "did:key"
    assert res.authentic is False


def test_supplied_key_path():
    signer = LocalEd25519Signer.generate()
    res = verify_proof(_HASH, signer.sign(_HASH), public_key=signer.public_key)
    assert res.authentic is True
    assert res.method == "supplied-key"


def test_external_issuer_vm_signature_is_not_attributable():
    # An external issuer URI (did:web) is not self-certifying. The proof's
    # verificationMethod records the real signing key, so the SIGNATURE is valid
    # -- but nothing binds that key to the external issuer without resolution, so
    # it must NOT count as the issuer signing. This is the forgeable path
    # (stamp issuer=eip155:0xVICTIM, sign with your own VM key), so authentic=False.
    signer = LocalEd25519Signer.generate(issuer_override="did:web:example.com:agent-7")
    proof = signer.sign(_HASH)
    assert proof["issuer"] == "did:web:example.com:agent-7"
    res = verify_proof(_HASH, proof)
    assert res.method == "verification-method"
    assert res.authentic is False  # a key signed, but not provably the claimed issuer
    # Supplying the issuer's key (caller asserts the binding) does authenticate it.
    assert verify_proof(_HASH, proof, public_key=signer.public_key).authentic is True


def test_forged_issuer_with_own_vm_key_is_not_authentic():
    # The concrete attack: claim a victim's external identity, sign with your own
    # key, advertise your own did:key as the verificationMethod.
    attacker = LocalEd25519Signer.generate()
    proof = attacker.sign(_HASH)
    proof["issuer"] = "eip155:1:0xVICTIM"  # claim someone else's chain identity
    res = verify_proof(_HASH, proof)
    assert res.authentic is False
    assert res.issuer == "eip155:1:0xVICTIM"


def test_external_issuer_unresolved_without_vm():
    # With no did:key anywhere and no supplied key, authenticity is unresolved.
    proof = {
        "type": "bulla/ed25519-2026",
        "issuer": "did:web:example.com:agent",
        "verificationMethod": "did:web:example.com:agent#key-1",
        "proofValue": "AAAA",
    }
    res = verify_proof(_HASH, proof)
    assert res.authentic is False
    assert res.method == "unresolved"


def test_keyfile_roundtrip():
    signer = LocalEd25519Signer.generate()
    restored = LocalEd25519Signer.from_keyfile_dict(signer.to_keyfile_dict())
    assert restored.public_key == signer.public_key
    assert restored.issuer == signer.issuer


def test_did_key_helper_rejects_non_ed25519():
    import pytest

    with pytest.raises(ValueError):
        pubkey_from_did_key("did:key:zNotAnEd25519Key")
    with pytest.raises(ValueError):
        did_key_from_pubkey(b"too-short")
