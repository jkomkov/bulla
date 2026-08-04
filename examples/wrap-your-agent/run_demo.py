#!/usr/bin/env python3
"""Wrap your agent's actions in receipts — the positive case.

A tiny agent loop with three tools. Each tool is decorated with ``wrap_action``,
so every consequential action emits a verifiable ActionReceipt with no envelope
tree to hand-build. Then ``event_coverage`` reconciles the emitted receipts
against the loop's own record of what it did: here every action carried a
receipt, so coverage is full. (The incident replay shows the other case — an
action that leaves no receipt, caught by the same reconciliation.)

    PYTHONPATH=src python examples/wrap-your-agent/run_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bulla import event_coverage, verify_receipt, wrap_action

_OUT = Path(__file__).resolve().parent / "demo-output.json"

# The independent record of what the agent did — the coverage denominator. In a
# real system this comes from outside the agent (a gateway log, an audit trail);
# here the harness records each tool call as it dispatches it.
_observed: list[dict] = []
_receipts: list[dict] = []


def tool(action_type: str):
    """Decorate a tool so each call emits a receipt and is recorded for coverage."""
    def decorate(fn):
        def inner(**subject):
            event_id = f"{action_type}:{len(_observed)}"
            _observed.append({"id": event_id, "kind": action_type})
            scope = wrap_action(
                action_type, {"event_id": event_id, **subject},
                principal="did:web:example#agent",
                diagnostic_ref={"status": "not_applicable"},
                timestamp="2026-08-04T00:00:00Z",
            )
            with scope:
                result = fn(**subject)
            _receipts.append(scope.receipt)
            return result
        return inner
    return decorate


@tool("fs.read")
def read_file(path: str) -> str:
    return f"contents of {path}"


@tool("http.get")
def fetch(url: str) -> str:
    return f"body of {url}"


@tool("fs.write")
def write_file(path: str, content: str) -> str:
    return f"wrote {len(content)} bytes to {path}"


def run() -> dict:
    # A three-step agent trajectory. Every action is wrapped.
    read_file(path="/etc/config.yaml")
    fetch(url="https://api.example/data")
    write_file(path="/tmp/out.txt", content="ok")

    for receipt in _receipts:
        assert verify_receipt(receipt).ok

    coverage = event_coverage(_observed, _receipts, anchor="agent-harness-log")
    return {
        "observed_actions": _observed,
        "receipts": _receipts,
        "coverage": {k: v for k, v in coverage.items() if k != "generated_at"},
    }


def main() -> int:
    output = run()
    _OUT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    cov = output["coverage"]
    print(json.dumps({
        "actions": [a["id"] for a in output["observed_actions"]],
        "receipts": len(output["receipts"]),
        "coverage": f"{cov['receipted']}/{cov['total_anchored']}",
        "unreceipted": cov["unreceipted_delta"],
    }, indent=2))
    print(f"\nwrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
