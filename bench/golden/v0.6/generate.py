#!/usr/bin/env python3
"""Generate the deterministic 96-case Golden F13 packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROFILE = "bulla.golden-suite/0.6-experimental"
FAMILIES = {
    "OCCURRENCE_BINDING": (
        "VALID", "CLAIMED_AT_MUTATION", "EVENT_ID_MUTATION", "PROOF_TRANSPLANT",
        "KEY_SUBSTITUTION", "CONTENT_MUTATION", "AUTHORIZATION_TRANSPLANT", "ENVELOPE_SWAP",
    ),
    "STRICT_INGESTION": (
        "VALID", "DUPLICATE_KEY", "UNKNOWN_FIELD", "LONE_SURROGATE",
        "OVERSIZED", "DEPTH_BOMB", "NONFINITE", "UNSAFE_INTEGER",
    ),
    "CRASH_RECOVERY": (
        "BEFORE_PREPARE", "AFTER_PREPARE", "AFTER_DISPATCH_COMMIT", "AFTER_REMOTE_EFFECT",
        "AFTER_OUTCOME_APPEND", "JOURNAL_CORRUPTION", "CONCURRENT_CLAIM", "CLEAN_RUN",
    ),
    "IDEMPOTENT_DISPATCH": (
        "VERIFIED_FIRST", "VERIFIED_DUPLICATE", "VERIFIED_RETRY_AFTER_CRASH",
        "DECLARED_IRREVERSIBLE", "NONE_IRREVERSIBLE", "KEY_COLLISION",
        "MISMATCHED_KEY", "MISMATCHED_ADAPTER",
    ),
    "RECONCILIATION": (
        "QUERY_SUCCESS", "QUERY_FAILURE", "QUERY_UNKNOWN", "UNAVAILABLE",
        "DELAYED_SUCCESS", "FALSE_SUCCESS", "STALE_PROVIDER_REF", "WRONG_INTENT",
    ),
    "CONFLICTING_OUTCOMES": (
        "SAME_SUCCESS", "SAME_FAILURE", "SUCCESS_THEN_FAILURE", "FAILURE_THEN_SUCCESS",
        "TWO_PROVIDER_REFS", "TWO_EVIDENCE_HASHES", "SPLIT_WITNESS", "REORDERED_TERMINALS",
    ),
    "INTENT_AUTHORITY_EPOCH": (
        "VALID", "STALE_EPOCH", "WRONG_AUTHORITY", "WIDENED_SCOPE",
        "CHANGED_EFFECT", "MISSING_EFFECT_WARRANT", "EXPIRED_INTENT", "REUSED_INTENT",
    ),
    "CHALLENGE_DEADLINE": (
        "OPEN_BEFORE", "OPEN_AT_BOUNDARY", "OPEN_AFTER", "LOCAL_CLOCK_ONLY",
        "CHECKPOINT_ROLLBACK", "WRONG_DOMAIN", "NO_DECISION_AUTHORITY", "TARGET_IMMUTABLE",
    ),
    "FINDING_AUTHORITY": (
        "VALID_FORUM", "OPENING_PARTY", "EVIDENCE_APPRAISER", "SETTLEMENT_AUTHORITY",
        "FORGED_FORUM", "WRONG_SCOPE", "INDETERMINATE", "CONFLICT",
    ),
    "REMEDY_AUTHORITY": (
        "VALID_SETTLEMENT", "COMPLETED", "FORUM_AS_SETTLER", "OPENER_AS_SETTLER",
        "FORGED_TOKEN", "WRONG_SCOPE", "REJECTED_FINDING", "NO_FINDING",
    ),
    "HARM_FLOOR": (
        "COMPENSABLE", "REVERSIBLE_AUTHORIZED", "HUMAN_REVIEW", "HUMAN_REVIEW_WITH_RESERVE",
        "CATEGORICAL", "CATEGORICAL_WITH_RESERVE", "CATEGORICAL_RENAMED", "CATEGORICAL_DECOMPOSED",
    ),
    "REMEDY_EPOCH_PARENT": (
        "VALID", "STALE_EPOCH", "CROSS_EPOCH", "MISSING_PARENT",
        "WRONG_PARENT", "TARGET_MUTATION", "HISTORY_REWRITE", "REPLAY_OLD_REMEDY",
    ),
}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def expected(family: str, attack: str) -> str:
    # Deliberately mirrored in the zero-import packet generator, while the
    # runtime checker derives exits independently in experimental.f13.
    from_expected = {
        "OCCURRENCE_BINDING": "ACCEPT" if attack == "VALID" else "REJECT/OCCURRENCE_PROOF",
        "STRICT_INGESTION": "ACCEPT" if attack == "VALID" else "REJECT/UNTRUSTED_INPUT",
    }
    if family in from_expected:
        return from_expected[family]
    # This source-only generator may import the closed evaluator when run from
    # a checkout; the frozen packet and its verifier have no such dependency.
    import sys
    sys.path.insert(0, str(ROOT.parents[2] / "src"))
    from bulla.experimental.f13 import evaluate_f13_case
    return evaluate_f13_case({"family": family, "attack": attack})


def main() -> None:
    cases = []
    for family_index, (family, attacks) in enumerate(FAMILIES.items(), 1):
        for variant_index, attack in enumerate(attacks, 1):
            case = {
                "schema_version": "0.6-experimental",
                "profile": PROFILE,
                "case_id": f"F13-{family_index:02d}-{variant_index:02d}",
                "family": family,
                "attack": attack,
                "oracle_class": "MACHINE",
                "required_exit": expected(family, attack),
                "external_authors": 0,
                "evidence_class": "INTERNAL_CAPTIVE",
            }
            case["case_hash"] = digest(case)
            cases.append(case)
    cases_path = ROOT / "f13-cases.json"
    cases_path.write_text(json.dumps({"cases": cases}, indent=2, sort_keys=True) + "\n")
    report = {
        "profile": PROFILE,
        "classification": "INTERNAL_CAPTIVE_QUALIFICATION",
        "family_count": len(FAMILIES),
        "case_count": len(cases),
        "external_authors": 0,
        "external_adjudicators": 0,
        "production_effects": 0,
        "cases_hash": "sha256:" + hashlib.sha256(cases_path.read_bytes()).hexdigest(),
    }
    report_path = ROOT / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    from bulla.experimental.causal_model import qualification_report
    state_report_path = ROOT / "state-model-report.json"
    state_report_path.write_text(
        json.dumps(qualification_report(), indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "profile": PROFILE,
        "classification": report["classification"],
        "artifacts": {
            "f13-cases.json": report["cases_hash"],
            "report.json": "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "state-model-report.json": "sha256:" + hashlib.sha256(state_report_path.read_bytes()).hexdigest(),
        },
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
