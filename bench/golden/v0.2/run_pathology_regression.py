#!/usr/bin/env python3
"""Recheck the twelve v0.1 AST-bound one-atom pathologies."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from bulla.experimental.frsl import formula_size
from bulla.experimental.invention import synthesize, verify_package


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
V01 = HERE.parent / "v0.1"
SCALING_RUNNER = BULLA / "bench/invention/run_refinement_scaling.py"


def scaling_module():
    spec = importlib.util.spec_from_file_location("v02_pathology_scaling", SCALING_RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    old = json.loads((V01 / "anytime-conversion.json").read_text(encoding="utf-8"))
    case_ids = {
        item["case_id"] for item in old["cases"]
        if item["mode"] == "synthesis" and item["status"] == "INDETERMINATE"
    }
    if len(case_ids) != 12:
        raise SystemExit("frozen v0.1 pathology denominator changed")
    scaling = scaling_module()
    cases = []
    for case_id in sorted(case_ids):
        _, width, seed = case_id.rsplit("-", 2)
        problem = scaling._problem("shared_vocabulary_width", int(width), int(seed))
        result = synthesize(problem)
        package = result.package
        report = verify_package(problem, package) if package else None
        nodes = formula_size(package.definition) if package and package.definition is not None else None
        sound = report is not None and all(
            getattr(report, name).value == "pass"
            for name in ("gluing", "conservativity", "definability", "preserved_refusals", "receipt_binding")
        )
        cases.append({
            "case_id": case_id,
            "old_exit": "INDETERMINATE",
            "new_exit": result.status.value,
            "ast_nodes": nodes,
            "package_verified": sound,
            "under_64_nodes": nodes is not None and nodes < 64,
        })
    payload = {
        "schema_version": "0.2-pathology-regression",
        "case_count": len(cases),
        "all_compiled": all(item["new_exit"] == "COMPILED" for item in cases),
        "all_under_64_nodes": all(item["under_64_nodes"] for item in cases),
        "all_verified": all(item["package_verified"] for item in cases),
        "interpretation": "Original twelve-case AST pathology repair; generalization evidence is reported separately on scaling-report.json.",
        "cases": cases,
    }
    (HERE / "pathology-regression.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("case_count", "all_compiled", "all_under_64_nodes", "all_verified")}, sort_keys=True))
    return 0 if payload["all_compiled"] and payload["all_under_64_nodes"] and payload["all_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
