"""Exact-byte contract for evidence created before any release identity."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_preflight_manifest",
    ROOT / "scripts/release_preflight_manifest.py",
)
assert SPEC is not None and SPEC.loader is not None
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)

VERSION = "0.49.2"
COMMIT = "a" * 40
TREE = "sha256:" + "b" * 64
EPOCH = 1_788_236_263
RUN_ID = 31_415_926


def _preflight_tree(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    artifacts = tmp_path / "artifacts"
    build_a = tmp_path / "build-a"
    build_b = tmp_path / "build-b"
    for directory in (artifacts, build_a, build_b):
        directory.mkdir(parents=True)
    for name, payload in (
        (f"bulla-{VERSION}-py3-none-any.whl", b"wheel"),
        (f"bulla-{VERSION}.tar.gz", b"sdist"),
    ):
        (build_a / name).write_bytes(payload)
        (build_b / name).write_bytes(payload)
        (artifacts / name).write_bytes(payload)
    (artifacts / "action-receipt-v0.2-verification-kit.zip").write_bytes(b"kit")
    (artifacts / "action-receipt-v0.2-verification-kit.zip.sha256").write_text(
        "digest  action-receipt-v0.2-verification-kit.zip\n", encoding="ascii"
    )
    for name in MANIFEST.SUMMARY_NAMES:
        (artifacts / name).write_text(f"details\n{name}: passed\n", encoding="utf-8")
    sources = [tmp_path / name for name in ("reference", "source-a", "source-b")]
    for source in sources:
        source.mkdir()
        (source / "source.txt").write_bytes(b"exact source\n")
    return (
        artifacts,
        build_a,
        build_b,
        *sources,
        artifacts / MANIFEST.MANIFEST_NAME,
    )


def _write(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    artifacts, build_a, build_b, reference, source_a, source_b, manifest = (
        _preflight_tree(tmp_path)
    )
    MANIFEST.write_manifest(
        artifacts=artifacts,
        build_a=build_a,
        build_b=build_b,
        reference_source=reference,
        source_a=source_a,
        source_b=source_b,
        out=manifest,
        version=VERSION,
        source_commit=COMMIT,
        source_tree_sha256=TREE,
        source_date_epoch=EPOCH,
        run_id=RUN_ID,
    )
    return artifacts, build_a, build_b, manifest


def _verify(artifacts: Path, manifest: Path, **changes: object) -> dict:
    values = {
        "artifacts": artifacts,
        "manifest_path": manifest,
        "version": VERSION,
        "source_commit": COMMIT,
        "source_tree_sha256": TREE,
        "source_date_epoch": EPOCH,
        "run_id": RUN_ID,
    }
    values.update(changes)
    return MANIFEST.verify_manifest(**values)


def test_manifest_binds_identity_matrix_two_builds_and_every_retained_byte(
    tmp_path: Path,
) -> None:
    artifacts, _, _, manifest_path = _write(tmp_path)
    manifest = _verify(artifacts, manifest_path)

    assert manifest["schema"] == "bulla.release-preflight/0.1"
    assert manifest["compatibility_matrix"] == MANIFEST.COMPATIBILITY_MATRIX
    assert manifest["builds"]["a"] == manifest["builds"]["b"]
    assert set(manifest["artifacts"]) == {
        f"bulla-{VERSION}-py3-none-any.whl",
        f"bulla-{VERSION}.tar.gz",
        "action-receipt-v0.2-verification-kit.zip",
        "action-receipt-v0.2-verification-kit.zip.sha256",
        *MANIFEST.SUMMARY_NAMES,
    }


def test_manifest_rejects_changed_artifact_or_unfrozen_member(tmp_path: Path) -> None:
    artifacts, _, _, manifest = _write(tmp_path)
    (artifacts / f"bulla-{VERSION}.tar.gz").write_bytes(b"changed")
    with pytest.raises(MANIFEST.ManifestError, match="bytes differ"):
        _verify(artifacts, manifest)

    artifacts, _, _, manifest = _write(tmp_path / "extra")
    (artifacts / "unreviewed.whl").write_bytes(b"extra")
    with pytest.raises(MANIFEST.ManifestError, match="inventory mismatch"):
        _verify(artifacts, manifest)


def test_manifest_rejects_post_slot_installed_summary_mutation(tmp_path: Path) -> None:
    artifacts, _, _, manifest = _write(tmp_path)
    (artifacts / "installed-summary.txt").write_text(
        "forged after the signed slot\n", encoding="utf-8"
    )
    with pytest.raises(MANIFEST.ManifestError, match="artifact bytes differ"):
        _verify(artifacts, manifest)


def test_manifest_rejects_nonreproducible_second_build(tmp_path: Path) -> None:
    artifacts, build_a, build_b, reference, source_a, source_b, manifest = (
        _preflight_tree(tmp_path)
    )
    (build_b / f"bulla-{VERSION}-py3-none-any.whl").write_bytes(b"different")
    with pytest.raises(MANIFEST.ManifestError, match="not byte-identical"):
        MANIFEST.write_manifest(
            artifacts=artifacts,
            build_a=build_a,
            build_b=build_b,
            reference_source=reference,
            source_a=source_a,
            source_b=source_b,
            out=manifest,
            version=VERSION,
            source_commit=COMMIT,
            source_tree_sha256=TREE,
            source_date_epoch=EPOCH,
            run_id=RUN_ID,
        )


def test_manifest_rejects_build_source_mutated_after_initial_check(
    tmp_path: Path,
) -> None:
    artifacts, build_a, build_b, reference, source_a, source_b, manifest = (
        _preflight_tree(tmp_path)
    )
    (source_a / "source.txt").write_bytes(b"mutated after pre-build check\n")
    with pytest.raises(MANIFEST.ManifestError, match="build source differs"):
        MANIFEST.write_manifest(
            artifacts=artifacts,
            build_a=build_a,
            build_b=build_b,
            reference_source=reference,
            source_a=source_a,
            source_b=source_b,
            out=manifest,
            version=VERSION,
            source_commit=COMMIT,
            source_tree_sha256=TREE,
            source_date_epoch=EPOCH,
            run_id=RUN_ID,
        )


def test_verifier_rejects_manifest_substitution_outside_artifact(tmp_path: Path) -> None:
    artifacts, _, _, manifest = _write(tmp_path)
    substitute = tmp_path / "substitute.json"
    substitute.write_bytes(manifest.read_bytes())
    with pytest.raises(MANIFEST.ManifestError, match="fixed name inside"):
        _verify(artifacts, substitute)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "0.49.3"),
        ("source_commit", "c" * 40),
        ("source_tree_sha256", "sha256:" + "d" * 64),
        ("source_date_epoch", EPOCH + 1),
        ("run_id", RUN_ID + 1),
    ),
)
def test_manifest_rejects_identity_rebinding(
    tmp_path: Path, field: str, value: object,
) -> None:
    artifacts, _, _, manifest = _write(tmp_path)
    with pytest.raises(MANIFEST.ManifestError, match="identity differs"):
        _verify(artifacts, manifest, **{field: value})
