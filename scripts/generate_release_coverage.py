#!/usr/bin/env python3
"""Generate the committed PyPI coverage instrument from its source snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.coverage import load_pypi_project, pypi_coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", type=Path, default=Path("releases"))
    parser.add_argument("--snapshot", type=Path, default=Path("releases/pypi-project.json"))
    parser.add_argument("--out", type=Path, default=Path("releases/coverage.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = pypi_coverage(
        args.receipts,
        project_doc=load_pypi_project(args.snapshot),
        verify_integrity=False,
    )
    # The source snapshot carries the acquisition time. Reuse it so --check is
    # deterministic and does not turn every validation run into generated drift.
    snapshot = load_pypi_project(args.snapshot)
    report["generated_at"] = snapshot.get("fetched_at")
    content = json.dumps(report, indent=2) + "\n"
    if args.check:
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != content:
            print(f"release coverage drift: regenerate {args.out}")
            return 1
        print(f"release coverage current: {report['total_anchored']} PyPI releases")
        return 0
    args.out.write_text(content, encoding="utf-8")
    print(f"wrote {args.out}: {report['status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
