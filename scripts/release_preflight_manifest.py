#!/usr/bin/env python3
"""Freeze and verify the exact evidence produced before release identity exists."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


SCHEMA = "bulla.release-preflight/0.1"
REPOSITORY = "jkomkov/bulla"
WORKFLOW_PATH = ".github/workflows/release-preflight.yml"
WORKFLOW_REF = "refs/heads/main"
MANIFEST_NAME = "release-preflight-manifest.json"
COMPATIBILITY_MATRIX = [
    {"os": "ubuntu-latest", "python": "3.10"},
    {"os": "ubuntu-latest", "python": "3.11"},
    {"os": "ubuntu-latest", "python": "3.12"},
    {"os": "ubuntu-latest", "python": "3.13"},
    {"os": "macos-latest", "python": "3.12"},
    {"os": "windows-latest", "python": "3.12"},
]
SUMMARY_NAMES = (
    "pytest-summary.txt",
    "installed-summary.txt",
    "lifecycle-summary.txt",
)
FIXED_EVIDENCE_NAMES = (
    "action-receipt-v0.2-verification-kit.zip",
    "action-receipt-v0.2-verification-kit.zip.sha256",
    *SUMMARY_NAMES,
)


class ManifestError(RuntimeError):
    """The release-preflight evidence is incomplete or does not match identity."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, int | str]:
    if not path.is_file() or path.is_symlink():
        raise ManifestError(f"preflight member is not one regular file: {path.name}")
    return {"sha256": _sha256(path), "size": path.stat().st_size}


def _distribution_names(version: str) -> tuple[str, str]:
    return (
        f"bulla-{version}-py3-none-any.whl",
        f"bulla-{version}.tar.gz",
    )


def _validate_identity(
    version: str,
    source_commit: str,
    source_tree_sha256: str,
    source_date_epoch: int,
    run_id: int,
) -> None:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ManifestError("version must be MAJOR.MINOR.PATCH")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ManifestError("source commit must be lowercase 40-hex")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", source_tree_sha256) is None:
        raise ManifestError("source tree digest must be one sha256 commitment")
    if (
        isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch <= 0
    ):
        raise ManifestError("source date epoch must be a positive integer")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ManifestError("preflight run ID must be a positive integer")


def _directory_records(path: Path, expected: set[str]) -> dict[str, dict[str, int | str]]:
    if not path.is_dir() or path.is_symlink():
        raise ManifestError(f"preflight directory is unavailable: {path}")
    members = {member.name for member in path.iterdir()}
    if members != expected:
        raise ManifestError(
            f"preflight inventory mismatch: expected {sorted(expected)}, got {sorted(members)}"
        )
    return {name: _file_record(path / name) for name in sorted(expected)}


def _last_nonempty_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ManifestError(f"test summary is not UTF-8: {path.name}") from exc
    nonempty = [line for line in lines if line.strip()]
    if not nonempty:
        raise ManifestError(f"test summary is empty: {path.name}")
    return nonempty[-1]


def _strict_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ManifestError("preflight manifest is not one bounded regular file")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ManifestError(f"duplicate manifest key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("preflight manifest is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ManifestError("preflight manifest must be an object")
    return value


def write_manifest(
    *,
    artifacts: Path,
    build_a: Path,
    build_b: Path,
    out: Path,
    version: str,
    source_commit: str,
    source_tree_sha256: str,
    source_date_epoch: int,
    run_id: int,
) -> dict[str, Any]:
    _validate_identity(
        version, source_commit, source_tree_sha256, source_date_epoch, run_id
    )
    distributions = set(_distribution_names(version))
    expected_artifacts = distributions | set(FIXED_EVIDENCE_NAMES)
    artifact_records = _directory_records(artifacts, expected_artifacts)
    build_records: dict[str, dict[str, dict[str, int | str]]] = {}
    for label, directory in (("a", build_a), ("b", build_b)):
        build_records[label] = _directory_records(directory, distributions)
    if build_records["a"] != build_records["b"]:
        raise ManifestError("the two preflight builds are not byte-identical")
    for name in distributions:
        if artifact_records[name] != build_records["a"][name]:
            raise ManifestError(f"retained artifact differs from reproducible build: {name}")

    summaries = {
        name: {"final_line": _last_nonempty_line(artifacts / name), "path": name}
        for name in SUMMARY_NAMES
    }
    manifest: dict[str, Any] = {
        "artifacts": artifact_records,
        "builds": build_records,
        "compatibility_matrix": COMPATIBILITY_MATRIX,
        "preflight_run_id": run_id,
        "repository": REPOSITORY,
        "schema": SCHEMA,
        "source_commit": source_commit,
        "source_date_epoch": source_date_epoch,
        "source_tree_sha256": source_tree_sha256,
        "tests": summaries,
        "version": version,
        "workflow_path": WORKFLOW_PATH,
        "workflow_ref": WORKFLOW_REF,
    }
    if out.parent.resolve() != artifacts.resolve() or out.name != MANIFEST_NAME:
        raise ManifestError("manifest must use its fixed name inside the artifact directory")
    out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify_manifest(
    *,
    artifacts: Path,
    manifest_path: Path,
    version: str,
    source_commit: str,
    source_tree_sha256: str,
    source_date_epoch: int,
    run_id: int,
) -> dict[str, Any]:
    _validate_identity(
        version, source_commit, source_tree_sha256, source_date_epoch, run_id
    )
    if (
        manifest_path.parent.resolve() != artifacts.resolve()
        or manifest_path.name != MANIFEST_NAME
    ):
        raise ManifestError("manifest must use its fixed name inside the artifact directory")
    manifest = _strict_json(manifest_path)
    expected_keys = {
        "artifacts",
        "builds",
        "compatibility_matrix",
        "preflight_run_id",
        "repository",
        "schema",
        "source_commit",
        "source_date_epoch",
        "source_tree_sha256",
        "tests",
        "version",
        "workflow_path",
        "workflow_ref",
    }
    if set(manifest) != expected_keys:
        raise ManifestError("preflight manifest fields differ from the frozen schema")
    identity = {
        "preflight_run_id": run_id,
        "repository": REPOSITORY,
        "schema": SCHEMA,
        "source_commit": source_commit,
        "source_date_epoch": source_date_epoch,
        "source_tree_sha256": source_tree_sha256,
        "version": version,
        "workflow_path": WORKFLOW_PATH,
        "workflow_ref": WORKFLOW_REF,
    }
    if any(manifest.get(key) != value for key, value in identity.items()):
        raise ManifestError("preflight manifest identity differs from the requested release")
    if manifest.get("compatibility_matrix") != COMPATIBILITY_MATRIX:
        raise ManifestError("preflight compatibility matrix differs from the release contract")

    distributions = set(_distribution_names(version))
    expected_files = distributions | set(FIXED_EVIDENCE_NAMES)
    actual_records = _directory_records(
        artifacts, expected_files | {MANIFEST_NAME}
    )
    actual_records.pop(MANIFEST_NAME)
    if manifest.get("artifacts") != actual_records:
        raise ManifestError("preflight artifact bytes differ from the frozen manifest")

    builds = manifest.get("builds")
    if not isinstance(builds, dict) or set(builds) != {"a", "b"}:
        raise ManifestError("preflight manifest lacks the two reproducible builds")
    expected_build_records = {name: actual_records[name] for name in sorted(distributions)}
    if builds["a"] != expected_build_records or builds["b"] != expected_build_records:
        raise ManifestError("reproducible build records differ from retained artifacts")

    expected_tests = {
        name: {"final_line": _last_nonempty_line(artifacts / name), "path": name}
        for name in SUMMARY_NAMES
    }
    if manifest.get("tests") != expected_tests:
        raise ManifestError("test summaries differ from the frozen manifest")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("write", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--artifacts", type=Path, required=True)
        subparser.add_argument("--version", required=True)
        subparser.add_argument("--source-commit", required=True)
        subparser.add_argument("--source-tree-sha256", required=True)
        subparser.add_argument("--source-date-epoch", type=int, required=True)
        subparser.add_argument("--run-id", type=int, required=True)
    write_parser = subparsers.choices["write"]
    write_parser.add_argument("--build-a", type=Path, required=True)
    write_parser.add_argument("--build-b", type=Path, required=True)
    write_parser.add_argument("--out", type=Path, required=True)
    verify_parser = subparsers.choices["verify"]
    verify_parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    common = {
        "artifacts": args.artifacts,
        "version": args.version,
        "source_commit": args.source_commit,
        "source_tree_sha256": args.source_tree_sha256,
        "source_date_epoch": args.source_date_epoch,
        "run_id": args.run_id,
    }
    try:
        if args.command == "write":
            result = write_manifest(
                **common,
                build_a=args.build_a,
                build_b=args.build_b,
                out=args.out,
            )
        else:
            result = verify_manifest(
                **common,
                manifest_path=args.manifest,
            )
    except (ManifestError, OSError) as exc:
        print(f"release-preflight: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
