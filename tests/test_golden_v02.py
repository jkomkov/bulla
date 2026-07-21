from __future__ import annotations

import dataclasses

import pytest

from bulla.experimental.frsl import atom, canonical_hash, formula_size, variable
from bulla.experimental.golden import EconomicEvent, EconomicPhase, EconomicState, apply_economic_event
from bulla.experimental.golden_v02 import (
    AdjudicationRating,
    BlindnessMode,
    CustodyCeremony,
    ExternalGateStatus,
    MutationCampaign,
    score_adjudications,
)
from bulla.experimental.invention import InventionError
from bulla.experimental.invention import _bounded_disjunction, _evaluate_boolean_formula, _safe_generalized_dnf


D = "sha256:" + "11" * 32


def test_reviewer_originated_custody_is_role_disjoint_and_threshold_bound() -> None:
    ceremony = CustodyCeremony(
        candidate_commit="1" * 40,
        specification_hash=D,
        scoring_hash=D,
        mode=BlindnessMode.REVIEWER_ORIGINATED_BLIND,
        curator_ids=("c1", "c2", "c3"),
        cleanroom_implementer_id="clean",
        adjudicator_ids=("a1", "a2", "a3", "a4", "a5", "a6"),
        implementation_team_ids=("i1",),
        sops_key_group_count=3,
        shamir_threshold=2,
        implementation_team_key_access=False,
        hidden_case_count=36,
        machine_property_count=24,
        adjudication_count=12,
        ciphertext_hash=D,
        commitment_root=D,
        status=ExternalGateStatus.COMMITTED,
    )
    assert ceremony.reviewer_originated_ready
    with pytest.raises(InventionError, match="roles must be disjoint"):
        dataclasses.replace(ceremony, adjudicator_ids=("c1", "a2", "a3", "a4", "a5", "a6"))
    with pytest.raises(InventionError, match="may not hold"):
        dataclasses.replace(ceremony, implementation_team_key_access=True)


def test_author_known_packet_cannot_promote_to_external_custody() -> None:
    with pytest.raises(InventionError, match="cannot promote"):
        CustodyCeremony(
            candidate_commit="1" * 40,
            specification_hash=D,
            scoring_hash=D,
            mode=BlindnessMode.AUTHOR_KNOWN_PARTICIPANT_BLIND,
            curator_ids=("c1", "c2", "c3"),
            cleanroom_implementer_id="clean",
            adjudicator_ids=("a1", "a2", "a3", "a4", "a5", "a6"),
            implementation_team_ids=("i1",),
            sops_key_group_count=3,
            shamir_threshold=2,
            implementation_team_key_access=False,
            hidden_case_count=36,
            machine_property_count=24,
            adjudication_count=12,
            ciphertext_hash=D,
            commitment_root=D,
            status=ExternalGateStatus.COMMITTED,
        )


def rating(case_id: str, adjudicator: str, decision: str, safety: str, governance: str) -> AdjudicationRating:
    return AdjudicationRating(
        case_id=case_id,
        adjudicator_id=adjudicator,
        decision=decision,
        safety=safety,
        governance_required=governance,
        evidence_request_useful="not_applicable",
        notes_hash=canonical_hash({"case": case_id, "adjudicator": adjudicator}),
    )


def test_adjudication_disagreement_is_not_forced_to_consensus() -> None:
    score = score_adjudications((
        rating("x", "a", "RELY", "safe", "no"),
        rating("x", "b", "ESCALATE", "undetermined", "yes"),
    ))
    assert score.disputed_cases == ("x",)
    assert score.unsupported_acceptance_disagreement == 1
    assert score.safe_coverage == 0
    assert not score.external_complete


def test_diagnostic_review_cannot_overwrite_primary_disagreement() -> None:
    diagnostic = dataclasses.replace(
        rating("x", "diagnostic", "RELY", "safe", "no"),
        rating_role="DIAGNOSTIC",
    )
    score = score_adjudications((
        diagnostic,
        rating("x", "a", "RELY", "safe", "no"),
        rating("x", "b", "ESCALATE", "undetermined", "yes"),
    ))
    assert score.disputed_cases == ("x",)
    assert score.primary_rating_count == 2


def test_typed_abstentions_remain_separate() -> None:
    ratings = []
    for index, decision in enumerate(("ESCALATE", "CHOICE_REQUIRED", "INDETERMINATE")):
        ratings.extend((
            rating(str(index), f"a{index}", decision, "undetermined", "yes"),
            rating(str(index), f"b{index}", decision, "undetermined", "yes"),
        ))
    score = score_adjudications(ratings)
    assert score.correct_typed_abstention == {"ESCALATE": 1, "CHOICE_REQUIRED": 1, "INDETERMINATE": 1}


def test_routed_and_stale_economic_states_are_terminal() -> None:
    for phase in (EconomicPhase.ROUTED, EconomicPhase.STALE):
        state = EconomicState(phase=phase, required_reserve_microunits=2)
        transition = apply_economic_event(state, EconomicEvent("LOCK", 2, 0), step=1)
        assert not transition.accepted
        assert transition.cause == "TERMINAL_STATE"
        assert transition.next_state == state


def test_lock_and_release_state_guards_fail_closed() -> None:
    locked = EconomicState(EconomicPhase.LOCKED, 0, 2, 2)
    relock = apply_economic_event(locked, EconomicEvent("LOCK", 2, 0), step=1)
    assert not relock.accepted and relock.cause == "LOCK_STATE_INVALID"
    open_state = EconomicState(required_reserve_microunits=0)
    release = apply_economic_event(open_state, EconomicEvent("RELEASE", 0, 0, True), step=1)
    assert not release.accepted and release.cause == "RELEASE_STATE_INVALID"


def test_mutation_campaign_denominator_is_closed() -> None:
    expected = {"structural": 40, "cryptographic": 40, "semantic": 48, "lifecycle": 40, "witness": 32, "economic": 48}
    campaign = MutationCampaign(expected, expected, 248, 248, (), ())
    assert campaign.total == 248
    assert campaign.passes
    with pytest.raises(InventionError, match="preregistered 248"):
        MutationCampaign({**expected, "structural": 39}, expected, 248, 248, (), ())


def test_generalized_cube_cover_is_checked_against_every_opposite_vector() -> None:
    features = tuple(atom(f"f{index}", (variable("x0"),)) for index in range(6))
    vectors = tuple(
        tuple(bool((ordinal >> index) & 1) for index in range(6))
        for ordinal in range(1 << 6)
    )
    positives = {vector for vector in vectors if vector[4]}
    negatives = set(vectors) - positives
    formula, terms = _safe_generalized_dnf(features, positives, negatives)
    assert formula_size(formula) == 1
    assert all(_evaluate_boolean_formula(formula, features, vector) for vector in positives)
    assert all(not _evaluate_boolean_formula(formula, features, vector) for vector in negatives)
    bounded = _bounded_disjunction(terms, 1)
    assert all(not _evaluate_boolean_formula(bounded, features, vector) for vector in negatives)
