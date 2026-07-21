from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


BULLA = Path(__file__).resolve().parents[1]
FREEZE = BULLA / "bench/golden/v0.4/bulla-claim-flow-v0.4-freeze.json"
VERIFIER = BULLA / "scripts/verify_claim_flow_freeze.py"
MONOREPO_ARTIFACTS_AVAILABLE = (
    BULLA.parent / "papers/interpolant-envelope/lean/InterpolantEnvelope/ClaimFlow.lean"
).is_file()


def run_verifier(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(VERIFIER), str(path)],
        cwd=BULLA.parent,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(
    not MONOREPO_ARTIFACTS_AVAILABLE,
    reason="the Claim Flow freeze binds research artifacts outside the standalone Bulla subtree",
)
def test_claim_flow_freeze_replays_without_bulla_imports() -> None:
    completed = run_verifier(FREEZE)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "artifacts_verified": 11,
        "classification": "INTERNAL_CAPTIVE",
        "frozen_main_commit": "293f6e7f3cd1bbd32ef2c05ec458c34fe905c3dd",
        "ok": True,
        "profile": "bulla.claim-flow/0.4-freeze",
    }


def test_claim_flow_freeze_fails_closed_on_content_tamper(tmp_path: Path) -> None:
    document = json.loads(FREEZE.read_text(encoding="utf-8"))
    document["content"]["classification"] = "INDEPENDENT"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    completed = run_verifier(tampered)
    assert completed.returncode == 1
    assert "classification must remain INTERNAL_CAPTIVE" in completed.stderr


def test_claim_flow_freeze_fails_closed_on_unknown_fields(tmp_path: Path) -> None:
    document = json.loads(FREEZE.read_text(encoding="utf-8"))
    document["claim"] = "independently validated"
    tampered = tmp_path / "unknown.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    completed = run_verifier(tampered)
    assert completed.returncode == 1
    assert "exactly content and content_hash" in completed.stderr
