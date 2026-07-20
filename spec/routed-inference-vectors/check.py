#!/usr/bin/env python3
"""Zero-Bulla checker for Routed Inference Profile v0.1 trace bundles.

The digest and profile-predicate rungs use only the Python standard library. When
PyNaCl is available, the checker also verifies the fixtures' self-certifying
did:key content, authorization, and log-head signatures and reports identity depth.
Without it, authority-dependent results remain undetermined at digest depth.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
PROFILE = "bulla.routed-inference/0.1-draft"
GROUNDING = [
    "self_asserted", "counterparty_signed", "third_party_anchored", "execution_verified",
]
ACTION_ORDER = (
    "inference.order", "inference.route", "inference.accept", "inference.delivery", "bulla.rely",
)
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SAFE_INTEGER_MAX = 2**53 - 1
PROOF_TYPE = "bulla/ed25519-2026"
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_MULTICODEC = b"\xed\x01"
_RECEIPT_KEYS = {
    "schema_version", "kind", "action", "diagnostic_ref", "evidence_refs",
    "anchor_ref", "mandate", "remedy", "retention", "stake", "conventions",
    "signature", "authorization", "timestamp", "producer", "hashes",
}
_ACTION_KEYS = {"type", "profile", "parents", "slot_id", "term_root", "subject"}
_SUBJECT_KEYS = {
    "inference.order": {
        "slot_id", "term_root", "request_ref", "budget_ceiling", "budget_unit",
        "remedy_adapter_ref", "witness_policy_ref",
    },
    "inference.route": {"slot_id", "term_root", "selection", "budget_ledger"},
    "inference.accept": {
        "slot_id", "term_root", "accepted_route", "accepted_selection",
        "remedy_adapter_ref", "witness_policy_ref", "budget_ledger",
    },
    "inference.delivery": {
        "slot_id", "term_root", "selection", "artifact_ref", "resource_usage",
    },
    "bulla.rely": {"relied_on", "policy", "decision"},
}
_TERM_KEYS = {
    "profile", "route_topology", "term_disclosure", "request_ref",
    "process_constraints", "evidence_policy", "budget_policy", "deadline",
    "witness_policy_ref", "remedy_adapter_ref", "forum_ref", "reliance_policy_ref",
}
_PROCESS_KEYS = {
    "permitted_providers", "permitted_models", "min_precision_bits",
    "approved_hardware_classes", "randomness_policy", "max_route_depth",
    "resource_ceilings",
}
_SELECTION_KEYS = {
    "provider", "model", "precision_bits", "hardware_class",
    "randomness_policy", "route_depth",
}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _h(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _safe_nonnegative_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SAFE_INTEGER_MAX
    )


def _b58decode(value: str) -> bytes:
    n = 0
    for char in value:
        index = _B58_ALPHABET.find(char)
        if index < 0:
            raise ValueError("invalid base58")
        n = n * 58 + index
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    padding = len(value) - len(value.lstrip("1"))
    return b"\x00" * padding + body


def _did_key_public_key(value: str) -> bytes:
    prefix = "did:key:z"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError("profile identity checker supports self-certifying did:key")
    raw = _b58decode(value[len(prefix):])
    if raw[:2] != _ED25519_MULTICODEC or len(raw[2:]) != 32:
        raise ValueError("did:key is not ed25519")
    return raw[2:]


def _proof_authentic(proof: Any, digest: str, purpose: str | None) -> bool | None:
    """Return true/false with PyNaCl, or None when the crypto rung is unavailable."""
    if not isinstance(proof, dict):
        return False
    expected = {"type", "issuer", "verificationMethod", "proofValue"}
    if purpose is not None:
        expected.add("purpose")
    if set(proof) != expected or proof.get("type") != PROOF_TYPE:
        return False
    if purpose is not None and proof.get("purpose") != purpose:
        return False
    issuer = proof.get("issuer")
    if issuer != proof.get("verificationMethod"):
        return False
    try:
        public_key = _did_key_public_key(issuer)
        signature = base64.b64decode(proof.get("proofValue"), validate=True)
    except (TypeError, ValueError):
        return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        return None
    signed = (
        _canon({
            "context": "bulla-proof", "schema": "0.3",
            "purpose": purpose, "digest": digest,
        }).encode("utf-8")
        if purpose is not None
        else digest.encode("utf-8")
    )
    try:
        VerifyKey(public_key).verify(signed, signature)
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def _identity_available() -> bool:
    try:
        import nacl.signing  # noqa: F401
    except ImportError:
        return False
    return True


def _envelope(receipt: dict) -> dict:
    mandate = receipt.get("mandate") or {}
    remedy = receipt.get("remedy") or {}
    retention = receipt.get("retention") or {}
    mandate = mandate if isinstance(mandate, dict) else {}
    remedy = remedy if isinstance(remedy, dict) else {}
    retention = retention if isinstance(retention, dict) else {}
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


def _receipt_hashes(receipt: dict) -> dict:
    content_pre = {
        "schema_version": receipt.get("schema_version"),
        "kind": receipt.get("kind"),
        "action": receipt.get("action"),
        "diagnostic_ref": receipt.get("diagnostic_ref"),
        "evidence_refs": receipt.get("evidence_refs") or [],
        "anchor_ref": receipt.get("anchor_ref") or {},
    }
    if receipt.get("conventions"):
        content_pre["conventions"] = receipt["conventions"]
    content = _h(content_pre)
    event = _h({"content_hash": content, "timestamp": receipt.get("timestamp", "")})
    attestation_pre = {
        "content_hash": content,
        "signature": receipt.get("signature"),
        "recourse_envelope": _envelope(receipt),
    }
    if receipt.get("schema_version") == "0.3":
        attestation_pre["authorization"] = receipt.get("authorization")
    attestation = _h(attestation_pre)
    log_leaf = "sha256:" + hashlib.sha256(b"\x00" + attestation.encode("utf-8")).hexdigest()
    return {"content": content, "event": event, "attestation": attestation, "log_leaf": log_leaf}


def _ref(receipt: dict) -> dict:
    hashes = receipt.get("hashes") if isinstance(receipt, dict) else None
    hashes = hashes if isinstance(hashes, dict) else {}
    return {"event": hashes.get("event"), "attestation": hashes.get("attestation")}


def _action(receipt: Any) -> dict:
    if not isinstance(receipt, dict):
        return {}
    action = receipt.get("action")
    return action if isinstance(action, dict) else {}


def _subject(receipt: Any) -> dict:
    subject = _action(receipt).get("subject")
    return subject if isinstance(subject, dict) else {}


def _ref_valid(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"event", "attestation"}
        and all(isinstance(value[k], str) and HASH_RE.fullmatch(value[k]) for k in value)
    )


def _portable_json(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return -SAFE_INTEGER_MAX <= value <= SAFE_INTEGER_MAX
    if isinstance(value, list):
        return all(_portable_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and key and _portable_json(item)
            for key, item in value.items()
        )
    return False


def _terms_valid(terms: Any) -> bool:
    if not isinstance(terms, dict) or set(terms) != _TERM_KEYS or not _portable_json(terms):
        return False
    process = terms.get("process_constraints")
    evidence = terms.get("evidence_policy")
    budget = terms.get("budget_policy")
    deadline = terms.get("deadline")
    if not all(isinstance(value, dict) for value in (process, evidence, budget, deadline)):
        return False
    if set(process) != _PROCESS_KEYS \
            or set(evidence) != {"minimum_process_grounding", "appraisal_policy_ref"} \
            or set(budget) != {"mode", "unit", "ceiling"} \
            or set(deadline) != {"domain", "value"}:
        return False
    string_lists = (
        process.get("permitted_providers"), process.get("permitted_models"),
        process.get("approved_hardware_classes"),
    )
    if any(
        not isinstance(values, list) or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
        for values in string_lists
    ):
        return False
    ceilings = process.get("resource_ceilings")
    if not isinstance(ceilings, dict) or not ceilings or not all(
        isinstance(name, str) and name and _safe_nonnegative_int(value)
        for name, value in ceilings.items()
    ):
        return False
    if not _safe_nonnegative_int(process.get("min_precision_bits")) \
            or process.get("max_route_depth") != 1:
        return False
    if not isinstance(process.get("randomness_policy"), str) \
            or not process["randomness_policy"]:
        return False
    if evidence.get("minimum_process_grounding") not in GROUNDING \
            or not isinstance(evidence.get("appraisal_policy_ref"), str) \
            or not evidence["appraisal_policy_ref"]:
        return False
    if budget.get("mode") != "disclosed_components" \
            or not isinstance(budget.get("unit"), str) or not budget["unit"] \
            or not _safe_nonnegative_int(budget.get("ceiling")):
        return False
    if not isinstance(deadline.get("domain"), str) or not deadline["domain"] \
            or not _safe_nonnegative_int(deadline.get("value")):
        return False
    if terms.get("profile") != PROFILE \
            or terms.get("route_topology") != "single_route_single_provider" \
            or terms.get("term_disclosure") != "full" \
            or not HASH_RE.fullmatch(str(terms.get("request_ref", ""))):
        return False
    for name in (
        "witness_policy_ref", "remedy_adapter_ref", "forum_ref", "reliance_policy_ref",
    ):
        if not isinstance(terms.get(name), str) or not terms[name]:
            return False
    return True


def _receipt_integrity(receipt: Any) -> tuple[bool, list[str], str, bool]:
    if not isinstance(receipt, dict):
        return False, ["receipt is not an object"], "invalid", False
    reasons: list[str] = []
    if set(receipt) != _RECEIPT_KEYS:
        reasons.append("profile receipt has missing or unknown top-level fields")
    if receipt.get("schema_version") != "0.3" or receipt.get("kind") != "action_receipt":
        reasons.append("profile receipt must be an ActionReceipt v0.3")
    action = receipt.get("action")
    if not isinstance(action, dict) or action.get("profile") != PROFILE:
        reasons.append("action profile missing or wrong")
        action = {}
    if action.get("type") not in ACTION_ORDER:
        reasons.append("unsupported profile action type")
    allowed_action_keys = _ACTION_KEYS | ({"discharges"} if "discharges" in action else set())
    if set(action) != allowed_action_keys:
        reasons.append("profile action has missing or unknown fields")
    if not isinstance(action.get("subject"), dict):
        reasons.append("action.subject must be an object")
    elif action.get("type") in _SUBJECT_KEYS \
            and set(action["subject"]) != _SUBJECT_KEYS[action["type"]]:
        reasons.append("profile action subject has missing or unknown fields")
    parents = action.get("parents")
    if not isinstance(parents, list) or any(not _ref_valid(parent) for parent in parents):
        reasons.append("action.parents must be a list")
    if "discharges" in action and not isinstance(action.get("discharges"), list):
        reasons.append("action.discharges must be a list when present")
    diagnostic = receipt.get("diagnostic_ref")
    if not isinstance(diagnostic, dict) or set(diagnostic) - {"status", "ref"} \
            or diagnostic.get("status") not in ("reference", "not_applicable", "deferred") \
            or ("ref" in diagnostic and not HASH_RE.fullmatch(str(diagnostic["ref"]))):
        reasons.append("diagnostic_ref is malformed")
    evidence_refs = receipt.get("evidence_refs")
    if not isinstance(evidence_refs, list) or any(
        not isinstance(evidence, dict)
        or set(evidence) != {"name", "hash", "grounding"}
        or not isinstance(evidence.get("name"), str) or not evidence["name"]
        or not HASH_RE.fullmatch(str(evidence.get("hash", "")))
        or evidence.get("grounding") not in GROUNDING
        for evidence in (evidence_refs if isinstance(evidence_refs, list) else [])
    ):
        reasons.append("evidence_refs is malformed")
    if not isinstance(receipt.get("anchor_ref"), dict):
        reasons.append("anchor_ref must be an object")
    if not isinstance(receipt.get("retention"), dict) or receipt.get("stake") is not None:
        reasons.append("retention/stake container is malformed")
    if receipt.get("conventions") != []:
        reasons.append("profile terms must be pinned in term_document, not receipt conventions")
    if not isinstance(receipt.get("timestamp"), str) or not receipt["timestamp"]:
        reasons.append("timestamp must be a non-empty actor-supplied string")
    if not isinstance(receipt.get("producer"), dict):
        reasons.append("producer must be an object")
    stored = receipt.get("hashes")
    stored = stored if isinstance(stored, dict) else {}
    if set(stored) != {"content", "event", "attestation", "log_leaf"}:
        reasons.append("hashes must contain exactly four profile digests")
    computed = _receipt_hashes(receipt)
    if any(stored.get(name) != value for name, value in computed.items()):
        reasons.append("stored ActionReceipt hashes do not recompute")

    # Digest checker only: require the v0.3 proof pair and direct-principal binding
    # structurally, but do not claim ed25519 authenticity.
    signature = receipt.get("signature")
    authorization = receipt.get("authorization")
    mandate = receipt.get("mandate")
    mandate = mandate if isinstance(mandate, dict) else {}
    authority = mandate.get("authority")
    authority = authority if isinstance(authority, dict) else {}
    if set(mandate) - {"deed_schema", "authority", "bounds"}:
        reasons.append("mandate contains unknown fields")
    if set(authority) != {"principal", "policy", "delegation"} \
            or not isinstance(authority.get("policy"), str) or not authority["policy"] \
            or authority.get("delegation") != []:
        reasons.append("profile requires a closed direct-principal authority")
    authority_status = "invalid"
    if not isinstance(signature, dict) or not isinstance(authorization, dict):
        reasons.append("v0.3 proof pair missing")
    else:
        if signature.get("purpose") != "content" or authorization.get("purpose") != "authorization":
            reasons.append("v0.3 proof purposes wrong")
        if any(signature.get(k) != authorization.get(k) for k in ("issuer", "verificationMethod")):
            reasons.append("content and authorization proofs name different signers")
        if authority.get("principal") != signature.get("issuer"):
            reasons.append("direct signer is not the bound authority principal")
        authorization_hash = _h({
            "content_hash": computed["content"],
            "envelope_hash": _h(_envelope(receipt)),
        })
        content_authentic = _proof_authentic(signature, computed["content"], "content")
        envelope_authentic = _proof_authentic(
            authorization, authorization_hash, "authorization"
        )
        if content_authentic is None or envelope_authentic is None:
            authority_status = "unverified"
        elif content_authentic and envelope_authentic:
            authority_status = "verified"
        else:
            reasons.append("v0.3 content or authorization signature is not authentic")

    remedy = receipt.get("remedy")
    remedy = remedy if isinstance(remedy, dict) else {}
    forum = remedy.get("forum")
    forum = forum if isinstance(forum, dict) else {}
    remedies = remedy.get("remedies")
    remedies = remedies if isinstance(remedies, list) else []
    if not authority.get("principal"):
        reasons.append("transition has no attributable principal")
    recourse_valid = not (
        set(remedy) != {"challenge_window", "forum", "remedies"} \
            or not isinstance(remedy.get("challenge_window"), str) \
            or not remedy["challenge_window"] \
            or set(forum) != {"log_endpoint", "trusted_root_ref"} \
            or not all(isinstance(forum.get(key), str) and forum[key]
                       for key in ("log_endpoint", "trusted_root_ref")) \
            or not remedies \
            or any(
                not isinstance(item, dict)
                or set(item) != {"rung", "verifier", "anchor"}
                or item.get("rung") not in (
                    "recompute", "challenge", "cure", "revert", "slash", "escalate",
                )
                or not isinstance(item.get("verifier"), str) or not item["verifier"]
                or not isinstance(item.get("anchor"), str) or not item["anchor"]
                for item in remedies
            )
    )
    if not recourse_valid:
        reasons.append("transition has no well-formed conveyed recourse terms")
    return not reasons, reasons, authority_status, recourse_valid


def _grounding(receipts: list[dict]) -> str | None:
    values: list[str] = []
    for receipt in receipts:
        if _action(receipt).get("type") != "inference.delivery":
            continue
        evidence_refs = receipt.get("evidence_refs") if isinstance(receipt, dict) else []
        evidence_refs = evidence_refs if isinstance(evidence_refs, list) else []
        for evidence in evidence_refs:
            if isinstance(evidence, dict) and evidence.get("name") == "process_evidence" \
                    and evidence.get("grounding") in GROUNDING:
                values.append(evidence["grounding"])
        usage = _subject(receipt).get("resource_usage")
        if isinstance(usage, dict) and usage.get("grounding") in GROUNDING:
            values.append(usage["grounding"])
    return min(values, key=GROUNDING.index) if values else None


def _ledger_valid(ledger: Any, unit: str) -> bool:
    fields = ("charge_to_upstream", "charge_from_downstream", "retained_amount")
    return (
        isinstance(ledger, dict)
        and ledger.get("unit") == unit
        and set(ledger) == {"unit", *fields}
        and all(_safe_nonnegative_int(ledger.get(k)) for k in fields)
    )


def check_bundle(bundle: dict) -> dict:
    violations: set[str] = set()
    undetermined: set[str] = set()
    bundle = bundle if isinstance(bundle, dict) else {}
    receipts = bundle.get("receipts")
    receipts = receipts if isinstance(receipts, list) else []
    terms = bundle.get("term_document")
    terms = terms if isinstance(terms, dict) else {}
    term_root = bundle.get("term_root")
    if not _terms_valid(terms):
        violations.add("TERM_DOCUMENT_MALFORMED")
    if bundle.get("profile") != PROFILE or term_root != _h(terms) \
            or not HASH_RE.fullmatch(str(term_root or "")):
        violations.add("TERM_ROOT_CHANGED")

    for receipt in receipts:
        ok, _, authority, recourse_valid = _receipt_integrity(receipt)
        if not ok:
            violations.add("RECEIPT_INTEGRITY_INVALID")
        if not recourse_valid:
            violations.add("RECOURSE_CONVEYANCE_INVALID")
        if authority == "unverified":
            undetermined.add("AUTHORITY_UNVERIFIED")
        elif authority == "invalid":
            violations.add("AUTHORITY_INVALID")

    by_type: dict[str, list[dict]] = {name: [] for name in ACTION_ORDER}
    for receipt in receipts:
        action_type = _action(receipt).get("type")
        if action_type in by_type:
            by_type[action_type].append(receipt)
        else:
            violations.add("UNSUPPORTED_ACTION")

    orders = by_type["inference.order"]
    routes = by_type["inference.route"]
    accepts = by_type["inference.accept"]
    deliveries = by_type["inference.delivery"]
    reliance = by_type["bulla.rely"]
    accounting_depth = "ACCOUNTING_UNDETERMINED"
    if len(orders) != 1 or len(routes) != 1 or len(accepts) != 1:
        violations.add("ORPHANED_TRANSITION")
    else:
        order, route, accept = orders[0], routes[0], accepts[0]
        order_subject = _subject(order)
        slot_id = order_subject.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id:
            violations.add("SLOT_ID_CHANGED")
        if _action(order).get("parents") != []:
            violations.add("ORPHANED_TRANSITION")
        route_parents = _action(route).get("parents") or []
        if route_parents != [_ref(order)]:
            violations.add("ORPHANED_TRANSITION")
        if (_action(accept).get("parents") or []) != [_ref(route)]:
            violations.add("ORPHANED_TRANSITION")
        for delivery in deliveries:
            if (_action(delivery).get("parents") or []) != [_ref(accept)]:
                violations.add("ORPHANED_TRANSITION")
        if deliveries and not reliance:
            undetermined.add("MISSING_RELIANCE")
        elif len(reliance) > 1:
            violations.add("DUPLICATE_RELIANCE")
        if reliance and deliveries:
            expected_delivery_refs = [_ref(d) for d in deliveries]
            for relied in reliance:
                parents = _action(relied).get("parents") or []
                subject_ref = _subject(relied).get("relied_on")
                if len(parents) != 1 or parents[0] not in expected_delivery_refs or subject_ref != parents[0]:
                    violations.add("ORPHANED_TRANSITION")

        for receipt in [order, route, accept, *deliveries, *reliance]:
            action = _action(receipt)
            if action.get("slot_id") != slot_id:
                violations.add("SLOT_ID_CHANGED")
            if action.get("term_root") != term_root:
                violations.add("TERM_ROOT_CHANGED")
        for receipt in [order, route, accept, *deliveries]:
            subject = _subject(receipt)
            if subject.get("slot_id") != slot_id:
                violations.add("SLOT_ID_CHANGED")
            if subject.get("term_root") != term_root:
                violations.add("TERM_ROOT_CHANGED")

        if order_subject.get("request_ref") != terms.get("request_ref") \
                or order_subject.get("budget_ceiling") != (terms.get("budget_policy") or {}).get("ceiling") \
                or order_subject.get("budget_unit") != (terms.get("budget_policy") or {}).get("unit") \
                or order_subject.get("remedy_adapter_ref") != terms.get("remedy_adapter_ref") \
                or order_subject.get("witness_policy_ref") != terms.get("witness_policy_ref"):
            violations.add("ORDER_TERMS_MISMATCH")

        for relied in reliance:
            relied_subject = _subject(relied)
            relied_ref = relied_subject.get("relied_on")
            expected_policy = terms.get("reliance_policy_ref")
            diagnostic = relied.get("diagnostic_ref") if isinstance(relied, dict) else None
            diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
            evidence = relied.get("evidence_refs") if isinstance(relied, dict) else None
            evidence = evidence if isinstance(evidence, list) else []
            expected_pin = _h({
                "relied_on": relied_ref,
                "policy": expected_policy,
                "decision": "rely",
            })
            if set(relied_subject) != {"relied_on", "policy", "decision"} \
                    or not _ref_valid(relied_ref) \
                    or relied_subject.get("policy") != expected_policy \
                    or relied_subject.get("decision") != "rely" \
                    or diagnostic.get("ref") != expected_pin:
                violations.add("RELIANCE_CLAIM_MISMATCH")
            matching_evidence = [
                item for item in evidence
                if isinstance(item, dict) and item.get("name") == "relied_on"
            ]
            if len(evidence) != 1 or len(matching_evidence) != 1 \
                    or matching_evidence[0].get("hash") != (
                        relied_ref.get("attestation") if isinstance(relied_ref, dict) else None
                    ) \
                    or matching_evidence[0].get("grounding") != "counterparty_signed":
                violations.add("RELIANCE_EVIDENCE_MISMATCH")

        accept_subject = _subject(accept)
        if accept_subject.get("accepted_route") != _ref(route) \
                or accept_subject.get("accepted_selection") != _subject(route).get("selection"):
            violations.add("PARTIAL_ACCEPTANCE")
        accept_remedy = accept.get("remedy") if isinstance(accept, dict) else None
        accept_remedy = accept_remedy if isinstance(accept_remedy, dict) else {}
        accept_remedies = accept_remedy.get("remedies")
        accept_remedies = accept_remedies if isinstance(accept_remedies, list) else []
        if accept_subject.get("remedy_adapter_ref") != terms.get("remedy_adapter_ref") \
                or not any(isinstance(rem, dict) and rem.get("anchor") == terms.get("remedy_adapter_ref")
                           for rem in accept_remedies):
            violations.add("REMEDY_NOT_ACCEPTED")
        if accept_subject.get("witness_policy_ref") != terms.get("witness_policy_ref"):
            violations.add("WITNESS_POLICY_NOT_ACCEPTED")

        constraints = terms.get("process_constraints") or {}
        route_selection = _subject(route).get("selection") or {}
        selections = [route_selection] + [
            _subject(delivery).get("selection") or {}
            for delivery in deliveries
        ]
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != _SELECTION_KEYS:
                violations.add("SELECTION_MALFORMED")
                selection = selection if isinstance(selection, dict) else {}
            if selection.get("provider") not in constraints.get("permitted_providers", []):
                violations.add("PROVIDER_NOT_PERMITTED")
            if selection.get("model") not in constraints.get("permitted_models", []):
                violations.add("MODEL_NOT_PERMITTED")
            if not _safe_nonnegative_int(selection.get("precision_bits")) \
                    or selection["precision_bits"] < constraints.get("min_precision_bits", 0):
                violations.add("PRECISION_BELOW_FLOOR")
            if selection.get("hardware_class") not in constraints.get("approved_hardware_classes", []):
                violations.add("HARDWARE_NOT_PERMITTED")
            if selection.get("randomness_policy") != constraints.get("randomness_policy"):
                violations.add("RANDOMNESS_POLICY_MISMATCH")
            if not _safe_nonnegative_int(selection.get("route_depth")) \
                    or selection["route_depth"] > constraints.get("max_route_depth", -1):
                violations.add("ROUTE_DEPTH_EXCEEDED")
        for delivery in deliveries:
            selection = _subject(delivery).get("selection")
            if selection != route_selection:
                violations.add("DELIVERY_ROUTE_SUBSTITUTION")
            usage = _subject(delivery).get("resource_usage")
            ceilings = constraints.get("resource_ceilings") or {}
            if not isinstance(usage, dict) or set(usage) != {"deltas", "grounding"} \
                    or usage.get("grounding") not in GROUNDING \
                    or not isinstance(usage.get("deltas"), dict) \
                    or set(usage["deltas"]) != set(ceilings) \
                    or not all(_safe_nonnegative_int(value) for value in usage["deltas"].values()):
                undetermined.add("RESOURCE_USAGE_MALFORMED")
            elif any(usage["deltas"][name] > ceiling for name, ceiling in ceilings.items()):
                violations.add("RESOURCE_CEILING_EXCEEDED")

        budget = terms.get("budget_policy") or {}
        unit, ceiling = budget.get("unit"), budget.get("ceiling")
        route_ledger = _subject(route).get("budget_ledger")
        provider_ledger = accept_subject.get("budget_ledger")
        accounting_depth = "ACCOUNTING_CONFORMS"
        if not _ledger_valid(route_ledger, unit) or not _ledger_valid(provider_ledger, unit):
            accounting_depth = "ACCOUNTING_UNDETERMINED"
            undetermined.add("BUDGET_LEDGER_MALFORMED")
        else:
            for ledger in (route_ledger, provider_ledger):
                if ledger["charge_to_upstream"] != (
                    ledger["charge_from_downstream"] + ledger["retained_amount"]
                ):
                    violations.add("BUDGET_LEDGER_UNBALANCED")
            if route_ledger["charge_from_downstream"] != provider_ledger["charge_to_upstream"]:
                violations.add("DOWNSTREAM_CHARGE_MISMATCH")
            if not _safe_nonnegative_int(ceiling) or route_ledger["charge_to_upstream"] > ceiling:
                violations.add("BUDGET_CEILING_EXCEEDED")
            if violations & {
                "BUDGET_LEDGER_UNBALANCED", "DOWNSTREAM_CHARGE_MISMATCH",
                "BUDGET_CEILING_EXCEEDED",
            }:
                accounting_depth = "ACCOUNTING_VIOLATES"
    if not (orders and routes and accepts):
        accounting_depth = "ACCOUNTING_UNDETERMINED"

    for receipt in receipts:
        if _action(receipt).get("discharges"):
            violations.add("DISCHARGE_UNSUPPORTED")

    if not deliveries:
        undetermined.add("DELIVERY_UNAVAILABLE")
    elif len(deliveries) > 1:
        violations.add("CONFLICTING_DELIVERIES")

    witness = bundle.get("witness")
    witness = witness if isinstance(witness, dict) else {}
    if witness.get("status") == "unavailable":
        undetermined.add("WITNESS_UNAVAILABLE")
    elif witness.get("status") not in ("not_exercised", "equivocated"):
        undetermined.add("WITNESS_EVIDENCE_MALFORMED")
    heads = witness.get("heads")
    heads = heads if isinstance(heads, list) else []
    grouped: dict[tuple[str, int], set[str]] = {}
    for head in heads:
        if not isinstance(head, dict):
            undetermined.add("WITNESS_EVIDENCE_MALFORMED")
            continue
        statement = {
            "operator": head.get("operator"),
            "tree_size": head.get("tree_size"),
            "root": head.get("root"),
        }
        if not isinstance(statement["operator"], str) \
                or not _safe_nonnegative_int(statement["tree_size"]) \
                or not HASH_RE.fullmatch(str(statement["root"] or "")):
            undetermined.add("WITNESS_EVIDENCE_MALFORMED")
            continue
        authentic = _proof_authentic(head.get("signature"), _h(statement), None)
        if authentic is None:
            undetermined.add("WITNESS_HEAD_UNVERIFIED")
        elif authentic is False:
            violations.add("WITNESS_HEAD_INVALID")
        else:
            grouped.setdefault(
                (statement["operator"], statement["tree_size"]), set()
            ).add(statement["root"])
    consistency = witness.get("consistency_proofs", [])
    if isinstance(consistency, list) and any(
        isinstance(proof, dict) and proof.get("verified") is False for proof in consistency
    ):
        violations.add("LOG_EQUIVOCATION")
    if any(len(roots) > 1 for roots in grouped.values()):
        violations.add("LOG_EQUIVOCATION")

    process_grounding = _grounding(receipts)
    minimum_grounding = (terms.get("evidence_policy") or {}).get("minimum_process_grounding")
    if deliveries and minimum_grounding in GROUNDING:
        if process_grounding is None:
            undetermined.add("PROCESS_GROUNDING_UNDETERMINED")
        elif GROUNDING.index(process_grounding) < GROUNDING.index(minimum_grounding):
            violations.add("PROCESS_GROUNDING_BELOW_FLOOR")

    if violations:
        outcome = "VIOLATES"
        fault_codes = sorted(violations)
    elif undetermined:
        outcome = "UNDETERMINED"
        fault_codes = sorted(undetermined)
    else:
        outcome = "CONFORMS"
        fault_codes = []
    settlement = bundle.get("settlement_evidence")
    settlement = settlement if isinstance(settlement, list) else []
    budget_unit = ((terms.get("budget_policy") or {}).get("unit")
                   if isinstance(terms.get("budget_policy"), dict) else None)
    settlement_depth = (
        "SETTLEMENT_CONFORMS"
        if settlement and all(
            isinstance(evidence, dict)
            and evidence.get("verified") is True
            and evidence.get("grounding") in GROUNDING[2:]
            and isinstance(evidence.get("rail_ref"), str)
            and bool(evidence["rail_ref"])
            and evidence.get("unit") == budget_unit
            and _safe_nonnegative_int(evidence.get("amount"))
            for evidence in settlement
        )
        else "SETTLEMENT_UNVERIFIED"
    )
    recourse_faults = {
        "RECOURSE_CONVEYANCE_INVALID", "REMEDY_NOT_ACCEPTED",
        "WITNESS_POLICY_NOT_ACCEPTED", "ORDER_TERMS_MISMATCH",
    }
    if violations & recourse_faults:
        recourse_conveyance = "VIOLATES"
    elif "AUTHORITY_UNVERIFIED" in undetermined:
        recourse_conveyance = "UNDETERMINED"
    else:
        recourse_conveyance = "CONFORMS"

    coverage_faults = {
        "AUTHORITY_INVALID", "RECEIPT_INTEGRITY_INVALID", "ORPHANED_TRANSITION",
        "SLOT_ID_CHANGED", "TERM_ROOT_CHANGED", "ORDER_TERMS_MISMATCH",
        "RELIANCE_CLAIM_MISMATCH", "RELIANCE_EVIDENCE_MISMATCH",
        "DUPLICATE_RELIANCE", "PARTIAL_ACCEPTANCE", "REMEDY_NOT_ACCEPTED",
        "WITNESS_POLICY_NOT_ACCEPTED", "RECOURSE_CONVEYANCE_INVALID",
        "DISCHARGE_UNSUPPORTED", "CONFLICTING_DELIVERIES",
    }
    if violations & coverage_faults:
        answerability_coverage = "BROKEN"
    elif "AUTHORITY_UNVERIFIED" in undetermined:
        answerability_coverage = "UNDETERMINED"
    else:
        answerability_coverage = "COVERED"

    return {
        "outcome": outcome,
        "fault_codes": fault_codes,
        "answerability_coverage": answerability_coverage,
        "binding_state": "RETAINED",
        "recourse_conveyance": recourse_conveyance,
        "recourse_reachability": "UNVERIFIED",
        "process_grounding": process_grounding,
        "accounting_depth": accounting_depth,
        "settlement_depth": settlement_depth,
        "verification_depth": "identity" if _identity_available() else "digest",
    }


def _corpus(names: list[str] | None = None) -> int:
    expected = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
    names = names or sorted(expected)
    failures = 0
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = HERE / name
        got = check_bundle(json.loads(path.read_text(encoding="utf-8")))
        want = expected[path.name]
        ok = got == want
        print(
            f"  {'✓' if ok else '✗'} {path.name:45s} "
            f"{got['outcome']:12s} faults={','.join(got['fault_codes']) or '-'}"
        )
        if not ok:
            failures += 1
            print(f"      expected: {want}")
            print(f"      got:      {got}")
    print(f"\n{'OK' if not failures else 'FAIL'}: {len(names) - failures}/{len(names)} routed-inference traces")
    return 1 if failures else 0


def _verify_one(path_text: str, *, as_json: bool) -> int:
    try:
        path = Path(path_text)
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 64
    report = check_bundle(bundle)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['outcome']} faults="
            f"{','.join(report['fault_codes']) or '-'} "
            f"coverage={report['answerability_coverage']}"
        )
    return {"CONFORMS": 0, "VIOLATES": 2, "UNDETERMINED": 3}[report["outcome"]]


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        return _corpus()
    if args[0] == "corpus":
        return _corpus(args[1:] or None)
    if args[0] == "verify":
        positional = [arg for arg in args[1:] if arg != "--json"]
        unknown_flags = [arg for arg in args[1:] if arg.startswith("-") and arg != "--json"]
        if len(positional) != 1 or unknown_flags:
            print("usage: check.py verify TRACE.json [--json]", file=sys.stderr)
            return 64
        return _verify_one(positional[0], as_json="--json" in args[1:])
    # Compatibility with the original checker: positional vector names run as a corpus subset.
    if any(arg.startswith("-") for arg in args):
        print("usage: check.py [corpus [TRACE...]] | verify TRACE.json [--json]", file=sys.stderr)
        return 64
    return _corpus(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
