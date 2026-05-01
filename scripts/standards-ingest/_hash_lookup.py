"""Shared registry-hash lookup for the build_*.py generators.

Reads ``calibration/data/registry-hashes.json`` (produced by
``compute_real_hashes.py``) and exposes ``lookup(pack, dimension,
version)`` → ``"sha256:..."`` (real) or
``"placeholder:awaiting-ingest"`` (no real fetch on file).

The pattern keeps the placeholder sentinel as the source-of-truth
fallback: a generator that doesn't find a real hash for its specific
(pack, dimension, version) tuple emits the sentinel rather than
silently fabricating one.
"""

from __future__ import annotations

import json
from pathlib import Path

_HASH_FILE = (
    Path(__file__).resolve().parents[2]
    / "calibration"
    / "data"
    / "registry-hashes.json"
)


def _load_table() -> dict[tuple[str, str, str], str]:
    """Read the hashes JSON into a (pack, dimension, version) → hash dict."""
    if not _HASH_FILE.exists():
        return {}
    data = json.loads(_HASH_FILE.read_text(encoding="utf-8"))
    out: dict[tuple[str, str, str], str] = {}
    for entry in data.get("entries", []):
        if entry.get("status") != "ok":
            continue
        h = entry.get("hash")
        if not isinstance(h, str) or not h.startswith("sha256:"):
            continue
        out[(entry["pack"], entry["dimension"], entry["version"])] = h
    return out


_TABLE = _load_table()


def lookup(
    pack: str,
    dimension: str,
    version: str,
    *,
    licensed: bool = False,
) -> str:
    """Return the real ``sha256:...`` for this pointer if available,
    else the appropriate placeholder sentinel.

    Args:
        pack:       pack name (e.g. ``"iana-media-types"``)
        dimension:  dimension name (e.g. ``"media_type"``)
        version:    pack-author-supplied version label
        licensed:   True for license-gated registries — produces
                    ``placeholder:awaiting-license`` rather than
                    ``placeholder:awaiting-ingest``.
    """
    real = _TABLE.get((pack, dimension, version))
    if real is not None:
        return real
    return "placeholder:awaiting-license" if licensed else "placeholder:awaiting-ingest"
