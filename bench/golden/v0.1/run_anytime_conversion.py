#!/usr/bin/env python3
"""Re-run the frozen 45+24 exhaustion cohort with certified anytime routes."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bulla.experimental.invention import synthesize, verify_failure_certificate, verify_package
from bulla.experimental.observability import (
    ConservationManifest,
    LogicPassport,
    PlanningStatus,
    plan_enrichment,
    verify_enrichment_plan,
)


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
FROZEN = BULLA / "bench/invention/results/refinement-scaling-2026-07-18.json"
SCALING_RUNNER = BULLA / "bench/invention/run_refinement_scaling.py"


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def scaling_module():
    spec = importlib.util.spec_from_file_location("golden_scaling", SCALING_RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def limit_memory() -> None:
    one_gib = 1024 * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (one_gib, one_gib))
    except (ValueError, OSError):
        pass


def worker(dimension: str, level: int, seed: int, mode: str) -> dict:
    limit_memory()
    scaling = scaling_module()
    problem = scaling._problem(dimension, level, seed)
    started = time.perf_counter()
    if mode == "synthesis":
        problem = dataclasses.replace(
            problem,
            synthesis_policy=dataclasses.replace(
                problem.synthesis_policy,
                max_candidate_atoms=problem.synthesis_policy.max_candidate_atoms * 10,
                exact_minimality=False,
            ),
        )
        result = synthesize(problem)
        verified = False
        if result.package is not None:
            report = verify_package(problem, result.package)
            verified = bool(
                report.gluing.value == "pass"
                and report.conservativity.value == "pass"
                and report.preserved_refusals.value == "pass"
                and report.receipt_binding.value == "pass"
            )
        elif result.certificate is not None and result.status.value != "INDETERMINATE":
            verified = verify_failure_certificate(
                problem, result.certificate, alternatives=result.alternatives
            )
        cause = result.certificate.witness.get("reason") if result.certificate else None
        return {
            "status": result.status.value,
            "cause": cause,
            "result_hash": result.result_hash,
            "verified": verified,
            "seconds": time.perf_counter() - started,
            "minimality": result.package.cost["minimality"] if result.package else None,
        }
    passport = LogicPassport.for_problem(problem)
    passport = dataclasses.replace(
        passport,
        resource_bounds={
            **passport.resource_bounds,
            "max_opposing_pairs": 10_240,
            "max_minimal_plans": passport.resource_bounds["max_minimal_plans"] * 10,
        },
    )
    offers = scaling._offers(problem, dimension, level, seed)
    manifest = ConservationManifest.for_problem(problem)
    result = plan_enrichment(problem, offers, passport=passport, manifest=manifest)
    verified = bool(
        result.status is PlanningStatus.PLANNED
        and all(
            verify_enrichment_plan(
                problem, offers, plan, passport=passport, manifest=manifest
            )
            for plan in result.plans
        )
    ) or result.status in {PlanningStatus.NOT_NEEDED, PlanningStatus.NO_SUFFICIENT_PLAN}
    return {
        "status": result.status.value,
        "cause": result.reason,
        "result_hash": result.result_hash,
        "verified": verified,
        "seconds": time.perf_counter() - started,
        "minimality": (
            result.plans[0].minimality if result.status is PlanningStatus.PLANNED else None
        ),
        "opposing_pair_count": result.opposing_pair_count,
    }


def run_one(item: tuple[dict, str]) -> dict:
    case, mode = item
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        case["dimension"],
        str(case["level"]),
        str(case["seed"]),
        mode,
    ]
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, timeout=20, env={**os.environ}
        )
        if completed.returncode == 0:
            result = json.loads(completed.stdout)
        else:
            result = {
                "status": "INDETERMINATE",
                "cause": "subprocess_error",
                "verified": False,
                "stderr": completed.stderr[-1000:],
            }
    except subprocess.TimeoutExpired:
        result = {
            "status": "INDETERMINATE",
            "cause": "subprocess_time_ceiling_20s",
            "verified": False,
        }
    return {
        "case_id": case["case_id"],
        "mode": mode,
        "original_exit": "INDETERMINATE",
        "subprocess_time_limit_seconds": 20,
        "subprocess_address_space_limit_bytes": 1024 * 1024 * 1024,
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=4)
    parser.add_argument("--output", type=Path, default=HERE / "anytime-conversion.json")
    args = parser.parse_args()
    if args.worker:
        dimension, level, seed, mode = args.worker
        print(json.dumps(worker(dimension, int(level), int(seed), mode)))
        return 0
    before = file_hash(FROZEN)
    original = json.loads(FROZEN.read_text(encoding="utf-8"))
    selected: list[tuple[dict, str]] = []
    for case in original["cases"]:
        if case["synthesis_status"] == "INDETERMINATE":
            selected.append((case, "synthesis"))
        if case["planning_status"] == "INDETERMINATE":
            selected.append((case, "planning"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        cases = list(executor.map(run_one, selected))
    converted = [item for item in cases if item["status"] != "INDETERMINATE"]
    verified_converted = [item for item in converted if item.get("verified")]
    share = len(verified_converted) / len(cases)
    classification = "demonstrated-improvement" if share > 0.50 else "limited" if share >= 0.20 else "failed-product-scaling-route"
    payload = {
        "schema_version": "0.1-golden-anytime",
        "frozen_240": {
            "hash_before": before,
            "hash_after": file_hash(FROZEN),
            "mutated": before != file_hash(FROZEN),
        },
        "cohort_count": len(cases),
        "by_mode_and_exit": dict(
            sorted(Counter(f"{item['mode']}:{item['status']}" for item in cases).items())
        ),
        "verified_conversion_count": len(verified_converted),
        "verified_conversion_share": share,
        "classification": classification,
        "claim_boundary": (
            "finite complete model/pair quotients only; incomplete enumeration remains INDETERMINATE"
        ),
        "cases": cases,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "conversion": payload["by_mode_and_exit"],
                "verified_share": share,
                "classification": classification,
                "frozen_mutated": payload["frozen_240"]["mutated"],
            },
            sort_keys=True,
        )
    )
    return 1 if payload["frozen_240"]["mutated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
