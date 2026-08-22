#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from bulla.experimental.answerability_network import verify_answerability_network

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("dossier", type=Path); parser.add_argument("--context", required=True, type=Path); args = parser.parse_args()
    report = verify_answerability_network(args.dossier, args.context.read_bytes()); print(json.dumps(report, indent=2, sort_keys=True)); return report["exit_code"]
if __name__ == "__main__": raise SystemExit(main())
