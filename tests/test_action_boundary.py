from __future__ import annotations

import concurrent.futures

import pytest

pytest.importorskip("nacl")

from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.action_boundary import (
    AdapterCapabilities,
    ActionBoundary,
    BoundaryError,
    BoundaryPolicy,
    BoundaryState,
    DispatchResult,
    LocalPaymentRail,
    NonIdempotentAdapter,
    OutcomeClass,
    IdempotencyStatus,
    OrderingStatus,
    ReconciliationStatus,
)
from bulla.experimental.frsl import RelationDecl, Signature, atom
from bulla.experimental.generalization import EffectWarrant, HarmClass
from bulla.experimental.scope import StructuredScope
from bulla.identity import LocalEd25519Signer


def digest(ch: str) -> str:
    return "sha256:" + ch * 64


EPOCH = digest("1")
REGIME = digest("2")


def warrant(harm: HarmClass = HarmClass.COMPENSABLE) -> EffectWarrant:
    signature = Signature(
        sorts={"Action": ("a0",)},
        relations={"effect": RelationDecl("effect", ("Action",))},
    )
    scope = StructuredScope(
        signature,
        {"op": "forall", "var": "x", "sort": "Action", "body": atom("effect", ({"var": "x"},))},
    )
    return EffectWarrant(
        effect_predicate=atom("effect", ({"const": "a0"},)),
        applicability_scope=scope, harm_class=harm,
        protected_effect_hashes=(digest("3"),), semantic_epoch=EPOCH,
        authority_regime_hash=REGIME, warrant_receipt_hash=digest("4"),
    )


def envelope(signer: LocalEd25519Signer) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal=signer.issuer, policy="policy://boundary"),
        bounds=Bounds(scope="payments.charge"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum("https://forum.example", digest("5")),
            remedies=(Remedy("challenge", "bulla experimental challenge replay", "forum:payments"),),
        ),
        retention_class="operational",
    )


def prepared(tmp_path, *, adapter=None, harm=HarmClass.COMPENSABLE):
    signer = LocalEd25519Signer.generate()
    adapter = adapter or LocalPaymentRail()
    boundary = ActionBoundary(tmp_path / "boundary.sqlite", policy=BoundaryPolicy(REGIME, EPOCH))
    snapshot = boundary.prepare(
        request={"amount_micros": 12_500_000, "currency": "USD"},
        adapter=adapter, idempotency_key="charge-001", effect_warrant=warrant(harm),
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:00Z",
        deadline_checkpoint={"domain": "test", "value": 10},
        intent_id="6f9a62a4-f431-422e-b587-24e6e5169624",
        event_id="d76f9175-c19e-4d6a-a084-a73d75cb260d",
    )
    return boundary, adapter, signer, snapshot


def test_intent_is_durable_before_dispatch_and_reconstructs_after_restart(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)
    assert snapshot.state is BoundaryState.PREPARED
    assert len(boundary.receipt_history(snapshot.intent_id)) == 1
    reopened = ActionBoundary(boundary.path, policy=BoundaryPolicy(REGIME, EPOCH))
    assert reopened.inspect(snapshot.intent_id) == snapshot
    assert reopened.receipt_history(snapshot.intent_id)[0]["action"]["type"] == "bulla.action.intent"


def test_successful_dispatch_is_hash_chained_and_idempotent(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)
    result = boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
    )
    assert result.state is BoundaryState.SUCCEEDED
    assert rail.effect_count == 1
    history = boundary.receipt_history(snapshot.intent_id)
    assert [event["action"]["type"] for event in history] == [
        "bulla.action.intent", "bulla.action.dispatch", "bulla.action.outcome",
    ]
    assert history[1]["action"]["subject"]["parent_attestation_hash"] == history[0]["hashes"]["attestation"]
    assert history[2]["action"]["subject"]["parent_attestation_hash"] == history[1]["hashes"]["attestation"]


def test_crash_after_remote_effect_recovers_without_second_effect(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)
    crashed = boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
        crash_after_remote=True,
    )
    assert crashed.state is BoundaryState.DISPATCHING and rail.effect_count == 1
    recovered = boundary.recover(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:02Z",
    )
    assert recovered.state is BoundaryState.SUCCEEDED
    assert rail.effect_count == 1


def test_crash_before_remote_effect_becomes_unknown_then_safe_retry(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)
    boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
        crash_after_commit=True,
    )
    unresolved = boundary.recover(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:02Z",
    )
    assert unresolved.state is BoundaryState.UNKNOWN
    completed = boundary.retry_dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:03Z",
    )
    assert completed.state is BoundaryState.SUCCEEDED and rail.effect_count == 1


def test_unsafe_irreversible_adapter_routes_without_dispatch(tmp_path):
    adapter = NonIdempotentAdapter()
    boundary, _, signer, snapshot = prepared(tmp_path, adapter=adapter)
    routed = boundary.dispatch(
        snapshot.intent_id, adapter=adapter, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
    )
    assert routed.state is BoundaryState.ROUTED
    assert routed.cause == "NO_VERIFIED_IDEMPOTENCY_OR_RECONCILIATION"


@pytest.mark.parametrize(
    ("harm", "cause"),
    [
        (HarmClass.CATEGORICAL_REFUSE, "CATEGORICAL_EFFECT_BARRIER"),
        (HarmClass.HUMAN_REVIEW_REQUIRED, "HUMAN_REVIEW_REQUIRED"),
    ],
)
def test_lexical_effect_barriers_route_even_with_safe_adapter(tmp_path, harm, cause):
    boundary, rail, signer, snapshot = prepared(tmp_path, harm=harm)
    result = boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
    )
    assert result.state is BoundaryState.ROUTED and result.cause == cause
    assert rail.effect_count == 0


def test_conflicting_terminal_outcome_is_preserved_as_conflict(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)
    boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
    )
    conflict = boundary.record_external_outcome(
        snapshot.intent_id,
        result=DispatchResult(OutcomeClass.FAILED, "provider:contradiction", digest("6")),
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:02Z",
    )
    assert conflict.state is BoundaryState.CONFLICT
    assert conflict.cause == "CONFLICTING_TERMINAL_OUTCOME"


def test_concurrent_workers_cannot_both_claim_one_intent(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)

    def run():
        try:
            return boundary.dispatch(
                snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
                envelope=envelope(signer), signer=signer,
                claimed_at="2026-07-21T22:00:01Z", crash_after_commit=True,
            ).state
        except BoundaryError:
            return "REJECTED"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results.count(BoundaryState.DISPATCHING) == 1
    assert results.count("REJECTED") == 1


def test_idempotency_key_is_bound_to_one_exact_intent(tmp_path):
    boundary, rail, signer, first = prepared(tmp_path)
    repeated = boundary.prepare(
        request={"amount_micros": 12_500_000, "currency": "USD"},
        adapter=rail, idempotency_key="charge-001", effect_warrant=warrant(),
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T23:00:00Z",
        deadline_checkpoint={"domain": "test", "value": 10},
    )
    assert repeated == first
    with pytest.raises(BoundaryError, match="different intent"):
        boundary.prepare(
            request={"amount_micros": 99_000_000, "currency": "USD"},
            adapter=rail, idempotency_key="charge-001", effect_warrant=warrant(),
            envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T23:00:01Z",
            deadline_checkpoint={"domain": "test", "value": 10},
        )


def test_adapter_capability_or_envelope_substitution_fails_before_io(tmp_path):
    boundary, rail, signer, snapshot = prepared(tmp_path)

    class InflatedRail(LocalPaymentRail):
        capabilities = AdapterCapabilities(
            IdempotencyStatus.VERIFIED, ReconciliationStatus.QUERYABLE,
            OrderingStatus.PROVIDER, reversible_effect=True,
        )

    with pytest.raises(BoundaryError, match="capabilities"):
        boundary.dispatch(
            snapshot.intent_id, adapter=InflatedRail(), idempotency_key="charge-001",
            envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
        )
    changed = RecourseEnvelope(
        authority=envelope(signer).authority,
        bounds=Bounds(scope="payments.refund"),
        recourse=envelope(signer).recourse,
        retention_class="operational",
    )
    with pytest.raises(BoundaryError, match="envelope differs"):
        boundary.dispatch(
            snapshot.intent_id, adapter=rail, idempotency_key="charge-001",
            envelope=changed, signer=signer, claimed_at="2026-07-21T22:00:01Z",
        )
    assert rail.effect_count == 0


def test_unpermitted_provider_outcome_is_preserved_as_conflict(tmp_path):
    signer = LocalEd25519Signer.generate()
    rail = LocalPaymentRail()
    boundary = ActionBoundary(tmp_path / "restricted.sqlite", policy=BoundaryPolicy(REGIME, EPOCH))
    snapshot = boundary.prepare(
        request={"amount_micros": 1, "currency": "USD"}, adapter=rail,
        idempotency_key="restricted", effect_warrant=warrant(), envelope=envelope(signer),
        signer=signer, claimed_at="2026-07-21T22:00:00Z",
        deadline_checkpoint={"domain": "test", "value": 10},
        permitted_terminal_outcomes=(OutcomeClass.FAILED,),
    )
    result = boundary.dispatch(
        snapshot.intent_id, adapter=rail, idempotency_key="restricted",
        envelope=envelope(signer), signer=signer, claimed_at="2026-07-21T22:00:01Z",
    )
    assert result.state is BoundaryState.CONFLICT
    assert result.cause == "UNPERMITTED_EXTERNAL_OUTCOME"
    assert rail.effect_count == 1
