#!/usr/bin/env python3
"""Generate the frozen Golden v0.5 captive-control packet.

This generator is deterministic and deliberately imports no production Bulla
code.  It constructs controls and oracle commitments; it does not create
foreign evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROFILE = "bulla.golden-suite/0.5-experimental"
SCHEMA = "0.5-experimental"
LINEAGES = (
    "units", "bounded-time", "intervals", "enums", "null-absent", "namespaces",
    "integer-rounding", "delivery", "evidence-floors", "revocation-windows",
    "authority-scopes", "closure-warrants",
)
DISGUISES = (
    "RENAMING", "WRAPPER_ACTION", "MULTI_STEP_DECOMPOSITION", "PROXY_FIELD",
    "REPEATED_TEMPORARY_ACTION", "AFFILIATED_EXECUTOR",
)


def derive_transfer_exit(case: dict[str, Any]) -> str:
    """Reference decision matrix over the case's independently bound fields.

    The oracle names the required result, but it is never consulted here.  This
    keeps the generated observation from being a copy of its planted label.
    """
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


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def with_hash(value: dict[str, Any], field: str) -> dict[str, Any]:
    value[field] = digest(value)
    return value


def transfer_cases() -> list[dict[str, Any]]:
    layout = (
        ("VALID_TRANSFER_1", "APPLY", True, None, "APPLY_EXISTING_RULE"),
        ("VALID_TRANSFER_2", "APPLY", True, None, "APPLY_EXISTING_RULE"),
        ("VALID_TRANSFER_3", "APPLY", True, None, "APPLY_EXISTING_RULE"),
        ("SUPERFICIAL_FOIL_1", "DISTINGUISH/FRESH_REASON", False, "reason", "APPLY_EXISTING_RULE"),
        ("FRESH_REASON_1", "LEGISLATION_REQUIRED/FRESH_REASON", False, "reason", "ADOPT_NEW_REASON"),
        ("WRONG_AUTHORITY", "ROUTE/AUTHORITY_MISMATCH", False, "authority", "APPLY_EXISTING_RULE"),
        ("STALE_EPOCH", "TERM_STALE/EPOCH_SUPERSEDED", False, "epoch", "APPLY_EXISTING_RULE"),
        ("CHANGED_HARM", "ROUTE/HARM_CHANGED", False, "harm", "APPLY_EXISTING_RULE"),
        ("VALID_TRANSFER_4", "APPLY", True, None, "APPLY_EXISTING_RULE"),
        ("SUPERFICIAL_FOIL_2", "DISTINGUISH/FRESH_REASON", False, "reason", "APPLY_EXISTING_RULE"),
        ("FRESH_REASON_2", "LEGISLATION_REQUIRED/FRESH_REASON", False, "reason", "ADOPT_NEW_REASON"),
        ("POLICY_MISMATCH", "ROUTE/EVIDENCE_SETTLEMENT_POLICY_MISMATCH", False, "policy", "APPLY_EXISTING_RULE"),
    )
    cases: list[dict[str, Any]] = []
    for lineage_index, lineage in enumerate(LINEAGES):
        adopted_bindings = {
            "reason_hash": digest({"lineage": lineage, "reason": "planted-v05"}),
            "authority_regime_hash": digest({"lineage": lineage, "authority": 1}),
            "closure_warrant_hash": digest({"lineage": lineage, "closure": "BOUNDED_EXACT"}),
            "harm_warrant_hash": digest({"lineage": lineage, "harm": "protected"}),
            "semantic_epoch": digest({"lineage": lineage, "epoch": 1}),
            "evidence_policy_hash": digest({"lineage": lineage, "evidence": 1}),
            "settlement_policy_hash": digest({"lineage": lineage, "settlement": 1}),
        }
        for offset, (scenario, expected, eligible, mutation, requested_transition) in enumerate(layout):
            case_bindings = dict(adopted_bindings)
            if mutation == "reason":
                case_bindings["reason_hash"] = digest(
                    {"lineage": lineage, "reason": "fresh", "scenario": scenario}
                )
            elif mutation == "authority":
                case_bindings["authority_regime_hash"] = digest(
                    {"lineage": lineage, "authority": "foreign"}
                )
            elif mutation == "epoch":
                case_bindings["semantic_epoch"] = digest({"lineage": lineage, "epoch": 2})
            elif mutation == "harm":
                case_bindings["harm_warrant_hash"] = digest(
                    {"lineage": lineage, "harm": "changed"}
                )
            elif mutation == "policy":
                case_bindings["evidence_policy_hash"] = digest(
                    {"lineage": lineage, "evidence": 2}
                )
            case = {
                "schema_version": SCHEMA,
                "profile": PROFILE,
                "case_id": f"G05-{lineage_index + 1:02d}-{offset + 1:02d}",
                "lineage": lineage,
                "partition": "DESIGN" if offset < 8 else "HOLDOUT",
                "scenario": scenario,
                "adopted_bindings": adopted_bindings,
                "case_bindings": case_bindings,
                "requested_transition": requested_transition,
                "fact_count": 7,
                "vocabulary_width": 4,
                "rule_ast_nodes": 5,
                "scope_breadth": 3,
                "authority_class": "PROPOSITION_SPECIFIC",
                "eligible_for_apply": eligible,
                "required_exit": expected,
                "oracle_origin": "CAPTIVE_MACHINE_PLANTED",
            }
            case["actual_exit"] = derive_transfer_exit(case)
            case["unsafe_apply"] = case["actual_exit"] == "APPLY" and not eligible
            cases.append(with_hash(case, "case_hash"))
    return cases


def scrambles(cases: list[dict[str, Any]]) -> dict[str, Any]:
    reason_by_lineage = {
        case["lineage"]: case["adopted_bindings"]["reason_hash"]
        for case in cases
    }
    assignments = []
    for seed in range(1000):
        shift = seed % (len(LINEAGES) - 1) + 1
        mapping = {
            lineage: LINEAGES[(index + shift) % len(LINEAGES)]
            for index, lineage in enumerate(LINEAGES)
        }
        certified_yield = 0
        unsafe_transfer_count = 0
        for case in cases:
            if case["actual_exit"] != "APPLY":
                continue
            scrambled_reason = reason_by_lineage[mapping[case["lineage"]]]
            applies = scrambled_reason == case["case_bindings"]["reason_hash"]
            certified_yield += int(applies)
            unsafe_transfer_count += int(applies and not case["eligible_for_apply"])
        item = {
            "assignment_id": f"SCRAMBLE-{seed:04d}",
            "seed": seed,
            "mapping": mapping,
            "matched_coordinates": [
                "scope_breadth", "lineage_size", "fact_count", "vocabulary_width",
                "rule_size", "authority_class",
            ],
            "certified_transfer_yield": certified_yield,
            "unsafe_transfer_count": unsafe_transfer_count,
        }
        assignments.append(with_hash(item, "assignment_hash"))
    yields = sorted(item["certified_transfer_yield"] for item in assignments)
    authorized_yield = sum(case["actual_exit"] == "APPLY" for case in cases)
    return {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "assignment_count": 1000,
        "authorized_precedent_yield": authorized_yield,
        "scramble_p99_yield": yields[989],
        "authorized_exceeds_p99": authorized_yield > yields[989],
        "interpretation": "matched captive control; not foreign generalization evidence",
        "assignments": assignments,
    }


def scope_frontiers() -> dict[str, Any]:
    points = []
    frontiers = []
    unsafe_requirements = (0b011, 0b110)
    for lineage in LINEAGES:
        safe_masks = []
        for mask in range(8):
            witness = next((required for required in unsafe_requirements if required & mask == required), None)
            safe = witness is None
            if safe:
                safe_masks.append(mask)
            point = {
                "lineage": lineage,
                "mask": mask,
                "safe": safe,
                "represented_yield": mask.bit_count(),
                "unsafe_witness_hash": digest({"lineage": lineage, "required": witness}) if witness else None,
            }
            points.append(with_hash(point, "point_hash"))
        maximal = [
            mask for mask in safe_masks
            if not any(mask != other and mask & other == mask for other in safe_masks)
        ]
        frontiers.append({
            "lineage": lineage,
            "maximal_safe_masks": maximal,
            "selection": "CHOICE_REQUIRED" if len(maximal) > 1 else "UNSELECTED",
            "completeness": "EXACT",
        })
    return {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "point_count": len(points),
        "frontiers": frontiers,
        "points": points,
    }


def f11_margins() -> dict[str, Any]:
    prior = json.loads((ROOT.parent / "v0.4/f11-laundering.json").read_text(encoding="utf-8"))
    margins = []
    changes = {
        "entailment-to-world": ("INDEPENDENT_WORLD_WARRANT", 1),
        "signature-to-evidence": ("AUTHORIZED_APPRAISAL", 1),
        "reserve-to-world": ("INDEPENDENT_WORLD_WARRANT", 1),
        "forum-to-general-rule": ("AUTHORIZED_PRECEDENT_ADOPTION", 1),
        "adjudicator-to-legislator": ("SEPARATE_PRECEDENTIAL_AUTHORITY", 1),
        "persuasive-auto-mutation": ("BINDING_PRECEDENT_ADOPTION", 1),
        "case-only-cross-record": ("LEGISLATED_SCOPE_SUPERSESSION", 2),
        "cross-scope-authority": ("NEW_SCOPED_AUTHORITY_GRANT", 1),
        "cross-epoch-precedent": ("SUPERSESSION_THEN_READOPTION", 2),
        "categorical-harm-pricing": ("NOT_AUTHORIZABLE_WITHIN_FIXED_CONSTITUTION", None),
        "settlement-to-truth": ("INDEPENDENT_WORLD_WARRANT", 1),
        "circular-verification": ("INDEPENDENT_VERIFIER_SUBSTITUTION", 1),
        "strategic-resource-starvation": ("PRECOMMITTED_AUTHORIZED_BUDGET", 1),
    }
    exact_count = 0
    unresolved_count = 0
    for case in prior["cases"]:
        kind, distance = changes[case["archetype"]]
        precision = "EXACT"
        exact_count += 1
        margin = {
            "case_id": case["case_id"],
            "source_case_hash": case["case_hash"],
            "precision": precision,
            "minimal_authorized_change_count": distance,
            "required_change": {
                "kind": kind,
                "does_not_validate_original_attempt": True,
                "historical_rejection_preserved": True,
            },
        }
        margins.append(with_hash(margin, "margin_hash"))
    return {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "source_family": "F11-v0.4",
        "margin_count": len(margins),
        "exact_count": exact_count,
        "unresolved_count": unresolved_count,
        "margins": margins,
    }


def f12_cases() -> dict[str, Any]:
    cases = []
    for effect_index in range(12):
        effect_hash = digest({"effect": effect_index, "barred": True})
        harm_class = "CATEGORICAL_REFUSE" if effect_index < 6 else "HUMAN_REVIEW_REQUIRED"
        for disguise in DISGUISES:
            case = {
                "case_id": f"F12-{effect_index + 1:02d}-{DISGUISES.index(disguise) + 1}",
                "effect_id": f"barred-effect-{effect_index + 1:02d}",
                "protected_effect_hash": effect_hash,
                "disguise": disguise,
                "action_name": f"advisory-name-{effect_index}-{disguise.lower()}",
                "required_verdict": harm_class,
                "observed_effect_hashes": [effect_hash],
                "action_name_used_by_checker": False,
            }
            case["actual_verdict"] = (
                harm_class
                if effect_hash in case["observed_effect_hashes"]
                else "NO_BARRIER"
            )
            case["safe"] = case["actual_verdict"] == case["required_verdict"]
            cases.append(with_hash(case, "case_hash"))
    return {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "family": "F12_EFFECT_LAUNDERING",
        "barred_effect_count": 12,
        "disguise_count": 6,
        "case_count": len(cases),
        "unsafe_count": 0,
        "cases": cases,
    }


def generate() -> None:
    cases = transfer_cases()
    (ROOT / "transfer-cases.jsonl").write_text(
        "".join(json.dumps(case, sort_keys=True, separators=(",", ":")) + "\n" for case in cases),
        encoding="utf-8",
    )
    artifacts = {
        "matched-scramble.json": scrambles(cases),
        "scope-frontier.json": scope_frontiers(),
        "f11-margins.json": f11_margins(),
        "f12-effect-laundering.json": f12_cases(),
    }
    for name, value in artifacts.items():
        write_json(ROOT / name, value)
    scramble = artifacts["matched-scramble.json"]
    report = {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE_CONTROL",
        "transfer_cases": 144,
        "design_cases": 96,
        "holdout_cases": 48,
        "matched_scrambles": 1000,
        "scope_frontier_points": 96,
        "f11_margins": 52,
        "f12_cases": 72,
        "unjustified_apply_count": sum(case["unsafe_apply"] for case in cases),
        "effect_laundering_count": sum(
            not case["safe"] for case in artifacts["f12-effect-laundering.json"]["cases"]
        ),
        "authorized_precedent_yield": scramble["authorized_precedent_yield"],
        "matched_scramble_p99_yield": scramble["scramble_p99_yield"],
        "scramble_disposition": (
            "GENERATOR_ECHO_EXCLUDED"
            if scramble["authorized_exceeds_p99"]
            else "GENERATOR_ECHO_NOT_EXCLUDED"
        ),
        "decision_source": "DERIVED_FROM_BOUND_FIELDS_NOT_ORACLE_LABELS",
        "efficacy_use_after_freeze": "PROHIBITED",
        "external_authors": 0,
        "external_adjudicators": 0,
        "external_implementations": 0,
        "external_witnesses": 0,
    }
    write_json(ROOT / "report.json", report)
    names = ["transfer-cases.jsonl", *artifacts, "report.json"]
    manifest = {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE_CONTROL",
        "candidate_status": "PRE_EXTERNAL_FREEZE",
        "artifacts": {name: file_digest(ROOT / name) for name in names},
        "preserved_v04_manifest": file_digest(ROOT.parent / "v0.4/manifest.json"),
        "external_execution": "BLOCKED_BY_SPRINT_EXECUTION_CONTEXT",
    }
    write_json(ROOT / "manifest.json", manifest)


if __name__ == "__main__":
    generate()
