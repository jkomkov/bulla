"""Deed v0.2 envelope — schema construction and the modality law.

The modality law: recourse under the absent master cannot assume a stateful
respondent, so every remedy must name its verifier and the stateful artifact
or stake it executes against. These tests pin that as a CONSTRUCTION-time
property: an envelope violating it cannot be built, parsed, or verified.
"""

from __future__ import annotations

import json

import pytest

from bulla.envelope import (
    DEED_SCHEMA_VERSION,
    Authority,
    Bounds,
    EnvelopeError,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
    ladder_ordered,
)


def _forum() -> Forum:
    return Forum(
        log_endpoint="https://registry.example/v1",
        trusted_root_ref="ots:anchored-root-2026-07-03",
    )


def _full_envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal="did:web:acme.example#operations",
            policy="sha256:" + "aa" * 32,
            delegation=("mandate:2026-07/ops-42",),
        ),
        bounds=Bounds(scope="repo:acme/billing", expires="2026-07-10T00:00:00Z",
                      rollback_window="PT72H"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=_forum(),
            remedies=(
                Remedy("recompute", "bulla verify --registry", "attestation:self"),
                Remedy("challenge", "rfc6962-inclusion", "root:pinned"),
                Remedy("cure", "bulla repair", "composition:sha256:" + "bb" * 32),
                Remedy("escalate", "human-review", "delegation:mandate:2026-07/ops-42"),
            ),
        ),
        retention_class="operational",
        disclosure_class="party",
    )


class TestModalityLaw:
    def test_remedy_without_anchor_refused(self):
        with pytest.raises(EnvelopeError, match="stateful anchor"):
            Remedy("revert", "compensating-action", "")

    def test_remedy_without_verifier_refused(self):
        with pytest.raises(EnvelopeError, match="verifier"):
            Remedy("cure", "  ", "composition:sha256:ff")

    def test_unknown_rung_refused(self):
        with pytest.raises(EnvelopeError, match="ladder"):
            Remedy("sue-the-agent", "court", "the-defendant")  # no such modality

    def test_escalate_requires_surviving_principal(self):
        with pytest.raises(EnvelopeError, match="surviving principal"):
            RecourseEnvelope(
                recourse=Recourse(
                    challenge_window="P7D",
                    forum=_forum(),
                    remedies=(Remedy("escalate", "human-review", "delegation:x"),),
                )
            )

    def test_appeal_path_with_no_remedies_refused(self):
        with pytest.raises(EnvelopeError, match="process theater"):
            Recourse(challenge_window="P7D", forum=_forum(), remedies=())

    def test_forum_must_pin_the_root(self):
        with pytest.raises(EnvelopeError, match="Pin-the-Root"):
            Forum(log_endpoint="https://registry.example/v1", trusted_root_ref="")


class TestSchema:
    def test_full_envelope_constructs_and_round_trips(self):
        env = _full_envelope()
        d = env.to_dict()
        assert d["deed_schema"] == DEED_SCHEMA_VERSION
        again = RecourseEnvelope.from_dict(d)
        assert again.to_dict() == d

    def test_canonical_is_deterministic(self):
        a = _full_envelope().canonical()
        b = RecourseEnvelope.from_dict(json.loads(a) if False else _full_envelope().to_dict()).canonical()
        assert a == b
        assert json.loads(a) == _full_envelope().to_dict()

    def test_empty_envelope_refused(self):
        with pytest.raises(EnvelopeError, match="vacuous"):
            RecourseEnvelope()

    def test_unknown_schema_version_refused(self):
        with pytest.raises(EnvelopeError, match="deed_schema"):
            RecourseEnvelope(bounds=Bounds(scope="x"), deed_schema="9.9")

    def test_unknown_retention_class_refused(self):
        with pytest.raises(EnvelopeError, match="retention_class"):
            RecourseEnvelope(bounds=Bounds(scope="x"), retention_class="forever")

    def test_from_dict_revalidates_modality_law(self):
        d = _full_envelope().to_dict()
        d["recourse"]["remedies"][0]["anchor"] = ""  # hostile serialization
        with pytest.raises(EnvelopeError, match="stateful anchor"):
            RecourseEnvelope.from_dict(d)

    def test_authority_requires_principal_and_policy(self):
        with pytest.raises(EnvelopeError, match="surviving principal"):
            Authority(principal="", policy="sha256:aa")
        with pytest.raises(EnvelopeError, match="policy"):
            Authority(principal="did:web:x", policy="")

    def test_bounds_requires_scope(self):
        with pytest.raises(EnvelopeError, match="scope"):
            Bounds(scope="")


class TestLadderOrder:
    def test_full_envelope_is_ladder_ordered(self):
        assert ladder_ordered(_full_envelope().recourse.remedies)

    def test_out_of_order_detected(self):
        remedies = (
            Remedy("escalate", "human-review", "delegation:x"),
            Remedy("recompute", "bulla verify", "attestation:self"),
        )
        assert not ladder_ordered(remedies)
