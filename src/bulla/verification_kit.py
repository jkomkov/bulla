"""Export the immutable ActionReceipt v0.2 verification kit."""

from __future__ import annotations

import hashlib
import io
from importlib import resources
import json
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import stat
from zipfile import ZIP_STORED, BadZipFile, ZipFile


ARCHIVE_NAME = "action-receipt-v0.2-verification-kit.zip"
MANIFEST_PATH = "MANIFEST.json"
MANIFEST_DIGEST_PATH = "MANIFEST.sha256"
MAX_ARCHIVE_MEMBERS = 128
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_CONTAINER_BYTES = 20 * 1024 * 1024
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class VerificationKitError(ValueError):
    """A retained verification kit failed its structural or digest contract."""


def _safe_member(name: str) -> str:
    pure = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        not name
        or "\\" in name
        or pure.is_absolute()
        or windows.drive
        or windows.root
        or windows.is_absolute()
        or pure.as_posix() != name
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise VerificationKitError(f"unsafe archive member: {name!r}")
    return name


def validate_verification_kit(payload: bytes) -> tuple[str, dict[str, bytes]]:
    """Validate and return the exact regular payload members of one kit."""
    if len(payload) > MAX_ARCHIVE_CONTAINER_BYTES:
        raise VerificationKitError("archive container-byte limit exceeded")
    try:
        archive = ZipFile(io.BytesIO(payload), "r")
    except (BadZipFile, OSError) as exc:
        raise VerificationKitError(f"verification kit is not a readable ZIP: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise VerificationKitError("archive member limit exceeded")
        names = [info.filename for info in infos]
        if names != sorted(names):
            raise VerificationKitError("archive members are not lexicographically ordered")
        if len(names) != len(set(names)):
            raise VerificationKitError("archive contains duplicate members")
        folded: set[str] = set()
        members: dict[str, bytes] = {}
        total = 0
        for info in infos:
            name = _safe_member(info.filename)
            if name.casefold() in folded:
                raise VerificationKitError(f"case-folding archive collision: {name}")
            folded.add(name.casefold())
            mode = info.external_attr >> 16
            if (
                info.compress_type != ZIP_STORED
                or info.date_time != FIXED_TIME
                or not stat.S_ISREG(mode)
                or stat.S_IMODE(mode) != 0o644
            ):
                raise VerificationKitError(f"noncanonical archive member metadata: {name}")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise VerificationKitError("archive uncompressed-byte limit exceeded")
            members[name] = archive.read(info)

    controls = {MANIFEST_PATH, MANIFEST_DIGEST_PATH}
    if not controls.issubset(members):
        raise VerificationKitError("manifest controls are missing")
    try:
        manifest_bytes = members[MANIFEST_PATH]
        claimed, marker = members[MANIFEST_DIGEST_PATH].decode("ascii").rstrip("\n").split("  ", 1)
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationKitError("manifest controls are malformed") from exc
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if marker != MANIFEST_PATH or claimed != manifest_digest:
        raise VerificationKitError("manifest digest differs")
    if set(manifest) != {"format", "authenticates", "members"}:
        raise VerificationKitError("manifest shape differs")
    if (
        manifest["format"] != "bulla.action-receipt-v0.2-verification-kit/1"
        or manifest["authenticates"] != "payload-members-only"
        or not isinstance(manifest["members"], list)
    ):
        raise VerificationKitError("manifest contract differs")

    expected: list[str] = []
    for row in manifest["members"]:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "role"}:
            raise VerificationKitError("manifest member shape differs")
        name = _safe_member(row["path"])
        if name in controls or name not in members:
            raise VerificationKitError(f"manifest payload is absent or reserved: {name}")
        value = members[name]
        if row["bytes"] != len(value) or row["sha256"] != hashlib.sha256(value).hexdigest():
            raise VerificationKitError(f"manifest payload differs: {name}")
        if not isinstance(row["role"], str) or not row["role"]:
            raise VerificationKitError(f"manifest role is absent: {name}")
        expected.append(name)
    if expected != sorted(expected) or len(expected) != len(set(expected)):
        raise VerificationKitError("manifest members are not unique and ordered")
    if set(members) != set(expected) | controls:
        raise VerificationKitError("archive member set differs from manifest")
    return hashlib.sha256(payload).hexdigest(), members


def extract_verification_kit(payload: bytes, destination: Path) -> str:
    """Safely write a validated kit into an empty directory."""
    digest, members = validate_verification_kit(payload)
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in sorted(members.items()):
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        target.chmod(0o644)
    return digest


def verification_kit_bytes() -> bytes:
    """Return the exact kit bytes installed in this Bulla distribution."""
    return resources.files("bulla.data").joinpath(ARCHIVE_NAME).read_bytes()


def verification_kit_sha256() -> str:
    """Return the lowercase SHA-256 digest of the installed kit."""
    return hashlib.sha256(verification_kit_bytes()).hexdigest()


def export_verification_kit(out: str | Path) -> tuple[Path, str]:
    """Write the installed kit without rebuilding or changing its bytes.

    An existing identical file is accepted. A symlink or differing file is
    refused so this narrow export command cannot silently replace local data.
    """
    destination = Path(out)
    payload = verification_kit_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise FileExistsError(f"refusing symbolic-link output: {destination}")
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace existing output: {destination}")
        return destination, digest
    with destination.open("xb") as stream:
        stream.write(payload)
    return destination, digest
