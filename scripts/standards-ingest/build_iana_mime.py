"""Generate the IANA Media Types (MIME) pack.

The full IANA registry has ~2000 entries with continuous additions. We
ship a small inline seed of the canonical 25 most-used media types
(documentation), and a ``values_registry`` pointer to the IANA-published
CSV (authoritative). The hash on the pointer is a placeholder until a
real ingest captures it; ``bulla pack verify --fetch`` will be the path
that materializes and verifies the actual registry contents (the fetch
runtime lands when the network-fetcher implementation arrives in a
follow-on; for Phase 1 the static inspection is the production
surface).

Why both? IANA MIME types are ``open``, so the canonicalization step
in ``_hash_pack`` strips the inline list before computing the hash —
authors can curate the inline documentation without producing
pack-hash drift, and the registry pointer remains the binding object.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml

# Resolve real registry hashes when an ingest has been performed,
# else fall back to the placeholder sentinel.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from _hash_lookup import lookup as _hash_for  # noqa: E402


# Inline seed: the canonical 25 most-used media types as documentation.
# Full registry lives behind the values_registry pointer.
INLINE_SEED = [
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/javascript",
    "application/yaml",
    "application/xhtml+xml",
    "text/plain",
    "text/html",
    "text/css",
    "text/csv",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/svg+xml",
    "image/webp",
    "audio/mpeg",
    "video/mp4",
    "multipart/form-data",
    "multipart/mixed",
    "message/rfc822",
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "iana-media-types",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Public-Domain",
            "source_url": (
                "https://www.iana.org/assignments/media-types/"
                "media-types.xhtml"
            ),
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "IANA-Media-Types",
            "version": f"snapshot-{today}",
            "source_uri": (
                "https://www.iana.org/assignments/media-types/"
                "media-types.csv"
            ),
        },
        "dimensions": {
            "media_type": {
                "description": (
                    "IANA media type (MIME type). The full registry has "
                    "~2000 entries with continuous additions; the "
                    "authoritative list lives at the values_registry "
                    "pointer. The 25 inline values are documentation "
                    "of the most commonly observed types and are "
                    "stripped from the pack hash via the canonicalization "
                    "rule (Extension B), so curating examples doesn't "
                    "produce pack-hash drift."
                ),
                "field_patterns": [
                    "content_type",
                    "contentType",
                    "content-type",
                    "media_type",
                    "mime_type",
                    "mimeType",
                    "*_content_type",
                    "*_mime",
                    "*_mime_type",
                    "accept",
                    "accept_type",
                ],
                "description_keywords": [
                    "media type",
                    "mime type",
                    "content type",
                    "iana media",
                    "rfc 6838",
                    "rfc6838",
                ],
                "domains": ["universal"],
                "known_values": INLINE_SEED,
                "values_registry": {
                    "uri": (
                        "https://www.iana.org/assignments/media-types/"
                        "media-types.xhtml"
                    ),
                    "hash": _hash_for("iana-media-types", "media_type", "snapshot"),
                    "version": "snapshot",
                },
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
