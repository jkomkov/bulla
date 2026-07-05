"""Certificate signing: creation-time issuer binding, the corrected hash-invariance
asserts, and tamper-evidence over the issuer."""

from __future__ import annotations

from bulla.certificate import (
    certify,
    sign_certificate,
    to_dict,
    verify_certificate_integrity,
)
from bulla.identity import LocalEd25519Signer, verify_proof
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _comp() -> Composition:
    a = ToolSpec("a", internal_state=("amount",), observable_schema=("amount",))
    b = ToolSpec("b", internal_state=("amount",), observable_schema=("amount",))
    e = Edge(
        "a",
        "b",
        dimensions=(SemanticDimension("amount_unit", from_field="amount", to_field="amount"),),
    )
    return Composition("demo", tools=(a, b), edges=(e,))


def test_unsigned_cert_still_verifies_by_hash():
    d = to_dict(certify(_comp()))
    assert d["issuer"] == {"type": "local", "id": None}
    assert d["signature"] is None
    assert verify_certificate_integrity(d) is True  # backward-compatible


def test_signing_is_creation_time_and_changes_the_hash():
    cert = certify(_comp())
    signed = sign_certificate(cert, LocalEd25519Signer.generate())
    # issuer is in the preimage, so binding it mints a new content hash.
    assert signed.issuer["type"] == "did:key"
    assert signed.issuer["id"].startswith("did:key:")
    assert signed.certificate_content_hash != cert.certificate_content_hash
    assert signed.signature is not None
    assert signed.attestation_hash is not None


def test_signed_cert_integrity_and_authenticity():
    signed = sign_certificate(certify(_comp()), LocalEd25519Signer.generate())
    d = to_dict(signed)
    assert verify_certificate_integrity(d) is True
    res = verify_proof(d["certificate_content_hash"], d["signature"])
    assert res.authentic is True
    assert res.method == "did:key"


def test_signature_is_hash_invariant_but_authenticity_is_not():
    # Plan assert: changing `signature` post-hoc does NOT change the content hash.
    signed = sign_certificate(certify(_comp()), LocalEd25519Signer.generate())
    d = to_dict(signed)
    # A structurally valid proof (real did:key) that signs a DIFFERENT hash:
    # integrity is unaffected (signature is excluded), authenticity now fails.
    d["signature"] = LocalEd25519Signer.generate().sign("sha256:" + "00" * 32)
    assert verify_certificate_integrity(d) is True  # signature excluded from hash
    assert verify_proof(d["certificate_content_hash"], d["signature"]).authentic is False


def test_issuer_is_in_the_hash_so_swapping_it_breaks_integrity():
    # Plan assert: changing `issuer` DOES change the content hash. So a swap
    # without re-signing is caught by integrity (not only by authenticity).
    signed = sign_certificate(certify(_comp()), LocalEd25519Signer.generate())
    d = to_dict(signed)
    d["issuer"] = {"type": "did:key", "id": "did:key:z6MkSomeoneElse"}
    assert verify_certificate_integrity(d) is False


def test_tampering_diagnostic_breaks_integrity():
    signed = sign_certificate(certify(_comp()), LocalEd25519Signer.generate())
    d = to_dict(signed)
    d["diagnostic"]["coherence_fee"] = 999
    assert verify_certificate_integrity(d) is False


def test_signed_cert_survives_json_roundtrip():
    import json

    signed = sign_certificate(certify(_comp()), LocalEd25519Signer.generate())
    d = json.loads(json.dumps(to_dict(signed)))
    assert verify_certificate_integrity(d) is True
    assert verify_proof(d["certificate_content_hash"], d["signature"]).authentic is True
