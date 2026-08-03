#!/usr/bin/env python3
"""Build the deterministic ActionReceipt v0.2 verification kit.

The kit packages the frozen v0.2 specification, a zero-dependency checker, and
constructed vectors. It does not define a new receipt or bundle wire format.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
BULLA_ROOT = HERE.parent
ARCHIVE_NAME = "action-receipt-v0.2-verification-kit.zip"
DEFAULT_OUT = HERE / "dist" / ARCHIVE_NAME
PACKAGE_OUT = BULLA_ROOT / "src" / "bulla" / "data" / ARCHIVE_NAME
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
MANIFEST_PATH = "MANIFEST.json"
MANIFEST_DIGEST_PATH = "MANIFEST.sha256"
MAX_ARCHIVE_MEMBERS = 128
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024

SOURCE_MEMBERS: dict[str, tuple[Path, str]] = {
    "COMPATIBILITY.md": (HERE / "COMPATIBILITY.md", "compatibility-checklist"),
    "IMPLEMENTATION-CHECKLIST.md": (
        HERE / "IMPLEMENTATION-CHECKLIST.md",
        "implementation-checklist",
    ),
    "LICENSE": (BULLA_ROOT / "LICENSE", "license"),
    "NOTICE": (BULLA_ROOT / "NOTICE", "notice"),
    "README.md": (HERE / "verification-kit" / "README.md", "kit-instructions"),
    "claims-file/README.md": (
        HERE / "verification-kit" / "claims-file" / "README.md",
        "constructed-claims-file",
    ),
    "claims-file/expected-local-verdict.json": (
        HERE / "verification-kit" / "claims-file" / "expected-local-verdict.json",
        "expected-dimensional-verdict",
    ),
    "claims-file/receipt.json": (
        HERE / "vectors" / "payment-authorization.json",
        "constructed-payment-receipt",
    ),
    "spec/action-receipt-v0.2.md": (
        HERE / "action-receipt-v0.2.md",
        "normative-specification",
    ),
    "spec/action-receipt-v0.2.schema.json": (
        HERE / "action-receipt-v0.2.schema.json",
        "normative-schema",
    ),
    "vectors/convention-receipt.json": (
        HERE / "vectors" / "convention-receipt.json",
        "grounding-vector",
    ),
    "vectors/independent_check.py": (
        HERE / "vectors" / "independent_check.py",
        "zero-dependency-checker",
    ),
    "vectors/malformed-executable.json": (
        HERE / "vectors" / "malformed-executable.json",
        "malformed-vector",
    ),
    "vectors/payment-authorization.json": (
        HERE / "vectors" / "payment-authorization.json",
        "constructed-payment-vector",
    ),
    "vectors/signed-authorized.json": (
        HERE / "vectors" / "signed-authorized.json",
        "nonnormative-signed-vector",
    ),
    "vectors/tampered-convention.json": (
        HERE / "vectors" / "tampered-convention.json",
        "tampered-vector",
    ),
    "vectors/tampered-evidence.json": (
        HERE / "vectors" / "tampered-evidence.json",
        "tampered-vector",
    ),
    "vectors/tampered-timestamp.json": (
        HERE / "vectors" / "tampered-timestamp.json",
        "occurrence-boundary-vector",
    ),
    "vectors/valid-release.json": (
        HERE / "vectors" / "valid-release.json",
        "valid-vector",
    ),
    "vectors/witness-canon2.json": (
        HERE / "vectors" / "witness-canon2.json",
        "witness-vector",
    ),
    "vectors/witness-legacy-v1.json": (
        HERE / "vectors" / "witness-legacy-v1.json",
        "legacy-vector",
    ),
    "verify.py": (HERE / "verification-kit" / "verify.py", "kit-checker"),
}
VECTOR_NAMES = {
    PurePosixPath(name).name
    for name in SOURCE_MEMBERS
    if name.startswith("vectors/") and name.endswith(".json")
}


class KitBuildError(ValueError):
    """The requested kit cannot be represented safely and deterministically."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_path(name: str) -> str:
    if not name or "\\" in name or name.startswith("/"):
        raise KitBuildError(f"unsafe archive member: {name!r}")
    pure = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        pure.is_absolute()
        or windows.drive
        or windows.root
        or windows.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise KitBuildError(f"unsafe archive member: {name!r}")
    canonical = pure.as_posix()
    if canonical != name:
        raise KitBuildError(f"noncanonical archive member: {name!r}")
    return canonical


def _regular_file_bytes(source: Path, name: str) -> bytes:
    if source.is_symlink():
        raise KitBuildError(f"symlink input is forbidden: {name}")
    try:
        mode = source.stat().st_mode
    except FileNotFoundError as exc:
        raise KitBuildError(f"missing kit input: {name}") from exc
    if not stat.S_ISREG(mode):
        raise KitBuildError(f"non-regular kit input: {name}")
    return source.read_bytes()


def _expected_bytes() -> bytes:
    expected = json.loads((HERE / "vectors" / "expected.json").read_text(encoding="utf-8"))
    selected = {name: expected[name] for name in sorted(VECTOR_NAMES)}
    return (json.dumps(selected, indent=2, sort_keys=True) + "\n").encode("utf-8")


def payload_members() -> dict[str, tuple[bytes, str]]:
    members = {
        _canonical_path(name): (_regular_file_bytes(source, name), role)
        for name, (source, role) in SOURCE_MEMBERS.items()
    }
    members["vectors/expected.json"] = (_expected_bytes(), "expected-dimensional-verdicts")
    folded: dict[str, str] = {}
    for name in sorted(members):
        folded_name = name.casefold()
        if folded_name in folded:
            raise KitBuildError(
                f"case-folding collision: {folded[folded_name]!r} and {name!r}"
            )
        folded[folded_name] = name
    return members


def _manifest(members: dict[str, tuple[bytes, str]]) -> bytes:
    document = {
        "format": "bulla.action-receipt-v0.2-verification-kit/1",
        "authenticates": "payload-members-only",
        "members": [
            {
                "path": name,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "role": role,
            }
            for name, (payload, role) in sorted(members.items())
        ],
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.internal_attr = 0
    return info


def validate_archive(path: Path) -> str:
    """Fail closed on unsafe structure or any manifest/payload mismatch."""
    seen: set[str] = set()
    folded: set[str] = set()
    payloads: dict[str, bytes] = {}
    with ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise KitBuildError("archive member limit exceeded")
        if [info.filename for info in infos] != sorted(info.filename for info in infos):
            raise KitBuildError("archive members are not lexicographically ordered")
        total = 0
        for info in infos:
            name = _canonical_path(info.filename)
            if name in seen:
                raise KitBuildError(f"duplicate archive member: {name}")
            if name.casefold() in folded:
                raise KitBuildError(f"case-folding archive collision: {name}")
            seen.add(name)
            folded.add(name.casefold())
            mode = info.external_attr >> 16
            if info.compress_type != ZIP_STORED:
                raise KitBuildError(f"compressed archive member: {name}")
            if info.date_time != FIXED_TIME:
                raise KitBuildError(f"non-deterministic timestamp: {name}")
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o644:
                raise KitBuildError(f"non-regular or noncanonical mode: {name}")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise KitBuildError("archive uncompressed-byte limit exceeded")
            payloads[name] = archive.read(info)
    if set(CONTROL := {MANIFEST_PATH, MANIFEST_DIGEST_PATH}) - set(payloads):
        raise KitBuildError("manifest controls are missing")
    try:
        manifest_bytes = payloads[MANIFEST_PATH]
        claimed, marker = payloads[MANIFEST_DIGEST_PATH].decode("ascii").rstrip("\n").split("  ", 1)
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise KitBuildError("manifest controls are malformed") from exc
    if marker != MANIFEST_PATH or claimed != _sha256(manifest_bytes):
        raise KitBuildError("manifest digest differs")
    if set(manifest) != {"format", "authenticates", "members"}:
        raise KitBuildError("manifest shape differs")
    if (
        manifest["format"] != "bulla.action-receipt-v0.2-verification-kit/1"
        or manifest["authenticates"] != "payload-members-only"
        or not isinstance(manifest["members"], list)
    ):
        raise KitBuildError("manifest contract differs")
    expected_names: list[str] = []
    for row in manifest["members"]:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "role"}:
            raise KitBuildError("manifest member shape differs")
        name = _canonical_path(row["path"])
        if name in CONTROL or name not in payloads:
            raise KitBuildError(f"manifest payload is absent or reserved: {name}")
        payload = payloads[name]
        if row["bytes"] != len(payload) or row["sha256"] != _sha256(payload):
            raise KitBuildError(f"manifest payload differs: {name}")
        if not isinstance(row["role"], str) or not row["role"]:
            raise KitBuildError(f"manifest role is absent: {name}")
        expected_names.append(name)
    if expected_names != sorted(expected_names) or len(expected_names) != len(set(expected_names)):
        raise KitBuildError("manifest members are not unique and ordered")
    if set(payloads) != set(expected_names) | CONTROL:
        raise KitBuildError("archive member set differs from manifest")
    return _sha256(path.read_bytes())


def build(out: Path) -> tuple[Path, str]:
    members = payload_members()
    manifest = _manifest(members)
    archive_members = dict(members)
    archive_members[MANIFEST_PATH] = (manifest, "manifest")
    archive_members[MANIFEST_DIGEST_PATH] = (
        f"{_sha256(manifest)}  {MANIFEST_PATH}\n".encode("ascii"),
        "manifest-digest",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for name, (payload, _role) in sorted(archive_members.items()):
            archive.writestr(_zip_info(name), payload)
    digest = validate_archive(out)
    out.with_name(out.name + ".sha256").write_text(
        f"{digest}  {out.name}\n", encoding="ascii", newline="\n"
    )
    return out, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--package-out",
        type=Path,
        default=PACKAGE_OUT,
        help="Second byte-identical copy embedded in wheel and sdist",
    )
    args = parser.parse_args()
    out, digest = build(args.out)
    if args.package_out != args.out:
        args.package_out.parent.mkdir(parents=True, exist_ok=True)
        args.package_out.write_bytes(out.read_bytes())
        args.package_out.with_name(args.package_out.name + ".sha256").write_text(
            f"{digest}  {args.package_out.name}\n", encoding="ascii", newline="\n"
        )
    print(f"{out}  sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
