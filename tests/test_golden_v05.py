"""Golden v0.5 freeze, zero-import, and captive-evidence gates."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "bench/golden/v0.5"
VERIFY = ROOT / "scripts/verify_golden_v05.py"


def hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in GOLDEN.glob("*.json*")
    }


def verify(path: Path = GOLDEN):
    return subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_zero_import_replay_and_exact_denominators() -> None:
    result = verify()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "classification": "INTERNAL_CAPTIVE_CONTROL",
        "f11_margins": 52,
        "f12_cases": 72,
        "frontier_points": 96,
        "matched_scrambles": 1000,
        "ok": True,
        "profile": "bulla.golden-suite/0.5-experimental",
        "transfer_cases": 144,
    }
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_zero_import_verifier.py"), str(VERIFY)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0, audit.stderr


def test_generation_is_deterministic_and_v04_remains_bound() -> None:
    before = hashes()
    result = subprocess.run(
        [sys.executable, str(GOLDEN / "generate.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert hashes() == before
    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    v04 = ROOT / "bench/golden/v0.4/manifest.json"
    assert manifest["preserved_v04_manifest"] == "sha256:" + hashlib.sha256(v04.read_bytes()).hexdigest()


def test_transfer_holdout_is_untouched_and_negative_controls_never_apply() -> None:
    cases = [json.loads(line) for line in (GOLDEN / "transfer-cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(case["partition"] == "DESIGN" for case in cases) == 96
    assert sum(case["partition"] == "HOLDOUT" for case in cases) == 48
    assert all(case["actual_exit"] != "APPLY" for case in cases if not case["eligible_for_apply"])
    report = json.loads((GOLDEN / "report.json").read_text(encoding="utf-8"))
    assert report["efficacy_use_after_freeze"] == "PROHIBITED"
    assert report["external_authors"] == report["external_adjudicators"] == 0


def test_tamper_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "v0.5"
    shutil.copytree(GOLDEN, copied)
    f12 = copied / "f12-effect-laundering.json"
    value = json.loads(f12.read_text(encoding="utf-8"))
    value["cases"][0]["actual_verdict"] = "APPLY"
    f12.write_text(json.dumps(value), encoding="utf-8")
    result = verify(copied)
    assert result.returncode == 1
    assert "artifact hash mismatch" in result.stderr
