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
    readme_flat = " ".join(readme.split())
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    combined = readme + "\n" + metadata
    assert "SEAM is the underlying theory" not in combined
    assert "coherence fee as a safety" not in combined.lower()
    assert "coherence fee as an execution" not in combined.lower()
    assert "authorless action" not in combined.lower()
    assert "independently validated" not in combined.lower()
    assert "**Receipts for Agents.**" in readme
    assert "Bulla creates portable ActionReceipts" in readme
    assert "receiving system can verify the record locally" in readme_flat
    assert "apply its own `ReliancePolicy`" in readme_flat
    assert "reconcile the receipt set against its own event records" in readme_flat
    assert "Build with Bulla. Require ActionReceipts." in readme_flat
    assert "They are not part of the installed package." in readme_flat
    assert "Glyph Standard publishes and stewards the Bulla protocol family" in readme_flat
    assert "The altered file fails its integrity check" in readme
    assert (
        "The supplied receiver record contains one action with no matching receipt"
        in readme_flat
    )
    assert "Bulla 0.49.2 ships ActionReceipt creation and verification" in readme_flat
    assert "local Doorstep MCP capture commands described above" in readme_flat
    assert "They do not add installed commands or stable Python exports." in readme_flat
    assert "Legacy composition diagnostics" not in readme
    assert "Research frontier" not in readme
    assert "A bulla was the clay envelope" not in readme
    assert (
        'description = "Portable ActionReceipts and receiver-side verification for '
        'consequential agent transactions"'
    ) in metadata
    assert 'Publisher = "https://glyphstandard.com/about"' in metadata


def test_readme_coverage_example_matches_the_public_api() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'receipt_for("network.egress", {"event_id": "action-001"})' in readme
    assert 'assert complete["coverage"] == 1.0' in readme
    assert 'assert with_gap["coverage"] == 0.5' in readme
    assert 'assert with_gap["unreceipted_delta"] == ["action-002"]' in readme
