"""Export the immutable ActionReceipt v0.2 verification kit."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path


ARCHIVE_NAME = "action-receipt-v0.2-verification-kit.zip"


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
