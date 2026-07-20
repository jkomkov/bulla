#!/usr/bin/env python3
"""Generate the golden vectors + expected.json.

Two families, one contract — the *spec* (not this repo's source) reproduces
every verdict:

ActionReceipt vectors
  * ``valid-release.json``        — bulla's real 0.40.0 release reconstruction (v0.1)
  * ``tampered-evidence.json``    — evidence mutated, hashes not recomputed
  * ``blank-remedy-anchor.json``  — a modality-law violation (process theater)
  * ``convention-receipt.json``   — a v0.2 receipt coining one executable and one
                                    semantic convention at a payment seam
  * ``tampered-convention.json``  — the convention relaxed after the fact (pin
                                    recomputed, so only the content hash catches it)

WitnessReceipt vectors (CANON_VERSION 2)
  * ``witness-canon2.json``       — a fresh v2 receipt (compact, canon stamped)
  * ``witness-legacy-v1.json``    — the same content minted the pre-v2 way
                                    (spaced, unstamped): MUST verify, as canon 1

``expected.json`` records bulla's ground-truth verdict for each;
``independent_check.py`` (which imports no bulla) must reproduce those
verdicts from the spec alone.

    python bulla/spec/vectors/generate.py
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from bulla._canonical import canonical_json, legacy_json_v1
from bulla.action_receipt import (
    ActionReceipt,
    build_tool_call_receipt,
    convention_definition_hash,
    sign_action_receipt,
    verify_receipt,
)
from bulla.identity import LocalEd25519Signer
from bulla.delegation import DelegationGrant, hash_ref, sign_grant
from bulla.diagnostic import diagnose
from bulla.envelope import (
    Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy,
)
from bulla.reliance import PRAGMATIC_RELIANCE_POLICY, build_reliance_receipt
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness import receipt_integrity_report, witness

_HERE = Path(__file__).resolve().parent
_CORPUS = _HERE.parents[1] / "releases" / "0.40.0.json"

_FIXED_TS = "2026-07-13T00:00:00+00:00"

# A FIXED ed25519 seed — ed25519 is deterministic (RFC 8032), so a fixed seed
# gives byte-reproducible signatures and diff-clean regenerated vectors. This
# key exists only to sign the golden vectors; it guards nothing.
_VECTOR_SEED = bytes(range(32))
_ATTACKER_SEED = bytes(reversed(range(32)))


def _h(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _raw_action_hashes(doc: dict) -> dict:
    """Hash a deliberately malformed fixture without asking the strict parser to
    reconstruct it. This proves independent verifiers reject its semantics even when
    an adversarial producer made every stored digest internally consistent."""
    pre = {
        "schema_version": doc["schema_version"],
        "kind": doc["kind"],
        "action": doc["action"],
        "diagnostic_ref": doc["diagnostic_ref"],
        "evidence_refs": doc["evidence_refs"],
        "anchor_ref": doc["anchor_ref"],
    }
    if doc.get("conventions"):
        pre["conventions"] = doc["conventions"]
    content = _h(pre)
    event = _h({"content_hash": content, "timestamp": doc["timestamp"]})
    mandate, remedy, retention = doc["mandate"], doc["remedy"], doc["retention"]
    envelope = {"deed_schema": mandate.get("deed_schema", "0.2")}
    if mandate.get("authority"):
        envelope["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        envelope["bounds"] = mandate["bounds"]
    if remedy:
        envelope["recourse"] = remedy
    if retention.get("record"):
        envelope["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        envelope["disclosure_class"] = retention["disclosure"]
    attestation_pre = {
        "content_hash": content,
        "signature": doc.get("signature"),
        "recourse_envelope": envelope,
    }
    if doc["schema_version"] == "0.3":
        attestation_pre["authorization"] = doc.get("authorization")
    attestation = _h(attestation_pre)
    log_leaf = "sha256:" + hashlib.sha256(b"\x00" + attestation.encode("utf-8")).hexdigest()
    return {"content": content, "event": event, "attestation": attestation, "log_leaf": log_leaf}

# The composition behind the witness vectors — small enough to recompute by
# hand, mirrored in tests/test_canonicalization.py.
_COMPOSITION = Composition(
    name="canon-guard",
    tools=(
        ToolSpec("a", ("x", "y"), ("x",)),
        ToolSpec("b", ("x", "z"), ("x",)),
    ),
    edges=(Edge("a", "b", (SemanticDimension("d", "y", "z"),)),),
)


def _envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal="did:key:zSpecVector", policy="policy://payments@sha256:aa"),
        bounds=Bounds(scope="payments.charge amount<=100000"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla receipt verify", anchor="hashes.content"),
                Remedy(rung="escalate", verifier="maintainer review", anchor="did:key:zSpecVector"),
            ),
        ),
        retention_class="operational",
        disclosure_class="party",
    )


def _convention_receipt_dict() -> dict:
    executable = {
        "name": "amount-in-usd-cents",
        "scope": "seam:caller->payments.charge",
        "kind": "executable",
        "definition": {
            "form": "jsonschema+quantum/1",
            "schema": {
                "type": "object",
                "required": ["amount", "currency"],
                "properties": {
                    "amount": {"type": "integer", "minimum": 0},
                    "currency": {"const": "USD"},
                },
            },
            "quantum": {"amount": {"unit": "USD_cents", "multipleOf": 1}},
        },
    }
    semantic = {
        "name": "refund-honored-in-full",
        "scope": "seam:caller->payments.charge",
        "kind": "semantic",
        "definition": "A refund request within the challenge window is honored in full.",
        "forum": {"log_endpoint": "https://log.example", "trusted_root_ref": "ots:root"},
    }
    r = build_tool_call_receipt(
        tool="payments.charge",
        call_subject={"amount": 1250, "currency": "USD", "merchant": "acme"},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "e" * 64},
        envelope=_envelope(),
        result_hash="sha256:" + "f" * 64,
        conventions=(executable, semantic),
        timestamp=_FIXED_TS,
        producer={"bulla_version": "0.43.0"},
    )
    return r.to_dict()


def _signed_dicts() -> tuple[dict, dict, dict]:
    """Honest receipt plus stale-proof and re-signed authority forgeries.

    ``signed-authorized`` carries BOTH proofs: a content signature (the claim)
    and an authorization proof over ``H(content_hash, envelope_hash)`` (the
    mandate). ``tampered-authority`` is the envelope-swap forgery: keep the valid
    content signature, rewrite the mandate to a wider scope and a new principal,
    and recompute every unsigned downstream hash so it passes the digest rung.
    Its lesson is depth: structurally each verifies, and ONLY the identity rung
    catches the forged authority. The re-signed variant uses an attacker's valid
    key, exercising the same-signer invariant rather than stale-signature failure."""
    signer = LocalEd25519Signer(seed=_VECTOR_SEED)
    r0 = build_tool_call_receipt(
        tool="github.create_file",
        call_subject={"repo": "acme/site", "path": "docs/policy.md"},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "c" * 64},
        envelope=_envelope(),
        timestamp=_FIXED_TS,
        producer={"bulla_version": "0.44.0"},
    )
    signed = sign_action_receipt(r0, signer).to_dict()

    forged = copy.deepcopy(signed)
    forged["mandate"]["authority"]["principal"] = "did:key:zATTACKER"
    forged["mandate"]["bounds"]["scope"] = "payments.charge amount<=100000000"
    forged["hashes"] = ActionReceipt.from_dict(forged).hashes()  # recompute — passes digest

    resigned = copy.deepcopy(forged)
    attacker = LocalEd25519Signer(seed=_ATTACKER_SEED)
    # The attacker mounts the STRONGEST substitution: a valid v0.3 domain-separated
    # authorization proof under their own key over the swapped envelope. It clears
    # verify_proof_domain; the same-signer invariant rejects the pair.
    resigned["authorization"] = attacker.sign_domain(
        "authorization", ActionReceipt.from_dict(resigned).authorization_hash
    )
    resigned["hashes"] = ActionReceipt.from_dict(resigned).hashes()
    return signed, forged, resigned


def _delegation_dict() -> dict:
    """A v0.3 receipt whose authority is backed by a signed did:key delegation
    chain P → M → L, with the leaf L signing the act. Demonstrates the four
    independent delegation dimensions all reaching ``verified``."""
    P = LocalEd25519Signer(seed=bytes([1]) + bytes(31))
    M = LocalEd25519Signer(seed=bytes([2]) + bytes(31))
    L = LocalEd25519Signer(seed=bytes([3]) + bytes(31))
    pol = "policy://payments@sha256:aa"
    # scope_digest binds to the RECEIPT's declared bounds.scope, byte for byte —
    # a grant that conveys some other string does not authorize this act.
    scope = "payments.charge amount<=100000"
    pd, sd = hash_ref(pol), hash_ref(scope)
    g0 = sign_grant(DelegationGrant(P.verification_method, M.verification_method,
                                    P.verification_method, None, pd, sd), P)
    g1 = sign_grant(DelegationGrant(M.verification_method, L.verification_method,
                                    P.verification_method, g0.grant_hash, pd, sd), M)
    env = RecourseEnvelope(
        authority=Authority(principal=P.verification_method, policy=pol,
                            delegation=(g0.to_dict(), g1.to_dict())),
        bounds=Bounds(scope=scope),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla receipt verify", anchor="hashes.content"),
                Remedy(rung="escalate", verifier="maintainer review", anchor=P.verification_method),
            ),
        ),
        deed_schema="0.3",
    )
    r = build_tool_call_receipt(
        tool="payments.charge", call_subject={"amount": 1250},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "d" * 64},
        envelope=env, timestamp=_FIXED_TS, producer={"bulla_version": "0.44.0"},
    )
    return sign_action_receipt(r, L).to_dict()


def _scoped_delegation_dict(amount: int) -> dict:
    """A v0.3 delegated receipt whose ``bounds.scope`` is a STRUCTURED
    ``jsonschema+quantum/1`` predicate, so ``bounds_conformance`` recomputes whether
    the act (``amount``) obeyed its scope — the missing half of authorization. Used
    twice: an in-scope act (``conforms``) and an over-scope act (``violates``). The
    over-scope receipt STILL verifies (``ok=True``): conformance is surfaced, not
    folded into record integrity. ``scope_binding`` stays ``verified`` in both — the
    chain conveyed the scope; only ``bounds_conformance`` distinguishes obedience."""
    P = LocalEd25519Signer(seed=bytes([1]) + bytes(31))
    L = LocalEd25519Signer(seed=bytes([3]) + bytes(31))
    pol = "policy://payments@sha256:aa"
    scope = {
        "form": "jsonschema+quantum/1",
        "schema": {
            "type": "object",
            "properties": {"amount": {"type": "integer", "minimum": 0, "maximum": 100000}},
        },
    }
    pd, sd = hash_ref(pol), convention_definition_hash(scope)  # structured pin (canonical JSON)
    g0 = sign_grant(DelegationGrant(P.verification_method, L.verification_method,
                                    P.verification_method, None, pd, sd), P)
    env = RecourseEnvelope(
        authority=Authority(principal=P.verification_method, policy=pol,
                            delegation=(g0.to_dict(),)),
        bounds=Bounds(scope=scope),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla receipt verify", anchor="hashes.content"),
                Remedy(rung="escalate", verifier="maintainer review", anchor=P.verification_method),
            ),
        ),
        deed_schema="0.3",
    )
    r = build_tool_call_receipt(
        tool="payments.charge", call_subject={"amount": amount},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "d" * 64},
        envelope=env, timestamp=_FIXED_TS, producer={"bulla_version": "0.44.0"},
    )
    return sign_action_receipt(r, L).to_dict()


def _reliance_dict() -> dict:
    """A ``bulla.rely`` receipt: a relying party records RELY on the delegated receipt
    under the pragmatic policy, and signs its OWN envelope. It is an ordinary
    ActionReceipt (action.type "bulla.rely") — the checker validates it with zero bulla
    imports, which is the cross-implementation proof that reliance is NOT a new type."""
    relied_on = _delegation_dict()
    relier = LocalEd25519Signer(seed=bytes([7]) + bytes(31))
    renv = RecourseEnvelope(
        authority=Authority(principal=relier.verification_method, policy="policy://relier@sha256:bb"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(Remedy(rung="recompute", verifier="bulla reliance verify", anchor="hashes.content"),),
        ),
    )
    rr = build_reliance_receipt(relied_on=relied_on, policy=PRAGMATIC_RELIANCE_POLICY,
                                envelope=renv, timestamp=_FIXED_TS, producer={"bulla_version": "0.44.0"})
    return sign_action_receipt(rr, relier).to_dict()


def _witness_dicts() -> tuple[dict, dict]:
    """(canon2, legacy-v1) — same semantic content, two minting rules."""
    diag = diagnose(_COMPOSITION)
    r = witness(diag, _COMPOSITION)
    # These vectors were minted under 0.43.0. Pin both time and producer version
    # so regeneration under a later worktree does not create unrelated hash churn.
    r = replace(r, timestamp=_FIXED_TS, kernel_version="0.43.0")
    v2 = r.to_dict()

    legacy = copy.deepcopy(v2)
    del legacy["canon_version"]  # pre-v2 receipts carried no stamp
    obj = {k: v for k, v in legacy.items() if k not in ("receipt_hash", "anchor_ref")}
    legacy["receipt_hash"] = hashlib.sha256(legacy_json_v1(obj).encode()).hexdigest()
    return v2, legacy


def main() -> int:
    valid = json.loads(_CORPUS.read_text())  # a real, unsigned reconstruction

    vectors: dict[str, dict] = {"valid-release.json": valid}

    tampered = copy.deepcopy(valid)
    tampered["evidence_refs"][0]["hash"] = "sha256:" + "0" * 64
    vectors["tampered-evidence.json"] = tampered

    blanked = copy.deepcopy(valid)
    blanked["remedy"]["remedies"][0]["anchor"] = ""
    vectors["blank-remedy-anchor.json"] = blanked

    conv = _convention_receipt_dict()
    vectors["convention-receipt.json"] = conv

    # The convention forgery: relax the quantum after the fact and recompute
    # the entry pin, so ONLY the content hash catches the edit.
    forged = copy.deepcopy(conv)
    forged["conventions"][0]["definition"]["quantum"]["amount"]["multipleOf"] = 100
    forged["conventions"][0]["definition_hash"] = convention_definition_hash(
        forged["conventions"][0]["definition"]
    )
    vectors["tampered-convention.json"] = forged

    malformed = copy.deepcopy(conv)
    malformed_def = malformed["conventions"][0]["definition"]
    malformed_def["schema"]["properties"] = []
    malformed["conventions"][0]["definition_hash"] = convention_definition_hash(malformed_def)
    malformed["hashes"] = _raw_action_hashes(malformed)
    vectors["malformed-executable.json"] = malformed

    expected: dict[str, dict] = {}
    for name, doc in vectors.items():
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        v = verify_receipt(doc)
        expected[name] = {"kind": "action_receipt", "ok": v.ok, "verified_to": v.verified_to}
        if v.conventions:
            expected[name]["conventions"] = v.conventions
        if v.effective_grounding:
            expected[name]["effective_grounding"] = v.effective_grounding
        print(f"wrote {name:26s} bulla: ok={v.ok} verified_to={v.verified_to}")

    # Signed pair — the authority-binding vectors. Their `expected` splits the
    # verdict by depth: `ok`/`verified_to` are the STDLIB rung (structure +
    # hashes), and `identity` is the optional ed25519 rung. Both signed vectors
    # are structurally valid at the digest rung — the forgery is caught ONLY by
    # the signature rung, which is the depth lesson made concrete.
    signed, forged, resigned = _signed_dicts()
    delegated = _delegation_dict()
    for name, doc, is_deleg in (
        ("signed-authorized.json", signed, False),
        ("tampered-authority.json", forged, False),
        ("tampered-authority-resigned.json", resigned, False),
        ("delegated-receipt.json", delegated, True),
        # Structured-scope pair: the missing half of authorization. The over-scope
        # act still verifies (ok=True) — bounds_conformance=violates is surfaced.
        ("delegated-scope-conforms.json", _scoped_delegation_dict(1250), True),
        ("delegated-scope-violates.json", _scoped_delegation_dict(999999), True),
        # Receipted reliance: a bulla.rely receipt is an ordinary ActionReceipt, so the
        # zero-import checker validates it — the cross-impl proof it is NOT a new type.
        ("reliance-rely.json", _reliance_dict(), False),
    ):
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        v = verify_receipt(doc)  # bulla, with crypto
        identity = {
            "ok": v.ok,
            "verified_to": v.verified_to,
            "signature_authentic": v.checks.get("signature"),
            "authority_authentic": v.authority_authentic,
        }
        if is_deleg:
            # the six independent delegation dimensions, reproduced by the checker
            identity.update({
                "chain_integrity": v.chain_integrity,
                "principal_binding": v.principal_binding,
                "policy_binding": v.policy_binding,
                "scope_binding": v.scope_binding,
                "temporal_status": v.temporal_status,
                "revocation_status": v.revocation_status,
            })
        expected[name] = {
            "kind": "action_receipt",
            "ok": True,             # stdlib rung: hashes recompute, modality holds
            "verified_to": "digest",
            # bounds_conformance is a DIGEST-rung dimension (crypto-free predicate
            # recompute), so it belongs at the top level, not under `identity`.
            "bounds_conformance": v.bounds_conformance,
            "identity": identity,
        }
        extra = (f" chain={v.chain_integrity} principal={v.principal_binding} "
                 f"policy={v.policy_binding} scope={v.scope_binding} "
                 f"bounds_conformance={v.bounds_conformance}") if is_deleg else ""
        print(f"wrote {name:26s} bulla: ok={v.ok} verified_to={v.verified_to} "
              f"authority={v.authority_authentic}{extra}")

    w2, w1 = _witness_dicts()
    for name, doc in (("witness-canon2.json", w2), ("witness-legacy-v1.json", w1)):
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        rep = receipt_integrity_report(doc)
        expected[name] = {
            "kind": "witness_receipt", "ok": rep["ok"],
            "verified_to": "digest" if rep["ok"] else "none",
            "canon": rep["canon"],
        }
        print(f"wrote {name:26s} bulla: ok={rep['ok']} canon={rep['canon']}")

    (_HERE / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote expected.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
