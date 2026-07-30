#!/usr/bin/env python3
"""Verify built Bulla archives against the declared distribution boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import zipfile


class DistributionError(RuntimeError):
    """The built archive does not match the declared distribution policy."""


def _safe_member(name: str) -> str:
    components = name.split("/")
    path = PurePosixPath(name)
    canonical = path.as_posix()
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or any(component in {"", ".", ".."} for component in components)
        or name != canonical
    ):
        raise DistributionError(f"unsafe archive member: {name!r}")
    return canonical


def _wheel_members(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        members: set[str] = set()
        for info in archive.infolist():
            name = _safe_member(info.filename)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise DistributionError(f"wheel contains a non-regular member: {name}")
            if name in members:
                raise DistributionError(f"wheel contains a duplicate member: {name}")
            members.add(name)
    return members


def _sdist_members(path: Path, release: str) -> set[str]:
    root = f"bulla-{release}/"
    members: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        for info in archive.getmembers():
            name = _safe_member(info.name)
            if not info.isfile():
                raise DistributionError(f"sdist contains a non-regular member: {name}")
            if not name.startswith(root):
                raise DistributionError(
                    f"sdist member is outside the expected {root!r} root: {name}"
                )
            relative = name[len(root) :]
            if relative in members:
                raise DistributionError(f"sdist contains a duplicate member: {relative}")
            members.add(relative)
    return members


def _require_members(
    actual: set[str], required: list[str], archive_label: str
) -> None:
    missing = sorted(set(required) - actual)
    if missing:
        raise DistributionError(
            f"{archive_label} is missing required members: {', '.join(missing)}"
        )


def _forbid_prefixes(
    actual: set[str], prefixes: list[str], archive_label: str
) -> None:
    found = sorted(
        member
        for member in actual
        if any(member == prefix or member.startswith(prefix) for prefix in prefixes)
    )
    if found:
        raise DistributionError(
            f"{archive_label} contains source-only members: {', '.join(found)}"
        )


def _require_exact_member_set(
    actual: set[str], commitment: object, archive_label: str
) -> None:
    if (
        not isinstance(commitment, dict)
        or set(commitment) != {"algorithm", "count", "sha256"}
        or commitment.get("algorithm") != "sha256-sorted-posix-lines-v1"
        or not isinstance(commitment.get("count"), int)
        or isinstance(commitment.get("count"), bool)
        or not isinstance(commitment.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", commitment["sha256"])
    ):
        raise DistributionError(
            f"{archive_label} has an invalid member-set commitment"
        )
    canonical = "".join(f"{member}\n" for member in sorted(actual)).encode("utf-8")
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if (
        len(actual) != commitment["count"]
        or actual_digest != commitment["sha256"]
    ):
        raise DistributionError(
            f"{archive_label} member set is undeclared: "
            f"expected {commitment['count']}/sha256:{commitment['sha256']}, "
            f"got {len(actual)}/sha256:{actual_digest}"
        )


def verify(policy_path: Path, dist: Path) -> dict[str, object]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1 or policy.get("package") != "bulla":
        raise DistributionError("unsupported distribution policy")
    release = policy.get("release")
    if not isinstance(release, str) or not re.fullmatch(r"\d+\.\d+\.\d+", release):
        raise DistributionError("distribution policy has an invalid release")

    wheels = sorted(dist.glob(f"bulla-{release}-*.whl"))
    sdists = sorted(dist.glob(f"bulla-{release}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise DistributionError(
            f"expected one wheel and one sdist for {release}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    wheel = _wheel_members(wheels[0])
    sdist = _sdist_members(sdists[0], release)
    dist_info = re.compile(rf"^bulla-{re.escape(release)}\.dist-info/")
    unexpected_wheel = sorted(
        member
        for member in wheel
        if not member.startswith("bulla/") and not dist_info.match(member)
    )
    if unexpected_wheel:
        raise DistributionError(
            "wheel contains members outside bulla and its dist-info: "
            + ", ".join(unexpected_wheel)
        )

    forbidden_modules = [
        module.replace(".", "/") + ".py" for module in policy["source_only_modules"]
    ]
    _require_members(wheel, policy["required_wheel_members"], "wheel")
    _forbid_prefixes(wheel, forbidden_modules, "wheel")
    _require_members(sdist, policy["required_sdist_members"], "sdist")
    _forbid_prefixes(sdist, policy["forbidden_sdist_prefixes"], "sdist")
    member_sets = policy.get("archive_member_sets")
    if not isinstance(member_sets, dict) or set(member_sets) != {"wheel", "sdist"}:
        raise DistributionError("distribution policy lacks exact archive commitments")
    _require_exact_member_set(wheel, member_sets["wheel"], "wheel")
    _require_exact_member_set(sdist, member_sets["sdist"], "sdist")

    return {
        "release": release,
        "wheel": wheels[0].name,
        "wheel_members": len(wheel),
        "sdist": sdists[0].name,
        "sdist_members": len(sdist),
        "source_only_modules_excluded": len(forbidden_modules),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy", type=Path, default=Path("distribution-policy.json")
    )
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        result = verify(args.policy, args.dist)
    except (DistributionError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"distribution-policy: {error}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
