"""COSE_Sign1 Signed-Statement serialization (the [scitt] extra)."""

from __future__ import annotations

import pytest

cbor2 = pytest.importorskip("cbor2")

from bulla.certificate import certify, sign_certificate, to_dict
from bulla.cose import sign_statement_cose, verify_statement_cose
from bulla.envelope import Bounds, RecourseEnvelope
from bulla.identity import LocalEd25519Signer
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _signed(signer, envelope=None) -> dict:
    a = ToolSpec("a", ("amount",), ("amount",))
    b = ToolSpec("b", ("amount",), ("amount",))
    comp = Composition(
        "demo",
        (a, b),
        (Edge("a", "b", (SemanticDimension("amount_unit", "amount", "amount"),)),),
    )
    return to_dict(sign_certificate(certify(comp), signer, envelope=envelope))


def test_cose_round_trip_and_payload_matches_attestation_preimage():
    signer = LocalEd25519Signer.generate()
    cert = _signed(signer, envelope=RecourseEnvelope(bounds=Bounds(scope="repo:x")))
    blob = sign_statement_cose(signer, cert)
    stmt = verify_statement_cose(blob, public_key=signer.public_key)
    assert stmt["certificate_content_hash"] == cert["certificate_content_hash"]
    assert stmt["recourse_envelope"]["bounds"]["scope"] == "repo:x"


def test_cose_rejects_wrong_key():
    signer = LocalEd25519Signer.generate()
    other = LocalEd25519Signer.generate()
    blob = sign_statement_cose(signer, _signed(signer))
    with pytest.raises(ValueError, match="verification failed"):
        verify_statement_cose(blob, public_key=other.public_key)


def test_cose_rejects_tampered_payload():
    signer = LocalEd25519Signer.generate()
    blob = bytearray(sign_statement_cose(signer, _signed(signer)))
    # flip one byte inside the CBOR body
    blob[len(blob) // 2] ^= 0x01
    with pytest.raises(Exception):
        verify_statement_cose(bytes(blob), public_key=signer.public_key)


def test_unsigned_certificate_refused():
    from bulla.certificate import certify as _certify, to_dict as _to_dict
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    a = ToolSpec("a", ("amount",), ("amount",))
    b = ToolSpec("b", ("amount",), ("amount",))
    comp = Composition(
        "demo",
        (a, b),
        (Edge("a", "b", (SemanticDimension("amount_unit", "amount", "amount"),)),),
    )
    with pytest.raises(ValueError, match="not signed"):
        sign_statement_cose(LocalEd25519Signer.generate(), _to_dict(_certify(comp)))
