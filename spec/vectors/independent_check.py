"""Independent, stdlib-ONLY verifier for ActionReceipt v0.1.

This file imports nothing from bulla. It is the acceptance test that the wire
spec (``../action-receipt-v0.1.md``) is sufficient on its own: a second
implementer reproduces every hash and the modality law from the spec alone. When
this agrees with bulla's verdicts on the golden vectors, the *spec* — not the
source — is the contract, and the receipt is a protocol object rather than a
library artifact.

Scope: the ``digest`` rung (the four hashes + the modality law). Signature
verification is standard ed25519/COSE — a second implementer uses their own
crypto library — so it is out of scope for a hashing acceptance test.

    python bulla/spec/vectors/independent_check.py   # verify every vector vs expected.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_RUNGS = {"recompute", "challenge", "cure", "revert", "slash", "escalate"}


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def _H(x) -> str:
    return "sha256:" + hashlib.sha256(_canon(x).encode("utf-8")).hexdigest()


def _envelope_from_views(mandate: dict, remedy: dict, retention: dict) -> dict:
    env: dict = {"deed_schema": "0.2"}
    if mandate.get("authority"):
        env["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        env["bounds"] = mandate["bounds"]
    if remedy:
        env["recourse"] = remedy
    if retention.get("record"):
        env["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        env["disclosure_class"] = retention["disclosure"]
    return env


def content_hash(r: dict) -> str:
    return _H({
        "schema_version": r["schema_version"],
        "kind": r["kind"],
        "action": r["action"],
        "diagnostic_ref": r["diagnostic_ref"],
        "evidence_refs": r.get("evidence_refs", []),
        "anchor_ref": r.get("anchor_ref", {}),
    })


def attestation_hash(r: dict, content: str) -> str:
    env = _envelope_from_views(r.get("mandate", {}), r.get("remedy", {}), r.get("retention", {}))
    return _H({"content_hash": content, "signature": r.get("signature"), "recourse_envelope": env})


def event_hash(content: str, timestamp: str) -> str:
    return _H({"content_hash": content, "timestamp": timestamp})


def log_leaf(attestation: str) -> str:
    # RFC 6962 leaf: H(0x00 || utf8(attestation-hash string))
    return "sha256:" + hashlib.sha256(b"\x00" + attestation.encode("utf-8")).hexdigest()


def _modality_reasons(r: dict) -> list[str]:
    reasons: list[str] = []
    remedy = r.get("remedy") or {}
    forum = remedy.get("forum") or {}
    if not (forum.get("trusted_root_ref") or "").strip():
        reasons.append("forum.trusted_root_ref missing (self-consistency is not recourse)")
    rems = remedy.get("remedies") or []
    if not rems:
        reasons.append("no remedies (an appeal path with no remedy is process theater)")
    has_auth = bool((r.get("mandate") or {}).get("authority"))
    for rem in rems:
        if rem.get("rung") not in _RUNGS:
            reasons.append(f"unknown rung {rem.get('rung')!r}")
        if not (rem.get("verifier") or "").strip():
            reasons.append("remedy without a verifier")
        if not (rem.get("anchor") or "").strip():
            reasons.append("remedy without a stateful anchor")
        if rem.get("rung") == "escalate" and not has_auth:
            reasons.append("escalate without authority")
    return reasons


def verify(r: dict) -> tuple[bool, str, list[str]]:
    """Verify to the digest rung. Returns (ok, verified_to, reasons)."""
    reasons: list[str] = []
    dr = r.get("diagnostic_ref") or {}
    st = dr.get("status")
    if st not in ("reference", "not_applicable", "deferred"):
        reasons.append("diagnostic_ref.status invalid or null")
    if st == "reference" and not (dr.get("ref") or "").strip():
        reasons.append("diagnostic_ref status 'reference' without a ref")
    reasons += _modality_reasons(r)

    stored = r.get("hashes") or {}
    c = content_hash(r)
    a = attestation_hash(r, c)
    computed = {"content": c, "event": event_hash(c, r.get("timestamp", "")),
                "attestation": a, "log_leaf": log_leaf(a)}
    for name, val in computed.items():
        if val != stored.get(name):
            reasons.append(f"{name} hash mismatch (spec recomputed {val} != stored {stored.get(name)})")

    ok = not reasons
    return ok, ("digest" if ok else "none"), reasons


def main() -> int:
    here = Path(__file__).resolve().parent
    expected = json.loads((here / "expected.json").read_text())
    failures = 0
    for name, want in sorted(expected.items()):
        r = json.loads((here / name).read_text())
        ok, verified_to, reasons = verify(r)
        agree = (ok == want["ok"])
        print(f"  {'✓' if agree else '✗'} {name:26s} independent: ok={ok} verified_to={verified_to}  "
              f"(expected ok={want['ok']})")
        if not agree:
            failures += 1
            for why in reasons:
                print(f"        · {why}")
    print(f"\n{'OK' if not failures else 'FAIL'}: the spec reproduces {len(expected) - failures}/{len(expected)} verdicts with zero bulla imports")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
