from __future__ import annotations

from bulla.experimental.causal_model import (
    explore_challenge_authority,
    explore_one_intent,
    explore_two_intents,
    mutation_campaign,
    qualification_report,
)


def test_complete_one_intent_model_has_no_safety_violation() -> None:
    report = explore_one_intent()
    assert report["reachable_states"] > 10
    assert report["accepted_transitions"] >= report["reachable_states"] - 1
    assert report["violations"] == {}
    assert {"SUCCEEDED", "FAILED", "CONFLICT", "ROUTED"} <= set(report["reachable_state_names"])


def test_bounded_two_intent_shared_adapter_has_no_safety_violation() -> None:
    report = explore_two_intents()
    assert report["reachable_states"] > 100
    assert report["violations"] == {}


def test_authority_roles_are_separate_in_complete_boolean_model() -> None:
    report = explore_challenge_authority()
    assert report["authority_assignments"] == 4
    assert report["violations"] == []
    assert report["rejected_decisive_transitions"] > 0


def test_all_critical_model_mutants_are_killed() -> None:
    report = mutation_campaign()
    assert report["mutants"] == report["killed"]
    assert report["critical_kill_rate"] == 1.0
    assert all(report["witnesses"].values())


def test_qualification_report_never_claims_external_evidence() -> None:
    report = qualification_report()
    assert report["classification"] == "INTERNAL_CAPTIVE_MODEL"
