"""Executable checks for Bulla's GitHub and PyPI front door."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_readme_answerability_fixture_matches_real_cli(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    create = subprocess.run(
        (
            sys.executable,
            "-m",
            "bulla",
            "receipt",
            "create",
            "--type",
            "demo.write",
            "--subject",
            "path=/tmp/example.txt",
            "--principal",
            "did:web:example.invalid:agent",
            "--policy",
            "policy://demo-v1",
            "--scope",
            "path=/tmp/example.txt",
            "--evidence",
            "diff=sha256:1111:self_asserted",
            "--forum-endpoint",
            "https://example.invalid/challenge",
            "--forum-root",
            "fixture:independently-pinned-root",
            "--out",
            str(receipt),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert create.returncode == 0, create.stderr
    verify = subprocess.run(
        (
            sys.executable,
            "-m",
            "bulla",
            "receipt",
            "verify",
            str(receipt),
            "--format",
            "json",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    report = json.loads(verify.stdout)
    expected = json.loads(
        (ROOT / "docs/fixtures/unsigned-self-asserted-answerability.json").read_text()
    )
    assert report["answerability"] == expected


def test_first_level_copy_preserves_current_product_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    combined = readme + "\n" + metadata
    assert "SEAM is the underlying theory" not in combined
    assert "coherence fee as a safety" not in combined.lower()
    assert "coherence fee as an execution" not in combined.lower()
    assert "authorless action" not in combined.lower()
    assert "independently validated" not in combined.lower()
    assert readme.index("Portable, recomputable receipts") < readme.index(
        "Legacy composition diagnostics"
    )
