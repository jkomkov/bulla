#!/usr/bin/env python3
"""Measure serialized receipt sizes in the routed-inference fixture corpus."""

from __future__ import annotations

import json
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent


def _summary(values: list[int]) -> dict:
    ordered = sorted(values)
    p95 = ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]
    return {
        "min": ordered[0],
        "median": int(statistics.median(ordered)),
        "p95_nearest_rank": p95,
        "max": ordered[-1],
    }


def main() -> int:
    compact: list[int] = []
    pretty: list[int] = []
    traces = sorted(p for p in HERE.glob("[0-9][0-9]-*.json"))
    for path in traces:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        for receipt in bundle["receipts"]:
            compact.append(len(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()))
            pretty.append(len((json.dumps(receipt, indent=2) + "\n").encode()))
    report = {
        "schema_version": 1,
        "as_of": "2026-07-17",
        "source": "routed-inference-vectors/*.json",
        "trace_count": len(traces),
        "receipt_sample_count": len(compact),
        "compact_bytes": _summary(compact),
        "pretty_printed_bytes": _summary(pretty),
        "claim_boundary": "Fixture measurement only; not a protocol size guarantee.",
    }
    output = HERE / "size-report.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
