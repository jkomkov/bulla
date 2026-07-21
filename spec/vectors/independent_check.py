"""Independent, stdlib-ONLY verifier for the bulla receipt vectors.

This file imports nothing from bulla. It is the acceptance test that the wire
spec (``../action-receipt-v0.2.md``, ``../action-receipt-v0.3-draft.md``, and the WitnessReceipt canonicalization
section of ``../../WITNESS-CONTRACT.md``) is sufficient on its own: a second
implementer reproduces every hash, the modality law, the convention pins and
executable conformance, and the CANON-2/legacy distinction from the spec
alone. When this agrees with bulla's verdicts on the golden vectors, the
*spec* — not the source — is the contract, and the receipt is a protocol
object rather than a library artifact.

TWO RUNGS, honestly separated (verification depth is part of the thesis):

  * the **stdlib rung** (``digest``) — canonicalization, the four hashes, the
    modality law, convention pins and executable conformance, and the
    CANON-2/legacy distinction. Zero dependencies, and the guaranteed contract.
  * the **identity rung** (``attestation``) — ed25519 signature and authority
    verification. This needs an ed25519 library (an OPTIONAL audited dependency;
    the stdlib has no ed25519, and hand-rolled crypto is worse than none). When
    the library is absent the run REPORTS the skip and still passes the stdlib
    contract — it never pretends to a depth it did not reach.

The signed vectors (``signed-authorized.json`` / ``tampered-authority*.json``)
carry a split ``expected`` entry: ``ok``/``verified_to`` for the stdlib rung and
an ``identity`` block for the signature rung. The forgery vector is instructive
— it is structurally valid at the stdlib rung, and only the identity rung sees
the swapped authority.

    python bulla/spec/vectors/independent_check.py   # verify every vector vs expected.json
"""

from __future__ import annotations

import hashlib
import json
import math
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


# ── ActionReceipt (v0.2 + v0.3 authority-binding draft) ─────────────────────

def _envelope_from_views(mandate: dict, remedy: dict, retention: dict) -> dict:
    # deed_schema must survive the view round-trip ("0.3" carries structured
    # delegation grants); the receipt serializes it inside the mandate view.
    env: dict = {"deed_schema": (mandate or {}).get("deed_schema", "0.2")}
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


def envelope_hash(r: dict) -> str:
    return _H(_envelope_from_views(r.get("mandate", {}), r.get("remedy", {}), r.get("retention", {})))


def authorization_hash(r: dict, content: str) -> str:
    """§ authority binding — H({content_hash, envelope_hash}). The issuer signs
    THIS to vouch for the envelope; content alone is envelope-free."""
    return _H({"content_hash": content, "envelope_hash": envelope_hash(r)})


def attestation_hash(r: dict, content: str) -> str:
    env = _envelope_from_views(r.get("mandate", {}), r.get("remedy", {}), r.get("retention", {}))
    pre = {"content_hash": content, "signature": r.get("signature"), "recourse_envelope": env}
    if r.get("schema_version") == "0.3":
        pre["authorization"] = r.get("authorization")
    return _H(pre)


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


def _executable_definition_reasons(defn) -> list[str]:
    """Closed-form validation, kept byte-independent from the Bulla package."""
    reasons: list[str] = []
    if not isinstance(defn, dict):
        return ["executable definition must be an object"]
    if defn.get("form") != "jsonschema+quantum/1":
        reasons.append("executable definition.form invalid")
    if set(defn) - {"form", "schema", "quantum"}:
        reasons.append("unknown executable-definition keys")
    schema = defn.get("schema")
    if not isinstance(schema, dict):
        return reasons + ["executable definition needs a schema object"]
    if set(schema) - {"type", "properties", "required", "additionalProperties"}:
        reasons.append("unknown schema keywords")
    if schema.get("type", "object") != "object":
        reasons.append("schema.type must be object")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return reasons + ["schema.properties must be an object"]
    required = schema.get("required", [])
    if (not isinstance(required, list)
            or any(not isinstance(x, str) or not x.strip() for x in required)
            or len(set(required)) != len(required)):
        reasons.append("schema.required must be a unique list of non-empty field names")
    if not isinstance(schema.get("additionalProperties", True), bool):
        reasons.append("schema.additionalProperties must be boolean")
    prop_keys = {"type", "enum", "const", "minimum", "maximum", "pattern"}
    prop_types = {"string", "integer", "number", "boolean"}
    for pname, ps in props.items():
        if not isinstance(pname, str) or not pname.strip() or not isinstance(ps, dict):
            reasons.append(f"schema property {pname!r} malformed")
            continue
        if set(ps) - prop_keys:
            reasons.append(f"schema property {pname!r} has unknown keywords")
        ptype = ps.get("type")
        if ptype is not None and ptype not in prop_types:
            reasons.append(f"schema property {pname!r} has unknown type")
        if "enum" in ps and (not isinstance(ps["enum"], list) or not ps["enum"]):
            reasons.append(f"schema property {pname!r} enum must be a non-empty list")
        for keyword in ("minimum", "maximum"):
            if keyword not in ps:
                continue
            value = ps[keyword]
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or ptype not in ("integer", "number")):
                reasons.append(f"schema property {pname!r} {keyword} malformed")
        if "minimum" in ps and "maximum" in ps and ps["minimum"] > ps["maximum"]:
            reasons.append(f"schema property {pname!r} minimum exceeds maximum")
        if "pattern" in ps:
            if ptype != "string" or not isinstance(ps["pattern"], str):
                reasons.append(f"schema property {pname!r} pattern malformed")
            else:
                try:
                    re.compile(ps["pattern"])
                except re.error:
                    reasons.append(f"schema property {pname!r} pattern invalid")
    quantum = defn.get("quantum")
    if quantum is not None:
        if not isinstance(quantum, dict):
            return reasons + ["quantum must be an object"]
        for fname, q in quantum.items():
            if (not isinstance(fname, str) or not fname.strip() or not isinstance(q, dict)
                    or set(q) - {"unit", "multipleOf"}):
                reasons.append(f"quantum field {fname!r} malformed")
                continue
            mo = q.get("multipleOf", 1)
            if (not isinstance(q.get("unit"), str) or not q["unit"].strip()
                    or not isinstance(mo, int) or isinstance(mo, bool) or mo < 1):
                reasons.append(f"quantum field {fname!r} malformed")
            if fname not in props or not isinstance(props[fname], dict) \
                    or props[fname].get("type") != "integer":
                reasons.append(f"quantum field {fname!r} requires an integer property")
    return reasons


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
            else:
                reasons.extend(
                    f"convention {name!r}: {why}"
                    for why in _executable_definition_reasons(c["definition"])
                )
                if _definition_hash(c["definition"]) != dh:
                    reasons.append(f"convention {name!r}: definition_hash does not match definition")
        else:
            forum = c.get("forum") or {}
            if not (forum.get("log_endpoint") or "").strip() or not (forum.get("trusted_root_ref") or "").strip():
                reasons.append(f"convention {name!r}: semantic convention requires a forum (Pin-the-Root)")
            if c.get("definition") is not None and _definition_hash(c["definition"]) != dh:
                reasons.append(f"convention {name!r}: definition_hash does not match definition")
    return reasons


def _definition_conforms(defn: dict, subject: dict) -> bool:
    """True iff ``subject`` satisfies an executable ``jsonschema+quantum/1`` ``defn``.
    Shared by convention conformance and bounds-scope conformance."""
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
    return ok


def _conformance(c: dict, subject: dict) -> str:
    """§5.1/§5.2 — recompute one convention's verdict over action.subject."""
    if c.get("kind") == "semantic":
        return "pinned"
    return "conforms" if _definition_conforms(c["definition"], subject) else "violates"


def _bounds_conformance(r: dict) -> str:
    """Did the ACT obey a structured bounds.scope? The missing half of authorization,
    recomputed at the digest rung (crypto-free). "not_applicable" for a prose scope."""
    scope = ((r.get("mandate") or {}).get("bounds") or {}).get("scope")
    if not isinstance(scope, dict):
        return "not_applicable"
    subject = (r.get("action") or {}).get("subject")
    if not isinstance(subject, dict):
        return "not_checkable"
    return "conforms" if _definition_conforms(scope, subject) else "violates"


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
    schema_version = r.get("schema_version")
    if schema_version not in ("0.1", "0.2", "0.3"):
        reasons.append(f"unknown schema_version {schema_version!r}")
    if schema_version in ("0.2", "0.3"):
        for e in r.get("evidence_refs") or []:
            if e.get("grounding") not in _GROUNDING:
                reasons.append(f"evidence {e.get('name')!r}: v0.2+ requires a grounding class")
    if "authorization" in r and schema_version != "0.3":
        reasons.append("authorization member is a v0.3 field")
    if schema_version == "0.3" and "authorization" not in r:
        reasons.append("v0.3 receipt is missing authorization member")
    reasons += _modality_reasons(r)
    reasons += _convention_reasons(r)
    scope = ((r.get("mandate") or {}).get("bounds") or {}).get("scope")
    if isinstance(scope, dict):
        reasons += [f"bounds.scope: {why}" for why in _executable_definition_reasons(scope)]

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
            "conventions": conv, "effective_grounding": _effective_grounding(r) if ok else None,
            "bounds_conformance": _bounds_conformance(r) if ok else "not_applicable"}


# ── identity rung (OPTIONAL ed25519; needs an audited crypto library) ────────

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


def _ed25519_pubkey_from_did_key(did: str | None) -> bytes | None:
    """did:key:z<base58btc(0xed01 ‖ 32-byte pubkey)> → the raw ed25519 key."""
    if not did or not did.startswith("did:key:z"):
        return None
    try:
        raw = _b58decode(did[len("did:key:z"):])
    except (ValueError, IndexError):
        return None
    if raw[:2] != b"\xed\x01" or len(raw) != 34:
        return None
    return raw[2:]


_PROOF_CONTEXT = "bulla-proof"
_MAX_DEPTH = 8


def _domain_preimage(purpose: str, digest: str) -> bytes:
    """v0.3 signed bytes: canonical {context, schema, purpose, digest}."""
    return _canon(
        {"context": _PROOF_CONTEXT, "schema": "0.3", "purpose": purpose, "digest": digest}
    ).encode("utf-8")


def _hash_ref(reference: str) -> str:
    return "sha256:" + hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _grant_hash(grant: dict) -> str:
    core = {k: grant[k] for k in ("grantor", "grantee", "principal", "parent", "policy_digest", "scope_digest")}
    for opt in ("not_before", "not_after"):
        if grant.get(opt) is not None:
            core[opt] = grant[opt]
    return _H(core)


#: Every member a grant may carry. Unknown members are REJECTED, never ignored:
#: an unknown field sits outside grant_hash and therefore outside the grantor's
#: signature.
_GRANT_KEYS = frozenset({
    "grantor", "grantee", "principal", "parent", "policy_digest", "scope_digest",
    "not_before", "not_after", "proof",
})
_GRANT_REQUIRED = frozenset({
    "grantor", "grantee", "principal", "parent", "policy_digest", "scope_digest", "proof",
})
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _is_hash(value) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def _grant_shape_valid(grant) -> bool:
    """Validate every member whose semantics the grant signature covers."""
    if not isinstance(grant, dict) or not _GRANT_REQUIRED.issubset(grant):
        return False
    if set(grant) - _GRANT_KEYS:
        return False
    if any(_ed25519_pubkey_from_did_key(grant.get(k)) is None
           for k in ("grantor", "grantee", "principal")):
        return False
    if grant.get("parent") is not None and not _is_hash(grant.get("parent")):
        return False
    if not _is_hash(grant.get("policy_digest")) or not _is_hash(grant.get("scope_digest")):
        return False
    for name in ("not_before", "not_after"):
        if name not in grant:
            continue
        bound = grant[name]
        if not isinstance(bound, dict) or set(bound) != {"domain", "value"}:
            return False
        if not isinstance(bound["domain"], str) or not bound["domain"].strip():
            return False
        if (not isinstance(bound["value"], int) or isinstance(bound["value"], bool)
                or bound["value"] < 0):
            return False
    if "not_before" in grant and "not_after" in grant:
        before, after = grant["not_before"], grant["not_after"]
        if before["domain"] != after["domain"] or before["value"] > after["value"]:
            return False
    return isinstance(grant.get("proof"), dict)


def _verify_delegation(r: dict, verify_domain) -> dict:
    """Reproduce the six independent delegation dimensions from the spec, with zero
    bulla imports. ``verify_domain(proof, purpose, digest, expect_signer)`` returns
    True/False/'unresolved'/None. See spec/delegation-design-note.md."""
    mandate = r.get("mandate") or {}
    authority = mandate.get("authority") or {}
    bounds = mandate.get("bounds") or {}
    default = {"chain_integrity": "not_applicable", "principal_binding": "not_applicable",
               "policy_binding": "not_applicable", "scope_binding": "not_applicable",
               "temporal_status": "not_applicable", "revocation_status": "not_applicable"}
    if r.get("schema_version") != "0.3" or mandate.get("deed_schema") != "0.3" or not authority:
        return default
    raw_grants = authority.get("delegation")
    grants = [] if raw_grants is None else raw_grants
    principal = authority.get("principal", "")
    leaf_vm = (r.get("signature") or {}).get("verificationMethod")

    def _is_dk(s):
        return _ed25519_pubkey_from_did_key(s) is not None

    if not isinstance(grants, list):
        return {
            "chain_integrity": "broken", "principal_binding": "unresolved",
            "policy_binding": "mismatch", "scope_binding": "mismatch",
            "temporal_status": "unresolved", "revocation_status": "unresolved",
        }

    if not grants:
        pb = "unresolved" if (not _is_dk(principal) or leaf_vm is None) else (
            "verified" if leaf_vm == principal else "wrong_principal")
        return dict(default, principal_binding=pb)

    # chain_integrity
    ci = "verified"
    if len(grants) > _MAX_DEPTH:
        ci = "over_depth"
    elif not all(_grant_shape_valid(g) for g in grants):
        ci = "broken"
    else:
        for g in grants:
            proof = g.get("proof")
            # A grantor is self-certifying: the key derives from `grant.grantor`, never
            # from the proof's own claim. Unknown members fail closed.
            if (not isinstance(proof, dict)
                    or proof.get("issuer") != g.get("grantor")
                    or proof.get("verificationMethod") != g.get("grantor")
                    or verify_domain(proof, "delegation-grant", _grant_hash(g),
                                     expect_signer=g.get("grantor")) is not True):
                ci = "broken"
                break
        if ci == "verified":
            if grants[0].get("parent") is not None:
                ci = "broken"
            else:
                for i in range(1, len(grants)):
                    if grants[i].get("grantor") != grants[i - 1].get("grantee") \
                            or grants[i].get("parent") != _grant_hash(grants[i - 1]):
                        ci = "broken"
                        break
        if ci == "verified":
            path = [grants[0].get("grantor")] + [g.get("grantee") for g in grants]
            if len(set(path)) != len(path):
                ci = "cycle"

    # principal_binding
    if ci == "broken":
        pb = "unresolved"
    elif not _is_dk(principal) or leaf_vm is None:
        pb = "unresolved"
    elif grants[0].get("grantor") != principal or any(g.get("principal") != principal for g in grants) \
            or grants[-1].get("grantee") != leaf_vm:
        pb = "wrong_principal"
    else:
        pb = "verified"

    # policy_binding / scope_binding — hash agreement only, never authorization.
    policy_ref = authority.get("policy")
    expected_pd = _hash_ref(policy_ref) if isinstance(policy_ref, str) and policy_ref.strip() else None
    polb = "verified" if ci != "broken" and expected_pd is not None and all(
        g.get("policy_digest") == expected_pd for g in grants
    ) else "mismatch"
    scope_ref = bounds.get("scope")
    if scope_ref is None or (isinstance(scope_ref, str) and not scope_ref.strip()):
        scopeb = "mismatch"   # no declared scope to bind to — fail closed
    else:
        # Polymorphic pin: prose → UTF-8 (byte-identical), structured predicate → canonical.
        expected_sd = _hash_ref(scope_ref) if isinstance(scope_ref, str) else _H(scope_ref)
        scopeb = "verified" if ci != "broken" and all(
            g.get("scope_digest") == expected_sd for g in grants
        ) else "mismatch"

    # temporal: only same-domain typed positions are comparable; no checkpoint is
    # supplied to a static vector, so a windowed grant stays unresolved.
    temporal = "unresolved"
    # revocation transport is unbuilt — silence is never "still in force".
    return {"chain_integrity": ci, "principal_binding": pb, "policy_binding": polb,
            "scope_binding": scopeb, "temporal_status": temporal,
            "revocation_status": "unresolved"}


def verify_identity_rung(r: dict) -> dict:
    """Verify the content signature and the authorization proof (v0.3:
    domain-separated; v0.2: over the raw digest) with an ed25519 library, and —
    for v0.3 structured delegation — reproduce the six delegation dimensions.
    Returns ``{available: False}`` when no ed25519 library is installed (the
    honest skip, never a hand-rolled signature check)."""
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except Exception:
        return {"available": False}
    import base64

    v03 = r.get("schema_version") == "0.3"
    content = content_hash(r)

    def _verify(proof, purpose: str, digest: str, expect_signer: str | None = None):
        """`expect_signer`, when given, is the did:key the signature MUST verify
        under — the key is derived from that identity, never from the proof's own
        claim. Delegation grants pass their `grantor`, so no proof claim (and no
        caller-supplied key) can stand in for an upstream principal."""
        if not proof:
            return None
        if not isinstance(proof, dict):
            return False
        expected_keys = {"type", "issuer", "verificationMethod", "proofValue"}
        if v03:
            expected_keys.add("purpose")
        if set(proof) != expected_keys:
            return False
        if proof.get("type") != "bulla/ed25519-2026":
            return False
        if v03 and proof.get("purpose") != purpose:
            return False
        issuer = proof.get("issuer")
        verification_method = proof.get("verificationMethod")
        signer = expect_signer if expect_signer is not None else issuer
        if signer != issuer or verification_method != signer:
            return False
        pk = _ed25519_pubkey_from_did_key(signer)
        if pk is None:
            return "unresolved"  # non-did:key issuer — key resolution out of scope
        signed = _domain_preimage(purpose, digest) if v03 else digest.encode("utf-8")
        try:
            VerifyKey(pk).verify(signed, base64.b64decode(proof["proofValue"], validate=True))
            return True
        except (BadSignatureError, KeyError, TypeError, ValueError):
            return False

    sig = r.get("signature")
    auth = r.get("authorization")
    sig_res = _verify(sig, "content", content)
    env_nontrivial = bool(r.get("mandate") or r.get("remedy"))

    if v03 and sig and not auth and env_nontrivial:
        authority = "unauthenticated"
    elif auth and not sig:
        authority = "forged"
    elif auth:
        auth_res = _verify(auth, "authorization", authorization_hash(r, content))
        authority = {True: "verified", False: "forged", "unresolved": "unresolved"}[auth_res]
        signer_fields = ("type", "issuer", "verificationMethod")
        if authority == "verified" and (
            not isinstance(sig, dict)
            or not all(sig.get(k) == auth.get(k) for k in signer_fields)
        ):
            authority = "forged"
    else:
        authority = "unauthenticated" if env_nontrivial else "not_applicable"

    content_ok = sig_res is True
    authority_ok = authority not in ("forged", "unauthenticated") if v03 else authority != "forged"
    ok = bool(content_ok and authority_ok and (sig or auth))
    out = {
        "available": True,
        "ok": ok,
        "verified_to": "attestation" if ok else "digest",
        "signature_authentic": (sig_res if sig_res is not None else None),
        "authority_authentic": authority,
    }
    # delegation dimensions are computed on the fully-verified success path only
    out.update(_verify_delegation(r, _verify) if ok else {})
    return out


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
    identity_skipped = 0
    for name, want in sorted(expected.items()):
        r = json.loads((here / name).read_text())
        if want.get("kind") == "witness_receipt":
            got = verify_witness_receipt(r)
        else:
            got = verify_action_receipt(r)
        stdlib_keys = [k for k in want if k not in ("kind", "identity")]
        agree = all(got.get(k) == want[k] for k in stdlib_keys)
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

        # optional identity rung — only for vectors that carry a signature expectation
        if "identity" in want:
            idr = verify_identity_rung(r)
            if not idr.get("available"):
                identity_skipped += 1
                print("        identity rung: SKIPPED (no ed25519 library) — "
                      "stdlib structure verified; signature depth not reached")
            else:
                want_id = want["identity"]
                id_agree = all(idr.get(k) == want_id[k] for k in want_id)
                print(f"        {'✓' if id_agree else '✗'} identity rung: ok={idr['ok']} "
                      f"verified_to={idr['verified_to']} signature={idr['signature_authentic']} "
                      f"authority={idr['authority_authentic']}")
                if not id_agree:
                    failures += 1
                    print(f"          expected {want_id}")

    tail = f" ({identity_skipped} identity rung(s) skipped — no ed25519 lib)" if identity_skipped else ""
    print(f"\n{'OK' if not failures else 'FAIL'}: the spec reproduces "
          f"{len(expected) - failures}/{len(expected)} verdicts with zero bulla imports{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
