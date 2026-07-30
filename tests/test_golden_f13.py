from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from bulla.experimental.f13 import evaluate_f13_case


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "bench/golden/v0.6"


def _hashes() -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in GOLDEN.glob("*.json")}


def test_f13_has_exact_denominator_and_every_machine_exit_replays() -> None:
    cases = json.loads((GOLDEN / "f13-cases.json").read_text())["cases"]
    assert len(cases) == 96
    assert len({case["family"] for case in cases}) == 12
    assert all(evaluate_f13_case(case) == case["required_exit"] for case in cases)


def test_f13_zero_import_verifier() -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/verify_golden_f13.py"), str(GOLDEN)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cases"] == 96
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_zero_import_verifier.py"),
         str(ROOT / "scripts/verify_golden_f13.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert audit.returncode == 0, audit.stderr


def test_f13_generation_is_deterministic() -> None:
    before = _hashes()
    result = subprocess.run(
        [sys.executable, str(GOLDEN / "generate.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert _hashes() == before
