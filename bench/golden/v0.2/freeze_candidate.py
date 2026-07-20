#!/usr/bin/env python3
"""Bind a committed v0.2 candidate before reviewer case creation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from bulla.action_receipt import build_action_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.frsl import canonical_hash


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority("did:example:golden-v02-freeze", "policy:golden-v02-preregistration@1"),
        bounds=Bounds("bulla.golden-suite/0.2-experimental"),
        recourse=Recourse(
            "P30D",
            Forum("https://github.com/Integral-Systems/res-agentica", "git:golden-v02-candidate"),
            (Remedy("recompute", "python -I bulla/scripts/verify_golden_v02.py", "freeze-manifest.json"),),
        ),
        retention_class="authority-permanent",
        disclosure_class="auditor",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_commit")
    args = parser.parse_args()
    commit = args.candidate_commit
    if len(commit) != 40:
        raise SystemExit("candidate commit must be the full 40-character object id")
    try:
        int(commit, 16)
    except ValueError as exc:
        raise SystemExit("candidate commit is not hexadecimal") from exc
    subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT, check=True)
    artifact_names = (
        "SPEC.md", "preregistration.json", "internal-summary.json", "metamorphic-report.json",
        "economic-model-check.json", "mutation-campaign.json", "provenance-cards.json",
        "abstention-scorecard.json", "scaling-report.json", "pathology-regression.json",
        "drift-stress.json", "formal-audit.json", "provenance-dag-scout.json",
        "portability-observation.json", "external-status.json", "found-data-status.json",
        "source-accessibility-audit.json", "challenge-ledger.json", "falsification-ledger.json",
    )
    artifact_hashes = {name: file_hash(HERE / name) for name in artifact_names}
    runner_hashes = {
        name: file_hash(HERE / name)
        for name in ("run_suite.py", "run_scaling.py", "run_drift.py", "run_pathology_regression.py")
    }
    manifest_core = {
        "profile": "bulla.golden-suite/0.2-experimental",
        "candidate_commit": commit,
        "v01_lineage_commit": "2129cc1a",
        "specification_hash": artifact_hashes["SPEC.md"],
        "scoring_hash": canonical_hash({
            "preregistration": artifact_hashes["preregistration.json"],
            "metamorphic": artifact_hashes["metamorphic-report.json"],
            "abstention": artifact_hashes["abstention-scorecard.json"],
        }),
        "artifact_hashes": artifact_hashes,
        "runner_hashes": runner_hashes,
        "blindness_mode_requested": "REVIEWER_ORIGINATED_BLIND",
        "external_status": "BLOCKED_MISSING_EXTERNAL_PARTICIPANTS",
        "reviewer_cases_created": 0,
    }
    manifest = {**manifest_core, "manifest_hash": canonical_hash(manifest_core)}
    (HERE / "freeze-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    timestamp = datetime.now(timezone.utc).isoformat()
    receipt = build_action_receipt(
        action={"type": "bulla.golden.freeze", "subject": {
            "profile": manifest["profile"], "candidate_commit": commit,
            "manifest_hash": manifest["manifest_hash"],
        }},
        diagnostic_ref={"status": "reference", "ref": manifest["manifest_hash"]},
        envelope=envelope(),
        evidence_refs=({
            "name": "golden_v02_freeze_manifest",
            "hash": manifest["manifest_hash"],
            "grounding": "execution_verified",
        },),
        timestamp=timestamp,
        producer={"component": "bulla.golden-suite", "profile": "0.2-experimental"},
    )
    (HERE / "freeze-receipt.json").write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_commit": commit, "manifest_hash": manifest["manifest_hash"], "receipt_hashes": receipt.hashes()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
