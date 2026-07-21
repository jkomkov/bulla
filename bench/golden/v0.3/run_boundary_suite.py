#!/usr/bin/env python3
"""Build Golden v0.3 F9 oracle-boundary and F10 complexity evidence."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from bulla.experimental.frsl import atom, canonical_hash, formula_size, variable
from bulla.experimental.invention import _bounded_disjunction, _safe_generalized_dnf
from bulla.experimental.semantic_boundary import (
    ClaimChain,
    DerivationStatus,
    EntailmentClaim,
    EntailmentStatus,
    SettlementClaim,
    SubstantiveBoundary,
    WorldClaim,
    WorldClaimStatus,
    assess_semantic_boundary,
)
from bulla.experimental.semantic_finality import FinalityAssessment, FinalityStatus


HERE = Path(__file__).resolve().parent
V02 = HERE.parent / "v0.2"
PROFILE = "bulla.golden-suite/0.3-experimental"
MAX_NODES = 512
D = "sha256:" + "11" * 32
E = "sha256:" + "22" * 32
F = "sha256:" + "33" * 32


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def base_assessment() -> FinalityAssessment:
    return FinalityAssessment(
        status=FinalityStatus.FINALIZE,
        cause="F9_DECLARED_MODEL_RELY",
        available_alternatives=(),
        reserve=None,
        evidence_plan_hashes=(),
        authority_regime_hash=D,
        closure_warrant_hash=E,
        snapshot_hash=F,
        semantic_epoch=D,
        policy_hash=E,
        receipt_references=(),
    )


F9_STRATA = ("SEMANTIC", "GROUNDING", "AUTHORITY", "DERIVATION")


def f9_case(stratum: str, ordinal: int) -> dict[str, Any]:
    base = base_assessment()
    world_status = WorldClaimStatus.WARRANTED_RELATIVE
    entailment_status = EntailmentStatus.CERTIFIED
    residual: tuple[SubstantiveBoundary, ...] = ()
    derivation_status = DerivationStatus.CERTIFIED
    authority_hash = D
    if stratum == "GROUNDING":
        world_status = WorldClaimStatus.UNKNOWN
    elif stratum == "SEMANTIC":
        entailment_status = EntailmentStatus.INDETERMINATE
        residual = (SubstantiveBoundary.SEMANTIC,)
    elif stratum == "DERIVATION":
        entailment_status = EntailmentStatus.INDETERMINATE
        derivation_status = DerivationStatus.RESOURCE_BOUNDED
    elif stratum == "AUTHORITY":
        authority_hash = F
    else:
        raise ValueError(f"unknown F9 stratum {stratum!r}")
    chain = ClaimChain(
        q_world=WorldClaim(
            proposition_hash=canonical_hash({"stratum": stratum, "ordinal": ordinal, "layer": "qW"}),
            status=world_status,
            warrant_hashes=(F,) if world_status is WorldClaimStatus.WARRANTED_RELATIVE else (),
            closure_warrant_hash=E,
            scope={"family": "F9", "ordinal": ordinal},
        ),
        q_entailment=EntailmentClaim(
            premise_hash=canonical_hash({"stratum": stratum, "ordinal": ordinal, "layer": "premise"}),
            conclusion_hash=canonical_hash({"stratum": stratum, "ordinal": ordinal, "layer": "conclusion"}),
            status=entailment_status,
            certificate_hash=F if entailment_status is EntailmentStatus.CERTIFIED else None,
            model_class_hash=D,
            residual_boundaries=residual,
            derivation_status=derivation_status,
        ),
        q_settlement=SettlementClaim(
            action_hash=canonical_hash({"stratum": stratum, "ordinal": ordinal, "layer": "action"}),
            status=base.status,
            assessment_hash=base.assessment_hash,
            authority_regime_hash=authority_hash,
            semantic_epoch=D,
            recourse_forum=f"forum://golden/F9/{stratum.lower()}",
        ),
    )
    result = assess_semantic_boundary(base, chain, None, active_outcomes=("declared-safe",))
    expected = {
        "GROUNDING": ("ROUTE", "UNRESOLVED_GROUNDING"),
        "SEMANTIC": ("ROUTE", "SEMANTIC_INDETERMINACY"),
        "DERIVATION": ("ROUTE", "RESOURCE_BOUNDED_DERIVATION"),
        "AUTHORITY": ("TERM_STALE", "CLAIM_CHAIN_BINDING_MISMATCH"),
    }[stratum]
    safe = (result.status.value, result.cause) == expected
    return {
        "case_id": f"F9-{stratum.lower()}-{ordinal:02d}",
        "stratum": stratum,
        "ordinal": ordinal,
        "world_status": world_status.value,
        "entailment_status": entailment_status.value,
        "settlement_status": base.status.value,
        "expected_exit": expected[0],
        "expected_cause": expected[1],
        "actual_exit": result.status.value,
        "actual_cause": result.cause,
        "residual_boundaries": [item.value for item in result.residual_boundaries],
        "derivation_status": result.derivation_status.value,
        "safe": safe,
        "claim_chain_hash": chain.chain_hash,
    }


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


def formula_mask(formula: dict[str, Any], features: tuple[dict[str, Any], ...], vectors: tuple[tuple[bool, ...], ...]) -> int:
    universe = (1 << len(vectors)) - 1
    feature_masks: dict[str, int] = {}
    for feature_index, feature in enumerate(features):
        feature_masks[json.dumps(feature, sort_keys=True, separators=(",", ":"))] = sum(
            1 << vector_index
            for vector_index, vector in enumerate(vectors)
            if vector[feature_index]
        )

    def visit(node: dict[str, Any]) -> int:
        key = json.dumps(node, sort_keys=True, separators=(",", ":"))
        if key in feature_masks:
            return feature_masks[key]
        if node["op"] == "true":
            return universe
        if node["op"] == "false":
            return 0
        if node["op"] == "not":
            return universe ^ visit(node["body"])
        if node["op"] == "and":
            value = universe
            for child in node["args"]:
                value &= visit(child)
            return value
        if node["op"] == "or":
            value = 0
            for child in node["args"]:
                value |= visit(child)
            return value
        raise ValueError(f"unsupported Boolean node {node['op']}")

    return visit(formula)


def formula_cubes(formula: dict[str, Any]) -> list[list[int]]:
    """Encode a generated DNF as ordered signed, one-based feature indices."""

    def literal(node: dict[str, Any]) -> int:
        negated = node["op"] == "not"
        atom_node = node["body"] if negated else node
        if atom_node["op"] != "atom" or not atom_node["relation"].startswith("x"):
            raise ValueError("generated formula is outside the Boolean cube fragment")
        value = int(atom_node["relation"][1:]) + 1
        return -value if negated else value

    def cube(node: dict[str, Any]) -> list[int]:
        if node["op"] == "true":
            return []
        if node["op"] == "and":
            return [literal(item) for item in node["args"]]
        return [literal(node)]

    if formula["op"] == "false":
        return []
    if formula["op"] == "or":
        return [cube(item) for item in formula["args"]]
    return [cube(formula)]


def f10_case(family: str, width: int, seed: int) -> dict[str, Any]:
    features = tuple(atom(f"x{index}", (variable("x0"),)) for index in range(width))
    vectors = tuple(itertools.product((False, True), repeat=width))
    positive = {vector for vector in vectors if truth_label(family, vector, seed) is True}
    negative = {vector for vector in vectors if truth_label(family, vector, seed) is False}
    residual = set(vectors) - positive - negative
    positive_formula, positive_terms = _safe_generalized_dnf(features, positive, negative)
    negative_formula, negative_terms = _safe_generalized_dnf(features, negative, positive)
    complete = not residual
    within_bound = formula_size(positive_formula) <= MAX_NODES and formula_size(negative_formula) <= MAX_NODES
    if complete and within_bound:
        emitted_positive = positive_formula
        emitted_negative = negative_formula
        exit_status = "COMPILED"
    else:
        emitted_positive = _bounded_disjunction(positive_terms, MAX_NODES)
        emitted_negative = _bounded_disjunction(negative_terms, MAX_NODES)
        exit_status = "PARTIAL"
    index = {vector: ordinal for ordinal, vector in enumerate(vectors)}
    positive_mask = sum(1 << index[vector] for vector in positive)
    negative_mask = sum(1 << index[vector] for vector in negative)
    emitted_positive_mask = formula_mask(emitted_positive, features, vectors)
    emitted_negative_mask = formula_mask(emitted_negative, features, vectors)
    safe = not (emitted_positive_mask & negative_mask) and not (emitted_negative_mask & positive_mask)
    positive_covered = (emitted_positive_mask & positive_mask).bit_count()
    negative_covered = (emitted_negative_mask & negative_mask).bit_count()
    labeled = len(positive) + len(negative)
    return {
        "case_id": f"F10-{family}-w{width:02d}-s{seed}",
        "family": family,
        "width": width,
        "seed": seed,
        "max_nodes": MAX_NODES,
        "exit": exit_status,
        "safe": safe,
        "hypothesis_count": len(vectors),
        "opposing_pair_count": len(positive) * len(negative),
        "positive_total": len(positive),
        "negative_total": len(negative),
        "semantic_residual_total": len(residual),
        "positive_covered": positive_covered,
        "negative_covered": negative_covered,
        "certified_coverage": (positive_covered + negative_covered) / max(1, labeled),
        "positive_cubes": formula_cubes(emitted_positive),
        "negative_cubes": formula_cubes(emitted_negative),
        "positive_formula_hash": canonical_hash(emitted_positive),
        "negative_formula_hash": canonical_hash(emitted_negative),
        "positive_nodes": formula_size(emitted_positive),
        "negative_nodes": formula_size(emitted_negative),
        "candidate_observables": width,
        "authority_branching": 1 + seed,
    }


def partial_coverage() -> dict[str, Any]:
    source = json.loads((V02 / "scaling-report.json").read_text(encoding="utf-8"))
    cases = []
    for case in source["cases"]:
        if case["status"] != "PARTIAL":
            continue
        state = case["fingerprint"]["best_certified_partial_state"]
        labeled = state["positive_total"] + state["negative_total"]
        covered = state["positive_covered"] + state["negative_covered"]
        rely_mass = state["positive_covered"] / max(1, labeled)
        refuse_mass = state["negative_covered"] / max(1, labeled)
        certified_mass = covered / max(1, labeled)
        cases.append({
            "case_id": case["case_id"],
            "positive_coverage": state["positive_covered"] / max(1, state["positive_total"]),
            "negative_coverage": state["negative_covered"] / max(1, state["negative_total"]),
            "joint_certified_coverage": certified_mass,
            "rely_mass": rely_mass,
            "refuse_mass": refuse_mass,
            "escalate_mass": 1.0 - certified_mass,
            "semantic_residual_total": state["residual_total"],
            "claim_boundary": "coverage is model-relative and does not eliminate grounding uncertainty",
        })
    shares = sorted(case["joint_certified_coverage"] for case in cases)
    def quantiles(field: str) -> dict[str, float]:
        values = sorted(case[field] for case in cases)
        def nearest_rank(q: float) -> float:
            return values[max(0, math.ceil(q * len(values)) - 1)]
        return {
            "p10": nearest_rank(0.10),
            "median": nearest_rank(0.50),
            "p90": nearest_rank(0.90),
        }
    return {
        "schema_version": "0.3-partial-coverage",
        "source_profile": "bulla.golden-suite/0.2-experimental",
        "source_partial_denominator": len(cases),
        "minimum_joint_coverage": shares[0],
        "median_joint_coverage": shares[len(shares) // 2],
        "maximum_joint_coverage": shares[-1],
        "coverage_distribution": {
            "RELY": quantiles("rely_mass"),
            "REFUSE": quantiles("refuse_mass"),
            "ESCALATE": quantiles("escalate_mass"),
            "CERTIFIED": quantiles("joint_certified_coverage"),
        },
        "cases": cases,
    }


def definition_observation_scout() -> dict[str, Any]:
    cases = []
    for ordinal in range(24):
        definition_available = ordinal % 2 == 0
        ground_observed = (ordinal // 2) % 2 == 0
        authority_available = (ordinal // 4) % 2 == 0
        computationally_complete = (ordinal // 8) % 2 == 0
        finalizable = all((definition_available, ground_observed, authority_available, computationally_complete))
        cases.append({
            "case_id": f"definition-observation-{ordinal:02d}",
            "definition_available": definition_available,
            "ground_observed": ground_observed,
            "authority_available": authority_available,
            "computationally_complete": computationally_complete,
            "exit": "FINALIZE" if finalizable else "ROUTE",
            "unresolved_boundaries": [
                boundary
                for present, boundary in (
                    (definition_available, "SEMANTIC"),
                    (ground_observed, "GROUNDING"),
                    (authority_available, "AUTHORITY"),
                )
                if not present
            ],
            "derivation_status": "CERTIFIED" if computationally_complete else "RESOURCE_BOUNDED",
        })
    no_definition_ground_conflation = all(
        case["exit"] != "FINALIZE"
        for case in cases
        if case["definition_available"] and not case["ground_observed"]
    )
    return {
        "schema_version": "0.3-definition-observation-scout",
        "case_count": len(cases),
        "no_definition_ground_conflation": no_definition_ground_conflation,
        "disposition": "RETAIN_AS_BOUNDARY_MODEL_NOT_PRODUCT_PLANNER" if no_definition_ground_conflation else "KILLED",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    f9_cases = [f9_case(stratum, ordinal) for stratum in F9_STRATA for ordinal in range(12)]
    f9 = {
        "schema_version": "0.3-f9-oracle-boundary",
        "profile": PROFILE,
        "case_count": len(f9_cases),
        "by_stratum": dict(sorted(Counter(case["stratum"] for case in f9_cases).items())),
        "unsafe_count": sum(not case["safe"] for case in f9_cases),
        "cases": f9_cases,
    }
    families = ("irrelevant_literal", "sparse_interaction", "parity_frontier", "ground_hidden")
    f10_cases = [f10_case(family, width, seed) for family in families for width in range(5, 13) for seed in range(4)]
    f10 = {
        "schema_version": "0.3-f10-complexity-bombs",
        "profile": PROFILE,
        "case_count": len(f10_cases),
        "by_family": dict(sorted(Counter(case["family"] for case in f10_cases).items())),
        "by_exit": dict(sorted(Counter(case["exit"] for case in f10_cases).items())),
        "unsafe_count": sum(not case["safe"] for case in f10_cases),
        "partial_count": sum(case["exit"] == "PARTIAL" for case in f10_cases),
        "minimum_partial_coverage": min(case["certified_coverage"] for case in f10_cases if case["exit"] == "PARTIAL"),
        "cases": f10_cases,
    }
    partial = partial_coverage()
    scout = definition_observation_scout()
    write_json(args.output_dir / "f9-oracle-boundary.json", f9)
    write_json(args.output_dir / "f10-complexity-bombs.json", f10)
    write_json(args.output_dir / "partial-coverage.json", partial)
    write_json(args.output_dir / "definition-observation-scout.json", scout)
    reports = {
        name: canonical_hash(json.loads((args.output_dir / name).read_text(encoding="utf-8")))
        for name in (
            "f9-oracle-boundary.json",
            "f10-complexity-bombs.json",
            "partial-coverage.json",
            "definition-observation-scout.json",
        )
    }
    manifest = {
        "schema_version": "0.3-golden-manifest",
        "profile": PROFILE,
        "lineage": {"v0.1": "preserved", "v0.2": "preserved"},
        "family_counts": {"F9": len(f9_cases), "F10": len(f10_cases)},
        "report_hashes": reports,
        "gates": {
            "f9_zero_unsafe": f9["unsafe_count"] == 0,
            "f10_zero_unsafe": f10["unsafe_count"] == 0,
            "partial_coverage_reported": partial["source_partial_denominator"] == 70,
            "definition_grounding_separated": scout["no_definition_ground_conflation"],
        },
        "evidence_status": "INTERNALLY_VERIFIED_CAPTIVE",
        "external_replay_status": "BLOCKED_MISSING_EXTERNAL_PARTICIPANTS",
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps({
        "f9_cases": len(f9_cases),
        "f10_cases": len(f10_cases),
        "f10_exits": f10["by_exit"],
        "unsafe": f9["unsafe_count"] + f10["unsafe_count"],
        "v02_partial_cases": partial["source_partial_denominator"],
    }, sort_keys=True))
    return 0 if all(manifest["gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
