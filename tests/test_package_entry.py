"""Bound the package-entry release and its authorized Windows claim repair."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_package_entry_checks_run_in_compatibility_and_installed_preflight() -> None:
    workflow = (ROOT / ".github/workflows/release-preflight.yml").read_text()
    for name in ("test_package_entry.py", "test_public_front_door.py", "test_readme_examples.py"):
        assert workflow.count("tests/" + name) == 2
    pr_workflow = (ROOT / ".github/workflows/bulla.yml").read_text()
    assert "windows-capture:" in pr_workflow
    assert "tests/test_capture_mcp.py" in pr_workflow


def _protected_source_bytes(root: Path, scopes: list[str], exclusions: list[str]) -> dict[str, bytes]:
    working = {}
    for scope in scopes:
        for path in (root / scope).rglob("*"):
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or any(relative.startswith(p) for p in exclusions):
                continue
            assert not path.is_symlink(), relative
            if path.is_file():
                working[relative] = path.read_bytes()
    if not (root / ".git").exists():
        # Release archives have no checkout conversion: compare exact bytes.
        return working
    # Windows Git can materialize text as CRLF. Check that tracked work is
    # unchanged under Git's checkout rules, then hash the canonical source
    # bytes used by the existing git-archive release build. Never normalize
    # arbitrary evidence/archive bytes ourselves or ignore new source files.
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *scopes], cwd=root, check=True)
    archived = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", "HEAD", *scopes],
        cwd=root, capture_output=True, check=True,
    ).stdout
    canonical = {}
    with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as archive:
        for member in archive:
            if member.isdir() or any(member.name.startswith(p) for p in exclusions):
                continue
            assert member.isfile(), member.name
            canonical[member.name] = archive.extractfile(member).read()
    assert set(canonical) == set(working), "protected source inventory differs from HEAD"
    return canonical


def test_protected_package_sources_match_published_0492() -> None:
    manifest = json.loads((ROOT / "releases/package-entry-0.49.3-allowed-differences.json").read_text(encoding="utf-8"))
    sources = _protected_source_bytes(ROOT, manifest["protected_scopes"], manifest["baseline_scope_exclusions"])
    version = sources.pop("src/bulla/__init__.py")
    patches = manifest["authorized_runtime_patches"]
    assert set(patches) == {"src/bulla/capture_mcp.py"}
    rows = []
    for name, raw in sources.items():
        digest = hashlib.sha256(raw).hexdigest()
        if name in patches:
            assert digest == patches[name]["after_sha256"]
            digest = patches[name]["before_sha256"]
        rows.append((name, digest))
    material = "".join(f"{digest}  {path}\n" for path, digest in sorted(rows)).encode()
    assert len(rows) == manifest["protected_source_members"]
    assert "sha256:" + hashlib.sha256(material).hexdigest() == manifest["protected_source_root"]
    assert hashlib.sha256(version).hexdigest() == manifest["version_file_after"]


def test_source_guard_allows_git_checkout_conversion_but_not_edits(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    file = root / "src/example.py"
    raw = b"value = 1\n"
    file.write_bytes(raw)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=root, check=True)
    subprocess.run(["git", "add", "src/example.py"], cwd=root, check=True)
    subprocess.run([
        "git", "-c", "user.name=Constructed test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / 'no-hooks'}",
        "commit", "--quiet", "-m", "Constructed source",
    ], cwd=root, check=True)
    file.unlink()  # Only the constructed fixture; force a fresh Git checkout.
    subprocess.run(["git", "checkout-index", "--", "src/example.py"], cwd=root, check=True)
    assert file.read_bytes() == b"value = 1\r\n"
    assert _protected_source_bytes(root, ["src"], []) == {"src/example.py": raw}
    file.write_bytes(b"value = 2\r\n")
    with pytest.raises(subprocess.CalledProcessError):
        _protected_source_bytes(root, ["src"], [])
    file.write_bytes(b"value = 1\r\n")
    (root / "src/untracked.py").write_bytes(b"unexpected = True\n")
    with pytest.raises(AssertionError, match="inventory"):
        _protected_source_bytes(root, ["src"], [])


def test_materialized_source_bytes_are_not_line_ending_normalized(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_bytes(b"value = 1\r\n")
    assert _protected_source_bytes(tmp_path, ["src"], []) == {"src/example.py": b"value = 1\r\n"}


def test_readme_demo_without_output_path_is_repeat_safe(tmp_path: Path) -> None:
    reports = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "bulla", "demo", "--format", "json"],
            cwd=tmp_path, capture_output=True, text=True, timeout=60, check=True,
        )
        report = json.loads(result.stdout)
        assert report["verification"]["record_integrity"] == "VERIFIED"
        assert report["verification"]["authority"] == "UNAUTHENTICATED"
        assert report["tamper_control"]["record_integrity"] == "FAILED"
        assert report["coverage"]["after"]["unreceipted_delta"] == ["pay_demo_043"]
        reports.append(report)
    assert reports[0]["output_directory"] != reports[1]["output_directory"]
    for report in reports:
        assert Path(report["output_directory"]).is_dir()
