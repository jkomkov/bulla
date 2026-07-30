#!/usr/bin/env python3
"""Standalone no-Bulla verification of the incident bundle.

A stranger who received the handoff bundle can recompute, with only the Python
standard library:

  1. ActionReceipt v0.2/v0.3 digest integrity for every carried receipt.
  2. Declared issuer topology (not ed25519 authenticity).
  3. Coverage against the carried observed-event denominator.
  4. Hash-only sensitive-artifact references and epistemic separation.

The stdlib has no ed25519 verifier. This script therefore never calls the
issuer declarations cryptographically authenticated; run ``bulla receipt
verify`` with the identity extra for that independent attestation rung.

    python examples/eval-incident-replay/verify_bundle.py examples/eval-incident-replay/demo-output.json
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _envelope(receipt: dict) -> dict:
    mandate = receipt.get("mandate") or {}
    remedy = receipt.get("remedy") or {}
    retention = receipt.get("retention") or {}
    envelope: dict = {"deed_schema": mandate.get("deed_schema", "0.2")}
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
    return envelope


def _receipt_digest_failures(receipt: dict, label: str) -> list[str]:
    failures: list[str] = []
    schema = receipt.get("schema_version")
    if schema not in ("0.2", "0.3"):
        return [f"{label}: unsupported receipt schema {schema!r}"]
    try:
        content_preimage: dict = {
            "schema_version": schema,
            "kind": receipt["kind"],
            "action": receipt["action"],
            "diagnostic_ref": receipt["diagnostic_ref"],
            "evidence_refs": receipt.get("evidence_refs", []),
            "anchor_ref": receipt.get("anchor_ref", {}),
        }
        if receipt.get("conventions"):
            content_preimage["conventions"] = receipt["conventions"]
        content = _hash(content_preimage)
        event = _hash({"content_hash": content, "timestamp": receipt.get("timestamp", "")})
        attestation_preimage: dict = {
            "content_hash": content,
            "signature": receipt.get("signature"),
            "recourse_envelope": _envelope(receipt),
        }
        if schema == "0.3":
            attestation_preimage["authorization"] = receipt.get("authorization")
        attestation = _hash(attestation_preimage)
        log_leaf = "sha256:" + hashlib.sha256(
            b"\x00" + attestation.encode("utf-8")
        ).hexdigest()
    except (KeyError, TypeError, ValueError) as exc:
        return [f"{label}: malformed receipt: {exc}"]

    stored = receipt.get("hashes") or {}
    for name, recomputed in (
        ("content", content),
        ("event", event),
        ("attestation", attestation),
        ("log_leaf", log_leaf),
    ):
        if stored.get(name) != recomputed:
            failures.append(f"{label}: {name} hash mismatch")
    return failures


def _issuer(receipt: dict) -> str | None:
    return (receipt.get("signature") or {}).get("issuer")


def _one_id(record: dict, label: str, failures: list[str]) -> str | None:
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id:
        failures.append(f"{label}: missing non-empty id")
        return None
    return event_id


def verify(bundle: dict) -> list[str]:
    failures: list[str] = []
    topology = bundle["signer_topology"]
    caps = bundle["capability_receipts"]

    if bundle.get("profile") != "bulla.cyber-eval/0.2-draft":
        failures.append("bundle profile is not the corrected v0.2 draft")

    # 1 — every carried ActionReceipt recomputes at the digest rung.
    receipts = [
        ("mandate", bundle["mandate"]),
        *(
            (f"capability_receipts[{index}]", receipt)
            for index, receipt in enumerate(caps)
        ),
        ("trajectory_decision", bundle["trajectory_decision"]),
        ("incident_handoff", bundle["incident_handoff"]),
    ]
    for label, receipt in receipts:
        failures.extend(_receipt_digest_failures(receipt, label))

    expected_types = {
        "mandate": "eval.run.authorize",
        "trajectory_decision": "trajectory.decide",
        "incident_handoff": "incident.handoff",
    }
    for name, action_type in expected_types.items():
        if (bundle[name].get("action") or {}).get("type") != action_type:
            failures.append(f"{name} action type does not equal {action_type}")
    if any((receipt.get("action") or {}).get("type") != "capability.decide" for receipt in caps):
        failures.append("capability receipt action types must be capability.decide")

    # 2 — declared issuer topology. These declarations are covered by the
    # attestation hash above, but require ed25519 to become authenticated.
    for receipt in caps:
        if _issuer(receipt) != topology["gateway"]:
            failures.append(
                f"capability.decide declared issuer is not the gateway role: {_issuer(receipt)}"
            )
    if _issuer(bundle["mandate"]) != topology["eval-authority"]:
        failures.append("mandate declared issuer is not the evaluation authority")
    if _issuer(bundle["trajectory_decision"]) != topology["trajectory-monitor"]:
        failures.append("trajectory.decide declared issuer is not the monitor")
    if _issuer(bundle["incident_handoff"]) != topology["incident-commander"]:
        failures.append("incident.handoff declared issuer is not the incident commander")
    if "eval-model" in topology or "agent" in topology:
        failures.append("an agent key appears in the declared signer topology")

    mandate_ref = bundle["mandate"]["hashes"]["attestation"]
    for index, receipt in enumerate(caps):
        subject = (receipt.get("action") or {}).get("subject") or {}
        if subject.get("mandate_ref") != mandate_ref:
            failures.append(
                f"capability_receipts[{index}] mandate_ref does not bind the mandate"
            )
    trajectory_subject = (
        bundle["trajectory_decision"].get("action") or {}
    ).get("subject") or {}
    if trajectory_subject.get("mandate_ref") != mandate_ref:
        failures.append("trajectory mandate_ref does not bind the mandate")
    expected_lineage = [receipt["hashes"]["attestation"] for receipt in caps]
    if trajectory_subject.get("considered_attestation_hashes") != expected_lineage:
        failures.append(
            "trajectory considered_attestation_hashes does not equal the exact ordered lineage"
        )

    # 3 — recompute coverage from the carried observed-event denominator.
    observed = bundle.get("observed_events")
    if not isinstance(observed, list):
        failures.append("observed event denominator missing")
        observed = []
    if bundle.get("observed_events_digest") != _hash(observed):
        failures.append("observed event denominator digest mismatch")

    observed_ids: list[str] = []
    for index, record in enumerate(observed):
        if not isinstance(record, dict):
            failures.append(f"observed_events[{index}] is not an object")
            continue
        event_id = _one_id(record, f"observed_events[{index}]", failures)
        if event_id is not None:
            observed_ids.append(event_id)
    if len(set(observed_ids)) != len(observed_ids):
        failures.append("observed event denominator contains duplicate ids")

    attested: set[str] = set()
    for index, receipt in enumerate(caps):
        subject = (receipt.get("action") or {}).get("subject") or {}
        event_id = subject.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            failures.append(f"capability_receipts[{index}] has no content-bound event_id")
        else:
            attested.add(event_id)

    observed_set = set(observed_ids)
    expected_covered = [event_id for event_id in observed_ids if event_id in attested]
    expected_missing = [event_id for event_id in observed_ids if event_id not in attested]
    coverage = bundle["coverage"]
    expected_ratio = (
        round(len(expected_covered) / len(observed_ids), 4)
        if observed_ids
        else 1.0
    )
    expected = {
        "total_anchored": len(observed_ids),
        "receipted": len(expected_covered),
        "coverage": expected_ratio,
        "covered": expected_covered,
        "unreceipted_delta": expected_missing,
        "phantom_receipt_ids": sorted(attested - observed_set),
    }
    for key, value in expected.items():
        if coverage.get(key) != value:
            failures.append(f"coverage.{key} does not recompute")
    if coverage.get("minimum_verification_depth") != "attestation":
        failures.append("coverage did not declare the required attestation depth")
    if coverage.get("accepted_issuers") != [topology["gateway"]]:
        failures.append("coverage issuer allowlist does not match the gateway role")
    if coverage.get("invalid_receipts"):
        failures.append("coverage contains invalid or insufficiently verified receipts")
    if not expected_missing:
        failures.append("expected at least one unreceipted bypass action")

    timeline = [
        bundle["mandate"],
        *caps,
        bundle["trajectory_decision"],
    ]
    handoff = bundle["incident_handoff"]["action"]["subject"]
    if handoff.get("profile") != bundle.get("profile"):
        failures.append("incident.handoff profile does not bind the bundle profile")
    if handoff.get("mandate_ref") != mandate_ref:
        failures.append("incident.handoff mandate_ref does not bind the mandate")
    if handoff.get("timeline_digest") != _hash(timeline):
        failures.append("incident.handoff timeline_digest does not recompute")
    if handoff.get("timeline_attestation_hashes") != [
        receipt["hashes"]["attestation"] for receipt in timeline
    ]:
        failures.append(
            "incident.handoff timeline_attestation_hashes does not recompute"
        )
    if handoff.get("timeline_action_types") != [
        receipt["action"]["type"] for receipt in timeline
    ]:
        failures.append("incident.handoff timeline_action_types does not recompute")
    if handoff.get("coverage_digest") != _hash(coverage):
        failures.append("incident.handoff coverage_digest does not recompute")
    if handoff.get("parent_refs") != {
        "mandate": mandate_ref,
        "trajectory": bundle["trajectory_decision"]["hashes"]["attestation"],
    }:
        failures.append("incident.handoff parent_refs do not recompute")

    # 4 — hash-only artifacts and explicit epistemic buckets.
    for artifact in bundle["incident_handoff"]["action"]["subject"]["sensitive_artifacts"]:
        ref = artifact.get("ref", "")
        if not isinstance(ref, str) or _SHA256_RE.fullmatch(ref) is None:
            failures.append(f"sensitive artifact lacks a valid sha256 reference: {artifact}")
        blob = json.dumps(artifact).lower()
        secret_markers = ("bearer ", "password", "secret_key", "-----begin")
        if any(token in blob for token in secret_markers):
            failures.append(f"sensitive artifact appears to inline a secret: {artifact}")

    findings = bundle["incident_handoff"]["action"]["subject"]["findings"]
    for key in ("observed", "inferred", "counterparty_confirmed", "unresolved"):
        if key not in findings:
            failures.append(f"findings missing epistemic bucket: {key}")

    return failures


def main() -> int:
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).with_name("demo-output.json")
    )
    try:
        bundle = json.loads(path.read_text())
        if not isinstance(bundle, dict):
            raise TypeError("bundle must be a JSON object")
        failures = verify(bundle)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: malformed or unreadable bundle: {exc}")
        return 1
    for failure in failures:
        print(f"  ✗ {failure}")
    if failures:
        print(f"\nFAIL: {len(failures)} bundle invariant(s) violated")
        return 1
    print(
        "OK: digest integrity, declared issuer topology, exact lineage, handoff "
        "commitments, coverage reconciliation, hash-only artifacts, and epistemic "
        "separation verify with zero Bulla imports"
    )
    print("NOTE: ed25519 issuer authenticity requires the separate identity rung")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
