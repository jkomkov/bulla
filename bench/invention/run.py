#!/usr/bin/env python3
"""Run and independently replay the frozen FRSL-1 benchmark."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from bulla.experimental.frsl import atom, variable
from bulla.experimental.invention import (
    GateStatus,
    SeamProblem,
    SynthesisStatus,
    synthesize,
    verify_failure_certificate,
    verify_package,
)


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return "sha256:" + hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def verify_freeze(corpus):
    freeze = corpus["freeze"]
    design = [x for x in corpus["instances"] if x["split"] == "design"]
    holdout = [x for x in corpus["instances"] if x["split"] == "holdout"]
    checks = {
        "instance_count": len(corpus["instances"]) == freeze["instance_count"],
        "family_count": len(corpus["families"]) == freeze["family_count"],
        "holdout_count": len(holdout) == freeze["holdout_count"],
        "holdout_ids": [x["id"] for x in holdout] == freeze["holdout_ids"],
        "design_hash": digest(design) == freeze["design_hash"],
        "holdout_hash": digest(holdout) == freeze["holdout_hash"],
        "controls_hash": digest(corpus["adversarial_controls"]) == freeze["controls_hash"],
    }
    payload = copy.deepcopy(corpus)
    expected_payload_hash = payload["freeze"].pop("payload_hash")
    checks["payload_hash"] = digest(payload) == expected_payload_hash
    if not all(checks.values()):
        failed = [name for name, ok in checks.items() if not ok]
        raise RuntimeError(f"benchmark freeze verification failed: {failed}")
    return checks


def result_is_sound(problem, result):
    if result.package is not None:
        gates = verify_package(problem, result.package)
        package_safe = (
            gates.gluing is GateStatus.PASS
            and gates.conservativity is GateStatus.PASS
            and gates.preserved_refusals is GateStatus.PASS
            and gates.receipt_binding is GateStatus.PASS
            and (
                (
                    result.package.cost.get("minimality")
                    == "exact-finite-candidate-space"
                    and gates.minimality is GateStatus.PASS
                )
                or (
                    result.package.cost.get("minimality") == "unresolved"
                    and gates.minimality is GateStatus.UNRESOLVED
                )
            )
        )
        if result.status is SynthesisStatus.COMPILED:
            package_safe = package_safe and gates.definability is GateStatus.PASS
        if not package_safe:
            return False, gates.to_dict()
    else:
        gates = None
    if result.certificate is not None:
        certificate_valid = verify_failure_certificate(
            problem,
            result.certificate,
            alternatives=result.alternatives,
        )
        if result.status in (
            SynthesisStatus.PARTIAL,
            SynthesisStatus.ESCALATE,
            SynthesisStatus.CHOICE_REQUIRED,
        ) and not certificate_valid:
            return False, {
                "package_gates": gates.to_dict() if gates is not None else None,
                "certificate_valid": certificate_valid,
            }
    return True, gates.to_dict() if gates is not None else None


def nearest_rank(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def mutate_and_check(problem, package, control):
    mutation = control["mutation"]
    if mutation == "target_leakage":
        mutated = dataclasses.replace(
            package,
            definition=atom(problem.target_predicate, (variable("x0"),)),
        )
    elif mutation == "authority_expansion":
        mutated = dataclasses.replace(
            package,
            authority={"principal": "did:example:attacker", "scope": "unbounded"},
        )
    elif mutation == "noncanonical_duplicate":
        mutated = dataclasses.replace(
            package,
            definition={
                "op": "and",
                "args": [package.definition, package.definition],
            },
        )
    elif mutation == "protected_pin_swap":
        pins = dict(package.protected_signature_pins)
        owner = sorted(pins)[0]
        pins[owner] = "sha256:" + "0" * 64
        mutated = dataclasses.replace(package, protected_signature_pins=pins)
    else:
        raise RuntimeError(f"unknown package mutation {mutation!r}")
    report = verify_package(problem, mutated)
    actual = report.to_dict()[control["expected_gate"]]
    return {
        "id": control["id"],
        "passed": actual == control["expected_value"],
        "expected": control["expected_value"],
        "actual": actual,
        "reasons": list(report.reasons),
    }


def _standalone_replay(problem, result, checker_path):
    with tempfile.TemporaryDirectory(prefix="bulla-invention-parity-") as directory:
        root = Path(directory)
        problem_path = root / "problem.json"
        result_path = root / "result.json"
        problem_path.write_text(json.dumps(problem.to_dict()), encoding="utf-8")
        result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [sys.executable, str(checker_path), str(problem_path), str(result_path)],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": "standalone checker returned non-JSON output"}
    return {
        "ok": completed.returncode == 0 and payload.get("ok") is True,
        "returncode": completed.returncode,
        "status": payload.get("status"),
        "result_hash": payload.get("result_hash"),
        "hash_agreement": payload.get("result_hash") == result.result_hash,
        "error": payload.get("error") or completed.stderr.strip() or None,
    }


def run(corpus, split, *, standalone_checker=None):
    selected = [
        x for x in corpus["instances"]
        if split == "all" or x["split"] == split
    ]
    records = []
    durations = []
    sizes = []
    sound_count = 0
    compiled_count = 0
    admissible_count = 0
    base_compiled = None
    for instance in selected:
        problem = SeamProblem.from_dict(instance["problem"])
        if problem.problem_hash != instance["problem_hash"]:
            raise RuntimeError(f"problem hash drift for {instance['id']}")
        started = time.perf_counter()
        result = synthesize(problem)
        duration = time.perf_counter() - started
        sound, replay = result_is_sound(problem, result)
        standalone = (
            _standalone_replay(problem, result, standalone_checker)
            if standalone_checker is not None
            else None
        )
        expected_ok = result.status.value == instance["expected_status"]
        durations.append(duration)
        if result.package is not None:
            sizes.append(result.package.cost["predicate_ast_nodes"])
        if result.status not in (
            SynthesisStatus.INVALID_INPUT,
            SynthesisStatus.INDETERMINATE,
        ):
            admissible_count += 1
        if result.status is SynthesisStatus.COMPILED:
            compiled_count += 1
            if base_compiled is None:
                base_compiled = (problem, result.package)
        if sound:
            sound_count += 1
        records.append(
            {
                "id": instance["id"],
                "family": instance["family"],
                "split": instance["split"],
                "expected_status": instance["expected_status"],
                "actual_status": result.status.value,
                "expected_ok": expected_ok,
                "sound": sound,
                "seconds": duration,
                "predicate_ast_nodes": (
                    result.package.cost["predicate_ast_nodes"]
                    if result.package is not None
                    else None
                ),
                "result_hash": result.result_hash,
                "replay": replay,
                "standalone": standalone,
            }
        )

    control_records = []
    for control in corpus["adversarial_controls"]:
        if control["kind"] == "problem":
            problem = SeamProblem.from_dict(control["problem"])
            result = synthesize(problem)
            sound, replay = result_is_sound(problem, result)
            control_records.append(
                {
                    "id": control["id"],
                    "passed": (
                        result.status.value == control["expected_status"]
                        and (
                            sound
                            or result.status in (
                                SynthesisStatus.INVALID_INPUT,
                                SynthesisStatus.INDETERMINATE,
                            )
                        )
                    ),
                    "expected": control["expected_status"],
                    "actual": result.status.value,
                    "replay": replay,
                }
            )
        else:
            if base_compiled is None:
                raise RuntimeError("mutation controls need a compiled base instance")
            control_records.append(
                mutate_and_check(base_compiled[0], base_compiled[1], control)
            )
    status_counts = Counter(x["actual_status"] for x in records)
    compile_rate = (
        compiled_count / admissible_count if admissible_count else 0.0
    )
    return {
        "schema_version": "0.1-experimental",
        "split": split,
        "freeze_hash": corpus["freeze"]["payload_hash"],
        "instance_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "expected_status_agreement": sum(x["expected_ok"] for x in records) / len(records),
        "sound_packages_or_certificates": sound_count,
        "independently_rejected_emissions": sum(
            not x["sound"]
            and x["actual_status"] in ("COMPILED", "PARTIAL")
            for x in records
        ),
        "compile_rate": compile_rate,
        "p95_synthesis_seconds": nearest_rank(durations, 0.95),
        "p95_predicate_ast_nodes": nearest_rank(sizes, 0.95),
        "product_gate": {
            "compile_rate_at_least_25_percent": compile_rate >= 0.25,
            "compile_rate_below_kill_10_percent": compile_rate < 0.10,
            "p95_under_10_seconds": nearest_rank(durations, 0.95) < 10,
            "p95_under_256_nodes": (
                nearest_rank(sizes, 0.95) is not None
                and nearest_rank(sizes, 0.95) < 256
            ),
        },
        "adversarial_controls": control_records,
        "all_controls_pass": all(x["passed"] for x in control_records),
        "standalone_parity": {
            "enabled": standalone_checker is not None,
            "accepted_count": sum(
                bool(record["standalone"] and record["standalone"]["ok"])
                for record in records
            ),
            "hash_agreement_count": sum(
                bool(record["standalone"] and record["standalone"]["hash_agreement"])
                for record in records
            ),
        },
        "instances": records,
        "comparison_arms": {
            "exhaustive_reference": "run",
            "smtinterpol": "adapter implemented; requires separately pinned jar",
            "cegis": "DNF exact-cover search is internal to reference backend",
            "frozen_llm_repairs": corpus["bridge_baseline"],
            "llm_candidate_plus_verifier": "not run without a preregistered provider snapshot",
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).with_name("corpus.json"),
    )
    parser.add_argument("--split", choices=("design", "holdout", "all"), default="design")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--standalone-checker",
        type=Path,
        help="launch the zero-import checker in an isolated subprocess for every result",
    )
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    freeze_checks = verify_freeze(corpus)
    if args.standalone_checker is not None and not args.standalone_checker.is_file():
        raise SystemExit("standalone checker path is not a file")
    report = run(corpus, args.split, standalone_checker=args.standalone_checker)
    report["freeze_checks"] = freeze_checks
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(
            f"{args.split}: {report['instance_count']} instances, "
            f"agreement={report['expected_status_agreement']:.3f}, "
            f"compile_rate={report['compile_rate']:.3f}, output={args.output}"
        )
    else:
        print(rendered)
    return 0 if (
        report["expected_status_agreement"] == 1.0
        and report["independently_rejected_emissions"] == 0
        and report["all_controls_pass"]
        and (
            not report["standalone_parity"]["enabled"]
            or (
                report["standalone_parity"]["accepted_count"] == report["instance_count"]
                and report["standalone_parity"]["hash_agreement_count"] == report["instance_count"]
            )
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
