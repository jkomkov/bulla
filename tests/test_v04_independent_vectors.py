from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bulla.action_receipt import ActionReceipt


VECTORS = Path(__file__).resolve().parents[1] / "spec" / "vectors"
VECTOR = VECTORS / "v04-occurrence-bound.json"


def _python_checker():
    spec = importlib.util.spec_from_file_location("independent_v04", VECTORS / "independent_v04.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_zero_bulla_checker_matches_reference_implementation() -> None:
    document = json.loads(VECTOR.read_text())
    receipt = ActionReceipt.from_dict(document)
    result = _python_checker().verify(VECTOR)
    assert result["digest_ok"] and result["proofs_ok"] is True
    assert result["hashes"]["content"] == receipt.content_hash
    assert result["hashes"]["event"] == receipt.event_hash
    assert result["hashes"]["authorization"] == receipt.authorization_hash
    assert result["hashes"]["attestation"] == receipt.attestation_hash
    assert result["hashes"]["log_leaf"] == receipt.log_leaf


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_node_zero_bulla_checker_matches_python() -> None:
    completed = subprocess.run(
        ["node", str(VECTORS / "independent_v04.mjs"), str(VECTOR)],
        check=True, capture_output=True, text=True,
    )
    node_result = json.loads(completed.stdout)
    python_result = _python_checker().verify(VECTOR)
    assert node_result == python_result


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_both_independent_checkers_reject_claimed_at_transplant(tmp_path: Path) -> None:
    document = json.loads(VECTOR.read_text())
    document["claimed_at"] = "2036-01-01T00:00:00Z"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(document))
    assert not _python_checker().verify(path)["digest_ok"]
    completed = subprocess.run(
        ["node", str(VECTORS / "independent_v04.mjs"), str(path)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 1
