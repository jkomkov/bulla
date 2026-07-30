#!/usr/bin/env python3
"""Zero-import integrity verifier for the frozen Golden F13 packet."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1] / "bench/golden/v0.6"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["artifacts"].items():
        if digest(root / name) != expected:
            print(f"artifact hash mismatch: {name}", file=sys.stderr)
            return 1
    packet = json.loads((root / "f13-cases.json").read_text())
    report = json.loads((root / "report.json").read_text())
    state_report = json.loads((root / "state-model-report.json").read_text())
    cases = packet["cases"]
    families = {case["family"] for case in cases}
    if len(cases) != 96 or len(families) != 12:
        print("F13 denominator mismatch", file=sys.stderr)
        return 1
    if len({case["case_id"] for case in cases}) != 96:
        print("duplicate F13 case ID", file=sys.stderr)
        return 1
    if any(case["external_authors"] != 0 or case["evidence_class"] != "INTERNAL_CAPTIVE" for case in cases):
        print("F13 evidence classification mismatch", file=sys.stderr)
        return 1
    if report["cases_hash"] != digest(root / "f13-cases.json"):
        print("F13 report does not bind cases", file=sys.stderr)
        return 1
    if (
        state_report.get("classification") != "INTERNAL_CAPTIVE_MODEL"
        or state_report.get("one_intent", {}).get("violations")
        or state_report.get("two_intent", {}).get("violations")
        or state_report.get("mutation_campaign", {}).get("critical_kill_rate") != 1.0
    ):
        print("F13 state model or mutation gate failed", file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True, "profile": manifest["profile"], "cases": len(cases),
        "families": len(families), "classification": manifest["classification"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
