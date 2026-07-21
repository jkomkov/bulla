"""Release-boundary gates for Bulla 0.44.0."""

from __future__ import annotations

from pathlib import Path
import sys

import bulla
import pytest

from bulla.cli import main


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_status_language_are_synchronized() -> None:
    assert bulla.__version__ == "0.44.0"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.44.0 — 2026-07-19" in changelog
    spec = (ROOT / "spec/README.md").read_text(encoding="utf-8")
    assert "**Normative version:** `0.2`" in spec
    assert "**Opt-in released draft:** `0.3`" in spec


def test_release_workflow_is_publish_then_verify_then_receipt() -> None:
    workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    steps = (
        "Run full test suite on the tagged commit",
        "Verify tests left the tagged source tree unchanged",
        "Build sdist and wheel",
        "Verify source, wheel, and sdist runtime parity",
        "Publish to PyPI with Trusted Publishing attestations",
        "Verify PyPI accepted the exact artifacts and publisher identity",
        "Mint signed post-publication release receipt",
        "Attach receipt to durable GitHub release",
    )
    positions = [workflow.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "git status --porcelain=v1 --untracked-files=all" in workflow
    assert "test -n \"$BULLA_RELEASE_KEY\"" in workflow
    assert "--repository jkomkov/bulla" in workflow


def test_release_finalizer_recovers_without_republishing() -> None:
    workflow = (ROOT / ".github/workflows/finalize-release.yml").read_text(
        encoding="utf-8"
    )
    assert "download_pypi_release.py" in workflow
    assert "verify_pypi_release.py" in workflow
    assert "mint_release_receipt.py" in workflow
    assert "pypa/gh-action-pypi-publish" not in workflow
    assert "twine upload" not in workflow
    assert "id-token: write" not in workflow


def test_experimental_modules_are_not_reexported_from_stable_root() -> None:
    assert all("experimental" not in name for name in bulla.__all__)


def test_cli_front_door_uses_receipt_first_truth_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["bulla", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Portable, recomputable receipts for consequential agent actions" in help_text
    assert "does not establish worldly truth" in help_text
    assert "authorless agent action" not in help_text
    assert "coherence fee" in help_text
    assert "disclosure and omission signals" in help_text
