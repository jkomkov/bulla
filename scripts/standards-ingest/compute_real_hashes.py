"""Compute real SHA-256 for fetchable open-standard registries.

For each open-standard pack that points at a publicly-fetchable URL,
this script downloads the canonical artifact, computes its SHA-256,
and reports the result. The output is a JSON file at
``calibration/data/registry-hashes.json`` mapping
``(pack_name, dimension, version)`` → ``{uri, hash, bytes_fetched,
fetched_at}``.

The generator scripts then read this file and substitute the real
hash for ``placeholder:awaiting-ingest`` when the (pack, dimension,
version) tuple matches.

Networks fail; URLs drift; large registries (FHIR Definitions JSON)
take real time. So this is a separate one-shot script, not part of
the per-pack generator. Run it when:

  - You actually have network access
  - You're willing to spend ~10 minutes on the larger artifacts
  - You're producing a release artifact (not iterating on schema)

The pre-existing placeholder sentinel is the correct production
shape between hash refreshes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# (pack_name, dimension, version, uri, fetch_size_hint_bytes)
# Only fetchable open standards. License-gated registries stay on the
# placeholder sentinel until credentials are configured.
TARGETS: list[tuple[str, str, str, str, int]] = [
    # IANA Media Types — XHTML index page (the canonical landing page;
    # IANA does not publish a single CSV of all media types but the
    # XHTML page is content-stable and fetchable)
    ("iana-media-types", "media_type", "snapshot",
     "https://www.iana.org/assignments/media-types/media-types.xhtml",
     500_000),
    # UCUM essence XML
    ("ucum", "unit_of_measure", "snapshot",
     "https://ucum.org/ucum-essence.xml",
     500_000),
    # NAICS 2022 — XLSX
    ("naics-2022", "industry_code", "2022",
     "https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx",
     500_000),
    # ISO 639-3 SIL tab file
    ("iso-639", "language_code", "sil-snapshot",
     "https://iso639-3.sil.org/sites/iso639-3/files/downloads/iso-639-3.tab",
     1_000_000),
    # FIX 4.4 + 5.0 dictionaries — QuickFIX/J on GitHub (Apache-2.0)
    ("fix-4.4", "fix_msg_type", "4.4",
     "https://raw.githubusercontent.com/quickfix-j/quickfixj/master/quickfixj-core/src/main/resources/FIX44.xml",
     1_000_000),
    ("fix-5.0", "fix_msg_type", "5.0",
     "https://raw.githubusercontent.com/quickfix-j/quickfixj/master/quickfixj-core/src/main/resources/FIX50.xml",
     1_000_000),
    # FHIR R4 / R5 resource-types valueset (small JSON)
    ("fhir-r4", "fhir_resource_type", "R4",
     "https://hl7.org/fhir/R4/valueset-resource-types.json",
     200_000),
    ("fhir-r5", "fhir_resource_type", "R5",
     "https://hl7.org/fhir/R5/valueset-resource-types.json",
     200_000),
]


def fetch_and_hash(uri: str, timeout: int = 30) -> tuple[str, int] | None:
    """Fetch ``uri`` and return (sha256_hex, bytes_fetched).

    Returns None on any error. The caller is responsible for deciding
    whether to retry, fall back to the placeholder, or fail loud.
    """
    try:
        req = urllib.request.Request(
            uri,
            headers={"User-Agent": "bulla-standards-ingest/0.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  FAIL  {uri}: {e}", file=sys.stderr)
        return None

    h = hashlib.sha256(content).hexdigest()
    return h, len(content)


def main() -> None:
    out: dict = {
        "version": "0.1.0",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "entries": [],
    }
    print(f"Fetching {len(TARGETS)} open-standard registries...", file=sys.stderr)
    for pack, dim, version, uri, _hint in TARGETS:
        print(f"  {pack}/{dim} v{version} ← {uri}", file=sys.stderr)
        result = fetch_and_hash(uri)
        if result is None:
            out["entries"].append({
                "pack": pack,
                "dimension": dim,
                "version": version,
                "uri": uri,
                "hash": None,
                "bytes_fetched": 0,
                "status": "fetch_failed",
            })
            continue
        sha256_hex, n_bytes = result
        out["entries"].append({
            "pack": pack,
            "dimension": dim,
            "version": version,
            "uri": uri,
            "hash": f"sha256:{sha256_hex}",
            "bytes_fetched": n_bytes,
            "status": "ok",
        })
        print(f"    OK  {n_bytes} bytes  sha256:{sha256_hex[:16]}…", file=sys.stderr)

    out_path = REPO_ROOT / "calibration" / "data" / "registry-hashes.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    succeeded = sum(1 for e in out["entries"] if e["status"] == "ok")
    failed = sum(1 for e in out["entries"] if e["status"] == "fetch_failed")
    print(f"\nSummary: {succeeded} ok, {failed} failed", file=sys.stderr)
    print(f"Wrote: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
