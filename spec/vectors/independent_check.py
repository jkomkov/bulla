"""Independent, stdlib-ONLY verifier for the bulla receipt vectors.

This file imports nothing from bulla. It is the acceptance test that the wire
spec (``../action-receipt-v0.2.md`` and the WitnessReceipt canonicalization
section of ``../../WITNESS-CONTRACT.md``) is sufficient on its own: a second
implementer reproduces every hash, the modality law, the convention pins and
executable conformance, and the CANON-2/legacy distinction from the spec
alone. When this agrees with bulla's verdicts on the golden vectors, the
*spec* — not the source — is the contract, and the receipt is a protocol
object rather than a library artifact.

Scope: the ``digest`` rung. Signature verification is standard ed25519/COSE —
a second implementer uses their own crypto library — so it is out of scope
for a hashing acceptance test.

    python bulla/spec/vectors/independent_check.py   # verify every vector vs expected.json
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_RUNGS = {"recompute", "challenge", "cure", "revert", "slash", "escalate"}
_GROUNDING = ("self_asserted", "counterparty_signed", "third_party_anchored", "execution_verified")


def _canon(x) -> str:
    """CANON_VERSION 2 — spec §1."""
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def _legacy(x) -> str:
    """CANON_VERSION 1 (witness layer, pre-v2) — verification fallback only."""
    return json.dumps(x, sort_keys=True)


def _H(x) -> str:
    return "sha256:" + hashlib.sha256(_canon(x).encode("utf-8")).hexdigest()


# ── ActionReceipt (spec/action-receipt-v0.2.md) ──────────────────────────────

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
    pre = {
        "schema_version": r["schema_version"],   # the receipt's OWN version
        "kind": r["kind"],
        "action": r["action"],
        "diagnostic_ref": r["diagnostic_ref"],
        "evidence_refs": r.get("evidence_refs", []),
        "anchor_ref": r.get("anchor_ref", {}),
    }
    if r.get("conventions"):                      # present iff non-empty (§4.1)
        pre["conventions"] = r["conventions"]
    return _H(pre)


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


def _definition_hash(defn) -> str:
    """§5.3 — canonical JSON for structured definitions, raw UTF-8 for opaque."""
    if isinstance(defn, str):
        return "sha256:" + hashlib.sha256(defn.encode("utf-8")).hexdigest()
    return _H(defn)


def _convention_reasons(r: dict) -> list[str]:
    """Entry validity (§5): shape, pin, kind law. Fail closed."""
    reasons: list[str] = []
    for c in r.get("conventions") or []:
        name = c.get("name") or "<unnamed>"
        if not (c.get("name") or "").strip() or not (c.get("scope") or "").strip():
            reasons.append(f"convention {name!r}: name and scope are required")
        kind = c.get("kind")
        if kind not in ("executable", "semantic"):
            reasons.append(f"convention {name!r}: unknown kind {kind!r}")
            continue
        dh = c.get("definition_hash") or ""
        if not dh.startswith("sha256:"):
            reasons.append(f"convention {name!r}: definition_hash required")
        if kind == "executable":
            if "definition" not in c:
                reasons.append(f"convention {name!r}: executable definition must be in-line")
            elif _definition_hash(c["definition"]) != dh:
                reasons.append(f"convention {name!r}: definition_hash does not match definition")
        else:
            forum = c.get("forum") or {}
            if not (forum.get("log_endpoint") or "").strip() or not (forum.get("trusted_root_ref") or "").strip():
                reasons.append(f"convention {name!r}: semantic convention requires a forum (Pin-the-Root)")
            if c.get("definition") is not None and _definition_hash(c["definition"]) != dh:
                reasons.append(f"convention {name!r}: definition_hash does not match definition")
    return reasons


def _conformance(c: dict, subject: dict) -> str:
    """§5.1/§5.2 — recompute one convention's verdict over action.subject."""
    if c.get("kind") == "semantic":
        return "pinned"
    defn = c["definition"]
    schema = defn.get("schema") or {}
    props = schema.get("properties") or {}
    ok = True
    for req in schema.get("required") or []:
        ok &= req in subject
    if schema.get("additionalProperties") is False:
        ok &= all(k in props for k in subject)
    for pname, ps in props.items():
        if pname not in subject:
            continue
        v = subject[pname]
        t = ps.get("type")
        if t == "string":
            ok &= isinstance(v, str)
        elif t == "integer":
            ok &= isinstance(v, int) and not isinstance(v, bool)
        elif t == "number":
            ok &= isinstance(v, (int, float)) and not isinstance(v, bool)
        elif t == "boolean":
            ok &= isinstance(v, bool)
        if "const" in ps:
            ok &= v == ps["const"]
        if "enum" in ps:
            ok &= v in ps["enum"]
        if "minimum" in ps and isinstance(v, (int, float)):
            ok &= v >= ps["minimum"]
        if "maximum" in ps and isinstance(v, (int, float)):
            ok &= v <= ps["maximum"]
        if "pattern" in ps and isinstance(v, str):
            ok &= re.search(ps["pattern"], v) is not None
    for fname, q in (defn.get("quantum") or {}).items():
        v = subject.get(fname)
        ok &= isinstance(v, int) and not isinstance(v, bool) and v % q.get("multipleOf", 1) == 0
    return "conforms" if ok else "violates"


def _effective_grounding(r: dict) -> str | None:
    """§3 display rule: minimum class over carried evidence; None if unspecified."""
    refs = r.get("evidence_refs") or []
    ranks = [_GROUNDING.index(e["grounding"]) for e in refs if e.get("grounding") in _GROUNDING]
    if not ranks or len(ranks) != len(refs):
        return None
    return _GROUNDING[min(ranks)]


def verify_action_receipt(r: dict) -> dict:
    """Verify to the digest rung. Returns {ok, verified_to, reasons, conventions, effective_grounding}."""
    reasons: list[str] = []
    dr = r.get("diagnostic_ref") or {}
    st = dr.get("status")
    if st not in ("reference", "not_applicable", "deferred"):
        reasons.append("diagnostic_ref.status invalid or null")
    if st == "reference" and not (dr.get("ref") or "").strip():
        reasons.append("diagnostic_ref status 'reference' without a ref")
    if r.get("schema_version") == "0.2":
        for e in r.get("evidence_refs") or []:
            if e.get("grounding") not in _GROUNDING:
                reasons.append(f"evidence {e.get('name')!r}: v0.2 requires a grounding class")
    reasons += _modality_reasons(r)
    reasons += _convention_reasons(r)

    stored = r.get("hashes") or {}
    c = content_hash(r)
    a = attestation_hash(r, c)
    computed = {"content": c, "event": event_hash(c, r.get("timestamp", "")),
                "attestation": a, "log_leaf": log_leaf(a)}
    for name, val in computed.items():
        if val != stored.get(name):
            reasons.append(f"{name} hash mismatch (spec recomputed {val} != stored {stored.get(name)})")

    ok = not reasons
    conv = {c_["name"]: _conformance(c_, (r.get("action") or {}).get("subject") or {})
            for c_ in (r.get("conventions") or []) if ok}
    return {"ok": ok, "verified_to": "digest" if ok else "none", "reasons": reasons,
            "conventions": conv, "effective_grounding": _effective_grounding(r) if ok else None}


# ── WitnessReceipt (WITNESS-CONTRACT.md, CANON_VERSION 2) ────────────────────

def verify_witness_receipt(r: dict) -> dict:
    """receipt_hash covers every field except receipt_hash and anchor_ref.
    Try CANON-2 (compact) first, then the legacy spaced form — a format
    change is a version difference, not tampering."""
    claimed = r.get("receipt_hash")
    if claimed is None:
        return {"ok": False, "verified_to": "none", "canon": None,
                "reasons": ["no receipt_hash"]}
    obj = {k: v for k, v in r.items() if k not in ("receipt_hash", "anchor_ref")}
    if hashlib.sha256(_canon(obj).encode()).hexdigest() == claimed:
        return {"ok": True, "verified_to": "digest", "canon": 2, "reasons": []}
    if hashlib.sha256(_legacy(obj).encode()).hexdigest() == claimed:
        return {"ok": True, "verified_to": "digest", "canon": 1, "reasons": []}
    return {"ok": False, "verified_to": "none", "canon": None,
            "reasons": ["receipt_hash matches neither canon-2 nor legacy form"]}


# ── the acceptance run ───────────────────────────────────────────────────────

def main() -> int:
    here = Path(__file__).resolve().parent
    expected = json.loads((here / "expected.json").read_text())
    failures = 0
    for name, want in sorted(expected.items()):
        r = json.loads((here / name).read_text())
        if want.get("kind") == "witness_receipt":
            got = verify_witness_receipt(r)
        else:
            got = verify_action_receipt(r)
        agree = all(got.get(k) == want[k] for k in want if k != "kind")
        extra = "".join(
            f" {k}={got.get(k)}" for k in ("canon", "effective_grounding") if k in want
        )
        print(f"  {'✓' if agree else '✗'} {name:28s} independent: ok={got['ok']} "
              f"verified_to={got['verified_to']}{extra}  (expected ok={want['ok']})")
        if want.get("conventions") and agree:
            for cname, status in got["conventions"].items():
                print(f"        convention {cname}: {status}")
        if not agree:
            failures += 1
            for why in got["reasons"]:
                print(f"        · {why}")
    print(f"\n{'OK' if not failures else 'FAIL'}: the spec reproduces "
          f"{len(expected) - failures}/{len(expected)} verdicts with zero bulla imports")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
