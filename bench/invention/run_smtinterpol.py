#!/usr/bin/env python3
"""Exercise the frozen corpus with the pinned real SMTInterpol adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

from bulla.experimental.invention import (
    GateStatus,
    SeamProblem,
    SynthesisStatus,
    synthesize,
    verify_failure_certificate,
    verify_package,
)
from bulla.experimental.smtinterpol import SMTInterpolConfig, synthesize_with_smtinterpol


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "tools/smtinterpol/LOCK.json"
CORPUS_PATH = ROOT / "bench/invention/corpus.json"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sound(problem: SeamProblem, result) -> bool:
    if result.package is not None:
        report = verify_package(problem, result.package)
        if not (
            report.gluing is GateStatus.PASS
            and report.conservativity is GateStatus.PASS
            and report.preserved_refusals is GateStatus.PASS
            and report.receipt_binding is GateStatus.PASS
        ):
            return False
        if result.status is SynthesisStatus.COMPILED and report.definability is not GateStatus.PASS:
            return False
    if result.certificate is not None and result.status in {
        SynthesisStatus.PARTIAL,
        SynthesisStatus.ESCALATE,
        SynthesisStatus.CHOICE_REQUIRED,
    }:
        return verify_failure_certificate(
            problem, result.certificate, alternatives=result.alternatives
        )
    return result.status not in {SynthesisStatus.INVALID_INPUT, SynthesisStatus.INDETERMINATE}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--java", required=True)
    parser.add_argument("--split", choices=("all", "design", "holdout"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    actual_hash = _file_hash(args.jar)
    if actual_hash != lock["binary_sha256"]:
        raise SystemExit(f"jar hash mismatch: expected {lock['binary_sha256']}, got {actual_hash}")
    java_version = subprocess.run(
        [args.java, "-version"], capture_output=True, text=True, check=False
    )
    if java_version.returncode != 0:
        raise SystemExit("java version probe failed: " + java_version.stderr)
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    selected = [
        item for item in corpus["instances"]
        if args.split == "all" or item["split"] == args.split
    ]
    config = SMTInterpolConfig(
        jar_path=args.jar,
        jar_sha256=lock["binary_sha256"],
        version_contains=lock["version_contains"],
        java_command=args.java,
        timeout_seconds=10,
        require_resolute_proof=True,
        fallback_to_reference=True,
    )
    records = []
    started = time.perf_counter()
    for item in selected:
        problem = SeamProblem.from_dict(item["problem"])
        reference = synthesize(problem)
        accelerated = synthesize_with_smtinterpol(problem, config)
        native = accelerated.backend == "smtinterpol+exhaustive-verifier"
        proof_refs = accelerated.package.proof_references if native and accelerated.package else ()
        resolute_count = (
            sum(bool(x["artifact"].get("resolute_verified")) for x in proof_refs)
            if native
            else int(accelerated.verifier.get("resolute_verified_count", 0))
        )
        records.append(
            {
                "id": item["id"],
                "reference_status": reference.status.value,
                "accelerated_status": accelerated.status.value,
                "status_agreement": reference.status is accelerated.status,
                "native_interpolant": native,
                "reference_fallback": "reference-fallback" in accelerated.backend,
                "resolute_verified_queries": resolute_count,
                "sound": _sound(problem, accelerated),
                "backend": accelerated.backend,
                "result_hash": accelerated.result_hash,
            }
        )
    statuses = Counter(x["accelerated_status"] for x in records)
    payload = {
        "schema_version": "0.1-experimental",
        "split": args.split,
        "freeze_hash": corpus["freeze"]["payload_hash"],
        "solver_lock": lock,
        "java_version": (java_version.stderr or java_version.stdout).strip(),
        "instance_count": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "status_agreement_count": sum(x["status_agreement"] for x in records),
        "native_interpolant_count": sum(x["native_interpolant"] for x in records),
        "reference_fallback_count": sum(x["reference_fallback"] for x in records),
        "resolute_verified_query_count": sum(x["resolute_verified_queries"] for x in records),
        "sound_count": sum(x["sound"] for x in records),
        "elapsed_seconds": time.perf_counter() - started,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "records"}, indent=2))
    if payload["status_agreement_count"] != len(records) or payload["sound_count"] != len(records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
