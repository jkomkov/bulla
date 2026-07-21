#!/usr/bin/env python3
"""Verify that wheel and sdist carry identical runtime package bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_runtime(root: Path) -> dict[str, bytes]:
    base = root / "src/bulla"
    return {
        path.relative_to(root / "src").as_posix(): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def wheel_runtime(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name)
            for name in sorted(archive.namelist())
            if name.startswith("bulla/") and not name.endswith("/")
        }


def sdist_runtime(path: Path, version: str) -> dict[str, bytes]:
    prefix = f"bulla-{version}/src/"
    with tarfile.open(path, "r:gz") as archive:
        result: dict[str, bytes] = {}
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith(prefix + "bulla/"):
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read {member.name}")
                result[member.name[len(prefix):]] = extracted.read()
        return dict(sorted(result.items()))


def compare(expected: dict[str, bytes], actual: dict[str, bytes], label: str) -> None:
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(f"{label} path mismatch: missing={missing} extra={extra}")
    mismatches = [name for name in expected if expected[name] != actual[name]]
    if mismatches:
        raise ValueError(f"{label} byte mismatch: {mismatches}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    init_text = (args.root / "src/bulla/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    if match is None:
        raise SystemExit("cannot determine package version")
    version = match.group(1)
    wheels = sorted(args.dist.glob(f"bulla-{version}-*.whl"))
    sdists = sorted(args.dist.glob(f"bulla-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("expected exactly one wheel and one sdist")
    source = source_runtime(args.root)
    wheel = wheel_runtime(wheels[0])
    sdist = sdist_runtime(sdists[0], version)
    try:
        compare(source, wheel, "wheel")
        compare(source, sdist, "sdist")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    aggregate = "".join(f"{sha(source[name])}  {name}\n" for name in source)
    print(
        json.dumps(
            {
                "files": len(source),
                "ok": True,
                "runtime_surface_sha256": sha(aggregate.encode("utf-8")),
                "sdist_sha256": sha(sdists[0].read_bytes()),
                "version": version,
                "wheel_sha256": sha(wheels[0].read_bytes()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
