"""The package-entry patch changes presentation, not released receipt behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_package_entry_checks_run_in_compatibility_and_installed_preflight() -> None:
    workflow = (ROOT / ".github/workflows/release-preflight.yml").read_text()
    for name in ("test_package_entry.py", "test_public_front_door.py", "test_readme_examples.py"):
        assert workflow.count("tests/" + name) == 2


def test_protected_package_sources_match_published_0492() -> None:
    manifest = json.loads((ROOT / "releases/package-entry-0.49.3-allowed-differences.json").read_text())
    # The sdist deliberately excludes source-only research. This commitment
    # covers the unchanged shipped sources and specifications in either tree.
    exclusions = manifest["baseline_scope_exclusions"]
    rows = []
    for scope in manifest["protected_scopes"]:
        for path in (ROOT / scope).rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in path.parts or any(relative.startswith(p) for p in exclusions):
                continue
            assert not path.is_symlink(), relative
            if not path.is_file() or relative == "src/bulla/__init__.py":
                continue
            rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    material = "".join(f"{digest}  {path}\n" for path, digest in sorted(rows)).encode()
    assert len(rows) == manifest["protected_source_members"]
    assert "sha256:" + hashlib.sha256(material).hexdigest() == manifest["protected_source_root"]
    assert hashlib.sha256((ROOT / "src/bulla/__init__.py").read_bytes()).hexdigest() == manifest["version_file_after"]


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
