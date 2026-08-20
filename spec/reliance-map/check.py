#!/usr/bin/env python3
"""Repository-only Reliance Map checker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bulla.experimental.reliance_map import RelianceMapError, compute_reliance_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("graph", type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = compute_reliance_map(
            args.graph.read_bytes(), args.ledger.read_bytes(), args.context.read_bytes()
        )
    except (OSError, RelianceMapError) as exc:
        print(json.dumps({"error": str(exc), "exit_class": 2}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
