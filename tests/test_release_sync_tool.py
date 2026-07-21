"""Tests for the explicit, noninteractive standalone release synchronizer."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_to_standalone.py"


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(root), *args), check=True, capture_output=True)


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Bulla Release Test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        message,
    )


def _repos(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    for root in (source, destination):
        _git(root, "init", "-b", "main")

    (source / "src/bulla").mkdir(parents=True)
    (source / "src/bulla/__init__.py").write_text('__version__ = "0.44.0"\n')
    (source / "pyproject.toml").write_text("[project]\nname='bulla'\n")
    _commit_all(source, "source")

    (destination / "old.txt").write_text("delete me\n")
    _commit_all(destination, "old standalone")
    _git(destination, "switch", "-c", "release/v0.44.0")
    return source, destination


def _run(
    source: Path,
    destination: Path,
    manifest: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--expected-branch",
            "release/v0.44.0",
            "--deletion-manifest",
            str(manifest),
            *extra,
        ),
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_dry_run_emits_deletion_manifest_without_mutation(tmp_path: Path) -> None:
    source, destination = _repos(tmp_path)
    manifest = tmp_path / "deletions.json"
    result = _run(source, destination, manifest)
    assert result.returncode == 0, result.stderr
    assert (destination / "old.txt").exists()
    payload = json.loads(manifest.read_text())
    assert payload["deletions"] == ["old.txt"]
    assert payload["applied"] is False


def test_sync_apply_deletes_and_proves_equal_tree(tmp_path: Path) -> None:
    source, destination = _repos(tmp_path)
    manifest = tmp_path / "deletions.json"
    result = _run(source, destination, manifest, "--apply")
    assert result.returncode == 0, result.stderr
    assert not (destination / "old.txt").exists()
    assert (destination / "src/bulla/__init__.py").read_bytes() == (
        source / "src/bulla/__init__.py"
    ).read_bytes()
    payload = json.loads(manifest.read_text())
    assert payload["applied"] is True


def test_sync_rejects_default_destination_branch(tmp_path: Path) -> None:
    source, destination = _repos(tmp_path)
    _git(destination, "switch", "main")
    manifest = tmp_path / "deletions.json"
    result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--expected-branch",
            "main",
            "--deletion-manifest",
            str(manifest),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "cannot be main or master" in result.stderr


def test_sync_rejects_manifest_inside_release_trees(tmp_path: Path) -> None:
    source, destination = _repos(tmp_path)
    result = _run(source, destination, source / "deletions.json")
    assert result.returncode == 2
    assert "outside the source and destination trees" in result.stderr
