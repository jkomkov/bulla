#!/usr/bin/env python3
"""Compare a build source with an immutable exact-commit materialization."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat


ALGORITHM = "sha256-posix-path-mode-content-v1"


class SourceMaterializationError(RuntimeError):
    """A build source is not the exact immutable source inventory."""


def source_inventory(root: Path) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise SourceMaterializationError(f"source root is not one directory: {root}")
    records: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            raise SourceMaterializationError(
                f"source materialization contains a symlink: {relative}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceMaterializationError(
                f"source materialization contains a non-regular member: {relative}"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append((relative, stat.S_IMODE(metadata.st_mode), digest))
    payload = "".join(
        f"{mode:04o} {digest} {relative}\n"
        for relative, mode, digest in records
    ).encode("utf-8")
    return {
        "algorithm": ALGORITHM,
        "files": len(records),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def verify(reference: Path, candidate: Path) -> dict[str, object]:
    expected = source_inventory(reference)
    actual = source_inventory(candidate)
    if actual != expected:
        raise SourceMaterializationError(
            "build source inventory differs from immutable exact-commit materialization: "
            f"expected {expected}, got {actual}"
        )
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.reference, args.candidate)
    except (OSError, SourceMaterializationError) as exc:
        print(f"release-source: {exc}")
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
