"""Current corporate identity and exact documentation-only release boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from test_package_entry import _protected_source_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_rebrand_preserves_published_runtime_and_formats():
    manifest = json.loads((ROOT / "releases/corporate-rebrand-0.49.4-allowed-differences.json").read_text())
    sources = _protected_source_bytes(ROOT, manifest["protected_scopes"], manifest["baseline_scope_exclusions"])
    version = sources.pop("src/bulla/__init__.py")
    assert hashlib.sha256(version).hexdigest() == manifest["version_file_after"]
    assert hashlib.sha256(version.replace(b'__version__ = "0.49.4"', b'__version__ = "0.49.3"')).hexdigest() == manifest["version_file_before"]
    material = "".join(f"{hashlib.sha256(raw).hexdigest()}  {name}\n" for name, raw in sorted(sources.items())).encode()
    assert len(sources) == manifest["protected_source_members"]
    assert "sha256:" + hashlib.sha256(material).hexdigest() == manifest["protected_source_root"]
    if (ROOT / ".git").exists():
        names = subprocess.check_output(["git", "diff", "--name-only", manifest["baseline_source"], "HEAD"], cwd=ROOT, text=True).splitlines()
        assert set(names) <= set(manifest["allowed_changed_paths"])
        # Protect even source-only material excluded from installed archives.
        changes = subprocess.check_output(["git", "diff", "--name-only", manifest["baseline_source"], "HEAD", "--", "src", "spec"], cwd=ROOT, text=True).splitlines()
        assert changes == ["src/bulla/__init__.py"]


def test_current_brand_and_historical_identity_are_separate():
    for name in ("README.md", "GOVERNANCE.md", "NOTICE", "docs/CAPABILITIES.md", "docs/sa-ra.svg", "docs/sa-ra-favicon.svg", ".github/ISSUE_TEMPLATE/external_reliance_candidate.yml", ".github/ISSUE_TEMPLATE/independent_witness_candidate.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "Glyph Standard" not in text and "glyphstandard.com" not in text, name
    assert "Bulla Labs is operated by Bulla Labs, Inc." in (ROOT / "README.md").read_text()
    assert "Copyright 2025 John Komkov" in (ROOT / "NOTICE").read_text()
    assert '"authorized_runtime_patches"' in (ROOT / "releases/package-entry-0.49.3-allowed-differences.json").read_text()
    workflow = (ROOT / ".github/workflows/release-preflight.yml").read_text()
    assert workflow.count("tests/test_corporate_rebrand.py") == 2
