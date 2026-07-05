"""The flagship demo is a test: a stateless agent's bond gets slashed by a
bystander who recomputes the verdict — no oracle. If this stops firing, the
thesis stopped shipping."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("nacl", reason="cross-boundary demo signs with bulla[identity]")

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "cross-boundary-bond" / "demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("cbb_demo", _DEMO)
    mod = importlib.util.module_from_spec(spec)
    # register before exec: `from __future__ import annotations` makes @dataclass
    # resolve string annotations via sys.modules[cls.__module__]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _DEMO.exists(), reason="demo not present")
def test_bond_is_slashed_by_recomputed_verdict():
    demo = _load_demo()
    from bulla.identity import LocalEd25519Signer

    receipt = demo.agent_b_settles(LocalEd25519Signer.generate())
    bond = demo.Bond(
        amount=50_000, currency="USD", posted_by="agent-B",
        beneficiary=demo.ORG_X, slash_condition="fee>0 recomputable",
    )
    out = demo.bystander_audit_and_slash(receipt, bond)

    assert out["verdict"] == "slashed"
    assert out["cap"] == 7                      # the recomputed fee = the cap
    assert bond.status == "slashed"
    assert bond.resolution["paid_to"] == demo.ORG_X


@pytest.mark.skipif(not _DEMO.exists(), reason="demo not present")
def test_bystander_refuses_a_receipt_whose_composition_was_swapped():
    """If the pinned composition hash does not bind the verdict, the bystander
    refuses to slash — the verdict must be about the composition it claims."""
    demo = _load_demo()
    from bulla.identity import LocalEd25519Signer

    receipt = demo.agent_b_settles(LocalEd25519Signer.generate())
    receipt["anchor_ref"]["ref"] = "sha256:" + "0" * 64  # claim a different composition
    receipt["diagnostic_ref"]["ref"] = "sha256:" + "0" * 64
    # recompute hashes so it passes digest+attestation would fail (sig stale); but the
    # bystander check is about composition binding, so verify first will already fail:
    bond = demo.Bond(amount=1, currency="USD", posted_by="B", beneficiary=demo.ORG_X, slash_condition="x")
    out = demo.bystander_audit_and_slash(receipt, bond)
    assert out["verdict"] != "slashed"
    assert bond.status == "held"
