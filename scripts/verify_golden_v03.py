#!/usr/bin/env python3
"""Zero-import verifier for Golden v0.3 boundary and complexity reports."""

from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.golden-suite/0.3-experimental"


def closed(value: Any, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{where} has unknown or missing fields")
    return value


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def formula_size(formula: dict[str, Any]) -> int:
    op = formula["op"]
    if op in {"true", "false", "atom"}:
        expected = {"op"} if op in {"true", "false"} else {"op", "relation", "args"}
        closed(formula, expected, "formula")
        if op == "atom":
            if not isinstance(formula["relation"], str) or not isinstance(formula["args"], list):
                raise ValueError("formula atom is malformed")
            for term in formula["args"]:
                closed(term, {"var"}, "formula atom term")
        return 1
    if op == "not":
        closed(formula, {"op", "body"}, "formula")
        return 1 + formula_size(formula["body"])
    if op in {"and", "or"}:
        closed(formula, {"op", "args"}, "formula")
        if not isinstance(formula["args"], list) or len(formula["args"]) < 2:
            raise ValueError("Boolean connective requires at least two arguments")
        return 1 + sum(formula_size(item) for item in formula["args"])
    raise ValueError(f"unsupported formula op {op}")


def feature(index: int) -> dict[str, Any]:
    return {"op": "atom", "relation": f"x{index}", "args": [{"var": "x0"}]}


def formula_mask(formula: dict[str, Any], width: int) -> int:
    vectors = tuple(itertools.product((False, True), repeat=width))
    universe = (1 << len(vectors)) - 1
    masks = {
        json.dumps(feature(index), sort_keys=True, separators=(",", ":")): sum(
            1 << ordinal for ordinal, vector in enumerate(vectors) if vector[index]
        )
        for index in range(width)
    }

    def visit(node: dict[str, Any]) -> int:
        key = json.dumps(node, sort_keys=True, separators=(",", ":"))
        if key in masks:
            return masks[key]
        op = node.get("op")
        if op == "true":
            return universe
        if op == "false":
            return 0
        if op == "not":
            return universe ^ visit(node["body"])
        if op == "and":
            result = universe
            for child in node["args"]:
                result &= visit(child)
            return result
        if op == "or":
            result = 0
            for child in node["args"]:
                result |= visit(child)
            return result
        raise ValueError(f"unrecognized or non-feature formula node {node}")

    return visit(formula)


def formula_from_cubes(cubes: Any, width: int) -> dict[str, Any]:
    if not isinstance(cubes, list):
        raise ValueError("cube cover must be a list")

    def literal(value: Any) -> dict[str, Any]:
        if not isinstance(value, int) or isinstance(value, bool) or value == 0 or abs(value) > width:
            raise ValueError("cube literal is outside the declared vocabulary")
        node = feature(abs(value) - 1)
        return node if value > 0 else {"op": "not", "body": node}

    terms: list[dict[str, Any]] = []
    for cube in cubes:
        if not isinstance(cube, list) or len(set(cube)) != len(cube):
            raise ValueError("cube must contain distinct ordered literals")
        literals = [literal(value) for value in cube]
        if not literals:
            term = {"op": "true"}
        elif len(literals) == 1:
            term = literals[0]
        else:
            term = {"op": "and", "args": literals}
        terms.append(term)
    if not terms:
        return {"op": "false"}
    if len(terms) == 1:
        return terms[0]
    return {"op": "or", "args": terms}


def truth_label(family: str, vector: tuple[bool, ...], seed: int) -> bool | None:
    width = len(vector)
    if family == "irrelevant_literal":
        return vector[seed % min(3, width)]
    if family == "sparse_interaction":
        a, b, c = ((seed + offset) % width for offset in range(3))
        return (vector[a] and vector[b]) or (not vector[a] and vector[c])
    if family == "parity_frontier":
        return (sum(vector) + seed) % 2 == 1
    if family == "ground_hidden":
        if vector[-1] == vector[-2]:
            return None
        return vector[seed % min(3, width)]
    raise ValueError(f"unknown F10 family {family}")


def verify_f9(report: dict[str, Any]) -> None:
    closed(report, {"schema_version", "profile", "case_count", "by_stratum", "unsafe_count", "cases"}, "F9 report")
    if report.get("profile") != PROFILE or report.get("case_count") != 48:
        raise ValueError("F9 profile or denominator mismatch")
    expected = {
        "GROUNDING": ("ROUTE", "UNRESOLVED_GROUNDING"),
        "SEMANTIC": ("ROUTE", "SEMANTIC_INDETERMINACY"),
        "DERIVATION": ("ROUTE", "RESOURCE_BOUNDED_DERIVATION"),
        "AUTHORITY": ("TERM_STALE", "CLAIM_CHAIN_BINDING_MISMATCH"),
    }
    ids: set[str] = set()
    for case in report["cases"]:
        closed(case, {
            "case_id", "stratum", "ordinal", "world_status", "entailment_status",
            "settlement_status", "expected_exit", "expected_cause", "actual_exit",
            "actual_cause", "residual_boundaries", "derivation_status", "safe",
            "claim_chain_hash",
        }, "F9 case")
        if case["case_id"] in ids:
            raise ValueError("duplicate F9 case id")
        ids.add(case["case_id"])
        if (case["expected_exit"], case["expected_cause"]) != expected[case["stratum"]]:
            raise ValueError(f"F9 oracle rule mismatch: {case['case_id']}")
        if (case["actual_exit"], case["actual_cause"]) != expected[case["stratum"]] or case["safe"] is not True:
            raise ValueError(f"unsafe F9 result: {case['case_id']}")
        expected_derivation = "RESOURCE_BOUNDED" if case["stratum"] == "DERIVATION" else "CERTIFIED"
        if case["derivation_status"] != expected_derivation:
            raise ValueError(f"F9 derivation status mismatch: {case['case_id']}")
    if report["unsafe_count"] != 0:
        raise ValueError("F9 unsafe count must be zero")


def verify_f10(report: dict[str, Any]) -> None:
    closed(report, {
        "schema_version", "profile", "case_count", "by_family", "by_exit",
        "unsafe_count", "partial_count", "minimum_partial_coverage", "cases",
    }, "F10 report")
    if report.get("profile") != PROFILE or report.get("case_count") != 128:
        raise ValueError("F10 profile or denominator mismatch")
    ids: set[str] = set()
    partial_coverages: list[float] = []
    unsafe = 0
    for case in report["cases"]:
        closed(case, {
            "case_id", "family", "width", "seed", "max_nodes", "exit", "safe",
            "hypothesis_count", "opposing_pair_count", "positive_total",
            "negative_total", "semantic_residual_total", "positive_covered",
            "negative_covered", "certified_coverage", "positive_cubes",
            "negative_cubes", "positive_formula_hash", "negative_formula_hash",
            "positive_nodes", "negative_nodes", "candidate_observables",
            "authority_branching",
        }, "F10 case")
        if case["case_id"] in ids:
            raise ValueError("duplicate F10 case id")
        ids.add(case["case_id"])
        width, seed, family = case["width"], case["seed"], case["family"]
        vectors = tuple(itertools.product((False, True), repeat=width))
        positive_mask = negative_mask = 0
        positive_total = negative_total = residual_total = 0
        for ordinal, vector in enumerate(vectors):
            label = truth_label(family, vector, seed)
            if label is True:
                positive_total += 1
                positive_mask |= 1 << ordinal
            elif label is False:
                negative_total += 1
                negative_mask |= 1 << ordinal
            else:
                residual_total += 1
        positive_formula = formula_from_cubes(case["positive_cubes"], width)
        negative_formula = formula_from_cubes(case["negative_cubes"], width)
        if digest(positive_formula) != case["positive_formula_hash"] or digest(negative_formula) != case["negative_formula_hash"]:
            raise ValueError(f"F10 formula hash mismatch: {case['case_id']}")
        if formula_size(positive_formula) != case["positive_nodes"] or formula_size(negative_formula) != case["negative_nodes"]:
            raise ValueError(f"F10 formula size mismatch: {case['case_id']}")
        emitted_positive = formula_mask(positive_formula, width)
        emitted_negative = formula_mask(negative_formula, width)
        safe = not (emitted_positive & negative_mask) and not (emitted_negative & positive_mask)
        positive_covered = (emitted_positive & positive_mask).bit_count()
        negative_covered = (emitted_negative & negative_mask).bit_count()
        labeled = positive_total + negative_total
        coverage = (positive_covered + negative_covered) / max(1, labeled)
        expected_values = {
            "hypothesis_count": len(vectors),
            "positive_total": positive_total,
            "negative_total": negative_total,
            "semantic_residual_total": residual_total,
            "positive_covered": positive_covered,
            "negative_covered": negative_covered,
        }
        if any(case[key] != value for key, value in expected_values.items()) or abs(case["certified_coverage"] - coverage) > 1e-15:
            raise ValueError(f"F10 coverage mismatch: {case['case_id']}")
        if case["safe"] is not safe:
            raise ValueError(f"F10 safety field mismatch: {case['case_id']}")
        unsafe += not safe
        if case["exit"] == "PARTIAL":
            partial_coverages.append(coverage)
    if unsafe or report["unsafe_count"] != 0:
        raise ValueError("F10 unsafe output")
    if report["partial_count"] != len(partial_coverages):
        raise ValueError("F10 partial denominator mismatch")
    if abs(report["minimum_partial_coverage"] - min(partial_coverages)) > 1e-15:
        raise ValueError("F10 minimum partial coverage mismatch")


def verify_partial(report: dict[str, Any], v02: dict[str, Any]) -> None:
    closed(report, {
        "schema_version", "source_profile", "source_partial_denominator",
        "minimum_joint_coverage", "median_joint_coverage",
        "maximum_joint_coverage", "coverage_distribution", "cases",
    }, "partial coverage report")
    source = {case["case_id"]: case for case in v02["cases"] if case["status"] == "PARTIAL"}
    if report["source_partial_denominator"] != len(source) or len(report["cases"]) != len(source):
        raise ValueError("v0.2 partial denominator mismatch")
    for case in report["cases"]:
        closed(case, {
            "case_id", "positive_coverage", "negative_coverage",
            "joint_certified_coverage", "rely_mass", "refuse_mass",
            "escalate_mass", "semantic_residual_total", "claim_boundary",
        }, "partial coverage case")
        if case["case_id"] not in source:
            raise ValueError("partial coverage cites an unknown source case")
        state = source[case["case_id"]]["fingerprint"]["best_certified_partial_state"]
        labeled = state["positive_total"] + state["negative_total"]
        expected = (state["positive_covered"] + state["negative_covered"]) / max(1, labeled)
        if abs(case["joint_certified_coverage"] - expected) > 1e-15:
            raise ValueError("partial coverage does not recompute")
        expected_rely = state["positive_covered"] / max(1, labeled)
        expected_refuse = state["negative_covered"] / max(1, labeled)
        if abs(case["rely_mass"] - expected_rely) > 1e-15 or abs(case["refuse_mass"] - expected_refuse) > 1e-15:
            raise ValueError("partial RELY/REFUSE mass does not recompute")
        if abs(case["escalate_mass"] - (1.0 - expected)) > 1e-15:
            raise ValueError("partial ESCALATE mass does not recompute")
    for name, field in (
        ("RELY", "rely_mass"),
        ("REFUSE", "refuse_mass"),
        ("ESCALATE", "escalate_mass"),
        ("CERTIFIED", "joint_certified_coverage"),
    ):
        values = sorted(case[field] for case in report["cases"])
        expected_quantiles = {"p10": values[6], "median": values[34], "p90": values[62]}
        if report["coverage_distribution"].get(name) != expected_quantiles:
            raise ValueError(f"partial {name} distribution does not recompute")


def verify_scout(report: dict[str, Any]) -> None:
    closed(report, {
        "schema_version", "case_count", "no_definition_ground_conflation",
        "disposition", "cases",
    }, "definition-observation scout")
    if report["case_count"] != 24 or len(report["cases"]) != 24:
        raise ValueError("definition-observation scout denominator mismatch")
    for case in report["cases"]:
        closed(case, {
            "case_id", "definition_available", "ground_observed",
            "authority_available", "computationally_complete", "exit",
            "unresolved_boundaries", "derivation_status",
        }, "definition-observation case")
        expected = "FINALIZE" if all(
            case[key]
            for key in (
                "definition_available", "ground_observed",
                "authority_available", "computationally_complete",
            )
        ) else "ROUTE"
        if case["exit"] != expected:
            raise ValueError("definition-observation scout conflates an unresolved axis")
        expected_derivation = "CERTIFIED" if case["computationally_complete"] else "RESOURCE_BOUNDED"
        if case["derivation_status"] != expected_derivation:
            raise ValueError("definition-observation derivation status mismatch")
    if report["no_definition_ground_conflation"] is not True:
        raise ValueError("definition availability was allowed to impersonate observation")


def verify(root: Path) -> dict[str, Any]:
    names = (
        "f9-oracle-boundary.json", "f10-complexity-bombs.json",
        "partial-coverage.json", "definition-observation-scout.json",
    )
    reports = {name: load(root / name) for name in names}
    manifest = load(root / "manifest.json")
    closed(manifest, {
        "schema_version", "profile", "lineage", "family_counts", "report_hashes",
        "gates", "evidence_status", "external_replay_status",
    }, "v0.3 manifest")
    if manifest.get("profile") != PROFILE or manifest.get("family_counts") != {"F9": 48, "F10": 128}:
        raise ValueError("v0.3 manifest profile or denominators mismatch")
    for name, report in reports.items():
        if digest(report) != manifest["report_hashes"].get(name):
            raise ValueError(f"manifest hash mismatch: {name}")
    verify_f9(reports["f9-oracle-boundary.json"])
    verify_f10(reports["f10-complexity-bombs.json"])
    verify_partial(reports["partial-coverage.json"], load(root.parent / "v0.2" / "scaling-report.json"))
    verify_scout(reports["definition-observation-scout.json"])
    if not all(manifest["gates"].values()):
        raise ValueError("v0.3 manifest contains an open internal gate")
    return {
        "ok": True,
        "profile": PROFILE,
        "f9_cases": 48,
        "f10_cases": 128,
        "external_replay_status": manifest["external_replay_status"],
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_golden_v03.py <golden-v0.3-directory>", file=sys.stderr)
        return 2
    try:
        result = verify(Path(sys.argv[1]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
