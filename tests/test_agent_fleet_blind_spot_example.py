"""Pin the blind-spot example: valid receipts coexist with an unreceipted action."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from bulla import verify_receipt

_DIR = Path(__file__).resolve().parents[1] / "examples" / "agent-fleet-blind-spot"
_EX = _DIR / "run_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("agent_fleet_blind_spot_demo", _EX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_receipt_verifies_and_the_payment_is_still_missing() -> None:
    demo = _load()
    output = demo.run()
    assert len(output["observed_actions"]) == 4
    assert len(output["receipts"]) == 3
    for receipt in output["receipts"]:
        assert verify_receipt(receipt).ok
    cov = output["coverage"]
    assert cov["coverage"] == 0.75
    assert cov["unreceipted_delta"] == ["payments.charge:3"]
    assert cov["unreceipted"][0]["id"] == "payments.charge:3"
    assert cov["unreceipted"][0]["kind"] == "payments.charge"
    assert cov["unreceipted"][0]["record_sha256"].startswith("sha256:")
    assert cov["phantom_receipt_ids"] == []
    assert cov["invalid_receipts"] == []


def test_committed_fixture_is_byte_reproducible() -> None:
    demo = _load()
    regenerated = json.dumps(demo.run(), indent=2, sort_keys=True) + "\n"
    committed = (_DIR / "demo-output.json").read_text()
    assert committed == regenerated
