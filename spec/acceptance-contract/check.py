#!/usr/bin/env python3
"""Offline checker for one Acceptance Contract alpha bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bulla.experimental.acceptance_contract import (
    AcceptanceContractError,
    evaluate_acceptance_bundle,
    parse_acceptance_context,
    report_exit_code,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "story"), default="json")
    args = parser.parse_args()
    try:
        context = parse_acceptance_context(args.context.read_bytes())
        report = evaluate_acceptance_bundle(args.bundle, context)
    except (AcceptanceContractError, OSError, ValueError) as exc:
        print(_json({"error": str(exc), "profile": "bulla.acceptance-checker-error/0.1"}))
        return 2
    if args.format == "json":
        print(_json(report.to_dict()))
    else:
        rollback = next(
            item for item in report.requirements if item.requirement_id == "rollback-test"
        )
        print("Agent claim                 STAGING_READY")
        print("Buyer requirement           verified rollback test = PASS")
        print(f"Rollback evidence            {rollback.observed_value}")
        print(f"Receiver decision            {report.decision}")
        print(f"Promotion eligibility        {report.consequence_eligibility}")
        print(f"Authorization                {report.authorization}")
        print(f"Execution                    {report.execution}")
        if report.conditional_evidence_requests:
            print("Next requested record        signed rollback-test result")
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
