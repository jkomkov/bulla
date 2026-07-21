#!/usr/bin/env python3
"""Verify Sprint-13 seed-fixture supersession without importing Bulla."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "calibration/fixtures/sprint13-seed-lifecycle-v044.json"


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> int:
    print(json.dumps({"error": message, "ok": False}, sort_keys=True), file=sys.stderr)
    return 1


def main() -> int:
    try:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return fail(f"invalid lifecycle record: {exc}")
    if record.get("record_type") != "bulla.fixture.lifecycle/0.1":
        return fail("unknown lifecycle record type")
    if record.get("classification") != "EXPECTATION_DRIFT":
        return fail("unexpected drift classification")
    former = ROOT / record["former_canonical_path"]
    if former.exists():
        return fail("retired fixture remains loadable at its former canonical path")
    for field in ("archive", "replacement"):
        binding = record.get(field)
        if not isinstance(binding, dict):
            return fail(f"missing {field} binding")
        path = ROOT / binding["path"]
        if not path.is_file() or digest(path) != binding.get("digest"):
            return fail(f"{field} digest mismatch")
    if record["archive"].get("historical_results_remain_valid") is not True:
        return fail("historical disposition must be explicit")
    if record["authorization"].get("retirement_authority") != (
        "github:jkomkov/res-agentica:maintainer"
    ):
        return fail("retirement authority mismatch")
    print(
        json.dumps(
            {
                "archive_preserved": True,
                "classification": record["classification"],
                "current_epoch": record["replacement"]["epoch"],
                "ok": True,
                "retired_canonical_path_absent": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
