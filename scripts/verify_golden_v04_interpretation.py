#!/usr/bin/env python3
"""Verify the additive Golden v0.4 interpretation without importing Bulla."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


EXPECTED = {
    "observation": "COMPOUNDING_OBSERVED",
    "author_origin": "TEAM_AUTHORED",
    "adjudication_origin": "MACHINE_PLANTED",
    "replay": "INTERNAL",
    "closure": "BOUNDED_EXACT",
    "historical_preregistered_threshold_name": "DEMONSTRATED_COMPOUNDING",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> int:
    print(json.dumps({"error": message, "ok": False}, sort_keys=True), file=sys.stderr)
    return 1


def main() -> int:
    root = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) == 2
        else Path(__file__).resolve().parents[1] / "bench/golden/v0.4"
    )
    try:
        record = json.loads((root / "interpretation.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return fail(f"invalid interpretation record: {exc}")
    if not isinstance(record, dict):
        return fail("interpretation must be an object")
    for field, expected in EXPECTED.items():
        if record.get(field) != expected:
            return fail(f"unexpected {field}")
    bindings = {
        "cases_sha256": root / "cases.jsonl",
        "bounded_report_sha256": root / "precedent-yield-report.json",
        "manifest_sha256": root / "manifest.json",
    }
    for field, path in bindings.items():
        if record.get(field) != digest(path):
            return fail(f"{field} mismatch")
    observations = record.get("ci_observations")
    if not isinstance(observations, list) or not observations:
        return fail("ci_observations must be non-empty")
    if any(
        not isinstance(item, dict)
        or type(item.get("run_id")) is not int
        or item.get("conclusion") != "SUCCESS"
        for item in observations
    ):
        return fail("invalid CI observation")
    print(
        json.dumps(
            {
                "ci_observations": len(observations),
                "evidence_class": "INTERNAL_CAPTIVE",
                "observation": record["observation"],
                "ok": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
