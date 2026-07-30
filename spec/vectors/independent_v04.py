#!/usr/bin/env python3
"""Zero-Bulla ActionReceipt v0.4 reference checker.

The hash and shape rung is Python-stdlib only.  If PyNaCl is installed, the
same program also verifies all three Ed25519 proofs against the self-certifying
``did:key``.  It never imports application code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path


SAFE = 9_007_199_254_740_991
PROFILE = "bulla-jcs-int/1"
FIELDS = {
    "schema_version", "kind", "canonicalization", "action", "diagnostic_ref",
    "evidence_refs", "anchor_ref", "mandate", "remedy", "retention", "stake",
    "conventions", "signature", "event_id", "claimed_at", "occurrence",
    "authorization", "producer", "hashes",
}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number {value!r}")


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse(raw: bytes) -> dict:
    if len(raw) > 1_048_576:
        raise ValueError("receipt exceeds 1 MiB")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant,
    )
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("v0.4 top-level fields are not exact")
    if value["schema_version"] != "0.4" or value["canonicalization"] != PROFILE:
        raise ValueError("not an occurrence-bound v0.4 receipt")
    _walk(value, 1, [0])
    return value


def _walk(value, depth: int, count: list[int]) -> None:
    if depth > 32:
        raise ValueError("receipt exceeds depth 32")
    count[0] += 1
    if count[0] > 50_000:
        raise ValueError("receipt exceeds 50000 nodes")
    if isinstance(value, str):
        if len(value.encode("utf-8", "surrogatepass")) > 262_144:
            raise ValueError("string exceeds 256 KiB")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValueError("lone surrogate is not portable")
    elif isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > SAFE:
            raise ValueError("integer is outside the portable range")
    elif isinstance(value, float):
        raise ValueError("floats are outside bulla-jcs-int/1")
    elif isinstance(value, list):
        for item in value:
            _walk(item, depth + 1, count)
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk(key, depth + 1, count)
            _walk(item, depth + 1, count)
    elif value is not None and not isinstance(value, bool):
        raise ValueError("unsupported JSON value")


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be", "surrogatepass")


def canon(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > SAFE:
            raise ValueError("integer is outside the portable range")
        return str(value)
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValueError("lone surrogate")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canon(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_utf16_key)
        return "{" + ",".join(canon(key) + ":" + canon(value[key]) for key in keys) + "}"
    raise ValueError("unsupported canonical value")


def H(value) -> str:
    return "sha256:" + hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def envelope(receipt: dict) -> dict:
    mandate, remedy, retention = receipt["mandate"], receipt["remedy"], receipt["retention"]
    out = {"deed_schema": mandate.get("deed_schema", "0.2")}
    if mandate.get("authority"):
        out["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        out["bounds"] = mandate["bounds"]
    if remedy:
        out["recourse"] = remedy
    if retention.get("record"):
        out["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        out["disclosure_class"] = retention["disclosure"]
    return out


def hashes(receipt: dict) -> dict[str, str]:
    content_preimage = {
        "schema_version": "0.4", "kind": receipt["kind"],
        "action": receipt["action"], "diagnostic_ref": receipt["diagnostic_ref"],
        "evidence_refs": receipt["evidence_refs"], "anchor_ref": receipt["anchor_ref"],
        "canonicalization": PROFILE,
    }
    if receipt["conventions"]:
        content_preimage["conventions"] = receipt["conventions"]
    content = H(content_preimage)
    event = H({
        "content_hash": content, "event_id": receipt["event_id"],
        "claimed_at": receipt["claimed_at"],
    })
    env = envelope(receipt)
    authorization = H({"event_hash": event, "envelope_hash": H(env)})
    attestation = H({
        "content_hash": content, "signature": receipt["signature"],
        "event_hash": event, "occurrence": receipt["occurrence"],
        "recourse_envelope": env, "authorization": receipt["authorization"],
    })
    leaf = "sha256:" + hashlib.sha256(b"\x00" + attestation.encode("utf-8")).hexdigest()
    return {
        "content": content, "event": event, "authorization": authorization,
        "attestation": attestation, "log_leaf": leaf,
    }


def _b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + B58.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def verify_proofs(receipt: dict, computed: dict[str, str]) -> bool | None:
    try:
        from nacl.signing import VerifyKey
    except ImportError:
        return None
    purposes = (
        ("content", computed["content"], receipt["signature"]),
        ("occurrence", computed["event"], receipt["occurrence"]),
        ("authorization", computed["authorization"], receipt["authorization"]),
    )
    signer = None
    for purpose, digest, proof in purposes:
        if set(proof) != {"type", "purpose", "issuer", "verificationMethod", "proofValue"}:
            return False
        if proof["purpose"] != purpose or proof["issuer"] != proof["verificationMethod"]:
            return False
        if signer is None:
            signer = proof["verificationMethod"]
        elif signer != proof["verificationMethod"]:
            return False
        raw = _b58decode(proof["verificationMethod"].removeprefix("did:key:z"))
        if raw[:2] != b"\xed\x01" or len(raw[2:]) != 32:
            return False
        preimage = json.dumps(
            {"context": "bulla-proof", "schema": "0.4", "purpose": purpose, "digest": digest},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        try:
            VerifyKey(raw[2:]).verify(preimage, base64.b64decode(proof["proofValue"], validate=True))
        except Exception:
            return False
    return True


def verify(path: Path) -> dict:
    receipt = parse(path.read_bytes())
    computed = hashes(receipt)
    stored = receipt["hashes"]
    digest_ok = all(stored[key] == computed[key] for key in ("content", "event", "attestation", "log_leaf"))
    proof_ok = verify_proofs(receipt, computed)
    return {"digest_ok": digest_ok, "proofs_ok": proof_ok, "hashes": computed}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("v04-occurrence-bound.json")
    result = verify(target)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["digest_ok"] and result["proofs_ok"] is not False else 1)
