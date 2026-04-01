"""OpenTimestamps integration for manifest witnessing.

Anchors manifest commitment hashes to the Bitcoin timechain via
OpenTimestamps calendar servers. Requires: pip install bulla[ots]
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CALENDAR_URLS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
]

# Fields injected by publish — excluded when computing commitment hash
OTS_FIELDS = {"ots_proof", "commitment_hash"}


def _check_ots_available() -> None:
    """Raise ImportError with install hint if opentimestamps is missing."""
    try:
        import opentimestamps  # noqa: F401
    except ImportError:
        raise ImportError(
            "OpenTimestamps support requires the [ots] extra: "
            "pip install bulla[ots]"
        )


def commitment_hash(manifest_path: Path) -> str:
    """Compute SHA256 commitment hash of a manifest, excluding OTS fields."""
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a YAML mapping")

    # Remove OTS-injected fields before hashing
    clean = {k: v for k, v in data.items() if k not in OTS_FIELDS}
    canonical = yaml.dump(clean, default_flow_style=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stamp_manifest(
    manifest_path: Path,
    calendar_urls: list[str] | None = None,
    timeout: int = 10,
    min_attestations: int = 1,
) -> bytes:
    """Submit manifest commitment hash to OTS calendars. Returns proof bytes."""
    _check_ots_available()

    from opentimestamps.core.op import OpAppend, OpSHA256
    from opentimestamps.core.serialize import StreamSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    urls = calendar_urls or CALENDAR_URLS
    hash_hex = commitment_hash(manifest_path)
    hash_bytes = bytes.fromhex(hash_hex)

    # Build detached timestamp
    file_timestamp = DetachedTimestampFile(
        OpSHA256(), Timestamp(hash_bytes)
    )

    # Add nonce for privacy
    nonce_stamp = file_timestamp.timestamp.ops.add(
        OpAppend(os.urandom(16))
    )
    merkle_tip = nonce_stamp.ops.add(OpSHA256())

    # Submit to calendars asynchronously
    q: Queue = Queue()
    for url in urls:
        t = threading.Thread(
            target=_submit_to_calendar,
            args=(url, merkle_tip.msg, q, timeout),
        )
        t.start()

    # Collect responses
    merged = 0
    start = time.time()
    for _ in range(len(urls)):
        try:
            remaining = max(0, timeout - (time.time() - start))
            result = q.get(block=True, timeout=remaining)
            if isinstance(result, Timestamp):
                merkle_tip.merge(result)
                merged += 1
                logger.info("Got attestation (%d/%d)", merged, min_attestations)
            else:
                logger.debug("Calendar error: %s", result)
        except Empty:
            break

    if merged < min_attestations:
        raise RuntimeError(
            f"Failed to get {min_attestations} attestation(s) "
            f"from calendars (got {merged}). Check network connectivity."
        )

    # Serialize to bytes
    buf = io.BytesIO()
    ctx = StreamSerializationContext(buf)
    file_timestamp.serialize(ctx)
    return buf.getvalue()


def _submit_to_calendar(
    calendar_url: str, msg: bytes, q: Queue, timeout: int
) -> None:
    """Submit hash to a single calendar server (runs in thread)."""
    try:
        from opentimestamps.calendar import RemoteCalendar

        remote = RemoteCalendar(calendar_url, user_agent="bulla-ots/0.1")
        calendar_timestamp = remote.submit(msg, timeout=timeout)
        q.put(calendar_timestamp)
    except Exception as exc:
        q.put(exc)


def publish_manifest(manifest_path: Path, **kwargs: Any) -> Path:
    """Stamp a manifest and embed the OTS proof + commitment hash in-place.

    Returns the path to the updated manifest.
    """
    hash_hex = commitment_hash(manifest_path)
    proof_bytes = stamp_manifest(manifest_path, **kwargs)
    proof_b64 = base64.b64encode(proof_bytes).decode("ascii")

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data["commitment_hash"] = hash_hex
    data["ots_proof"] = proof_b64

    manifest_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return manifest_path


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    """Verify an OTS-stamped manifest. Returns status dict."""
    _check_ots_available()

    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation,
        PendingAttestation,
    )
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"valid": False, "error": "Manifest is not a YAML mapping"}

    stored_hash = data.get("commitment_hash")
    stored_proof = data.get("ots_proof")

    if not stored_hash or not stored_proof:
        return {"valid": False, "error": "No OTS proof found in manifest"}

    # Recompute commitment hash
    computed_hash = commitment_hash(manifest_path)
    if computed_hash != stored_hash:
        return {
            "valid": False,
            "error": (
                f"Commitment hash mismatch: stored {stored_hash[:16]}... "
                f"vs computed {computed_hash[:16]}..."
            ),
        }

    # Deserialize and inspect proof
    proof_bytes = base64.b64decode(stored_proof)
    buf = io.BytesIO(proof_bytes)
    ctx = StreamDeserializationContext(buf)
    detached = DetachedTimestampFile.deserialize(ctx)

    # Walk attestations
    attestations = _collect_attestations(detached.timestamp)

    pending = [a for a in attestations if isinstance(a, PendingAttestation)]
    confirmed = [
        a for a in attestations
        if isinstance(a, BitcoinBlockHeaderAttestation)
    ]

    if confirmed:
        block_heights = [a.height for a in confirmed]
        return {
            "valid": True,
            "status": "confirmed",
            "commitment_hash": stored_hash,
            "bitcoin_block_heights": block_heights,
            "attestation_count": len(confirmed),
        }
    elif pending:
        calendar_urls = [a.uri.decode() if isinstance(a.uri, bytes) else a.uri for a in pending]
        return {
            "valid": True,
            "status": "pending",
            "commitment_hash": stored_hash,
            "pending_calendars": calendar_urls,
            "note": (
                "Timestamp submitted but not yet confirmed on Bitcoin blockchain. "
                "Run `bulla manifest verify --upgrade` after ~2 hours."
            ),
        }
    else:
        return {
            "valid": False,
            "error": "Proof contains no attestations",
        }


def upgrade_proof(manifest_path: Path) -> dict[str, Any]:
    """Attempt to upgrade a pending OTS proof to a confirmed one."""
    _check_ots_available()

    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.serialize import (
        StreamDeserializationContext,
        StreamSerializationContext,
    )
    from opentimestamps.core.timestamp import DetachedTimestampFile

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    stored_proof = data.get("ots_proof")
    if not stored_proof:
        return {"upgraded": False, "error": "No OTS proof found"}

    proof_bytes = base64.b64decode(stored_proof)
    buf = io.BytesIO(proof_bytes)
    ctx = StreamDeserializationContext(buf)
    detached = DetachedTimestampFile.deserialize(ctx)

    # Try upgrading via calendars
    changed = _upgrade_timestamp(detached.timestamp, CALENDAR_URLS)

    if changed:
        # Re-serialize
        out = io.BytesIO()
        out_ctx = StreamSerializationContext(out)
        detached.serialize(out_ctx)
        data["ots_proof"] = base64.b64encode(out.getvalue()).decode("ascii")
        manifest_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return {"upgraded": True, "status": "Proof upgraded with blockchain attestation"}
    else:
        return {"upgraded": False, "status": "No upgrade available yet — try again later"}


def _collect_attestations(timestamp: Any) -> list[Any]:
    """Recursively collect all attestations from a timestamp tree."""
    result = list(timestamp.attestations)
    for op, ts in timestamp.ops.items():
        result.extend(_collect_attestations(ts))
    return result


def _upgrade_timestamp(timestamp: Any, calendar_urls: list[str]) -> bool:
    """Try to upgrade pending attestations to confirmed ones."""
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation

    changed = False
    for attestation in list(timestamp.attestations):
        if isinstance(attestation, PendingAttestation):
            uri = attestation.uri.decode() if isinstance(attestation.uri, bytes) else attestation.uri
            try:
                remote = RemoteCalendar(uri, user_agent="bulla-ots/0.1")
                upgraded = remote.get_timestamp(timestamp.msg)
                if upgraded is not None:
                    timestamp.merge(upgraded)
                    changed = True
            except Exception as e:
                logger.debug("Upgrade from %s failed: %s", uri, e)

    for op, ts in timestamp.ops.items():
        if _upgrade_timestamp(ts, calendar_urls):
            changed = True

    return changed
