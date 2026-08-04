#!/usr/bin/env python3
"""An agent run with a blind spot — the case coverage exists to catch.

A four-action agent trajectory. Three tools are decorated with ``wrap_action``,
so each call emits a verifiable ActionReceipt. The fourth — the payment, the
most consequential action in the run — is dispatched directly and never enters
the wrapped path. The harness log records all four dispatches, so
``event_coverage`` reconciles three receipts against a denominator of four:
every receipt verifies, and the payment is still missing. Verifying the
receipts that exist cannot surface it; only the reconciliation can.

This fixture drives the site's blind-spot walkthrough. Its output is
deterministic (unsigned receipts with a fixed constructed timestamp), so the committed
``demo-output.json`` is byte-reproducible:

    PYTHONPATH=src python examples/agent-fleet-blind-spot/run_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bulla import event_coverage, observed_record_sha256, verify_receipt, wrap_action

_OUT = Path(__file__).resolve().parent / "demo-output.json"

# The independent record of what the harness dispatched — the coverage
# denominator. Every dispatch lands here, wrapped or not.
_observed: list[dict] = []
_receipts: list[dict] = []


def _dispatch(action_type: str) -> tuple[str, dict]:
    event_id = f"{action_type}:{len(_observed)}"
    record = {"id": event_id, "kind": action_type}
    record["record_sha256"] = observed_record_sha256(record)
    _observed.append(record)
    return event_id, record


def tool(action_type: str):
    """Decorate a tool so each call emits a receipt and is recorded for coverage."""
    def decorate(fn):
        def inner(**subject):
            event_id, record = _dispatch(action_type)
            scope = wrap_action(
                action_type, {"event_id": event_id, **subject},
                principal="did:web:example#agent",
                diagnostic_ref={"status": "not_applicable"},
                evidence_refs=[{
                    "name": "harness_action_record",
                    "hash": record["record_sha256"],
                    "grounding": "self_asserted",
                }],
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


def charge(amount_minor: int, currency: str, destination: str) -> str:
    # The bypass: dispatched by the harness like every other call, but this
    # function was never wrapped — no receipt is emitted anywhere.
    _dispatch("payments.charge")
    return f"charged {amount_minor} {currency} to {destination}"


def run() -> dict:
    read_file(path="/etc/config.yaml")
    fetch(url="https://api.example/data")
    write_file(path="/workspace/report.md", content="ok")
    charge(amount_minor=12500, currency="USD", destination="acct:vendor-7")

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
