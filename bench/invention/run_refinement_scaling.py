#!/usr/bin/env python3
"""Run the frozen 240-case certified-refinement scaling study.

The study varies one declared dimension at a time across twelve levels and five
deterministic seeds.  It measures the reference synthesizer, independent package
or certificate replay, and exact observability planning.  Resource-limit exits
are retained as INDETERMINATE operational observations; they are never counted
as semantic counterexamples.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

from bulla.experimental.frsl import atom, canonical_hash, formula_size, variable
from bulla.experimental.invention import (
    GateStatus,
    SeamProblem,
    SynthesisStatus,
    synthesize,
    verify_failure_certificate,
    verify_package,
)
from bulla.experimental.observability import (
    BurdenVector,
    ConservationManifest,
    LogicPassport,
    ObservableOffer,
    PlanningStatus,
    plan_enrichment,
    verify_enrichment_plan,
)


SCHEMA_VERSION = "0.1-refinement-scaling"
DIMENSIONS = (
    "admissible_model_space",
    "shared_vocabulary_width",
    "observable_catalog_width",
    "constraint_depth",
)
LEVELS = tuple(range(1, 13))
SEEDS = tuple(range(5))
EXPECTED_CASES = len(DIMENSIONS) * len(LEVELS) * len(SEEDS)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _tautology(relation: str, depth: int) -> dict[str, Any]:
    leaf = atom(relation, [variable("x")])
    body: dict[str, Any] = {"op": "iff", "left": leaf, "right": leaf}
    for _ in range(depth - 1):
        body = {"op": "and", "args": [body, {"op": "true"}]}
    return {"op": "forall", "var": "x", "sort": "Record", "body": body}


def _explicit_definition() -> dict[str, Any]:
    return {
        "op": "forall",
        "var": "x",
        "sort": "Record",
        "body": {
            "op": "iff",
            "left": atom("target", [variable("x")]),
            "right": atom("shared_0", [variable("x")]),
        },
    }


def _problem(dimension: str, level: int, seed: int) -> SeamProblem:
    if dimension not in DIMENSIONS or level not in LEVELS or seed not in SEEDS:
        raise ValueError("case lies outside the frozen scaling grid")

    private_count = level if dimension == "admissible_model_space" else 1
    shared_count = level if dimension == "shared_vocabulary_width" else 1
    depth = level if dimension == "constraint_depth" else 1
    relations = ["target"]
    relations.extend(f"private_{index}" for index in range(private_count))
    relations.extend(f"shared_{index}" for index in range(shared_count))
    # A one-element sort isolates relation-count growth from domain growth and
    # keeps the full grid runnable by the direct finite reference checker.
    constraints = [_tautology("shared_0", depth)]
    # Even seeds exercise the positive compilation/checker path; odd seeds
    # retain a genuine same-reduct ambiguity for enrichment planning.
    if seed % 2 == 0:
        constraints.append(_explicit_definition())
    document = {
        "schema_version": "0.1-experimental",
        "language": "FRSL-1",
        "problem_id": f"scale-{dimension}-{level:02d}-{seed}",
        "signature": {
            "sorts": {"Record": [f"record_{seed}"]},
            "relations": [
                {"name": name, "sorts": ["Record"]} for name in sorted(relations)
            ],
        },
        "local_theories": [{"owner": "source", "constraints": constraints}],
        "overlap_maps": [],
        "target_predicate": "target",
        "shared_vocabulary": [f"shared_{index}" for index in range(shared_count)],
        "protected_signatures": {
            "source": [f"shared_{index}" for index in range(shared_count)]
        },
        "requested_judgment": "rely_refuse_escalate",
        "synthesis_policy": {
            "reference_max_ground_atoms": 14,
            "reference_max_models": 32768,
            "max_candidate_atoms": 8,
            "max_minimal_alternatives": 16,
            "exact_minimality": True,
            "require_unique_minimum": True,
        },
        "authority": {
            "principal": "did:example:scaling-owner",
            "policy": "policy:certified-refinement-scaling:v1",
        },
        "scope": {"study": "refinement-scaling", "dimension": dimension},
        "expiry": "2027-07-18T00:00:00Z",
        "evidence_requirements": ["signed_attestation"],
    }
    return SeamProblem.from_dict(document)


def _offers(problem: SeamProblem, dimension: str, level: int, seed: int) -> tuple[ObservableOffer, ...]:
    count = level if dimension == "observable_catalog_width" else 1
    return tuple(
        ObservableOffer(
            offer_id=f"observable_{index}",
            relation=f"observable_{index}",
            sorts=("Record",),
            meaning=atom("target", [variable("x0")]),
            provider=f"did:example:scaling-provider-{index}",
            warrant_profile={
                "kind": "signed_attestation",
                "evidence_class": "signed_attestation",
                "verifier": "scaling-attestation-profile/1",
                "reveals": "boolean_fact_only",
            },
            burden=BurdenVector(
                disclosure_units=1 + ((seed + index) % 3),
                latency_ms=1 + index,
                monetary_microunits=(seed + 1) * (index + 1),
            ),
            consent_subjects=(f"did:example:scaling-provider-{index}",),
        )
        for index in range(count)
    )


def _verify_result(problem: SeamProblem, result: Any) -> bool:
    package_ok = True
    if result.package is not None:
        report = verify_package(problem, result.package)
        package_ok = (
            report.gluing is GateStatus.PASS
            and report.conservativity is GateStatus.PASS
            and report.preserved_refusals is GateStatus.PASS
            and report.receipt_binding is GateStatus.PASS
            and (
                result.status is not SynthesisStatus.COMPILED
                or report.definability is GateStatus.PASS
            )
        )
    certificate_ok = True
    if result.certificate is not None and result.status in {
        SynthesisStatus.PARTIAL,
        SynthesisStatus.ESCALATE,
        SynthesisStatus.CHOICE_REQUIRED,
    }:
        certificate_ok = verify_failure_certificate(
            problem,
            result.certificate,
            alternatives=result.alternatives,
        )
    # A resource certificate is intentionally not reinterpreted as a complete
    # semantic witness.  Its typed INDETERMINATE result is the fail-closed check.
    if result.status is SynthesisStatus.INDETERMINATE:
        certificate_ok = result.certificate is not None and not result.certificate.complete_within_bound
    return package_ok and certificate_ok


def _predicate_nodes(result: Any) -> int | None:
    if result.package is None:
        return None
    formulas = (
        result.package.definition,
        result.package.rely_when,
        result.package.refuse_when,
    )
    return sum(formula_size(formula) for formula in formulas if formula is not None)


def _run_case(dimension: str, level: int, seed: int) -> dict[str, Any]:
    problem = _problem(dimension, level, seed)
    offers = _offers(problem, dimension, level, seed)
    passport = LogicPassport.for_problem(problem)
    passport = dataclasses.replace(
        passport,
        resource_bounds={
            **passport.resource_bounds,
            # The study's predeclared operational envelope.  Larger pair sets
            # must fail closed rather than make the routine gate unbounded.
            "max_opposing_pairs": 1_024,
        },
    )
    manifest = ConservationManifest.for_problem(problem)

    tracemalloc.start()
    synthesis_started = time.perf_counter_ns()
    result = synthesize(problem)
    synthesis_ns = time.perf_counter_ns() - synthesis_started
    check_started = time.perf_counter_ns()
    result_verified = _verify_result(problem, result)
    result_check_ns = time.perf_counter_ns() - check_started

    planning_started = time.perf_counter_ns()
    try:
        planning = plan_enrichment(
            problem,
            offers,
            passport=passport,
            manifest=manifest,
        )
        planning_error = None
    except ValueError as exc:
        planning = None
        planning_error = str(exc)
    planning_ns = time.perf_counter_ns() - planning_started
    plan_check_started = time.perf_counter_ns()
    plan_verified = None
    if planning is not None and planning.status is PlanningStatus.PLANNED:
        plan_verified = all(
            verify_enrichment_plan(
                problem,
                offers,
                plan,
                passport=passport,
                manifest=manifest,
            )
            for plan in planning.plans
        )
    plan_check_ns = time.perf_counter_ns() - plan_check_started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    result_document = result.to_dict()
    planning_document = planning.to_dict() if planning is not None else None
    return {
        "case_id": f"{dimension}-{level:02d}-{seed}",
        "dimension": dimension,
        "level": level,
        "seed": seed,
        "problem_hash": problem.problem_hash,
        "result_hash": result.result_hash,
        "synthesis_status": result.status.value,
        "planning_status": planning.status.value if planning is not None else "ERROR",
        "result_verified": result_verified,
        "plans_verified": plan_verified,
        "planning_error": planning_error,
        "synthesis_ms": synthesis_ns / 1_000_000,
        "result_check_ms": result_check_ns / 1_000_000,
        "planning_ms": planning_ns / 1_000_000,
        "plan_check_ms": plan_check_ns / 1_000_000,
        "peak_memory_bytes": peak_bytes,
        "proof_bytes": len(
            json.dumps(result_document, sort_keys=True, separators=(",", ":")).encode()
        ),
        "planning_bytes": (
            len(json.dumps(planning_document, sort_keys=True, separators=(",", ":")).encode())
            if planning_document is not None
            else None
        ),
        "predicate_ast_nodes": _predicate_nodes(result),
        "offer_count": len(offers),
        "minimal_plan_count": len(planning.plans) if planning is not None else 0,
        "opposing_pair_count": planning.opposing_pair_count if planning is not None else None,
    }


def run() -> dict[str, Any]:
    grid = [
        {"dimension": dimension, "level": level, "seed": seed}
        for dimension in DIMENSIONS
        for level in LEVELS
        for seed in SEEDS
    ]
    if len(grid) != EXPECTED_CASES:
        raise RuntimeError("scaling grid cardinality drift")
    freeze = {
        "dimensions": list(DIMENSIONS),
        "levels": list(LEVELS),
        "seeds": list(SEEDS),
        "case_count": EXPECTED_CASES,
        "grid_hash": _digest(grid),
        "logic": "FRSL-1",
        "semantics": "closed-finite-structures/1",
    }
    cases = [_run_case(**case) for case in grid]
    timing_fields = (
        "synthesis_ms",
        "result_check_ms",
        "planning_ms",
        "plan_check_ms",
    )
    summary: dict[str, Any] = {
        "case_count": len(cases),
        "synthesis_statuses": dict(sorted(Counter(x["synthesis_status"] for x in cases).items())),
        "planning_statuses": dict(sorted(Counter(x["planning_status"] for x in cases).items())),
        "result_verification_failures": sum(not x["result_verified"] for x in cases),
        "plan_verification_failures": sum(x["plans_verified"] is False for x in cases),
        "peak_memory_bytes_max": max(x["peak_memory_bytes"] for x in cases),
        "proof_bytes_p95": _percentile([float(x["proof_bytes"]) for x in cases], 0.95),
        "predicate_ast_nodes_p95": _percentile(
            [float(x["predicate_ast_nodes"]) for x in cases if x["predicate_ast_nodes"] is not None],
            0.95,
        ),
    }
    for field in timing_fields:
        values = [float(case[field]) for case in cases]
        summary[f"{field}_median"] = statistics.median(values)
        summary[f"{field}_p95"] = _percentile(values, 0.95)
    summary["by_dimension"] = {
        dimension: {
            "case_count": len(selected := [x for x in cases if x["dimension"] == dimension]),
            "synthesis_ms_p95": _percentile([x["synthesis_ms"] for x in selected], 0.95),
            "planning_ms_p95": _percentile([x["planning_ms"] for x in selected], 0.95),
            "indeterminate_count": sum(x["synthesis_status"] == "INDETERMINATE" for x in selected),
            "planning_error_count": sum(x["planning_status"] == "ERROR" for x in selected),
        }
        for dimension in DIMENSIONS
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-07-18T00:00:00Z",
        "freeze": freeze,
        "summary": summary,
        "cases": cases,
    }
    payload["artifact_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
