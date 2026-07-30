#!/usr/bin/env python3
"""Verify that wheel and sdist carry identical runtime package bytes."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_member_name(raw: str, archive: str) -> str:
    path = PurePosixPath(raw)
    canonical = path.as_posix()
    if (
        not raw
        or path.is_absolute()
        or "\\" in raw
        or any(component in {"", ".", ".."} for component in raw.split("/"))
        or raw != canonical
    ):
        raise ValueError(f"{archive} contains a noncanonical member: {raw!r}")
    return canonical


def load_policy(root: Path) -> dict:
    return json.loads((root / "distribution-policy.json").read_text(encoding="utf-8"))


def source_runtime(root: Path, policy: dict) -> dict[str, bytes]:
    base = root / "src/bulla"
    excluded = {
        module.replace(".", "/") + ".py" for module in policy["source_only_modules"]
    }
    result = {
        path.relative_to(root / "src").as_posix(): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.relative_to(root / "src").as_posix() not in excluded
        )
    }
    result["bulla/data/distribution-policy.json"] = (
        root / "distribution-policy.json"
    ).read_bytes()
    return dict(sorted(result.items()))


def wheel_runtime(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        result: dict[str, bytes] = {}
        for info in archive.infolist():
            name = canonical_member_name(info.filename, "wheel")
            mode = info.external_attr >> 16
            if (
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or info.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
            ):
                raise ValueError(f"wheel contains an unsafe or non-regular member: {name}")
            if name in result:
                raise ValueError(f"wheel contains a duplicate member: {name}")
            result[name] = archive.read(info)
        return {
            name: result[name]
            for name in sorted(result)
            if name.startswith("bulla/")
        }


def sdist_runtime(path: Path, version: str) -> dict[str, bytes]:
    prefix = f"bulla-{version}/src/"
    policy_member = f"bulla-{version}/distribution-policy.json"
    with tarfile.open(path, "r:gz") as archive:
        result: dict[str, bytes] = {}
        seen: set[str] = set()
        for member in archive.getmembers():
            name = canonical_member_name(member.name, "sdist")
            if (
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or not member.isfile()
            ):
                raise ValueError(f"sdist contains an unsafe or non-regular member: {name}")
            if name in seen:
                raise ValueError(f"sdist contains a duplicate member: {name}")
            seen.add(name)
            if member.isfile() and member.name.startswith(prefix + "bulla/"):
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read {member.name}")
                result[member.name[len(prefix):]] = extracted.read()
            elif member.isfile() and member.name == policy_member:
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read {member.name}")
                result["bulla/data/distribution-policy.json"] = extracted.read()
        return dict(sorted(result.items()))


def _archive_bytes(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        result: dict[str, bytes] = {}
        for info in archive.infolist():
            name = canonical_member_name(info.filename, "wheel")
            mode = info.external_attr >> 16
            if (
                info.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or name in result
            ):
                raise ValueError(f"wheel member is not one unique regular file: {name}")
            result[name] = archive.read(info)
        return result


def _sdist_bytes(path: Path, version: str) -> dict[str, bytes]:
    prefix = f"bulla-{version}/"
    with tarfile.open(path, "r:gz") as archive:
        result: dict[str, bytes] = {}
        for member in archive.getmembers():
            name = canonical_member_name(member.name, "sdist")
            if not member.isfile() or not name.startswith(prefix):
                raise ValueError(f"sdist member is not one regular rooted file: {name}")
            relative = name[len(prefix):]
            if relative in result:
                raise ValueError(f"sdist contains a duplicate member: {relative}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"cannot read {member.name}")
            result[relative] = extracted.read()
        return result


def verify_full_archive_bytes(
    root: Path,
    wheel_path: Path,
    sdist_path: Path,
    version: str,
    policy: dict,
) -> None:
    sdist = _sdist_bytes(sdist_path, version)
    for relative, archived in sdist.items():
        if relative == "PKG-INFO":
            continue
        source = (root / relative).resolve()
        try:
            source.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"sdist source path escapes the repository: {relative}") from exc
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"sdist member has no regular source file: {relative}")
        if source.read_bytes() != archived:
            raise ValueError(f"sdist member differs from committed source bytes: {relative}")

    wheel = _archive_bytes(wheel_path)
    dist_info = f"bulla-{version}.dist-info"
    metadata = f"{dist_info}/METADATA"
    record = f"{dist_info}/RECORD"
    expected_non_runtime = {
        metadata,
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/NOTICE",
        record,
    }
    actual_non_runtime = {name for name in wheel if not name.startswith("bulla/")}
    if actual_non_runtime != expected_non_runtime:
        raise ValueError(
            "wheel dist-info membership mismatch: "
            f"missing={sorted(expected_non_runtime - actual_non_runtime)} "
            f"extra={sorted(actual_non_runtime - expected_non_runtime)}"
        )
    if wheel[metadata] != sdist.get("PKG-INFO"):
        raise ValueError("wheel METADATA and sdist PKG-INFO differ")
    expected_metadata_sha256 = (
        policy.get("generated_archive_bytes") or {}
    ).get("metadata_sha256")
    if (
        not isinstance(expected_metadata_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_metadata_sha256)
        or sha(wheel[metadata]) != expected_metadata_sha256
    ):
        raise ValueError("wheel METADATA differs from the committed release policy")
    if wheel[f"{dist_info}/licenses/LICENSE"] != (root / "LICENSE").read_bytes():
        raise ValueError("wheel LICENSE differs from committed source")
    if wheel[f"{dist_info}/licenses/NOTICE"] != (root / "NOTICE").read_bytes():
        raise ValueError("wheel NOTICE differs from committed source")
    expected_wheel = (
        "Wheel-Version: 1.0\n"
        "Generator: hatchling 1.31.0\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()
    if wheel[f"{dist_info}/WHEEL"] != expected_wheel:
        raise ValueError("wheel build metadata differs from the pinned backend contract")
    if wheel[f"{dist_info}/entry_points.txt"] != (
        "[console_scripts]\nbulla = bulla.cli:main\n"
    ).encode():
        raise ValueError("wheel entry points differ from the release contract")

    rows = list(csv.reader(io.StringIO(wheel[record].decode("utf-8"))))
    if len(rows) != len(wheel) or any(len(row) != 3 for row in rows):
        raise ValueError("wheel RECORD does not contain one row per member")
    record_names: set[str] = set()
    for name, digest, size in rows:
        if name in record_names or name not in wheel:
            raise ValueError(f"wheel RECORD contains an invalid member: {name}")
        record_names.add(name)
        if name == record:
            if digest or size:
                raise ValueError("wheel RECORD self-entry must omit digest and size")
            continue
        expected_digest = base64.urlsafe_b64encode(
            hashlib.sha256(wheel[name]).digest()
        ).decode("ascii").rstrip("=")
        if digest != f"sha256={expected_digest}" or size != str(len(wheel[name])):
            raise ValueError(f"wheel RECORD does not bind exact bytes: {name}")
    if record_names != set(wheel):
        raise ValueError("wheel RECORD member set differs from the archive")


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
    policy = load_policy(args.root)
    if policy.get("release") != version:
        raise SystemExit(
            f"distribution policy release {policy.get('release')!r} "
            f"does not match package version {version}"
        )
    source = source_runtime(args.root, policy)
    wheel = wheel_runtime(wheels[0])
    sdist = sdist_runtime(sdists[0], version)
    try:
        compare(source, wheel, "wheel")
        compare(source, sdist, "sdist")
        verify_full_archive_bytes(
            args.root, wheels[0], sdists[0], version, policy
        )
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
