#!/usr/bin/env python3
"""Synchronize a clean Bulla subtree into a clean standalone release branch.

Dry-run is the default. ``--apply`` copies the tree only after writing a
content-addressed deletion manifest, and then proves byte-for-byte equality of
the two release inventories. The tool never fetches, creates branches, tags,
publishes, or prompts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


EXCLUDED_NAMES = {
    ".DS_Store",
    ".git",
    ".pytest_cache",
    "__pycache__",
    "dist",
}
EXCLUDED_SUFFIXES = (".egg-info", ".pyc")


class SyncError(RuntimeError):
    """Raised when release preconditions or postconditions do not hold."""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SyncError(f"git {' '.join(args)} failed for {root}: {detail}")
    return result.stdout.strip()


def _is_excluded(relative: Path) -> bool:
    return any(
        part in EXCLUDED_NAMES or part.endswith(EXCLUDED_SUFFIXES)
        for part in relative.parts
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _is_excluded(relative) or path.is_dir():
            continue
        key = relative.as_posix()
        if path.is_symlink():
            result[key] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            result[key] = {
                "kind": "file",
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
    return result


def inventory_digest(value: dict[str, dict[str, Any]]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _require_clean_repo(root: Path, label: str) -> None:
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SyncError(f"{label} repository is not clean: {root}")


def _validate(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    source = Path(args.source).expanduser().resolve(strict=True)
    destination = Path(args.destination).expanduser().resolve(strict=True)
    manifest = Path(args.deletion_manifest).expanduser().resolve(strict=False)

    if source == destination or source in destination.parents or destination in source.parents:
        raise SyncError("source and destination must be disjoint directories")
    if (
        manifest in {source, destination}
        or source in manifest.parents
        or destination in manifest.parents
    ):
        raise SyncError("deletion manifest must be outside the source and destination trees")
    if not (source / "pyproject.toml").is_file() or not (source / "src/bulla").is_dir():
        raise SyncError(f"source is not a Bulla release subtree: {source}")
    if _git(destination, "rev-parse", "--show-toplevel") != str(destination):
        raise SyncError("destination must be the standalone repository root")

    branch = _git(destination, "branch", "--show-current")
    if args.expected_branch in {"main", "master"}:
        raise SyncError("expected release branch cannot be main or master")
    if branch != args.expected_branch:
        raise SyncError(
            f"destination is on {branch!r}; expected explicit release branch "
            f"{args.expected_branch!r}"
        )
    _require_clean_repo(source, "source")
    _require_clean_repo(destination, "destination")
    return source, destination, manifest


def _remove_destination_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy(source: Path, destination: Path, source_inventory: dict[str, dict[str, Any]]) -> None:
    destination_inventory = inventory(destination)
    for relative in sorted(set(destination_inventory) - set(source_inventory), reverse=True):
        _remove_destination_entry(destination / relative)

    for relative in sorted(source_inventory):
        source_path = source / relative
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists() or destination_path.is_symlink():
            _remove_destination_entry(destination_path)
        if source_path.is_symlink():
            destination_path.symlink_to(os.readlink(source_path))
        else:
            shutil.copy2(source_path, destination_path)

    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_dir() and path.name != ".git" and not any(path.iterdir()):
            path.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--deletion-manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    try:
        source, destination, manifest = _validate(args)
        source_inventory = inventory(source)
        destination_inventory = inventory(destination)
        deletions = sorted(set(destination_inventory) - set(source_inventory))
        payload = {
            "schema": "bulla.standalone-sync-deletions/0.1",
            "source": str(source),
            "destination": str(destination),
            "expected_branch": args.expected_branch,
            "source_inventory_sha256": inventory_digest(source_inventory),
            "destination_inventory_sha256_before": inventory_digest(destination_inventory),
            "deletions": deletions,
            "applied": bool(args.apply),
        }
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        if args.apply:
            _copy(source, destination, source_inventory)
            observed = inventory(destination)
            if observed != source_inventory:
                raise SyncError("post-sync standalone inventory does not equal source")

        print(json.dumps(payload, sort_keys=True))
        return 0
    except (OSError, SyncError) as error:
        print(f"sync-to-standalone: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
