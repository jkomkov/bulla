#!/usr/bin/env python3
"""Check the frozen, content-addressed comprehension protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "comprehension-protocol.json"
DIGEST = HERE / "comprehension-protocol.json.sha256"
QUESTIONS = {
    "role_handoff",
    "relation_reproduction",
    "historical_provider_execution",
    "answer_correctness",
    "buyer_policy_eligibility",
    "settlement_stages",
    "funds_movement",
    "integrity_and_coverage",
    "receiver_record_completeness",
}


def main() -> int:
    raw = PROTOCOL.read_bytes()
    value = json.loads(raw)
    if value["profile"] != "bulla.inference-clearing-comprehension/0.1":
        raise SystemExit("wrong comprehension protocol profile")
    if value["state"] != "FROZEN_PENDING_GATE_OPEN":
        raise SystemExit("the committed protocol cannot claim a human result")
    slots = value["participant_slots"]
    if len(slots) != 5 or len({slot["id"] for slot in slots}) != 5:
        raise SystemExit("the protocol must contain five unique participant slots")
    if sum(slot["role"] == "DEVELOPER" for slot in slots) != 3:
        raise SystemExit("the protocol must contain three developer slots")
    if sum(slot["role"] == "TECHNICAL_DECISION_MAKER" for slot in slots) != 2:
        raise SystemExit("the protocol must contain two decision-maker slots")
    if [slot["id"] for slot in slots if slot["terminal_reproduction"]] != ["developer-1"]:
        raise SystemExit("developer-1 must be the sole terminal-reproduction slot")
    if {item["id"] for item in value["questions"]} != QUESTIONS:
        raise SystemExit("the protocol must freeze the role handoff and all eight distinctions")
    if value["facilitator"]["permitted_assistance"] != ["navigation", "accessibility"]:
        raise SystemExit("semantic assistance cannot be permitted")
    expected = hashlib.sha256(raw).hexdigest() + "  comprehension-protocol.json\n"
    if not DIGEST.exists() or DIGEST.read_text(encoding="ascii") != expected:
        raise SystemExit("comprehension protocol digest drifted")
    for schema in (
        "comprehension-gate-open.schema.json",
        "comprehension-context.schema.json",
        "comprehension-response.schema.json",
        "comprehension-attempt-manifest.schema.json",
        "comprehension-result.schema.json",
    ):
        document = json.loads((HERE / schema).read_bytes())
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"{schema} is not a Draft 2020-12 schema")
        if document.get("additionalProperties") is not False:
            raise SystemExit(f"{schema} is not closed")
    scorer = HERE / "score_comprehension_gate.py"
    if not scorer.is_file():
        raise SystemExit("authenticated comprehension scorer is missing")
    print(f"comprehension protocol matches sha256:{hashlib.sha256(raw).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
