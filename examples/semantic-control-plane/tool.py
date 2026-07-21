#!/usr/bin/env python3
"""Two tiny JSON tools used as real subprocess boundaries by the demo."""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"delivery", "claim-router"}:
        print("usage: tool.py <delivery|claim-router>", file=sys.stderr)
        return 2
    request = json.load(sys.stdin)
    if sys.argv[1] == "delivery":
        delivery_id = request["delivery_id"]
        accepted = bool(request["signed_acceptance"])
        json.dump({"accepted_evidence": [[delivery_id]] if accepted else []}, sys.stdout)
    else:
        decision = request["decision"]
        action = {"RELY": "release_claim", "REFUSE": "hold_claim", "ESCALATE": "manual_review"}[decision]
        json.dump({"action": action, "bound_application": request["application_result_hash"]}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
