#!/usr/bin/env python3
"""Check the frozen, content-addressed comprehension protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "comprehension-protocol.json"
DIGEST = HERE / "comprehension-protocol.json.sha256"
PROMOTION_POLICY = HERE / "promotion-policy.json"
QUESTIONS = {
    "same_output",
    "retained_evidence",
    "provider_contact",
    "relation_reproduction",
    "historical_provider_execution",
    "answer_correctness",
    "buyer_policy_decision",
    "integrity_coverage_completeness",
}


def main() -> int:
    raw = PROTOCOL.read_bytes()
    value = json.loads(raw)
    if value["profile"] != "bulla.inference-clearing-comprehension/0.2":
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
        raise SystemExit("the protocol must freeze all eight first-contact distinctions")
    if value["lineage"] != {
        "supersedes": "bulla.inference-clearing-comprehension/0.1",
        "superseded_sha256": "sha256:fe99b7c151b43632af68d81877f252f6cf33214b0dd39c2ecce5dc58d0a3e11a",
        "reason": "Version 0.1 was frozen but never opened or used. Version 0.2 tests the corrected causal surface and removes first-contact requirements that belong in the technical reference.",
    }:
        raise SystemExit("the unused v0.1 protocol lineage is not preserved")
    if value["facilitator"]["permitted_assistance"] != ["navigation", "accessibility"]:
        raise SystemExit("semantic assistance cannot be permitted")
    if value["acceptance"]["browser_interactions"] != {
        "required_outcomes": ["provider_exit_recheck", "receipt_boundary_challenge"],
        "minimum_complete_both": 4,
        "of": 5,
        "semantic_assistance_permitted": False,
    }:
        raise SystemExit("both browser interactions must be separately observed without semantic assistance")
    if value["failure"]["in_protocol_retest"] is not False:
        raise SystemExit("the official gate must remain single-shot")
    if value["failure"]["keeps_pull_request_draft"] is not True:
        raise SystemExit("the frozen protocol's original governance consequence drifted")
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
    policy = json.loads(PROMOTION_POLICY.read_bytes())
    if policy != {
        "profile": "bulla.inference-clearing-promotion-policy/0.1",
        "effective_date": "2026-08-07",
        "bound_comprehension_protocol": {
            "profile": value["profile"],
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        },
        "supersedes": {
            "document": "comprehension-protocol.json",
            "pointer": "/failure/keeps_pull_request_draft",
            "prior_value": True,
            "scope": "REPOSITORY_PROMOTION_GOVERNANCE_ONLY",
        },
        "requirements": {
            "source_only_profile_merge": "R3_AND_MAPPED_VALIDATION",
            "experimental_route_merge": "R3_AND_MAPPED_VALIDATION",
            "homepage_promotion": "HUMAN_COMPREHENSION_ESTABLISHED",
            "category_level_messaging": "HUMAN_COMPREHENSION_ESTABLISHED",
            "claim_of_unfamiliar_reader_comprehension": "HUMAN_COMPREHENSION_ESTABLISHED",
        },
        "human_comprehension_state": "BLOCKED",
        "does_not_change": [
            "the content-addressed comprehension protocol",
            "the authenticated scoring rules",
            "the result of any comprehension attempt",
            "the inference-clearing profile or verifier",
            "the external-reproduction gate",
        ],
    }:
        raise SystemExit("promotion policy is not bound to the frozen protocol and current governance")
    print(f"comprehension protocol matches sha256:{hashlib.sha256(raw).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
