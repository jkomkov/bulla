#!/usr/bin/env python3
"""Zero-Bulla-import replay for Semantic Finality v0.1 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(wire.encode("utf-8")).hexdigest()


def decide(case):
    snapshot = case["snapshot"]
    warrant = case["closure_warrant"]
    policy = case["policy"]
    reserve = case.get("reserve")
    lock = case.get("external_lock")
    alternatives = []
    if reserve:
        alternatives.append({"kind": "provisional", "reserve_microunits": reserve["required_reserve_microunits"]})
    alternatives += [{"kind": "evidence", "plan_hash": item} for item in case.get("evidence_plan_hashes", [])]
    alternatives += [{"kind": "route", "route": item} for item in case.get("route_options", [])]

    def result(status, cause):
        return {
            "profile": "bulla.semantic-finality/0.1-experimental",
            "status": status, "cause": cause, "available_alternatives": alternatives,
            "reserve": reserve, "evidence_plan_hashes": case.get("evidence_plan_hashes", []),
            "authority_regime_hash": case["authority_regime_hash"],
            "closure_warrant_hash": digest(warrant), "snapshot_hash": digest(snapshot),
            "semantic_epoch": case["current_semantic_epoch"], "policy_hash": digest(policy),
            "receipt_references": case.get("receipt_references", []),
            "ambiguity_claim": "relative-to-model-class-and-warrant",
        }

    if case["current_semantic_epoch"] != snapshot["semantic_epoch"] or digest(warrant) != snapshot["closure_warrant_hash"]:
        return result("TERM_STALE", "EPOCH_OR_CLOSURE_MISMATCH")
    if case.get("conflict_certificate_hash") is not None:
        return result("ROUTE", "CONFLICT")
    if case["certified_surface"] == "REFUSE":
        return result("REFUSE", "CERTIFIED_REFUSE")
    closure_ok = warrant["status"] in policy["permitted_closure_statuses"]
    closure_finalizable = closure_ok and warrant["status"] not in ("OPEN_WORLD", "UNKNOWN_COVERAGE")
    if case["certified_surface"] == "RELY" and closure_finalizable and len(set(case["represented_outcomes"])) <= policy["finality_threshold"]:
        return result("FINALIZE", "CERTIFIED_RELY_AND_SUFFICIENT_CLOSURE")
    lock_ok = bool(
        reserve and lock and lock["status"] == "LOCKED"
        and reserve["external_lock_reference"] == lock["lock_reference"]
        and reserve["required_reserve_microunits"] == lock["amount_microunits"]
        and reserve["currency"] == lock["currency"]
    )
    reserve_ok = bool(reserve and reserve["required_reserve_microunits"] <= policy["maximum_reserve_microunits"])
    if (
        case["certified_surface"] == "AMBIGUOUS" and closure_ok and reserve_ok and lock_ok
        and policy["provisional_execution_allowed"]
        and case.get("action_type", "procurement.payment") in policy["provisional_action_types"]
    ):
        return result("EXECUTE_PROVISIONALLY", "VERIFIED_AMBIGUITY_RESERVE")
    if case.get("evidence_plan_hashes") and set(case.get("evidence_classes", [])).issubset(policy["permitted_observation_classes"]):
        return result("REQUEST_EVIDENCE", "PERMITTED_ENRICHMENT_PLAN")
    routes = list(dict.fromkeys(case.get("route_options", [])))
    ranked = [item for item in policy["authored_resolution_order"] if item in routes]
    if len(routes) > 1 and not ranked:
        return result("ROUTE", "CHOICE_REQUIRED")
    if ranked:
        return result("ROUTE", "AUTHORED_ROUTE:" + ranked[0])
    return result("ROUTE", "UNRESOLVED")


def verify_reserve(case):
    profile = case["consequence_profile"]
    losses = {item["class_id"]: item["loss_microunits"] for item in profile["consequence_classes"]}
    outcomes = sorted(set(case["represented_outcomes"]))
    worst = max(losses[item] for item in outcomes)
    reserve = case["reserve"]
    return (
        reserve["represented_outcomes"] == outcomes
        and reserve["worst_case_loss_microunits"] == worst
        and reserve["required_reserve_microunits"] == worst + reserve["model_risk_buffer_microunits"]
        and reserve["action_hash"] == profile["action_hash"]
        and reserve["closure_warrant_hash"] == digest(case["closure_warrant"])
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text(encoding="utf-8"))
        expected = case.pop("expected_assessment")
        recomputed = decide(case)
        checks = {
            "assessment": recomputed == expected,
            "assessment_hash": digest(recomputed) == case["expected_assessment_hash"],
            "reserve": case.get("reserve") is None or verify_reserve(case),
        }
        payload = {"ok": all(checks.values()), "checks": checks, "recomputed": recomputed}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
