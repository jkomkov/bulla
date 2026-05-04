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
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]


# Default headers — bulla UA is honest. A small subset of
# Akamai-fronted hosts (CMS, GS1) reject our default UA outright; for
# those we fall back to a browser-shaped Mozilla string. Header swaps
# happen at fetch time, not in the target table, so the table reads
# cleanly.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": "bulla-standards-ingest/0.1",
    "Accept": "*/*",
}

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# (pack_name, dimension, version, uri, fetch_size_hint_bytes,
#  optional per-target header overrides)
# Only fetchable open standards. License-gated registries stay on the
# placeholder sentinel until credentials are configured.
TARGETS: list[tuple[str, str, str, str, int, dict[str, str] | None]] = [
    # IANA Media Types — XHTML index page (the canonical landing page;
    # IANA does not publish a single CSV of all media types but the
    # XHTML page is content-stable and fetchable)
    ("iana-media-types", "media_type", "snapshot",
     "https://www.iana.org/assignments/media-types/media-types.xhtml",
     1_500_000, None),
    # UCUM essence XML
    ("ucum", "unit_of_measure", "snapshot",
     "https://ucum.org/ucum-essence.xml",
     1_500_000, None),
    # NAICS 2022 — XLSX
    ("naics-2022", "industry_code", "2022",
     "https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx",
     500_000, None),
    # ISO 639-3 SIL tab file
    ("iso-639", "language_code", "sil-snapshot",
     "https://iso639-3.sil.org/sites/iso639-3/files/downloads/iso-639-3.tab",
     1_000_000, None),
    # FIX 4.4 — canonical XML in the QuickFIX C++ repository (the
    # quickfix-j Java fork no longer carries the dictionaries, hence
    # the old URL 404'd)
    ("fix-4.4", "fix_msg_type", "4.4",
     "https://raw.githubusercontent.com/quickfix/quickfix/master/spec/FIX44.xml",
     1_500_000, None),
    # FIX 5.0 SP2 — the published 5.0 dictionary is the SP2 form
    ("fix-5.0", "fix_msg_type", "5.0",
     "https://raw.githubusercontent.com/quickfix/quickfix/master/spec/FIX50SP2.xml",
     2_000_000, None),
    # FHIR R4 / R5 resource-types valueset (small JSON)
    ("fhir-r4", "fhir_resource_type", "R4",
     "https://hl7.org/fhir/R4/valueset-resource-types.json",
     200_000, None),
    ("fhir-r5", "fhir_resource_type", "R5",
     "https://hl7.org/fhir/R5/valueset-resource-types.json",
     200_000, None),
    # GS1 Application Identifiers — official ref.gs1.org JSON catalogue.
    # The legacy gs1.org PDF URL returns 403 to non-browser UAs and
    # the PDF is content-unstable across cosmetic revisions. The
    # JSON catalogue is small, machine-readable, and content-stable.
    ("gs1", "gs1_application_identifier", "snapshot",
     "https://ref.gs1.org/ai/",
     1_000_000,
     {**DEFAULT_HEADERS, "Accept": "application/json"}),
    # UN/EDIFACT D.21B — UNECE-published full directory ZIP
    ("un-edifact", "edifact_message_type", "D.21B",
     "https://service.unece.org/trade/untdid/d21b/d21b.zip",
     2_000_000, None),
    # ICD-10-CM — CMS-published 2026 tabular order ZIP. The legacy
    # 2024 URL pattern (``2024-icd-10-cm-code-files.zip``) returns
    # 404; the current naming convention is
    # ``<year>-code-descriptions-tabular-order.zip``.
    ("icd-10-cm", "icd_10_cm_code", "2026",
     "https://www.cms.gov/files/zip/2026-code-descriptions-tabular-order.zip",
     3_000_000, BROWSER_HEADERS),
]


# How long a stubborn fetch is allowed to block.
DEFAULT_TIMEOUT_SECONDS = 45
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 4.0, 12.0)


def fetch_and_hash(
    uri: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retry_delays: Iterable[float] = RETRY_DELAYS_SECONDS,
) -> tuple[str, int, dict[str, str]] | None:
    """Fetch ``uri`` and return (sha256_hex, bytes_fetched, headers_used).

    Strategy: try the supplied ``headers`` (or DEFAULT_HEADERS); on
    transient failure, retry up to ``len(retry_delays)`` times with
    exponential backoff. If all retries fail and the headers are not
    already the browser headers, try once more with BROWSER_HEADERS —
    a few hosts (CMS, GS1) reject the default UA outright.

    Returns None on terminal failure. The caller decides whether to
    fall back to the placeholder sentinel.
    """
    primary_headers = dict(headers or DEFAULT_HEADERS)
    delays = list(retry_delays)

    def _attempt(hh: Mapping[str, str]) -> tuple[str, int] | None:
        try:
            req = urllib.request.Request(uri, headers=dict(hh))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"    transient: {e}", file=sys.stderr)
            return None
        return hashlib.sha256(content).hexdigest(), len(content)

    for attempt_ix in range(len(delays) + 1):
        result = _attempt(primary_headers)
        if result is not None:
            sha, n = result
            return sha, n, dict(primary_headers)
        if attempt_ix < len(delays):
            time.sleep(delays[attempt_ix])

    # One last shot with the browser headers if we weren't already
    # using them. CMS / GS1 / a few Drupal-fronted hosts gate on UA.
    if primary_headers.get("User-Agent") != BROWSER_HEADERS["User-Agent"]:
        print("    retrying once with browser User-Agent…", file=sys.stderr)
        result = _attempt(BROWSER_HEADERS)
        if result is not None:
            sha, n = result
            return sha, n, dict(BROWSER_HEADERS)

    return None


def main() -> None:
    out: dict = {
        "version": "0.2.0",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "entries": [],
    }
    print(f"Fetching {len(TARGETS)} open-standard registries...", file=sys.stderr)
    for pack, dim, version, uri, _hint, header_override in TARGETS:
        print(f"  {pack}/{dim} v{version} ← {uri}", file=sys.stderr)
        result = fetch_and_hash(uri, headers=header_override)
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
            print(f"    FAIL  (all retries + UA fallback exhausted)", file=sys.stderr)
            continue
        sha256_hex, n_bytes, _ = result
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
