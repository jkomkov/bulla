"""Build sources remain identical to the immutable exact-commit materialization."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_source_materialization",
    ROOT / "scripts/verify_release_source_materialization.py",
)
assert SPEC is not None and SPEC.loader is not None
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    reference = tmp_path / "reference"
    candidate = tmp_path / "candidate"
    for root in (reference, candidate):
        (root / "nested").mkdir(parents=True)
        (root / "README.md").write_bytes(b"exact source\n")
        (root / "nested" / "module.py").write_bytes(b"VALUE = 1\n")
    return reference, candidate


def test_exact_materialization_has_one_deterministic_inventory(tmp_path: Path) -> None:
    reference, candidate = _sources(tmp_path)
    result = SOURCE.verify(reference, candidate)
    assert result["algorithm"] == "sha256-posix-path-mode-content-v1"
    assert result["files"] == 2
    assert len(result["sha256"]) == 64


def test_mutation_after_initial_clean_check_fails_the_post_build_check(
    tmp_path: Path,
) -> None:
    reference, candidate = _sources(tmp_path)
    SOURCE.verify(reference, candidate)

    (candidate / "nested" / "module.py").write_bytes(b"VALUE = 2\n")

    with pytest.raises(
        SOURCE.SourceMaterializationError,
        match="differs from immutable exact-commit materialization",
    ):
        SOURCE.verify(reference, candidate)


def test_extra_file_or_symlink_is_not_an_exact_materialization(tmp_path: Path) -> None:
    reference, candidate = _sources(tmp_path)
    (candidate / "untracked.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(SOURCE.SourceMaterializationError, match="inventory differs"):
        SOURCE.verify(reference, candidate)

    candidate, reference = reference, candidate
    link = candidate / "link"
    try:
        link.symlink_to(candidate / "README.md")
    except (NotImplementedError, OSError):
        pytest.skip("platform does not permit a local symlink regression")
    with pytest.raises(SOURCE.SourceMaterializationError, match="symlink"):
        SOURCE.source_inventory(candidate)
