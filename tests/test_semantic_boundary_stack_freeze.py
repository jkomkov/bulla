from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BULLA = Path(__file__).resolve().parents[1]
RECEIPT = BULLA / "bench/golden/v0.3/bulla-semantic-boundary-stack-v0.3.json"
VERIFIER = BULLA / "scripts/verify_semantic_boundary_stack.py"


def test_stack_freeze_replays() -> None:
    completed = subprocess.run(
        [sys.executable, str(VERIFIER), str(RECEIPT)],
        cwd=BULLA.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["classification"] == "INTERNAL_CAPTIVE_FOREIGN_SUBSTRATE"
    assert result["stable_surface_files"] == 153


def test_stack_freeze_fails_on_tamper(tmp_path: Path) -> None:
    document = json.loads(RECEIPT.read_text(encoding="utf-8"))
    document["content"]["classification"] = "INDEPENDENT"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(VERIFIER), str(tampered)],
        cwd=BULLA.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "content hash mismatch" in completed.stderr
