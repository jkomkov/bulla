#!/usr/bin/env python3
"""Allowlisted, schema-only source refresh; never installs or executes MCP code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
sys.path.insert(0, str(BULLA / "scripts/standards-ingest"))

from _external_fetcher import FetchError, ParseError, fetch_and_parse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, default=HERE / "source-allowlist.generated.json")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "sources"} or document["schema_version"] != "0.1":
        raise SystemExit("invalid closed source allowlist")
    if not args.fetch:
        print(json.dumps({"mode": "dry-run", "source_count": len(document["sources"]), "executed_code": False}))
        return 0
    cache = BULLA / "calibration/data/api-registry/_cache"
    results = []
    for source in document["sources"]:
        if set(source) != {"source_id", "upstream_owner", "url", "parser_hint", "redistribution_status"}:
            raise SystemExit("source allowlist entry has unknown or missing fields")
        try:
            parsed, raw_sha256, byte_count = fetch_and_parse(source["url"], cache_dir=cache)
            results.append({
                "source_id": source["source_id"],
                "status": "captured",
                "document_type": type(parsed).__name__,
                "raw_content_hash": "sha256:" + raw_sha256,
                "byte_count": byte_count,
            })
        except (FetchError, ParseError) as exc:
            results.append({"source_id": source["source_id"], "status": "failed", "reason": str(exc)})
    print(json.dumps({"mode": "schema-only-fetch", "executed_code": False, "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
