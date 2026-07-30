"""The cyber-eval incident replay: the four paths and the clean-room bundle check."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from bulla.action_receipt import verify_receipt

_EX = Path(__file__).resolve().parents[1] / "examples" / "eval-incident-replay"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _EX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_replay_runs_all_four_paths() -> None:
    demo = _load("run_demo")
    output = demo.run()
    decisions = [r["action"]["subject"]["decision"] for r in output["capability_receipts"]]
    assert decisions == ["PERMIT", "REFUSE"]                       # path 1
    assert output["coverage"]["unreceipted_delta"] == ["evt-0003-proxy-bypass-egress"]  # paths 2+3
    assert output["trajectory_decision"]["action"]["subject"]["state"] == "REFUSE_AND_FREEZE"
    assert output["incident_handoff"]["action"]["type"] == "incident.handoff"  # path 4


def test_capability_receipts_are_gateway_signed_not_agent() -> None:
    demo = _load("run_demo")
    output = demo.run()
    gateway = output["signer_topology"]["gateway"]
    for r in output["capability_receipts"]:
        assert r["signature"]["issuer"] == gateway
        assert verify_receipt(r).verified_to == "attestation"
        assert r["action"]["type"] == "capability.decide"
        assert {item["grounding"] for item in r["evidence_refs"]} == {
            "self_asserted"
        }
    # no agent key exists in the topology at all
    assert not any(k in output["signer_topology"] for k in ("agent", "eval-model"))


def test_v02_lineage_and_handoff_bind_exact_references() -> None:
    demo = _load("run_demo")
    output = demo.run()
    receipts = output["capability_receipts"]
    trajectory = output["trajectory_decision"]["action"]["subject"]
    timeline = [output["mandate"], *receipts, output["trajectory_decision"]]
    handoff = output["incident_handoff"]["action"]["subject"]

    assert output["profile"] == "bulla.cyber-eval/0.2-draft"
    assert trajectory["considered_attestation_hashes"] == [
        receipt["hashes"]["attestation"] for receipt in receipts
    ]
    assert handoff["timeline_attestation_hashes"] == [
        receipt["hashes"]["attestation"] for receipt in timeline
    ]
    assert handoff["timeline_action_types"] == [
        receipt["action"]["type"] for receipt in timeline
    ]


def test_bundle_carries_no_inline_secret() -> None:
    demo = _load("run_demo")
    output = demo.run()
    for art in output["incident_handoff"]["action"]["subject"]["sensitive_artifacts"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", art["ref"])


def test_clean_room_verifier_passes(tmp_path: Path) -> None:
    demo = _load("run_demo")
    (tmp_path / "bundle.json").write_text(json.dumps(demo.run()))
    proc = subprocess.run(
        [sys.executable, "-I", str(_EX / "verify_bundle.py"), str(tmp_path / "bundle.json")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "zero Bulla imports" in proc.stdout
    assert "ed25519 issuer authenticity requires" in proc.stdout


def test_clean_room_verifier_rejects_tampered_receipt(tmp_path: Path) -> None:
    demo = _load("run_demo")
    output = deepcopy(demo.run())
    output["capability_receipts"][0]["action"]["subject"]["decision"] = "REFUSE"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(output))
    proc = subprocess.run(
        [sys.executable, "-I", str(_EX / "verify_bundle.py"), str(path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "content hash mismatch" in proc.stdout


def test_clean_room_verifier_recomputes_denominator(tmp_path: Path) -> None:
    demo = _load("run_demo")
    output = deepcopy(demo.run())
    output["coverage"]["unreceipted_delta"] = []
    path = tmp_path / "lying-coverage.json"
    path.write_text(json.dumps(output))
    proc = subprocess.run(
        [sys.executable, "-I", str(_EX / "verify_bundle.py"), str(path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "coverage.unreceipted_delta does not recompute" in proc.stdout


def test_standalone_verifier_recomputes_handoff_parent_refs(tmp_path: Path) -> None:
    demo = _load("run_demo")
    output = deepcopy(demo.run())
    output["incident_handoff"]["action"]["subject"]["parent_refs"]["trajectory"] = (
        "sha256:" + "00" * 32
    )
    # Re-signing or recomputing downstream hashes is not needed for this
    # counterexample: the standalone checker must reject the content mutation
    # and the claimed parent relation independently.
    path = tmp_path / "wrong-parent.json"
    path.write_text(json.dumps(output))
    proc = subprocess.run(
        [sys.executable, "-I", str(_EX / "verify_bundle.py"), str(path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "parent_refs do not recompute" in proc.stdout
