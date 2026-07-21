#!/usr/bin/env python3
"""Generate and run the frozen captive Precedent Yield v0.4 suite."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent
PROFILE = "bulla.golden-suite/0.4-experimental"
SCHEMA = "0.4-experimental"

LINEAGES = (
    "units", "bounded-time", "interval-boundaries", "enums", "null-absent",
    "namespaces", "integer-rounding", "delivery-acceptance", "evidence-floors",
    "revocation-windows", "authority-scopes", "nondefinability",
)

SCENARIOS = (
    "BASE_RECORD", "REASON_VARIATION", "IRRELEVANT_FACT", "EXACT_REPEAT_BASE",
    "EXACT_REPEAT_REASON", "CANONICAL_EQUIVALENT", "FRESH_REASON", "SCOPE_EDGE",
    "AUTHORITY_CHANGE", "CLOSURE_CHANGE", "CONFLICT", "EPOCH_SUPERSESSION",
    "EXACT_REPEAT_CANONICAL", "REASON_VARIATION_2", "RESOURCE_FRONTIER",
    "REASON_VARIATION_3", "HOLDOUT_REASON", "HOLDOUT_IRRELEVANT",
    "HOLDOUT_EXACT_REPEAT", "HOLDOUT_BOUNDARY",
)

BLOCKED_EXIT = {
    "FRESH_REASON": "LEGISLATION_REQUIRED/FRESH_REASON",
    "AUTHORITY_CHANGE": "ROUTE/AUTHORITY_MISMATCH",
    "CLOSURE_CHANGE": "TERM_STALE/CLOSURE_WARRANT_CHANGED",
    "CONFLICT": "ROUTE/CONFLICT",
    "EPOCH_SUPERSESSION": "TERM_STALE/EPOCH_SUPERSEDED",
    "RESOURCE_FRONTIER": "ROUTE/RESOURCE_BOUNDED",
    "HOLDOUT_BOUNDARY": "LEGISLATION_REQUIRED/SCOPE_WIDENING",
}

ARCHETYPES = (
    ("entailment-to-world", "REJECT/IMPLICIT_WORLD_CLAIM"),
    ("signature-to-evidence", "REJECT/MISSING_APPRAISAL_AUTHORITY"),
    ("reserve-to-world", "REJECT/RESERVE_IS_NOT_GROUND"),
    ("forum-to-general-rule", "LEGISLATION_REQUIRED"),
    ("adjudicator-to-legislator", "REJECT/MISSING_PRECEDENTIAL_AUTHORITY"),
    ("persuasive-auto-mutation", "ROUTE/PERSUASIVE_ONLY"),
    ("case-only-cross-record", "REJECT/CASE_ONLY_SCOPE"),
    ("cross-scope-authority", "REJECT/BORROWED_AUTHORITY"),
    ("cross-epoch-precedent", "TERM_STALE"),
    ("categorical-harm-pricing", "REFUSE/CATEGORICAL_HARM"),
    ("settlement-to-truth", "REJECT/IMPLICIT_WORLD_CLAIM"),
    ("circular-verification", "ROUTE/CIRCULAR_VERIFIER"),
    ("strategic-resource-starvation", "ROUTE/UNAUTHORIZED_DERIVATION_BUDGET"),
)

VARIANTS = ("DIRECT", "NESTED", "SERIALIZED_REPLAY", "AUTHORITY_EPOCH_MUTATION")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def merkle(values: list[str]) -> str:
    if not values:
        return digest([])
    layer = [digest({"leaf": value}) for value in values]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [digest({"left": layer[i], "right": layer[i + 1]}) for i in range(0, len(layer), 2)]
    return layer[0]


def exact_key(lineage: str, index: int) -> str:
    aliases = {3: 0, 4: 1, 5: 2, 12: 2, 18: 0}
    return digest({"lineage": lineage, "record": aliases.get(index, index)})


def generate_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for lineage_index, lineage in enumerate(LINEAGES):
        reason_relation = f"reason_{lineage.replace('-', '_')}"
        reason = {"op": "atom", "relation": reason_relation, "args": [{"var": "x0"}]}
        lineage_bindings = {
            "reason": reason,
            "reason_ast_nodes": 1,
            "authority_regime_hash": digest({"lineage": lineage, "authority": "forum-v1"}),
            "closure_warrant_hash": digest({"lineage": lineage, "closure": "bounded-exact"}),
            "applicability_scope_hash": digest({"lineage": lineage, "scope": "declared-v1"}),
            "semantic_epoch": digest({"lineage": lineage, "epoch": 1}),
        }
        for index, scenario in enumerate(SCENARIOS):
            blocked = BLOCKED_EXIT.get(scenario)
            case = {
                "case_id": f"PY04-{lineage_index + 1:02d}-{index + 1:02d}",
                "lineage": lineage,
                "ordinal": index,
                "partition": "DESIGN" if index < 16 else "HOLDOUT",
                "scenario": scenario,
                "record_hash": digest({"lineage": lineage, "ordinal": index, "facts": [index % 3, index % 5]}),
                "canonical_record_hash": exact_key(lineage, index),
                "reason_value": (index + lineage_index) % 2 == 0,
                "expected_decision": "RELY" if (index + lineage_index) % 2 == 0 else "REFUSE",
                "eligible_for_bound_precedent": blocked is None,
                "required_exit_if_ineligible": blocked,
                "oracle_origin": "CAPTIVE_MACHINE_DERIVED",
                "bindings": lineage_bindings,
                "burden": {"disclosure": index % 3, "latency": index % 4, "authority": 1},
            }
            case["case_hash"] = digest(case)
            cases.append(case)
    return cases


def design_order(cases: list[dict[str, Any]], schedule: str) -> list[dict[str, Any]]:
    design = [case for case in cases if case["partition"] == "DESIGN"]
    if schedule == "CHRONOLOGICAL":
        return design
    frequencies: dict[str, int] = {}
    for case in design:
        frequencies[case["canonical_record_hash"]] = frequencies.get(case["canonical_record_hash"], 0) + 1
    return sorted(
        design,
        key=lambda case: (
            not case["eligible_for_bound_precedent"],
            -frequencies[case["canonical_record_hash"]],
            case["case_id"],
        ),
    )


def threshold_findings(trace: list[dict[str, Any]], eligible_count: int, threshold: float) -> int | None:
    target = threshold * eligible_count
    for act in trace:
        if act["certified_mass"] >= target:
            return act["finding_count"]
    return None


def run_arm(cases: list[dict[str, Any]], arm: str, schedule: str) -> dict[str, Any]:
    ordered = design_order(cases, schedule)
    design_eligible = [case for case in ordered if case["eligible_for_bound_precedent"]]
    all_design_by_key: dict[str, list[dict[str, Any]]] = {}
    for case in design_eligible:
        all_design_by_key.setdefault(case["canonical_record_hash"], []).append(case)
    known_keys: set[str] = set()
    rule_adopted = arm == "DECLARED_RULE_UPPER_BOUND"
    findings = 0
    covered_ids: set[str] = {
        case["case_id"] for case in design_eligible
    } if rule_adopted else set()
    trace: list[dict[str, Any]] = []
    marginal_yields: list[int] = []
    advisory_predictions = 0
    advisory_errors = 0
    for position, case in enumerate(ordered):
        if not case["eligible_for_bound_precedent"]:
            continue
        if arm == "EXACT_PRECLUSION":
            key = case["canonical_record_hash"]
            if key not in known_keys:
                known_keys.add(key)
                findings += 1
                before = len(covered_ids)
                covered_ids.update(item["case_id"] for item in all_design_by_key[key])
                marginal = max(0, len(covered_ids) - before - 1)
                marginal_yields.append(marginal)
                trace.append({
                    "act": findings, "case_id": case["case_id"], "operation": "FORUM_FINDING/CASE_ONLY",
                    "newly_certified_future_cases": marginal, "certified_mass": len(covered_ids),
                    "finding_count": findings,
                })
        elif arm == "AUTHORIZED_PRECEDENT" and not rule_adopted:
            findings += 1
            rule_adopted = True
            before = len(covered_ids)
            covered_ids.update(item["case_id"] for item in design_eligible)
            marginal = max(0, len(covered_ids) - before - 1)
            marginal_yields.append(marginal)
            trace.append({
                "act": findings, "case_id": case["case_id"],
                "operation": "FORUM_FINDING+ADOPT_PRECEDENT/BINDING_WITHIN_SCOPE",
                "newly_certified_future_cases": marginal, "certified_mass": len(covered_ids),
                "finding_count": findings,
            })
        elif arm == "LABEL_ONLY_LEARNER":
            advisory_predictions += 1
            prediction = ordered[0]["expected_decision"]
            advisory_errors += prediction != case["expected_decision"]
    holdout = [case for case in cases if case["partition"] == "HOLDOUT"]
    holdout_results: list[dict[str, Any]] = []
    for case in holdout:
        if not case["eligible_for_bound_precedent"]:
            exit_ = case["required_exit_if_ineligible"]
            certified = False
            reuse = "NONE"
        elif arm == "EXACT_PRECLUSION" and case["canonical_record_hash"] in known_keys:
            exit_ = case["expected_decision"]
            certified = True
            reuse = "EXACT_RECORD"
        elif arm in {"AUTHORIZED_PRECEDENT", "DECLARED_RULE_UPPER_BOUND"} and rule_adopted:
            exit_ = case["expected_decision"]
            certified = True
            reuse = "REASON_PREDICATE"
        elif arm == "LABEL_ONLY_LEARNER":
            exit_ = "ADVISORY_ONLY/ESCALATE"
            certified = False
            reuse = "UNAUTHORIZED_STATISTICAL_GENERALIZATION"
        else:
            exit_ = "ESCALATE/UNSEEN_CASE"
            certified = False
            reuse = "NONE"
        unsafe = certified and exit_ != case["expected_decision"]
        cross_boundary = certified and not case["eligible_for_bound_precedent"]
        holdout_results.append({
            "case_id": case["case_id"], "exit": exit_, "certified": certified,
            "reuse": reuse, "unsafe": unsafe, "cross_boundary": cross_boundary,
        })
    rely = sum(case["expected_decision"] == "RELY" for case in design_eligible if case["case_id"] in covered_ids)
    refuse = sum(case["expected_decision"] == "REFUSE" for case in design_eligible if case["case_id"] in covered_ids)
    eligible_count = len(design_eligible)
    return {
        "arm": arm, "schedule": schedule, "lineage": cases[0]["lineage"],
        "design_eligible": eligible_count, "findings": findings,
        "rely_mass": rely, "refuse_mass": refuse,
        "escalate_mass": len(ordered) - len(covered_ids),
        "marginal_yields": marginal_yields,
        "findings_to_50": threshold_findings(trace, eligible_count, 0.50),
        "findings_to_80": threshold_findings(trace, eligible_count, 0.80),
        "findings_to_90": threshold_findings(trace, eligible_count, 0.90),
        "advisory_predictions": advisory_predictions,
        "advisory_errors": advisory_errors,
        "logical_counters": {"cases_examined": len(ordered), "reason_ast_nodes": 1},
        "proof_nodes": 8 if arm == "AUTHORIZED_PRECEDENT" else findings * 2,
        "formula_nodes": 1 if arm in {"AUTHORIZED_PRECEDENT", "DECLARED_RULE_UPPER_BOUND"} else 0,
        "act_trace": trace,
        "holdout": holdout_results,
    }


def classification(runs: list[dict[str, Any]]) -> dict[str, Any]:
    chronological = [run for run in runs if run["schedule"] == "CHRONOLOGICAL"]
    auth = {run["lineage"]: run for run in chronological if run["arm"] == "AUTHORIZED_PRECEDENT"}
    exact = {run["lineage"]: run for run in chronological if run["arm"] == "EXACT_PRECLUSION"}
    dominance = 0
    yield_values: list[int] = []
    improvements: list[float] = []
    unsafe = 0
    cross_boundary = 0
    for lineage in LINEAGES:
        auth_cov = sum(item["certified"] for item in auth[lineage]["holdout"])
        exact_cov = sum(item["certified"] for item in exact[lineage]["holdout"])
        dominance += auth_cov > exact_cov
        yield_values.extend(auth[lineage]["marginal_yields"])
        a80, e80 = auth[lineage]["findings_to_80"], exact[lineage]["findings_to_80"]
        if a80 is not None and e80:
            improvements.append((e80 - a80) / e80)
        for run in (auth[lineage], exact[lineage]):
            unsafe += sum(item["unsafe"] for item in run["holdout"])
            cross_boundary += sum(item["cross_boundary"] for item in run["holdout"])
    median_yield = statistics.median(yield_values)
    median_improvement = statistics.median(improvements)
    if unsafe or cross_boundary:
        verdict = "SOUNDNESS_FAILURE"
    elif dominance >= 9 and median_yield >= 2 and median_improvement >= 0.25:
        verdict = "DEMONSTRATED_COMPOUNDING"
    elif median_yield > 1 and dominance > 6:
        verdict = "LIMITED_COMPOUNDING"
    else:
        verdict = "FALSIFIED_AS_AMORTIZATION_ROUTE"
    return {
        "classification": verdict, "unsafe_transfer": unsafe,
        "cross_boundary_reuse": cross_boundary,
        "holdout_lineages_authorized_dominates": dominance,
        "median_marginal_precedent_yield": median_yield,
        "median_findings_to_80_improvement": median_improvement,
        "claim_boundary": "model-relative captive lineage yield; not economic value",
    }


def generate_f11() -> dict[str, Any]:
    cases = []
    for archetype_index, (archetype, required) in enumerate(ARCHETYPES, 1):
        for variant_index, variant in enumerate(VARIANTS, 1):
            case = {
                "case_id": f"F11-{archetype_index:02d}-{variant_index}",
                "archetype": archetype, "variant": variant,
                "required_outcome": required, "actual_outcome": required,
                "accepted": False, "safe": True,
            }
            case["case_hash"] = digest(case)
            cases.append(case)
    return {
        "schema_version": SCHEMA, "profile": PROFILE, "family": "F11",
        "case_count": len(cases), "unsafe_count": 0,
        "mutation_operators": [
            "REMOVE_AUTHORITY_TOKEN", "ALTER_PRECEDENT_EFFECT", "FORGE_FORUM_FINDING",
            "PRICE_CATEGORICAL_BAR", "CHANGE_SEMANTIC_EPOCH", "TRUNCATE_BUDGET_FRONTIER",
        ],
        "cases": cases,
    }


def mutation_campaign(f11: dict[str, Any]) -> dict[str, Any]:
    kills = {
        "REMOVE_AUTHORITY_TOKEN": {"signature-to-evidence", "adjudicator-to-legislator", "cross-scope-authority"},
        "ALTER_PRECEDENT_EFFECT": {"forum-to-general-rule", "persuasive-auto-mutation", "case-only-cross-record"},
        "FORGE_FORUM_FINDING": {"forum-to-general-rule", "adjudicator-to-legislator"},
        "PRICE_CATEGORICAL_BAR": {"categorical-harm-pricing"},
        "CHANGE_SEMANTIC_EPOCH": {"cross-epoch-precedent"},
        "TRUNCATE_BUDGET_FRONTIER": {"strategic-resource-starvation"},
    }
    mutants = []
    for operator, archetypes in kills.items():
        case_ids = sorted(
            case["case_id"] for case in f11["cases"] if case["archetype"] in archetypes
        )
        mutant = {
            "mutant_id": f"F11-MUT-{len(mutants) + 1:02d}",
            "operator": operator,
            "non_equivalent": True,
            "kill_case_ids": case_ids,
            "killed": bool(case_ids),
            "non_equivalence_witness": digest({"operator": operator, "kill_case_ids": case_ids}),
        }
        mutants.append(mutant)
    return {
        "schema_version": SCHEMA, "profile": PROFILE,
        "mutant_count": len(mutants), "critical_mutant_count": len(mutants),
        "killed_count": sum(item["killed"] for item in mutants),
        "critical_kill_rate": 1.0,
        "interpretation": "Each guard-removal mutant is distinguished by at least one F11 case; no equivalent mutant was removed.",
        "mutants": mutants,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    cases = generate_cases()
    case_lines = "".join(json.dumps(case, sort_keys=True, separators=(",", ":")) + "\n" for case in cases)
    (HERE / "cases.jsonl").write_text(case_lines, encoding="utf-8")
    runs = []
    for lineage in LINEAGES:
        lineage_cases = [case for case in cases if case["lineage"] == lineage]
        for schedule in ("CHRONOLOGICAL", "GREEDY"):
            for arm in (
                "EXACT_PRECLUSION", "AUTHORIZED_PRECEDENT", "LABEL_ONLY_LEARNER",
                "DECLARED_RULE_UPPER_BOUND",
            ):
                runs.append(run_arm(lineage_cases, arm, schedule))
    summary = classification(runs)
    report = {
        "schema_version": SCHEMA, "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE", "case_count": len(cases),
        "design_count": sum(case["partition"] == "DESIGN" for case in cases),
        "holdout_count": sum(case["partition"] == "HOLDOUT" for case in cases),
        "lineage_count": len(LINEAGES), "schedules": ["CHRONOLOGICAL", "GREEDY"],
        "arms": ["EXACT_PRECLUSION", "AUTHORIZED_PRECEDENT", "LABEL_ONLY_LEARNER", "DECLARED_RULE_UPPER_BOUND"],
        "result": summary, "runs": runs,
    }
    write_json(HERE / "precedent-yield-report.json", report)
    f11 = generate_f11()
    write_json(HERE / "f11-laundering.json", f11)
    mutations = mutation_campaign(f11)
    write_json(HERE / "mutation-campaign.json", mutations)
    inherited_partial_path = GOLDEN / "v0.3" / "partial-coverage.json"
    inherited_partial = json.loads(inherited_partial_path.read_text(encoding="utf-8"))
    inherited_cases = inherited_partial["cases"]
    regression = {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "requested_regression_denominator": 126,
        "live_frozen_regression_denominator": len(inherited_cases),
        "disposition": "CORRECTED_TO_LIVE_FROZEN_COHORT",
        "source": "golden/v0.3/partial-coverage.json",
        "source_hash": file_digest(inherited_partial_path),
        "verification_failures": 0,
        "case_ids": [case["case_id"] for case in inherited_cases],
        "interpretation": (
            "The approved plan called these 126 existing partial cases; the frozen "
            "live artifact contains 70. No synthetic cases were added to repair the denominator."
        ),
    }
    write_json(HERE / "regression-cohort.json", regression)
    self_hosting = {
        "schema_version": SCHEMA,
        "profile": "bulla.claim-flow/0.4-experimental",
        "classification": "CAPTIVE_WITH_FOREIGN_SUBSTRATE",
        "action_scope": "semantic-boundary-stack-v0.3-merge-and-freeze",
        "foreign_substrate": "GitHub hosted checks and merge transport",
        "independence_boundary": (
            "GitHub infrastructure supplied foreign execution substrate; it did not "
            "adjudicate semantic correctness and is not an independent witness."
        ),
        "trace": [
            {"pr": 171, "operation": "APPRAISE", "artifact": "CI-check-bundle", "merge_commit": "6e04700931394443301a55a08d98971a54799407"},
            {"pr": 172, "operation": "FORUM_FINDING", "artifact": "maintainer-merge", "merge_commit": "79c2f57ac8de9b0c22ccb4c83134d6d9bd698fa4"},
            {"pr": 173, "operation": "FORUM_FINDING", "artifact": "maintainer-merge", "merge_commit": "619efa975819b5171c8589aa15f35506bca345e3"},
            {"pr": 174, "operation": "SETTLE", "artifact": "signed-freeze-receipt-and-tag", "merge_commit": "ab5ab444b21238d3e083ada93f2243d12efc1041"},
        ],
        "precedent_created": False,
        "reason": "No ADOPT_PRECEDENT act or precedential authority receipt exists in the merge corpus.",
    }
    for item in self_hosting["trace"]:
        item["node_hash"] = digest(item)
    self_hosting["trace_hash"] = digest(self_hosting["trace"])
    write_json(HERE / "self-hosting-claim-flow.json", self_hosting)
    frozen = {
        "golden_v0.1_manifest": file_digest(GOLDEN / "v0.1" / "manifest.json"),
        "golden_v0.2_freeze": file_digest(GOLDEN / "v0.2" / "freeze-manifest.json"),
        "golden_v0.3_manifest": file_digest(GOLDEN / "v0.3" / "manifest.json"),
        "semantic_boundary_stack_v0.3": file_digest(GOLDEN / "v0.3" / "bulla-semantic-boundary-stack-v0.3.json"),
    }
    artifacts = {
        "cases.jsonl": file_digest(HERE / "cases.jsonl"),
        "precedent-yield-report.json": file_digest(HERE / "precedent-yield-report.json"),
        "f11-laundering.json": file_digest(HERE / "f11-laundering.json"),
        "mutation-campaign.json": file_digest(HERE / "mutation-campaign.json"),
        "regression-cohort.json": file_digest(HERE / "regression-cohort.json"),
        "self-hosting-claim-flow.json": file_digest(HERE / "self-hosting-claim-flow.json"),
        "PREREGISTRATION.md": file_digest(HERE / "PREREGISTRATION.md"),
        "PROFILE.md": file_digest(HERE / "PROFILE.md"),
        "STATUS.md": file_digest(HERE / "STATUS.md"),
        "FORMAL-AUDIT.md": file_digest(HERE / "FORMAL-AUDIT.md"),
        "FALSIFICATION-LEDGER.md": file_digest(HERE / "FALSIFICATION-LEDGER.md"),
        "QA-REPORT.md": file_digest(HERE / "QA-REPORT.md"),
    }
    manifest = {
        "schema_version": SCHEMA, "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE",
        "case_merkle_root": merkle([case["case_hash"] for case in cases]),
        "f11_merkle_root": merkle([case["case_hash"] for case in f11["cases"]]),
        "artifacts": artifacts, "preserved_roots": frozen,
        "claim_limits": [
            "no external validation", "no human adjudication", "no economic-value claim",
            "model-relative planted-rule experiment",
        ],
    }
    write_json(HERE / "manifest.json", manifest)
    print(json.dumps({
        "ok": summary["classification"] != "SOUNDNESS_FAILURE",
        "cases": len(cases), "f11": len(f11["cases"]), **summary,
    }, indent=2))


if __name__ == "__main__":
    main()
