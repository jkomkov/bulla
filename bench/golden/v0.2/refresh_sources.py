#!/usr/bin/env python3
"""Re-retrieve allowlisted public schemas without executing third-party code."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
V01 = HERE.parent / "v0.1"
sys.path.insert(0, str(BULLA / "scripts/standards-ingest"))

from _external_fetcher import FetchError, ParseError, fetch_and_parse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, default=V01 / "source-allowlist.generated.json")
    parser.add_argument("--output", type=Path, default=HERE / "source-accessibility-audit.json")
    args = parser.parse_args()
    document = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "sources"} or document["schema_version"] != "0.1":
        raise SystemExit("invalid closed source allowlist")
    cache = BULLA / "calibration/data/api-registry/_cache"
    results = []
    for source in document["sources"]:
        if set(source) != {"source_id", "upstream_owner", "url", "parser_hint", "redistribution_status"}:
            raise SystemExit("source allowlist entry has unknown or missing fields")
        captured_at = datetime.now(timezone.utc).isoformat()
        try:
            parsed, raw_sha256, byte_count = fetch_and_parse(source["url"], cache_dir=cache)
            results.append({
                "source_id": source["source_id"], "upstream_owner": source["upstream_owner"],
                "url": source["url"], "status": "captured", "captured_at": captured_at,
                "document_type": type(parsed).__name__, "raw_content_hash": "sha256:" + raw_sha256,
                "byte_count": byte_count, "redistribution_status": source["redistribution_status"],
            })
        except (FetchError, ParseError) as exc:
            results.append({
                "source_id": source["source_id"], "upstream_owner": source["upstream_owner"],
                "url": source["url"], "status": "failed", "captured_at": captured_at,
                "reason": str(exc), "redistribution_status": source["redistribution_status"],
            })
    report = {
        "schema_version": "0.2-source-accessibility-audit",
        "mode": "allowlisted-schema-only-reretrieval",
        "executed_third_party_code": False,
        "source_count": len(results),
        "captured_count": sum(item["status"] == "captured" for item in results),
        "failed_count": sum(item["status"] == "failed" for item in results),
        "redistribution_promoted": False,
        "independence_boundary": "separate retrieval event under implementation-team control; not independent reviewer attestation",
        "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("source_count", "captured_count", "failed_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
