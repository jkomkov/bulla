#!/usr/bin/env python3
"""Copy and narrate the three checked Acceptance Contract alpha transactions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SPEC = ROOT / "spec" / "acceptance-contract"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bulla.experimental.acceptance_contract import (
    AcceptanceContext,
    evaluate_acceptance_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", action="store_true")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    source = SPEC / "generated"
    if args.out.exists():
        raise SystemExit(f"output already exists: {args.out}")
    shutil.copytree(source, args.out)
    context = AcceptanceContext.from_dict(
        json.loads((args.out / "context.json").read_text(encoding="utf-8"))
    )
    reports = {
        scenario: evaluate_acceptance_bundle(args.out / scenario, context)
        for scenario in ("missing", "passing", "failing")
    }
    if args.story:
        print("One deploy-agent claim. One release policy. Three evidence states.\n")
        print("The deploy agent reports: STAGING_READY")
        print("The release policy requires: a verified rollback test reporting PASS\n")
        print("missing   HOLD_FOR_EVIDENCE  promotion INELIGIBLE")
        print("passing   PROCEED            promotion ELIGIBLE")
        print("failing   REFUSE             promotion INELIGIBLE\n")
        print("The claim did not change. The supplied evidence did.")
        print("Eligibility did not issue authorization or attempt a deployment.")
    else:
        print(
            json.dumps(
                {name: report.to_dict() for name, report in reports.items()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
