"""Fetcher + parser for external API schema sources (Phase 7 growth).

The Phase 7 baseline indexed 57 captured MCP manifests + 8 synthetic
fixtures. The growth lever for real-world coverage is fetching real
OpenAPI / GraphQL schemas from public registries.

This module gives ``build_phase7_index.py`` a small, deterministic
fetcher with content-addressed caching:

  - One on-disk cache directory under ``calibration/data/api-registry/_cache/``.
  - Each fetched URL → ``<sha256>.bin`` (raw bytes; never re-downloaded
    if its URL is already in the manifest).
  - Manifest at ``_cache/manifest.json`` recording per-URL hash,
    fetch time, byte count, and parser hint.
  - On parse failure, the URL is skipped with a clear error; the
    pipeline carries on. Real-world specs drift; one bad URL must
    not block the whole rebuild.

The fetcher is shaped after ``compute_real_hashes.fetch_and_hash``:
honest UA, retries, browser-UA fallback for hosts that gate on UA.

Why content-addressed cache and not just per-URL files? Two reasons:

  - URLs drift; the cache directory keeps an invariant view by
    content even when an upstream removes a path.
  - The manifest carries the (url → hash) binding so a future
    pipeline run can know whether the cached bytes still represent
    the live URL (just diff the manifest sha256 against a fresh
    fetch's sha256 without committing the cache).
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
from typing import Any, Iterable, Mapping

import yaml


class _LenientLoader(yaml.SafeLoader):
    """SafeLoader that won't crash on malformed YAML timestamps.

    Some upstream OpenAPI specs encode invalid timestamp scalars
    (e.g. ``hour > 23`` due to a typo). PyYAML's default constructor
    raises ``ValueError`` and aborts the whole parse. We override the
    timestamp constructor to keep the original string scalar, which
    lets the parse complete; the field becomes a plain string and the
    pipeline still classifies surrounding fields correctly.
    """


def _construct_scalar_as_string(loader, node):
    """Materialize any scalar tag we don't otherwise handle as a
    plain string. Lossy by design — the classifier doesn't need
    semantic correctness on values it doesn't understand."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


_LenientLoader.add_constructor(
    "tag:yaml.org,2002:timestamp",
    _construct_scalar_as_string,
)
# YAML 1.1 had ``=`` as a "value-key" indicator; PyYAML resolves it to
# the ``tag:yaml.org,2002:value`` tag and otherwise has no constructor
# for it. Some upstream OpenAPI YAMLs (Atlassian Jira) emit it.
_LenientLoader.add_constructor(
    "tag:yaml.org,2002:value",
    _construct_scalar_as_string,
)
# Generic catch-all for any other unresolved tag so a single odd
# scalar doesn't kill the whole parse.
_LenientLoader.add_constructor(None, _construct_scalar_as_string)


DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": "bulla-standards-ingest/0.1",
    "Accept": "application/json, application/yaml, text/yaml, */*",
}

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, application/yaml, text/yaml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


DEFAULT_TIMEOUT_SECONDS = 60
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 4.0, 12.0)
DEFAULT_MAX_BYTES = 32 * 1024 * 1024  # 32 MiB hard cap per fetch


class FetchError(RuntimeError):
    """Terminal fetch failure — all retries exhausted, no cache hit."""


class ParseError(RuntimeError):
    """The fetched bytes are neither valid JSON nor valid YAML."""


def _fetch_bytes(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retry_delays: Iterable[float] = RETRY_DELAYS_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bytes:
    """Fetch raw bytes from ``url`` with retries + UA fallback.

    Raises ``FetchError`` if all retries (and the browser-UA fallback)
    fail. Caps the response at ``max_bytes`` to keep a hostile or
    accidentally-huge upstream from filling memory.
    """
    primary = dict(headers or DEFAULT_HEADERS)
    delays = list(retry_delays)

    def _attempt(hh: Mapping[str, str]) -> bytes | None:
        try:
            req = urllib.request.Request(url, headers=dict(hh))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # Read up to one byte beyond the cap so we can detect
                # over-cap without trusting Content-Length.
                content = resp.read(max_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"      transient: {e}", file=sys.stderr)
            return None
        if len(content) > max_bytes:
            raise FetchError(
                f"response from {url} exceeds {max_bytes} bytes; refusing"
            )
        return content

    for ix in range(len(delays) + 1):
        out = _attempt(primary)
        if out is not None:
            return out
        if ix < len(delays):
            time.sleep(delays[ix])

    if primary.get("User-Agent") != BROWSER_HEADERS["User-Agent"]:
        print("      retrying once with browser User-Agent…", file=sys.stderr)
        out = _attempt(BROWSER_HEADERS)
        if out is not None:
            return out

    raise FetchError(f"all retries exhausted for {url}")


def _coerce_to_json_safe(doc: Any) -> Any:
    """Round-trip the doc through ``json.dumps(default=str)`` so any
    non-JSON-native scalars (``datetime.date``, ``datetime.datetime``,
    ``set``, ``tuple``) become strings or lists.

    YAML safe_load happily emits ``datetime.date`` from an unquoted
    YYYY-MM-DD scalar; the downstream pipeline (``api_registry.capture``)
    serializes through ``json.dumps`` which would crash on those.
    Coercing once here keeps the pipeline call sites simple — they
    only ever see plain JSON-shaped data.
    """
    return json.loads(json.dumps(doc, default=str))


def _parse_doc(content: bytes, url_hint: str) -> dict:
    """Parse content as JSON first, falling back to YAML.

    OpenAPI specs ship in both formats; the URL extension is the only
    cheap signal. We try JSON unconditionally because every YAML doc
    that *also* parses as JSON (plain numbers, strings) is benign;
    the failure is only when the content is genuinely YAML-only
    (anchors, refs, multiline-block scalars).
    """
    text = content.decode("utf-8", errors="replace")
    parser_order: list[str] = ["json", "yaml"]
    if url_hint.endswith((".yaml", ".yml")):
        parser_order = ["yaml", "json"]

    # PyYAML's reader rejects non-printable / control characters that
    # sometimes leak into upstream specs (typos, copy-paste artefacts).
    # We sanitize once before the YAML pass — JSON's parser already
    # tolerates them as raw bytes inside string scalars, so the JSON
    # branch never needs this.
    sanitized: str | None = None

    for parser in parser_order:
        try:
            if parser == "json":
                doc = json.loads(text)
            else:
                if sanitized is None:
                    sanitized = _strip_yaml_unfriendly_chars(text)
                doc = yaml.load(sanitized, Loader=_LenientLoader)
            if not isinstance(doc, dict):
                # An OpenAPI / GraphQL doc must be a mapping.
                raise ParseError(
                    f"{url_hint}: expected a JSON object, got "
                    f"{type(doc).__name__}"
                )
            return _coerce_to_json_safe(doc)
        except (json.JSONDecodeError, yaml.YAMLError):
            continue
        except ValueError:
            # YAML safe_load raises ValueError on malformed timestamps
            # (a real failure mode in some upstream specs that encode
            # invalid hour/minute values in unquoted scalars). We treat
            # it as a parse failure and try the next parser.
            continue

    raise ParseError(f"{url_hint}: not valid JSON or YAML")


def _strip_yaml_unfriendly_chars(s: str) -> str:
    """Replace control / non-printable characters that PyYAML refuses
    to read with a single space.

    The YAML 1.1 spec restricts the allowed character set; PyYAML's
    reader raises ReaderError on the first violation. Specs from real
    upstream sources occasionally leak in malformed bytes (e.g. C1
    control characters from a stray copy/paste). Replacing them with
    spaces lets the parse complete and the offending field becomes a
    string with an extra space — semantically harmless for the
    classifier-driven downstream pipeline.
    """
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        if (
            ch in ("\t", "\n", "\r")
            or (0x20 <= cp <= 0x7E)
            or (0xA0 <= cp <= 0xD7FF)
            or (0xE000 <= cp <= 0xFFFD)
            or (0x10000 <= cp <= 0x10FFFF)
        ):
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _read_manifest(cache_dir: Path) -> dict[str, Any]:
    p = cache_dir / "manifest.json"
    if not p.exists():
        return {"version": "0.1.0", "entries": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_manifest(cache_dir: Path, manifest: dict[str, Any]) -> None:
    p = cache_dir / "manifest.json"
    p.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def fetch_and_parse(
    url: str,
    *,
    cache_dir: Path,
    headers: Mapping[str, str] | None = None,
    force_refresh: bool = False,
) -> tuple[dict, str, int]:
    """Fetch + parse a remote OpenAPI / GraphQL spec, with cache.

    Returns ``(parsed_doc, sha256_hex, bytes_fetched)``. The sha256 is
    the hash of the raw fetched bytes (binds the parsed dict to the
    on-disk cached blob).

    On cache hit, no network call is made; returns the cached parse.
    On cache miss, fetches, computes the hash, writes the blob to
    ``<cache_dir>/<sha256>.bin``, updates ``manifest.json``, and
    returns the result.

    Raises ``FetchError`` or ``ParseError`` on terminal failure.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(cache_dir)
    entry = manifest["entries"].get(url) if not force_refresh else None

    if entry is not None:
        blob_path = cache_dir / f"{entry['sha256']}.bin"
        if blob_path.exists():
            content = blob_path.read_bytes()
            doc = _parse_doc(content, url_hint=url)
            return doc, entry["sha256"], entry["bytes"]
        # Cache pointed at a missing blob; fall through to refetch.

    content = _fetch_bytes(url, headers=headers)
    sha = hashlib.sha256(content).hexdigest()

    blob_path = cache_dir / f"{sha}.bin"
    if not blob_path.exists():
        blob_path.write_bytes(content)

    manifest["entries"][url] = {
        "sha256": sha,
        "bytes": len(content),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_manifest(cache_dir, manifest)

    doc = _parse_doc(content, url_hint=url)
    return doc, sha, len(content)
