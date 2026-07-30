#!/usr/bin/env python3
"""Run the source-only localhost MCP incident-packet pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.experimental.incident_pilots import run_mcp_pilot, summarize_coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New output directory for runtime evidence.",
    )
    args = parser.parse_args()
    run = run_mcp_pilot(args.out)
    print(json.dumps(summarize_coverage(run), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
