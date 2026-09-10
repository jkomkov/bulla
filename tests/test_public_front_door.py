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
    import bulla
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    flat = " ".join(readme.split())
    assert 800 <= len(readme.split()) <= 1100
    for phrase in (
        "Bulla creates portable ActionReceipts",
        "apply receiver-supplied policies",
        "No funds move.",
        "This unsigned example is not an authentication test",
        "Coverage is relative to the supplied customer record",
        "Privacy default: commitments only.",
        "not the MCP server, tool execution, or result correctness",
        "Reduced integration effort and interoperability between independently operated systems remain unmeasured",
        "v0.2 remains the normative default",
        "research code, not package features",
    ):
        assert phrase in flat
    assert f'bulla=={bulla.__version__}' in readme
    assert f"MCP capture is included in Bulla {bulla.__version__}" in flat
    assert readme.index("| Original receipt |") < readme.index("## Capture")
    assert readme.count("```python") == 1
    assert "PYTHONPATH=" not in readme
    assert "/bulla/inspect" not in readme
    for claim in ("Stripe for", "tamper-proof", "different log integration for every provider"):
        assert claim not in readme


def test_current_documentation_identity_and_research_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for value in ("Bulla Labs", "Answerable Computing", "Glyph Standard, Inc.",
                  "John Komkov", "signed-JSON parity",
                  "https://bullalabs.com/answerable-computing",
                  "https://bullalabs.com/research/routed-buyer-continuity"):
        assert value in readme
    assert "https://glyphstandard.com" not in readme + metadata
    assert "/bulla/answerable-computing" not in readme + metadata
    assert 'Publisher = "https://bullalabs.com/about"' in metadata
    assert 'Security = "https://github.com/jkomkov/bulla/security/policy"' in metadata
