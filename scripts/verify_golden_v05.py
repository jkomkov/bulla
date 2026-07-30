#!/usr/bin/env python3
"""Zero-import verifier for Golden v0.5 captive controls."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.golden-suite/0.5-experimental"
SCHEMA = "0.5-experimental"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hashed(items: list[dict[str, Any]], field: str) -> None:
    for item in items:
        supplied = item.pop(field)
        if digest(item) != supplied:
            raise ValueError(f"hash mismatch: {item.get('case_id', item.get('assignment_id', '?'))}")
        item[field] = supplied


def derive_transfer_exit(case: dict[str, Any]) -> str:
    baseline = case["adopted_bindings"]
    current = case["case_bindings"]
    if current["semantic_epoch"] != baseline["semantic_epoch"]:
        return "TERM_STALE/EPOCH_SUPERSEDED"
    if current["authority_regime_hash"] != baseline["authority_regime_hash"]:
        return "ROUTE/AUTHORITY_MISMATCH"
    if current["harm_warrant_hash"] != baseline["harm_warrant_hash"]:
        return "ROUTE/HARM_CHANGED"
    if (
        current["evidence_policy_hash"] != baseline["evidence_policy_hash"]
        or current["settlement_policy_hash"] != baseline["settlement_policy_hash"]
    ):
        return "ROUTE/EVIDENCE_SETTLEMENT_POLICY_MISMATCH"
    if current["reason_hash"] != baseline["reason_hash"]:
        if case["requested_transition"] == "ADOPT_NEW_REASON":
            return "LEGISLATION_REQUIRED/FRESH_REASON"
        return "DISTINGUISH/FRESH_REASON"
    return "APPLY"


def verify_transfer(root: Path) -> None:
    cases = [json.loads(line) for line in (root / "transfer-cases.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(cases) != 144 or len({case["case_id"] for case in cases}) != 144:
        raise ValueError("transfer suite must contain 144 unique cases")
    if sum(case["partition"] == "DESIGN" for case in cases) != 96:
        raise ValueError("transfer design denominator changed")
    if sum(case["partition"] == "HOLDOUT" for case in cases) != 48:
        raise ValueError("transfer holdout denominator changed")
    lineages = {case["lineage"] for case in cases}
    if len(lineages) != 12 or any(sum(case["lineage"] == name for case in cases) != 12 for name in lineages):
        raise ValueError("transfer lineage matrix changed")
    verify_hashed(cases, "case_hash")
    for case in cases:
        if case["profile"] != PROFILE or case["schema_version"] != SCHEMA:
            raise ValueError("transfer case profile mismatch")
        independently_derived = derive_transfer_exit(case)
        if case["actual_exit"] != independently_derived:
            raise ValueError(f"recorded transfer observation disagrees with bindings: {case['case_id']}")
        if independently_derived != case["required_exit"] or case["unsafe_apply"]:
            raise ValueError(f"unsafe or incorrect transfer: {case['case_id']}")
        if (case["actual_exit"] == "APPLY") is not case["eligible_for_apply"]:
            raise ValueError(f"eligibility mismatch: {case['case_id']}")
        if case["oracle_origin"] != "CAPTIVE_MACHINE_PLANTED":
            raise ValueError("captive case was relabeled as foreign")


def verify_scramble(root: Path) -> None:
    report = load(root / "matched-scramble.json")
    assignments = report["assignments"]
    if len(assignments) != 1000 or report["assignment_count"] != 1000:
        raise ValueError("matched-scramble denominator changed")
    verify_hashed(assignments, "assignment_hash")
    if any(
        lineage == assigned
        for item in assignments
        for lineage, assigned in item["mapping"].items()
    ):
        raise ValueError("scramble contains an unmatched self-assignment")
    yields = sorted(item["certified_transfer_yield"] for item in assignments)
    cases = [
        json.loads(line)
        for line in (root / "transfer-cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    reason_by_lineage = {
        case["lineage"]: case["adopted_bindings"]["reason_hash"]
        for case in cases
    }
    for item in assignments:
        recomputed_yield = 0
        recomputed_unsafe = 0
        for case in cases:
            if derive_transfer_exit(case) != "APPLY":
                continue
            applies = (
                reason_by_lineage[item["mapping"][case["lineage"]]]
                == case["case_bindings"]["reason_hash"]
            )
            recomputed_yield += int(applies)
            recomputed_unsafe += int(applies and not case["eligible_for_apply"])
        if item["certified_transfer_yield"] != recomputed_yield:
            raise ValueError(f"scramble yield not reproducible: {item['assignment_id']}")
        if item["unsafe_transfer_count"] != recomputed_unsafe:
            raise ValueError(f"scramble safety count not reproducible: {item['assignment_id']}")
    if report["scramble_p99_yield"] != yields[989]:
        raise ValueError("scramble p99 mismatch")
    if report["authorized_precedent_yield"] <= yields[989] or not report["authorized_exceeds_p99"]:
        raise ValueError("GENERATOR_ECHO_NOT_EXCLUDED")


def verify_frontier(root: Path) -> None:
    report = load(root / "scope-frontier.json")
    if report["point_count"] != 96 or len(report["points"]) != 96 or len(report["frontiers"]) != 12:
        raise ValueError("scope-frontier denominator changed")
    verify_hashed(report["points"], "point_hash")
    by_lineage = {item["lineage"]: item for item in report["frontiers"]}
    for lineage, frontier in by_lineage.items():
        points = [item for item in report["points"] if item["lineage"] == lineage]
        safe = [item["mask"] for item in points if item["safe"]]
        exact = sorted(mask for mask in safe if not any(mask != other and mask & other == mask for other in safe))
        if frontier["maximal_safe_masks"] != exact or frontier["completeness"] != "EXACT":
            raise ValueError(f"scope frontier mismatch: {lineage}")
        if len(exact) > 1 and frontier["selection"] != "CHOICE_REQUIRED":
            raise ValueError("multiple safe maxima were silently selected")


def verify_f11(root: Path) -> None:
    report = load(root / "f11-margins.json")
    margins = report["margins"]
    if report["margin_count"] != 52 or len(margins) != 52:
        raise ValueError("F11 margin denominator changed")
    verify_hashed(margins, "margin_hash")
    if report["exact_count"] + report["unresolved_count"] != 52:
        raise ValueError("F11 margin precision accounting mismatch")
    if any(
        not item["required_change"]["historical_rejection_preserved"]
        or not item["required_change"]["does_not_validate_original_attempt"]
        for item in margins
    ):
        raise ValueError("authorized counterfactual rewrote historical rejection")
    categorical = [
        item for item in margins
        if item["required_change"]["kind"] == "NOT_AUTHORIZABLE_WITHIN_FIXED_CONSTITUTION"
    ]
    if len(categorical) != 4 or any(item["minimal_authorized_change_count"] is not None for item in categorical):
        raise ValueError("categorical harm margin was converted into a finite authorized change")


def verify_f12(root: Path) -> None:
    report = load(root / "f12-effect-laundering.json")
    cases = report["cases"]
    if report["case_count"] != 72 or len(cases) != 72:
        raise ValueError("F12 denominator changed")
    if report["barred_effect_count"] != 12 or report["disguise_count"] != 6:
        raise ValueError("F12 matrix changed")
    verify_hashed(cases, "case_hash")
    by_effect: dict[str, set[str]] = {}
    for case in cases:
        independently_derived = (
            case["required_verdict"]
            if case["protected_effect_hash"] in case["observed_effect_hashes"]
            else "NO_BARRIER"
        )
        if case["actual_verdict"] != independently_derived:
            raise ValueError(f"recorded effect observation disagrees with effects: {case['case_id']}")
        if not case["safe"] or case["actual_verdict"] != case["required_verdict"]:
            raise ValueError(f"effect laundering succeeded: {case['case_id']}")
        if case["action_name_used_by_checker"]:
            raise ValueError("F12 checker consulted an advisory action name")
        by_effect.setdefault(case["effect_id"], set()).add(case["actual_verdict"])
    if len(by_effect) != 12 or any(len(verdicts) != 1 for verdicts in by_effect.values()):
        raise ValueError("action disguise changed an effect verdict")


def main() -> int:
    root = (
        Path(sys.argv[1]) if len(sys.argv) == 2
        else Path(__file__).resolve().parents[1] / "bench/golden/v0.5"
    ).resolve()
    try:
        manifest = load(root / "manifest.json")
        if manifest["profile"] != PROFILE or manifest["classification"] != "INTERNAL_CAPTIVE_CONTROL":
            raise ValueError("manifest overstates evidence class")
        for name, expected in manifest["artifacts"].items():
            if file_digest(root / name) != expected:
                raise ValueError(f"artifact hash mismatch: {name}")
        if manifest["preserved_v04_manifest"] != file_digest(root.parent / "v0.4/manifest.json"):
            raise ValueError("Golden v0.4 binding changed")
        verify_transfer(root)
        verify_scramble(root)
        verify_frontier(root)
        verify_f11(root)
        verify_f12(root)
        report = load(root / "report.json")
        if report["classification"] != "INTERNAL_CAPTIVE_CONTROL":
            raise ValueError("report overstates evidence class")
        if report["unjustified_apply_count"] or report["effect_laundering_count"]:
            raise ValueError("captive soundness gate failed")
        if report["decision_source"] != "DERIVED_FROM_BOUND_FIELDS_NOT_ORACLE_LABELS":
            raise ValueError("captive observations do not identify an independent derivation path")
        if report["scramble_disposition"] != "GENERATOR_ECHO_EXCLUDED":
            raise ValueError("GENERATOR_ECHO_NOT_EXCLUDED")
        if report["efficacy_use_after_freeze"] != "PROHIBITED":
            raise ValueError("captive efficacy stopping rule absent")
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE_CONTROL",
        "transfer_cases": 144,
        "matched_scrambles": 1000,
        "frontier_points": 96,
        "f11_margins": 52,
        "f12_cases": 72,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
