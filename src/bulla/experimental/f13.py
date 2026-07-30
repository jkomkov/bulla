"""Closed decision oracle for Golden F13 causal-answerability cases."""

from __future__ import annotations

from typing import Any


def evaluate_f13_case(case: dict[str, Any]) -> str:
    """Evaluate one case from bound attack coordinates, never its planted label."""
    family = case["family"]
    attack = case["attack"]
    if family == "OCCURRENCE_BINDING":
        return "ACCEPT" if attack == "VALID" else "REJECT/OCCURRENCE_PROOF"
    if family == "STRICT_INGESTION":
        return "ACCEPT" if attack == "VALID" else "REJECT/UNTRUSTED_INPUT"
    if family == "CRASH_RECOVERY":
        return {
            "BEFORE_PREPARE": "NO_ACTION",
            "AFTER_PREPARE": "REPLAYABLE/PREPARED",
            "AFTER_DISPATCH_COMMIT": "REPLAYABLE/UNKNOWN",
            "AFTER_REMOTE_EFFECT": "REPLAYABLE/UNKNOWN",
            "AFTER_OUTCOME_APPEND": "TERMINAL/SUCCEEDED",
            "JOURNAL_CORRUPTION": "ROUTE/CONFLICT",
            "CONCURRENT_CLAIM": "REPLAYABLE/ONE_DISPATCH",
            "CLEAN_RUN": "TERMINAL/SUCCEEDED",
        }[attack]
    if family == "IDEMPOTENT_DISPATCH":
        return {
            "VERIFIED_FIRST": "SUCCEEDED/ONE_EFFECT",
            "VERIFIED_DUPLICATE": "SUCCEEDED/ONE_EFFECT",
            "VERIFIED_RETRY_AFTER_CRASH": "SUCCEEDED/ONE_EFFECT",
            "DECLARED_IRREVERSIBLE": "ROUTE/IDEMPOTENCY_UNVERIFIED",
            "NONE_IRREVERSIBLE": "ROUTE/IDEMPOTENCY_UNVERIFIED",
            "KEY_COLLISION": "ROUTE/IDEMPOTENCY_COLLISION",
            "MISMATCHED_KEY": "REJECT/INTENT_MISMATCH",
            "MISMATCHED_ADAPTER": "REJECT/INTENT_MISMATCH",
        }[attack]
    if family == "RECONCILIATION":
        return {
            "QUERY_SUCCESS": "TERMINAL/SUCCEEDED",
            "QUERY_FAILURE": "TERMINAL/FAILED",
            "QUERY_UNKNOWN": "REPLAYABLE/UNKNOWN",
            "UNAVAILABLE": "REPLAYABLE/UNKNOWN",
            "DELAYED_SUCCESS": "TERMINAL/SUCCEEDED",
            "FALSE_SUCCESS": "ROUTE/CONFLICT",
            "STALE_PROVIDER_REF": "ROUTE/CONFLICT",
            "WRONG_INTENT": "REJECT/INTENT_MISMATCH",
        }[attack]
    if family == "CONFLICTING_OUTCOMES":
        return "TERMINAL/SUCCEEDED" if attack == "SAME_SUCCESS" else (
            "TERMINAL/FAILED" if attack == "SAME_FAILURE" else "ROUTE/CONFLICT"
        )
    if family == "INTENT_AUTHORITY_EPOCH":
        return "ACCEPT" if attack == "VALID" else {
            "STALE_EPOCH": "TERM_STALE",
            "WRONG_AUTHORITY": "ROUTE/AUTHORITY_MISMATCH",
            "WIDENED_SCOPE": "REJECT/SCOPE_MISMATCH",
            "CHANGED_EFFECT": "ROUTE/EFFECT_WARRANT_CHANGED",
            "MISSING_EFFECT_WARRANT": "REJECT/MISSING_WARRANT",
            "EXPIRED_INTENT": "TERMINAL/EXPIRED",
            "REUSED_INTENT": "REJECT/TERMINAL_INTENT",
        }[attack]
    if family == "CHALLENGE_DEADLINE":
        return {
            "OPEN_BEFORE": "OPEN",
            "OPEN_AT_BOUNDARY": "OPEN",
            "OPEN_AFTER": "TERMINAL/EXPIRED",
            "LOCAL_CLOCK_ONLY": "REJECT/UNWITNESSED_TIME",
            "CHECKPOINT_ROLLBACK": "REJECT/CHECKPOINT_REGRESSION",
            "WRONG_DOMAIN": "REJECT/CHECKPOINT_DOMAIN",
            "NO_DECISION_AUTHORITY": "OPEN",
            "TARGET_IMMUTABLE": "OPEN",
        }[attack]
    if family == "FINDING_AUTHORITY":
        return "FINDING_ISSUED" if attack == "VALID_FORUM" else (
            "ROUTE/INDETERMINATE" if attack == "INDETERMINATE" else
            "ROUTE/CONFLICT" if attack == "CONFLICT" else "REJECT/FORUM_AUTHORITY"
        )
    if family == "REMEDY_AUTHORITY":
        return {
            "VALID_SETTLEMENT": "REMEDY_PENDING",
            "COMPLETED": "REMEDY_COMPLETED",
            "FORUM_AS_SETTLER": "REJECT/REMEDY_AUTHORITY",
            "OPENER_AS_SETTLER": "REJECT/REMEDY_AUTHORITY",
            "FORGED_TOKEN": "REJECT/REMEDY_AUTHORITY",
            "WRONG_SCOPE": "REJECT/REMEDY_AUTHORITY",
            "REJECTED_FINDING": "CLOSED/NO_REMEDY",
            "NO_FINDING": "REJECT/MISSING_SUSTAINED_FINDING",
        }[attack]
    if family == "HARM_FLOOR":
        return "REMEDY_PENDING" if attack in {"COMPENSABLE", "REVERSIBLE_AUTHORIZED"} else (
            "ROUTE/HUMAN_REVIEW_REQUIRED" if attack in {"HUMAN_REVIEW", "HUMAN_REVIEW_WITH_RESERVE"}
            else "REFUSE/CATEGORICAL_EFFECT"
        )
    if family == "REMEDY_EPOCH_PARENT":
        return "REMEDY_PENDING" if attack == "VALID" else {
            "STALE_EPOCH": "TERM_STALE",
            "CROSS_EPOCH": "TERM_STALE",
            "MISSING_PARENT": "REJECT/PARENT_HASH",
            "WRONG_PARENT": "REJECT/PARENT_HASH",
            "TARGET_MUTATION": "REJECT/IMMUTABLE_TARGET",
            "HISTORY_REWRITE": "REJECT/APPEND_ONLY",
            "REPLAY_OLD_REMEDY": "TERM_STALE",
        }[attack]
    raise ValueError(f"unknown F13 family {family!r}")
