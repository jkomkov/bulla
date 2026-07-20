#!/usr/bin/env python3
"""Tiny subprocess boundaries for the certified-refinement demonstration."""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"shared-adapter", "decision-router"}:
        print("usage: tool.py <shared-adapter|decision-router>", file=sys.stderr)
        return 2
    request = json.load(sys.stdin)
    if sys.argv[1] == "shared-adapter":
        relations = request["relations"]
        if not isinstance(relations, dict):
            raise ValueError("relations must be an object")
        json.dump(relations, sys.stdout, sort_keys=True)
    else:
        decision = request["decision"]
        action = {
            "RELY": "continue_automatically",
            "REFUSE": "stop_automatically",
            "ESCALATE": "route_to_named_forum",
        }[decision]
        json.dump({"action": action, "decision_hash": request["decision_hash"]}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
