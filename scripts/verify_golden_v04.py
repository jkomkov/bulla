#!/usr/bin/env python3
"""Zero-import verifier for Golden v0.4 precedent yield and F11."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.golden-suite/0.4-experimental"
SCHEMA = "0.4-experimental"
BLOCKED = {
    "FRESH_REASON": "LEGISLATION_REQUIRED/FRESH_REASON",
    "AUTHORITY_CHANGE": "ROUTE/AUTHORITY_MISMATCH",
    "CLOSURE_CHANGE": "TERM_STALE/CLOSURE_WARRANT_CHANGED",
    "CONFLICT": "ROUTE/CONFLICT",
    "EPOCH_SUPERSESSION": "TERM_STALE/EPOCH_SUPERSEDED",
    "RESOURCE_FRONTIER": "ROUTE/RESOURCE_BOUNDED",
    "HOLDOUT_BOUNDARY": "LEGISLATION_REQUIRED/SCOPE_WIDENING",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def merkle(values: list[str]) -> str:
    if not values:
        return digest([])
    layer = [digest({"leaf": value}) for value in values]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [digest({"left": layer[i], "right": layer[i + 1]}) for i in range(0, len(layer), 2)]
    return layer[0]


def formula_nodes(formula: Any) -> int:
    if not isinstance(formula, dict) or "op" not in formula:
        raise ValueError("reason is not a closed FRSL formula")
    op = formula["op"]
    if op == "atom":
        if set(formula) != {"op", "relation", "args"} or not isinstance(formula["args"], list):
            raise ValueError("malformed reason atom")
        return 1
    if op == "not":
        if set(formula) != {"op", "body"}:
            raise ValueError("malformed reason negation")
        return 1 + formula_nodes(formula["body"])
    if op in {"and", "or"}:
        if set(formula) != {"op", "args"} or len(formula["args"]) < 2:
            raise ValueError("malformed reason connective")
        return 1 + sum(formula_nodes(item) for item in formula["args"])
    raise ValueError(f"unsupported reason operator: {op}")


def verify_cases(root: Path) -> tuple[list[dict[str, Any]], str]:
    cases = [json.loads(line) for line in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(cases) != 240 or len({case["case_id"] for case in cases}) != 240:
        raise ValueError("lineage suite must contain 240 unique cases")
    if sum(case["partition"] == "HOLDOUT" for case in cases) != 48:
        raise ValueError("lineage suite must contain 48 holdout cases")
    lineages: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        supplied = case.pop("case_hash")
        if digest(case) != supplied:
            raise ValueError(f"case hash mismatch: {case['case_id']}")
        case["case_hash"] = supplied
        expected = BLOCKED.get(case["scenario"])
        if case["eligible_for_bound_precedent"] is not (expected is None):
            raise ValueError(f"eligibility mismatch: {case['case_id']}")
        if case["required_exit_if_ineligible"] != expected:
            raise ValueError(f"boundary exit mismatch: {case['case_id']}")
        if case["oracle_origin"] != "CAPTIVE_MACHINE_DERIVED":
            raise ValueError("v0.4 findings cannot be relabeled as human adjudications")
        if formula_nodes(case["bindings"]["reason"]) > 32:
            raise ValueError("planted reason exceeds preregistered AST bound")
        lineages.setdefault(case["lineage"], []).append(case)
    if len(lineages) != 12 or any(len(items) != 20 for items in lineages.values()):
        raise ValueError("expected twelve 20-case lineages")
    return cases, merkle([case["case_hash"] for case in cases])


def verify_run(run: dict[str, Any], lineage_cases: list[dict[str, Any]]) -> None:
    design = [case for case in lineage_cases if case["partition"] == "DESIGN"]
    holdout = [case for case in lineage_cases if case["partition"] == "HOLDOUT"]
    known = {case["canonical_record_hash"] for case in design if case["eligible_for_bound_precedent"]}
    if len(run["holdout"]) != 4:
        raise ValueError("each run must score exactly four holdout cases")
    for observed, case in zip(run["holdout"], holdout):
        if observed["case_id"] != case["case_id"]:
            raise ValueError("holdout order mismatch")
        arm = run["arm"]
        if not case["eligible_for_bound_precedent"]:
            expected_exit, certified, reuse = case["required_exit_if_ineligible"], False, "NONE"
        elif arm == "EXACT_PRECLUSION" and case["canonical_record_hash"] in known:
            expected_exit, certified, reuse = case["expected_decision"], True, "EXACT_RECORD"
        elif arm in {"AUTHORIZED_PRECEDENT", "DECLARED_RULE_UPPER_BOUND"}:
            expected_exit, certified, reuse = case["expected_decision"], True, "REASON_PREDICATE"
        elif arm == "LABEL_ONLY_LEARNER":
            expected_exit, certified, reuse = "ADVISORY_ONLY/ESCALATE", False, "UNAUTHORIZED_STATISTICAL_GENERALIZATION"
        else:
            expected_exit, certified, reuse = "ESCALATE/UNSEEN_CASE", False, "NONE"
        if (observed["exit"], observed["certified"], observed["reuse"]) != (expected_exit, certified, reuse):
            raise ValueError(f"holdout oracle mismatch: {case['case_id']} {arm}")
        if observed["unsafe"] or observed["cross_boundary"]:
            raise ValueError(f"unsafe precedent transfer: {case['case_id']} {arm}")
    if run["arm"] == "AUTHORIZED_PRECEDENT":
        if run["findings"] != 1 or len(run["act_trace"]) != 1:
            raise ValueError("authorized arm must expose its single rule adoption")
        if run["act_trace"][0]["operation"] != "FORUM_FINDING+ADOPT_PRECEDENT/BINDING_WITHIN_SCOPE":
            raise ValueError("authorized arm skipped explicit precedent adoption")
    if run["arm"] == "LABEL_ONLY_LEARNER" and any(item["certified"] for item in run["holdout"]):
        raise ValueError("label-only learner gated an action")


def verify_report(root: Path, cases: list[dict[str, Any]]) -> None:
    report = load(root / "precedent-yield-report.json")
    if report["schema_version"] != SCHEMA or report["profile"] != PROFILE:
        raise ValueError("precedent report profile mismatch")
    if (report["case_count"], report["design_count"], report["holdout_count"], report["lineage_count"]) != (240, 192, 48, 12):
        raise ValueError("precedent report denominators changed")
    if len(report["runs"]) != 96:
        raise ValueError("expected 12 x 2 x 4 experiment runs")
    by_lineage = {name: [case for case in cases if case["lineage"] == name] for name in {case["lineage"] for case in cases}}
    for run in report["runs"]:
        verify_run(run, by_lineage[run["lineage"]])
    chronological = [run for run in report["runs"] if run["schedule"] == "CHRONOLOGICAL"]
    auth = {run["lineage"]: run for run in chronological if run["arm"] == "AUTHORIZED_PRECEDENT"}
    exact = {run["lineage"]: run for run in chronological if run["arm"] == "EXACT_PRECLUSION"}
    dominance = sum(
        sum(item["certified"] for item in auth[name]["holdout"]) >
        sum(item["certified"] for item in exact[name]["holdout"])
        for name in auth
    )
    yields = [value for run in auth.values() for value in run["marginal_yields"]]
    improvements = [
        (exact[name]["findings_to_80"] - auth[name]["findings_to_80"]) / exact[name]["findings_to_80"]
        for name in auth
    ]
    result = report["result"]
    if dominance != result["holdout_lineages_authorized_dominates"]:
        raise ValueError("holdout dominance recomputation mismatch")
    if statistics.median(yields) != result["median_marginal_precedent_yield"]:
        raise ValueError("marginal-yield recomputation mismatch")
    if abs(statistics.median(improvements) - result["median_findings_to_80_improvement"]) > 1e-15:
        raise ValueError("coverage-efficiency recomputation mismatch")
    expected = "DEMONSTRATED_COMPOUNDING" if dominance >= 9 and statistics.median(yields) >= 2 and statistics.median(improvements) >= 0.25 else "LIMITED_COMPOUNDING"
    if result["classification"] != expected or result["unsafe_transfer"] or result["cross_boundary_reuse"]:
        raise ValueError("preregistered precedent classification mismatch")


def verify_f11(root: Path) -> str:
    report = load(root / "f11-laundering.json")
    cases = report["cases"]
    if report["case_count"] != 52 or len(cases) != 52 or report["unsafe_count"] != 0:
        raise ValueError("F11 denominator or unsafe count mismatch")
    if len({case["archetype"] for case in cases}) != 13 or len({case["variant"] for case in cases}) != 4:
        raise ValueError("F11 must be the complete 13-by-4 matrix")
    for case in cases:
        supplied = case.pop("case_hash")
        if digest(case) != supplied:
            raise ValueError(f"F11 case hash mismatch: {case['case_id']}")
        case["case_hash"] = supplied
        if case["actual_outcome"] != case["required_outcome"] or case["accepted"] or not case["safe"]:
            raise ValueError(f"F11 laundering case not rejected: {case['case_id']}")
    if set(report["mutation_operators"]) != {
        "REMOVE_AUTHORITY_TOKEN", "ALTER_PRECEDENT_EFFECT", "FORGE_FORUM_FINDING",
        "PRICE_CATEGORICAL_BAR", "CHANGE_SEMANTIC_EPOCH", "TRUNCATE_BUDGET_FRONTIER",
    }:
        raise ValueError("F11 mutation operator set changed")
    return merkle([case["case_hash"] for case in cases])


def verify_regression_and_self_hosting(root: Path) -> None:
    regression = load(root / "regression-cohort.json")
    inherited = load(root.parent / "v0.3/partial-coverage.json")
    inherited_ids = [case["case_id"] for case in inherited["cases"]]
    if regression["requested_regression_denominator"] != 126:
        raise ValueError("approved regression denominator was not retained")
    if regression["live_frozen_regression_denominator"] != len(inherited_ids) or len(inherited_ids) != 70:
        raise ValueError("live partial-regression denominator mismatch")
    if regression["case_ids"] != inherited_ids or regression["verification_failures"] != 0:
        raise ValueError("partial regression cohort changed or failed")
    if regression["source_hash"] != file_digest(root.parent / "v0.3/partial-coverage.json"):
        raise ValueError("partial regression source hash mismatch")
    self_hosting = load(root / "self-hosting-claim-flow.json")
    if self_hosting["classification"] != "CAPTIVE_WITH_FOREIGN_SUBSTRATE":
        raise ValueError("self-hosting evidence was overclassified")
    if self_hosting["precedent_created"] is not False:
        raise ValueError("merge transport silently created precedent")
    for item in self_hosting["trace"]:
        supplied = item.pop("node_hash")
        if digest(item) != supplied:
            raise ValueError("self-hosting claim-flow node hash mismatch")
        item["node_hash"] = supplied
    if self_hosting["trace_hash"] != digest(self_hosting["trace"]):
        raise ValueError("self-hosting trace hash mismatch")


def verify_mutations(root: Path) -> None:
    campaign = load(root / "mutation-campaign.json")
    f11 = load(root / "f11-laundering.json")
    cases = {case["case_id"]: case for case in f11["cases"]}
    expected_operators = set(f11["mutation_operators"])
    if campaign["mutant_count"] != 6 or campaign["critical_mutant_count"] != 6:
        raise ValueError("v0.4 mutation denominator changed")
    if campaign["killed_count"] != 6 or campaign["critical_kill_rate"] != 1.0:
        raise ValueError("critical F11 mutant survived")
    if {item["operator"] for item in campaign["mutants"]} != expected_operators:
        raise ValueError("F11 mutation operators and campaign differ")
    for mutant in campaign["mutants"]:
        if not mutant["non_equivalent"] or not mutant["killed"] or not mutant["kill_case_ids"]:
            raise ValueError(f"unjustified mutation disposition: {mutant['mutant_id']}")
        if not set(mutant["kill_case_ids"]) <= set(cases):
            raise ValueError("mutant cites an unknown F11 case")
        expected = digest({"operator": mutant["operator"], "kill_case_ids": mutant["kill_case_ids"]})
        if mutant["non_equivalence_witness"] != expected:
            raise ValueError("mutation non-equivalence witness mismatch")


def main() -> int:
    root = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path(__file__).resolve().parents[1] / "bench/golden/v0.4"
    ).resolve()
    try:
        cases, case_root = verify_cases(root)
        verify_report(root, cases)
        f11_root = verify_f11(root)
        verify_regression_and_self_hosting(root)
        verify_mutations(root)
        manifest = load(root / "manifest.json")
        if manifest["profile"] != PROFILE or manifest["classification"] != "INTERNAL_CAPTIVE":
            raise ValueError("manifest profile or evidence classification mismatch")
        if manifest["case_merkle_root"] != case_root or manifest["f11_merkle_root"] != f11_root:
            raise ValueError("manifest Merkle root mismatch")
        for name, expected in manifest["artifacts"].items():
            if file_digest(root / name) != expected:
                raise ValueError(f"artifact hash mismatch: {name}")
        golden = root.parent
        roots = {
            "golden_v0.1_manifest": golden / "v0.1/manifest.json",
            "golden_v0.2_freeze": golden / "v0.2/freeze-manifest.json",
            "golden_v0.3_manifest": golden / "v0.3/manifest.json",
            "semantic_boundary_stack_v0.3": golden / "v0.3/bulla-semantic-boundary-stack-v0.3.json",
        }
        for name, path in roots.items():
            if file_digest(path) != manifest["preserved_roots"][name]:
                raise ValueError(f"prior Golden root changed: {name}")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True, "profile": PROFILE, "cases": 240, "holdout": 48,
        "f11": 52, "classification": "INTERNAL_CAPTIVE",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
