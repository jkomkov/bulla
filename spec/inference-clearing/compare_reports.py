#!/usr/bin/env python3
"""Compare two inference-clearing JSON reports by value."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read a JSON report: {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()
    if _load(args.left) != _load(args.right):
        raise SystemExit("inference-clearing reports differ")
    print("Python and Node reports match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
