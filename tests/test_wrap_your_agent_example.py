"""Pin the wrap-your-agent example: every wrapped action carries a receipt."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from bulla import verify_receipt

_EX = Path(__file__).resolve().parents[1] / "examples" / "wrap-your-agent" / "run_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("wrap_your_agent_demo", _EX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_wrapped_action_has_a_verifiable_receipt() -> None:
    demo = _load()
    output = demo.run()
    assert len(output["receipts"]) == len(output["observed_actions"]) == 3
    for receipt in output["receipts"]:
        assert verify_receipt(receipt).ok
    cov = output["coverage"]
    assert cov["coverage"] == 1.0
    assert cov["unreceipted_delta"] == []
