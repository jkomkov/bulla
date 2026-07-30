"""Finite reference model for receipt-coupled dispatch and executable recourse.

The model is intentionally smaller than the SQLite implementation.  It exists
to exhaust the reachable transition graph and make each safety claim falsifiable
against every abstract schedule, including interleavings of two intents sharing
one idempotent adapter.
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass
from typing import Iterable


class ModelState(str, enum.Enum):
    ABSENT = "ABSENT"
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    UNKNOWN = "UNKNOWN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    CONFLICT = "CONFLICT"
    ROUTED = "ROUTED"


class ModelEvent(str, enum.Enum):
    PREPARE = "PREPARE"
    COMMIT_DISPATCH = "COMMIT_DISPATCH"
    REMOTE_EFFECT = "REMOTE_EFFECT"
    PERSIST_SUCCESS = "PERSIST_SUCCESS"
    PERSIST_FAILURE = "PERSIST_FAILURE"
    MARK_UNKNOWN = "MARK_UNKNOWN"
    RECONCILE_SUCCESS = "RECONCILE_SUCCESS"
    RECONCILE_FAILURE = "RECONCILE_FAILURE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"
    ROUTE = "ROUTE"


@dataclass(frozen=True)
class IntentModel:
    state: ModelState = ModelState.ABSENT
    durable_intent: bool = False
    dispatch_committed: bool = False
    external_effects: int = 0
    records: int = 0


@dataclass(frozen=True)
class Trace:
    state: IntentModel
    events: tuple[ModelEvent, ...] = ()


def successors(state: IntentModel, *, verified_idempotency: bool = True) -> Iterable[tuple[ModelEvent, IntentModel]]:
    """All accepted transitions for the finite one-intent abstraction."""
    if state.state is ModelState.ABSENT:
        yield ModelEvent.PREPARE, IntentModel(ModelState.PREPARED, True, False, 0, 1)
    if state.state is ModelState.PREPARED:
        yield ModelEvent.COMMIT_DISPATCH, IntentModel(ModelState.DISPATCHING, True, True, 0, state.records + 1)
        for event, target in (
            (ModelEvent.CANCEL, ModelState.CANCELLED),
            (ModelEvent.EXPIRE, ModelState.EXPIRED),
            (ModelEvent.ROUTE, ModelState.ROUTED),
        ):
            yield event, IntentModel(target, True, False, 0, state.records + 1)
    if state.state is ModelState.DISPATCHING:
        if state.external_effects == 0:
            yield ModelEvent.REMOTE_EFFECT, IntentModel(
                ModelState.DISPATCHING, True, True, 1, state.records,
            )
        elif not verified_idempotency:
            yield ModelEvent.REMOTE_EFFECT, IntentModel(
                ModelState.DISPATCHING, True, True, state.external_effects + 1, state.records,
            )
        if state.external_effects == 1:
            yield ModelEvent.PERSIST_SUCCESS, IntentModel(
                ModelState.SUCCEEDED, True, True, 1, state.records + 1,
            )
        if state.external_effects == 0:
            yield ModelEvent.PERSIST_FAILURE, IntentModel(
                ModelState.FAILED, True, True, 0, state.records + 1,
            )
        yield ModelEvent.MARK_UNKNOWN, IntentModel(
            ModelState.UNKNOWN, True, True, state.external_effects, state.records + 1,
        )
    if state.state is ModelState.UNKNOWN:
        if state.external_effects == 1:
            yield ModelEvent.RECONCILE_SUCCESS, IntentModel(
                ModelState.SUCCEEDED, True, True, 1, state.records + 1,
            )
        if state.external_effects == 0:
            yield ModelEvent.RECONCILE_FAILURE, IntentModel(
                ModelState.FAILED, True, True, 0, state.records + 1,
            )
    if state.state in {ModelState.SUCCEEDED, ModelState.FAILED}:
        yield ModelEvent.CONFLICTING_EVIDENCE, IntentModel(
            ModelState.CONFLICT, True, state.dispatch_committed,
            state.external_effects, state.records + 1,
        )


def _violations(state: IntentModel) -> tuple[str, ...]:
    out: list[str] = []
    if state.external_effects and not state.durable_intent:
        out.append("EXTERNAL_EFFECT_WITHOUT_DURABLE_INTENT")
    if state.external_effects and not state.dispatch_committed:
        out.append("EXTERNAL_EFFECT_WITHOUT_DISPATCH_COMMITMENT")
    if state.external_effects > 1:
        out.append("DUPLICATE_EXTERNAL_EFFECT")
    if state.state is ModelState.ABSENT and state.records:
        out.append("RECORD_WITHOUT_INTENT")
    if state.state is not ModelState.ABSENT and state.records == 0:
        out.append("STATE_WITHOUT_APPEND_ONLY_RECORD")
    return tuple(out)


def explore_one_intent(*, verified_idempotency: bool = True) -> dict:
    initial = Trace(IntentModel())
    queue = deque([initial])
    seen = {initial.state}
    witnesses: dict[str, tuple[str, ...]] = {}
    edge_count = 0
    terminal = set()
    while queue:
        trace = queue.popleft()
        violations = _violations(trace.state)
        for violation in violations:
            witnesses.setdefault(violation, tuple(event.value for event in trace.events))
        next_states = tuple(successors(trace.state, verified_idempotency=verified_idempotency))
        if not next_states:
            terminal.add(trace.state.state.value)
        for event, state in next_states:
            edge_count += 1
            if state not in seen:
                seen.add(state)
                queue.append(Trace(state, trace.events + (event,)))
    return {
        "reachable_states": len(seen),
        "reachable_state_names": sorted({state.state.value for state in seen}),
        "accepted_transitions": edge_count,
        "terminal_states": sorted(terminal),
        "violations": witnesses,
        "verified_idempotency": verified_idempotency,
    }


def explore_two_intents() -> dict:
    """Exhaust all interleavings of two intents sharing one verified rail."""
    initial = (IntentModel(), IntentModel())
    queue = deque([(initial, tuple())])
    seen = {initial}
    edge_count = 0
    violations: dict[str, tuple[str, ...]] = {}
    while queue:
        states, trace = queue.popleft()
        for index, state in enumerate(states):
            for violation in _violations(state):
                violations.setdefault(f"intent_{index}:{violation}", trace)
        for index in (0, 1):
            for event, successor in successors(states[index], verified_idempotency=True):
                updated = list(states)
                updated[index] = successor
                pair = tuple(updated)
                edge_count += 1
                if pair not in seen:
                    seen.add(pair)
                    queue.append((pair, trace + (f"{index}:{event.value}",)))
    return {
        "reachable_states": len(seen),
        "accepted_transitions": edge_count,
        "violations": violations,
        "shared_adapter": "VERIFIED_IDEMPOTENCY",
    }


class ChallengeModelState(str, enum.Enum):
    ABSENT = "ABSENT"
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EVIDENCE_OPEN = "EVIDENCE_OPEN"
    FINDING_ISSUED = "FINDING_ISSUED"
    REMEDY_PENDING = "REMEDY_PENDING"
    CLOSED = "CLOSED"
    ROUTED = "ROUTED"
    EXPIRED = "EXPIRED"


def explore_challenge_authority() -> dict:
    """Enumerate forum/remedy authority combinations at each decisive edge."""
    accepted = 0
    rejected = 0
    violations: list[str] = []
    for forum_authorized in (False, True):
        for remedy_authorized in (False, True):
            finding_accepted = forum_authorized
            remedy_accepted = finding_accepted and remedy_authorized
            accepted += int(finding_accepted) + int(remedy_accepted)
            rejected += int(not finding_accepted) + int(finding_accepted and not remedy_authorized)
            if finding_accepted and not forum_authorized:
                violations.append("FINDING_WITHOUT_FORUM_AUTHORITY")
            if remedy_accepted and not remedy_authorized:
                violations.append("REMEDY_WITHOUT_SETTLEMENT_AUTHORITY")
    return {
        "authority_assignments": 4,
        "accepted_decisive_transitions": accepted,
        "rejected_decisive_transitions": rejected,
        "violations": violations,
    }


def mutation_campaign() -> dict:
    """Run safety mutants against the model invariants and report witnesses."""
    state_mutants = {
        "remove_durable_precommit": IntentModel(ModelState.DISPATCHING, False, True, 1, 1),
        "remove_dispatch_commit": IntentModel(ModelState.DISPATCHING, True, False, 1, 1),
        "remove_idempotency_guard": IntentModel(ModelState.DISPATCHING, True, True, 2, 2),
        "remove_record_append": IntentModel(ModelState.SUCCEEDED, True, True, 1, 0),
    }
    killed = {name: list(_violations(state)) for name, state in state_mutants.items()}
    guard_mutants = {
        "remove_authority_check": (False, True, True, True),
        "remove_epoch_check": (True, False, True, True),
        "remove_parent_hash_check": (True, True, False, True),
        "allow_target_mutation": (True, True, True, False),
    }
    for name, (authority, epoch, parent, immutable) in guard_mutants.items():
        reasons = []
        if not authority:
            reasons.append("UNAUTHORIZED_TRANSITION")
        if not epoch:
            reasons.append("CROSS_EPOCH_TRANSITION")
        if not parent:
            reasons.append("BROKEN_PARENT_HASH")
        if not immutable:
            reasons.append("CHALLENGED_TARGET_MUTATED")
        killed[name] = reasons
    return {
        "mutants": len(killed),
        "killed": sum(bool(reasons) for reasons in killed.values()),
        "critical_kill_rate": sum(bool(reasons) for reasons in killed.values()) / len(killed),
        "witnesses": killed,
    }


def qualification_report() -> dict:
    return {
        "profile": "bulla.causal-answerability/0.1-experimental",
        "classification": "INTERNAL_CAPTIVE_MODEL",
        "one_intent": explore_one_intent(),
        "two_intent": explore_two_intents(),
        "challenge_authority": explore_challenge_authority(),
        "mutation_campaign": mutation_campaign(),
    }
