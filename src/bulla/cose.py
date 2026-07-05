"""COSE_Sign1 serialization for bulla deeds — the SCITT-compatibility posture.

A bulla deed maps onto a SCITT Signed Statement: the *statement* is the deed's
attestation preimage (the canonical JSON committing to the recomputable
content hash, the issuer's detached proof, and — v0.2 — the recourse
envelope); the *signature* here is a genuine COSE_Sign1 signature computed
over the COSE ``Sig_structure`` per RFC 9052 §4.4 — a parallel, standards-true
attestation by the same key, NOT a re-wrapping of the existing detached proof
(which signs the bare content hash and therefore cannot be a conformant
COSE_Sign1 signature). Emit both when interoperating with SCITT-shaped
transparency services; the registry's own leaf format is unchanged.

Requires the ``[scitt]`` extra (``cbor2``; signing also needs ``[identity]``).
See ``bulla/SCITT-MAPPING.md`` for the one-page correspondence.
"""

from __future__ import annotations

import json
from typing import Any

_ALG_EDDSA = -8          # COSE alg: EdDSA (RFC 9053 §2.2)
_HDR_ALG = 1
_HDR_CTY = 3
_HDR_KID = 4
CONTENT_TYPE = "application/bulla-deed+json"


def _require_cbor2():
    try:
        import cbor2  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - exercised via skipif
        raise ImportError(
            "COSE serialization needs the [scitt] extra: pip install 'bulla[scitt]'"
        ) from e
    return cbor2


def _attestation_payload(cert_dict: dict) -> bytes:
    """The statement: the deed's canonical attestation preimage, byte-exact
    with `bulla.certificate._attestation_hash`'s preimage."""
    preimage: dict[str, Any] = {
        "certificate_content_hash": cert_dict["certificate_content_hash"],
        "signature": cert_dict["signature"],
    }
    env = cert_dict.get("recourse_envelope")
    if env is not None:
        preimage["recourse_envelope"] = env
    return json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_statement_cose(signer: Any, cert_dict: dict) -> bytes:
    """Serialize a SIGNED certificate dict as a COSE_Sign1 Signed Statement.

    ``signer`` is the same ``LocalEd25519Signer`` that signed the deed; the
    COSE signature is computed over the RFC 9052 Sig_structure with the
    deed's attestation preimage as payload. Returns the tagged COSE_Sign1
    bytes (CBOR tag 18)."""
    cbor2 = _require_cbor2()
    from nacl.signing import SigningKey  # via the [identity] extra

    if not cert_dict.get("signature"):
        raise ValueError("certificate is not signed — sign it before COSE export")

    payload = _attestation_payload(cert_dict)
    protected = cbor2.dumps(
        {
            _HDR_ALG: _ALG_EDDSA,
            _HDR_CTY: CONTENT_TYPE,
            _HDR_KID: signer.verification_method.encode("utf-8"),
        }
    )
    sig_structure = cbor2.dumps(["Signature1", protected, b"", payload])
    signature = SigningKey(signer.seed).sign(sig_structure).signature
    return cbor2.dumps(cbor2.CBORTag(18, [protected, {}, payload, signature]))


def verify_statement_cose(blob: bytes, *, public_key: bytes) -> dict:
    """Verify a COSE_Sign1 Signed Statement and return the decoded deed
    attestation preimage. Raises on any failure."""
    cbor2 = _require_cbor2()
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    decoded = cbor2.loads(blob)
    if not (isinstance(decoded, cbor2.CBORTag) and decoded.tag == 18):
        raise ValueError("not a COSE_Sign1 (expected CBOR tag 18)")
    protected, _unprotected, payload, signature = decoded.value
    hdr = cbor2.loads(protected)
    if hdr.get(_HDR_ALG) != _ALG_EDDSA:
        raise ValueError(f"unsupported COSE alg {hdr.get(_HDR_ALG)!r} (want EdDSA)")
    sig_structure = cbor2.dumps(["Signature1", protected, b"", payload])
    try:
        VerifyKey(public_key).verify(sig_structure, signature)
    except BadSignatureError as e:
        raise ValueError("COSE_Sign1 signature verification failed") from e
    return json.loads(payload.decode("utf-8"))
