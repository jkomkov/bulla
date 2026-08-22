#!/usr/bin/env python3
"""Source-tree entry point for a witness covenant dossier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.experimental.witness_covenant import verify_witness_covenant


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dossier")
    parser.add_argument("--context", required=True)
    args = parser.parse_args()
    report = verify_witness_covenant(
        args.dossier,
        Path(args.context).read_bytes(),
    )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    raise SystemExit(report.exit_code)


if __name__ == "__main__":
    main()
