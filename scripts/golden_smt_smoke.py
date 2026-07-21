#!/usr/bin/env python3
"""Pinned SMTInterpol smoke used by the Golden Suite portability matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.experimental.frsl import RelationDecl, Signature, atom, variable
from bulla.experimental.invention import GateStatus, LocalTheory, SeamProblem, SynthesisPolicy, SynthesisStatus, verify_package
from bulla.experimental.smtinterpol import SMTInterpolConfig, synthesize_with_smtinterpol


def problem() -> SeamProblem:
    x = variable("x")
    constraint = {
        "op": "forall",
        "var": "x",
        "sort": "Item",
        "body": {
            "op": "iff",
            "left": atom("target", (x,)),
            "right": atom("signal", (x,)),
        },
    }
    return SeamProblem(
        problem_id="golden-smt-smoke",
        signature=Signature(
            sorts={"Item": ("a",)},
            relations={
                "signal": RelationDecl("signal", ("Item",)),
                "target": RelationDecl("target", ("Item",)),
            },
        ),
        local_theories=(LocalTheory("owner", (constraint,)),),
        overlap_maps=(),
        target_predicate="target",
        shared_vocabulary=("signal",),
        protected_signatures={"owner": ("signal",)},
        requested_judgment="boolean",
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tools/smtinterpol/LOCK.json",
    )
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    seam = problem()
    result = synthesize_with_smtinterpol(
        seam,
        SMTInterpolConfig(
            jar_path=args.jar,
            jar_sha256=lock["binary_sha256"],
            version_contains=lock["version_contains"],
            java_command=args.java,
        ),
    )
    if result.status is not SynthesisStatus.COMPILED or result.package is None:
        reasons = "; ".join(result.gate_report.reasons) or "no gate reason recorded"
        raise SystemExit(f"SMT smoke did not compile: {result.status.value}: {reasons}")
    report = verify_package(seam, result.package)
    semantic_gates = (
        report.gluing,
        report.conservativity,
        report.definability,
        report.preserved_refusals,
    )
    if any(status is not GateStatus.PASS for status in semantic_gates):
        raise SystemExit(f"independent reference replay rejected SMT package: {report.reasons}")
    print(json.dumps({"status": result.status.value, "package_hash": result.package.package_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
