#!/usr/bin/env python3
"""Frozen conversion, exit-algebra, phase-diagram, and adaptive scout run."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bulla.experimental.adaptive_observability import GenerativeWorld, scout_adaptive_observability
from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import SeamProblem, SynthesisStatus, synthesize
from bulla.experimental.observability import ConservationManifest, LogicPassport, plan_enrichment


HERE = Path(__file__).resolve().parent
FROZEN = HERE / "results/refinement-scaling-2026-07-18.json"
RUNNER = HERE / "run_refinement_scaling.py"
SCHEMA = "0.1-semantic-settlement-evaluation"


def load_scaling_module():
    spec = importlib.util.spec_from_file_location("frozen_scaling", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def worker(dimension: str, level: int, seed: int, mode: str) -> dict:
    limited_preexec()
    scaling = load_scaling_module()
    problem = scaling._problem(dimension, level, seed)
    started = time.perf_counter()
    if mode == "synthesis":
        policy = dataclasses.replace(
            problem.synthesis_policy,
            max_candidate_atoms=problem.synthesis_policy.max_candidate_atoms * 10,
        )
        problem = dataclasses.replace(problem, synthesis_policy=policy)
        result = synthesize(problem)
        cause = None
        if result.certificate is not None:
            cause = result.certificate.witness.get("reason")
        return {
            "status": result.status.value, "cause": cause,
            "result_hash": result.result_hash, "seconds": time.perf_counter() - started,
            "proof_bytes": len(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()),
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
    planning = plan_enrichment(
        problem, scaling._offers(problem, dimension, level, seed),
        passport=passport, manifest=ConservationManifest.for_problem(problem),
    )
    return {
        "status": planning.status.value, "cause": planning.reason,
        "result_hash": planning.result_hash, "seconds": time.perf_counter() - started,
        "proof_bytes": len(json.dumps(planning.to_dict(), sort_keys=True, separators=(",", ":")).encode()),
    }


def limited_preexec():
    one_gib = 1024 * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (one_gib, one_gib))
    except (ValueError, OSError):
        pass


def run_conversion(original: dict) -> list[dict]:
    selected = []
    for case in original["cases"]:
        if case["synthesis_status"] == "INDETERMINATE":
            selected.append((case, "synthesis"))
        if case["planning_status"] == "INDETERMINATE":
            selected.append((case, "planning"))
    def run_one(selected_case):
        case, mode = selected_case
        command = [
            sys.executable, str(Path(__file__).resolve()), "--worker",
            case["dimension"], str(case["level"]), str(case["seed"]), mode,
        ]
        try:
            completed = subprocess.run(
                command, text=True, capture_output=True, timeout=20,
                env={**os.environ},
            )
            if completed.returncode == 0:
                outcome = json.loads(completed.stdout)
            else:
                outcome = {"status": "INDETERMINATE", "cause": "subprocess_error", "stderr": completed.stderr[-1000:]}
        except subprocess.TimeoutExpired:
            outcome = {"status": "INDETERMINATE", "cause": "subprocess_time_ceiling_20s"}
        return {
            "case_id": case["case_id"], "mode": mode,
            "original_exit": "INDETERMINATE",
            "original_cause": (
                f"candidate feature bound exceeded: {2 * case['level'] + 1} atoms > 8"
                if mode == "synthesis"
                else "opposing-pair enumeration exceeded the pinned resource bound"
            ),
            "budget_multiplier": 10,
            "subprocess_time_limit_seconds": 20,
            "subprocess_address_space_limit_bytes": 1024 * 1024 * 1024,
            **outcome,
        }
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(run_one, selected))
    return records


def exit_algebra_suite() -> dict:
    corpus = json.loads((HERE / "corpus.json").read_text())
    by_id = {item["id"]: item for item in corpus["instances"]}
    result_docs = [
        json.loads((HERE / "results/design-2026-07-17.json").read_text()),
        json.loads((HERE / "results/holdout-2026-07-17.json").read_text()),
    ]
    ids_by_status = {status: [] for status in ("COMPILED", "PARTIAL", "ESCALATE", "CHOICE_REQUIRED")}
    for document in result_docs:
        for item in document["instances"]:
            ids_by_status[item["actual_status"]].append(item["id"])
    cases = []
    for status, ids in ids_by_status.items():
        if len(ids) < 12:
            raise RuntimeError(f"not enough frozen {status} cases")
        for case_id in ids[:12]:
            problem = SeamProblem.from_dict(by_id[case_id]["problem"])
            result = synthesize(problem)
            cases.append({
                "case_id": case_id, "expected_exit": status, "actual_exit": result.status.value,
                "result_hash": result.result_hash,
                "proof_bytes": len(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()),
            })
    scaling = load_scaling_module()
    for index in range(12):
        level = 4 + (index % 9)
        seed = index % 5
        problem = scaling._problem("shared_vocabulary_width", level, seed)
        problem = dataclasses.replace(
            problem,
            synthesis_policy=dataclasses.replace(problem.synthesis_policy, max_candidate_atoms=1),
        )
        result = synthesize(problem)
        cases.append({
            "case_id": f"forced-resource-{level:02d}-{seed}",
            "expected_exit": "INDETERMINATE", "actual_exit": result.status.value,
            "result_hash": result.result_hash,
            "proof_bytes": len(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()),
            "exhaustion_cause": result.certificate.witness.get("reason") if result.certificate else None,
        })
    return {
        "case_count": len(cases),
        "exit_counts": dict(sorted(Counter(item["actual_exit"] for item in cases).items())),
        "agreement": all(item["expected_exit"] == item["actual_exit"] for item in cases),
        "cases": cases,
    }


def phase_diagram(original: dict) -> list[dict]:
    records = []
    for case in original["cases"]:
        dimension, level, seed = case["dimension"], case["level"], case["seed"]
        private_count = level if dimension == "admissible_model_space" else 1
        shared_count = level if dimension == "shared_vocabulary_width" else 1
        relation_count = 1 + private_count + shared_count
        model_count = 2 ** (relation_count - (1 if seed % 2 == 0 else 0))
        records.append({
            "case_id": case["case_id"], "model_count": model_count,
            "vocabulary_width": shared_count,
            "opposing_pairs": case["opposing_pair_count"],
            "catalog_size": case["offer_count"], "proof_size_bytes": case["proof_bytes"],
            "runtime_ms": case["synthesis_ms"] + case["result_check_ms"],
            "synthesis_exit": case["synthesis_status"], "planning_exit": case["planning_status"],
        })
    return records


def adaptive_suite() -> dict:
    cases = []
    for index in range(60):
        offers = tuple(f"offer_{j}" for j in range(4))
        permutation = tuple((j + index) % 4 for j in range(4))
        worlds = []
        for world_index in range(4):
            observations = {offer: False for offer in offers}
            observations[offers[permutation[world_index]]] = True
            worlds.append(GenerativeWorld(
                world_id=f"case-{index:02d}-world-{world_index}",
                outcome=f"outcome-{world_index}", observations=observations,
                prior_weight=1 + ((index + world_index) % 3),
            ))
        result = scout_adaptive_observability(worlds, offer_order=offers, max_depth=4)
        cases.append(result.to_dict())
    improvements = [item["disclosure_reduction_ppm"] for item in cases if item["disclosure_reduction_ppm"] > 0]
    eligible = [item for item in cases if item["static_safe"]]
    unsafe = [item for item in cases if not item["adaptive_safe"]]
    improved_share = len(improvements) / len(eligible) if eligible else 0.0
    median_reduction = statistics.median(improvements) if improvements else 0
    promotion = not unsafe and improved_share >= 0.25 and median_reduction >= 150_000
    return {
        "case_count": len(cases), "max_offers": 8, "max_depth": 4,
        "declared_prior": "integer weights normalized exactly per case",
        "action_gating": "forbidden", "unsafe_terminal_leaves": len(unsafe),
        "eligible_cases": len(eligible), "pareto_improved_cases": len(improvements),
        "pareto_improved_share": improved_share,
        "median_disclosure_reduction_ppm": median_reduction,
        "promotion_threshold_met": promotion,
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=4, metavar=("DIMENSION", "LEVEL", "SEED", "MODE"))
    args = parser.parse_args()
    if args.worker:
        dimension, level, seed, mode = args.worker
        print(json.dumps(worker(dimension, int(level), int(seed), mode)))
        return 0
    if args.output is None:
        parser.error("--output is required")
    before_hash = file_hash(FROZEN)
    original = json.loads(FROZEN.read_text())
    conversions = run_conversion(original)
    payload = {
        "schema_version": SCHEMA, "generated_at": "2026-07-18T00:00:00Z",
        "frozen_240": {
            "path": str(FROZEN.relative_to(HERE.parents[2])),
            "file_hash_before": before_hash,
            "artifact_hash": original["artifact_hash"], "mutated": False,
        },
        "conversion_curve": {
            "case_count": len(conversions),
            "by_mode_and_exit": dict(sorted(Counter(f"{item['mode']}:{item['status']}" for item in conversions).items())),
            "cases": conversions,
        },
        "exit_algebra": exit_algebra_suite(),
        "phase_diagram": phase_diagram(original),
        "adaptive_scout": adaptive_suite(),
    }
    payload["frozen_240"]["file_hash_after"] = file_hash(FROZEN)
    payload["frozen_240"]["mutated"] = before_hash != payload["frozen_240"]["file_hash_after"]
    payload["artifact_hash"] = canonical_hash(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "conversion": payload["conversion_curve"]["by_mode_and_exit"],
        "exit_algebra": payload["exit_algebra"]["exit_counts"],
        "adaptive_threshold": payload["adaptive_scout"]["promotion_threshold_met"],
        "frozen_240_mutated": payload["frozen_240"]["mutated"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
