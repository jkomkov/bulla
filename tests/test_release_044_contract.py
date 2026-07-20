"""Release-boundary gates for Bulla 0.44.0."""

from __future__ import annotations

from pathlib import Path

import bulla


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
        "Build sdist and wheel",
        "Verify source, wheel, and sdist runtime parity",
        "Publish to PyPI with Trusted Publishing attestations",
        "Verify PyPI accepted the exact artifacts and publisher identity",
        "Mint signed post-publication release receipt",
        "Attach receipt to durable GitHub release",
    )
    positions = [workflow.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "test -n \"$BULLA_RELEASE_KEY\"" in workflow
    assert "--repository jkomkov/bulla" in workflow


def test_experimental_modules_are_not_reexported_from_stable_root() -> None:
    assert all("experimental" not in name for name in bulla.__all__)
