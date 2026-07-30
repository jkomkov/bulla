#!/usr/bin/env python3
"""Generate the reproduction-profile example receipts.

Deterministic: every timestamp and digest is pinned, so regeneration is
byte-stable and `python3 generate.py --check` fails on drift.

Run from the repository root or this directory:
    python3 bulla/spec/reproduction-examples/generate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from bulla.action_receipt import build_action_receipt  # noqa: E402
from bulla.envelope import (  # noqa: E402
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)

# A fixed instant so the committed examples are byte-stable.
TIMESTAMP = "2026-07-24T00:00:00Z"

# The claim under test: a published record the reproducing party did not author.
CLAIM_UNDER_TEST = {
    "uri": "https://example.invalid/claims/throughput-2026-07",
    "digest": "sha256:" + "9" * 64,
    "retrieved_at": "2026-07-22T09:00:00Z",
}

METHOD = {
    "repository": "https://example.invalid/reproducer/checker",
    "commit": "0" * 40,
    "checker_sha256": "sha256:" + "a" * 64,
    "independently_written": True,
}

ENVIRONMENT = {
    "language": "python",
    "runtime": "3.12",
    "operating_system": "linux",
    "cryptographic_library": "stdlib hashlib",
}


def _envelope() -> RecourseEnvelope:
    """The reproducing party acts under its own publication policy."""
    return RecourseEnvelope(
        authority=Authority(
            principal="did:web:reproducer.invalid#lab",
            policy="policy://reproducer/publication-v1",
        ),
        bounds=Bounds(scope="reproduction of one published claim", rollback_window="P30D"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://example.invalid/challenge",
                trusted_root_ref="fixture:independently-pinned-root",
            ),
            remedies=(
                Remedy(
                    rung="recompute",
                    verifier="bulla receipt verify",
                    anchor="hashes.content",
                ),
                Remedy(
                    rung="challenge",
                    verifier="named forum",
                    anchor="remedy.forum.trusted_root_ref",
                ),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )


def _attempt(event_id: str, result: dict) -> dict:
    receipt = build_action_receipt(
        action={
            "type": "reproduction.attempt",
            "subject": {
                "event_id": event_id,
                "claim_under_test": CLAIM_UNDER_TEST,
                "method": METHOD,
                "environment": ENVIRONMENT,
                "result": result,
            },
        },
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(),
        timestamp=TIMESTAMP,
    )
    return receipt.to_dict()


EXAMPLES: dict[str, dict] = {
    # The method ran and agreed.
    "attempt-reproduced.json": _attempt(
        "rep-1",
        {
            "status": "reproduced",
            "comparison": "exact",
            "fixture_results": [{"name": "vector-01", "agreed": True}],
        },
    ),
    # The method ran and disagreed. Divergences are required and non-empty.
    "attempt-diverged.json": _attempt(
        "rep-2",
        {
            "status": "diverged",
            "comparison": "exact",
            "fixture_results": [{"name": "vector-01", "agreed": False}],
            "divergences": [
                {
                    "name": "vector-01",
                    "expected": "sha256:" + "b" * 64,
                    "observed": "sha256:" + "c" * 64,
                }
            ],
        },
    ),
    # The method never ran. The refusal is the record.
    "attempt-blocked.json": _attempt(
        "rep-3",
        {
            "status": "blocked",
            "block": {
                "requested": "the pinned evaluation harness and its input corpus",
                "requested_from": "did:web:publisher.invalid#disclosure",
                "requested_at": "2026-07-18T12:00:00Z",
                "response": "denied",
                "response_at": "2026-07-19T15:30:00Z",
            },
        },
    ),
}


def main() -> int:
    check = "--check" in sys.argv
    drift = []
    for name, receipt in EXAMPLES.items():
        path = HERE / name
        rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                drift.append(name)
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path.name}")
    if drift:
        print("reproduction example drift: " + ", ".join(drift), file=sys.stderr)
        return 1
    if check:
        print(f"reproduction examples current: {len(EXAMPLES)} receipts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
