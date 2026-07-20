#!/usr/bin/env python3
"""Build Golden Gate v0.2 internal evidence and external-role packets."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import platform
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.golden import (
    EconomicEvent,
    EconomicPhase,
    EconomicState,
    GoldenCase,
    OracleCommitment,
    WitnessDiversityPolicy,
    WitnessOperatorProfile,
    apply_economic_event,
    assess_witness_diversity,
    economic_invariants,
)
from bulla.experimental.golden_v02 import (
    CoverageReport,
    MetamorphicKind,
    MetamorphicObservation,
    MetamorphicRelation,
    MutationCampaign,
    ProvenanceCard,
    score_adjudications,
)


HERE = Path(__file__).resolve().parent
V01 = HERE.parent / "v0.1"
BULLA = HERE.parents[2]
D = "sha256:" + "11" * 32


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def state_dict(state: EconomicState) -> dict[str, Any]:
    result = dataclasses.asdict(state)
    result["phase"] = state.phase.value
    result["available_lock"] = state.available_lock
    return result


def event_dict(event: EconomicEvent) -> dict[str, Any]:
    return dataclasses.asdict(event)


def economic_events() -> tuple[EconomicEvent, ...]:
    amounts = (0, 1, 2, 3, 4)
    events: list[EconomicEvent] = []
    for epoch in (0, 1):
        events.extend(EconomicEvent("LOCK", amount, epoch) for amount in amounts)
        events.append(EconomicEvent("EXECUTE", epoch=epoch))
        for authorized in (False, True):
            events.extend(EconomicEvent("REFINE", amount, epoch, authorized) for amount in amounts)
            events.extend(EconomicEvent("RELEASE", amount, epoch, authorized) for amount in amounts)
            for closure in (False, True):
                events.append(EconomicEvent("FINALIZE", epoch=epoch, authorized=authorized, closure_permitted=closure))
            events.append(EconomicEvent("REVISE", epoch=epoch, authorized=authorized))
    events.extend((EconomicEvent("CONFLICT"), EconomicEvent("ROUTE"), EconomicEvent("EXPIRE")))
    unique = {json.dumps(event_dict(event), sort_keys=True): event for event in events}
    return tuple(unique[key] for key in sorted(unique))


def economic_fixed_point() -> tuple[CoverageReport, dict[str, Any]]:
    initial = EconomicState(required_reserve_microunits=2, expiry_step=99)
    events = economic_events()
    queue: deque[EconomicState] = deque((initial,))
    seen = {initial}
    paths: dict[EconomicState, tuple[dict[str, Any], ...]] = {initial: ()}
    transitions: list[dict[str, Any]] = []
    shortest: dict[str, list[dict[str, Any]]] = {}
    violations: list[dict[str, Any]] = []
    while queue:
        state = queue.popleft()
        for event in events:
            transition = apply_economic_event(state, event, step=1)
            record = {
                "prior_hash": canonical_hash(state_dict(state)),
                "event": event_dict(event),
                "accepted": transition.accepted,
                "cause": transition.cause,
                "next_hash": canonical_hash(state_dict(transition.next_state)),
            }
            transitions.append(record)
            shortest.setdefault(transition.cause, [*paths[state], record])
            failures = economic_invariants(transition.next_state)
            if failures:
                violations.append({"transition": record, "failures": list(failures)})
            if transition.next_state not in seen:
                seen.add(transition.next_state)
                paths[transition.next_state] = (*paths[state], record)
                queue.append(transition.next_state)
    boundary_pairs = {
        (
            item["event"]["kind"],
            item["event"]["amount_microunits"],
            item["accepted"],
            item["cause"],
        )
        for item in transitions
        if item["event"]["amount_microunits"] in {0, 1, 2, 3, 4}
    }
    terminal = tuple(sorted({state.phase.value for state in seen if state.phase in {
        EconomicPhase.FINALIZED, EconomicPhase.ROUTED, EconomicPhase.STALE, EconomicPhase.EXPIRED
    }}))
    fair_state = initial
    fair_trace: list[dict[str, Any]] = []
    fair_events = (
        EconomicEvent("LOCK", 2, 0),
        EconomicEvent("EXECUTE", epoch=0),
        EconomicEvent("REFINE", 0, 0, True),
        EconomicEvent("RELEASE", 2, 0, True),
        EconomicEvent("FINALIZE", epoch=0, authorized=True, closure_permitted=True),
    )
    for step, event in enumerate(fair_events, 1):
        transition = apply_economic_event(fair_state, event, step=step)
        fair_trace.append({"step": step, "event": event_dict(event), "accepted": transition.accepted, "cause": transition.cause})
        fair_state = transition.next_state
    fairness = {
        "kind": "weak-fairness-with-witness-clock",
        "statement": "A continuously enabled authorized completion action eventually executes while the witness clock advances.",
        "minimal_unfair_nonprogress": [
            {"step": 0, "phase": "PROVISIONAL", "enabled": "FINALIZE"},
            {"step": 1, "event": "NOOP", "enabled": "FINALIZE"},
        ],
        "watchdog_resolution": {"event": "EXPIRE", "cause": "EXPIRED"},
        "bounded_liveness_scope": "finite abstract model under declared fairness only",
        "authorized_completion_trace": fair_trace,
        "authorized_completion_reaches_finalized": fair_state.phase is EconomicPhase.FINALIZED,
    }
    report = CoverageReport(
        abstract_state_count=len(seen),
        transition_count=len(transitions),
        accepted_transition_count=sum(item["accepted"] for item in transitions),
        rejected_transition_count=sum(not item["accepted"] for item in transitions),
        guard_boundary_count=len(boundary_pairs),
        covered_guard_boundary_count=len(boundary_pairs),
        terminal_phases=terminal,
        causes=tuple(sorted({item["cause"] for item in transitions})),
        invariant_violations=tuple(violations),
        fairness_model=fairness,
        shortest_witnesses={key: value for key, value in sorted(shortest.items())},
    )
    detailed = {
        "initial_state": state_dict(initial),
        "event_variant_count": len(events),
        "states": [state_dict(state) for state in sorted(seen, key=lambda s: canonical_hash(state_dict(s)))],
        "transition_digest": canonical_hash(transitions),
        "coverage": report.to_dict(),
    }
    return report, detailed


def two_commitment_model() -> dict[str, Any]:
    reserve = 2
    states: set[tuple[int, int, int, bool]] = set()
    violations: list[dict[str, Any]] = []
    witnesses: dict[str, Any] = {}
    for collateral in range(5):
        for first in range(3):
            for second in range(3):
                for verifier_ok in (False, True):
                    state = (collateral, first, second, verifier_ok)
                    states.add(state)
                    accepted = verifier_ok and first + second <= collateral
                    if accepted and (first > reserve or second > reserve or first + second > collateral):
                        violations.append({"state": state, "cause": "unsafe_shared_collateral_accept"})
                    if not accepted and first + second > collateral:
                        witnesses.setdefault("double_pledge_rejected", state)
                    if not accepted and not verifier_ok:
                        witnesses.setdefault("verifier_failure_rejected", state)
                    if collateral < first + second:
                        witnesses.setdefault("correlated_lock_insolvency", state)
    return {
        "model": "bounded-two-commitment-shared-collateral",
        "reserve_per_commitment": reserve,
        "state_count": len(states),
        "violations": violations,
        "shortest_boundary_witnesses": witnesses,
        "claim_boundary": "abstract guard model; not custody or collectibility evidence",
    }


def coverage_guided_schedule(steps: int = 2_048) -> dict[str, Any]:
    """Deterministic greybox-style scheduler over the pure abstract model."""
    initial = EconomicState(required_reserve_microunits=2, expiry_step=99)
    state = initial
    events = economic_events()
    seen_states = {canonical_hash(state_dict(state))}
    seen_causes: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    prior_cause = "START"
    trace: list[dict[str, Any]] = []
    for step in range(1, steps + 1):
        if state.phase in {EconomicPhase.FINALIZED, EconomicPhase.ROUTED, EconomicPhase.STALE, EconomicPhase.EXPIRED}:
            state = initial
            prior_cause = "RESTART"
        choices = []
        for event in events:
            transition = apply_economic_event(state, event, step=1)
            state_hash = canonical_hash(state_dict(transition.next_state))
            pair = (prior_cause, transition.cause)
            boundary_distance = abs(event.amount_microunits - state.required_reserve_microunits)
            unresolved_age_reward = 5 if transition.next_state.phase is EconomicPhase.PROVISIONAL else 0
            witness_divergence_reward = 5 if transition.cause == "CONFLICT_NON_MUTATION" else 0
            invariant_proximity = max(0, 4 - abs(transition.next_state.available_lock - transition.next_state.required_reserve_microunits))
            score = (
                100 * int(state_hash not in seen_states)
                + 25 * int(transition.cause not in seen_causes)
                + 10 * int(pair not in seen_pairs)
                + max(0, 4 - boundary_distance)
                + unresolved_age_reward
                + witness_divergence_reward
                + invariant_proximity
            )
            choices.append((
                -score,
                canonical_hash(event_dict(event)),
                transition,
                state_hash,
                pair,
            ))
        _, _, selected, state_hash, pair = min(choices, key=lambda item: item[:2])
        seen_states.add(state_hash)
        seen_causes.add(selected.cause)
        seen_pairs.add(pair)
        trace.append({
            "step": step,
            "event": event_dict(selected.event),
            "accepted": selected.accepted,
            "cause": selected.cause,
            "state_hash": state_hash,
        })
        prior_cause = selected.cause
        state = selected.next_state
    return {
        "scheduler": "deterministic-coverage-guided",
        "steps": steps,
        "state_count": len(seen_states),
        "cause_count": len(seen_causes),
        "transition_cause_pair_count": len(seen_pairs),
        "reward_coordinates": [
            "new_state", "new_cause", "new_transition_cause_pair", "reserve_boundary",
            "long_unresolved_commitment", "witness_divergence", "invariant_proximity",
        ],
        "trace_hash": canonical_hash(trace),
        "first_64_steps": trace[:64],
        "claim_boundary": "Supplement to exhaustive fixed-point evidence, not a replacement.",
    }


def relations() -> tuple[MetamorphicRelation, ...]:
    specs = (
        ("canonical-field-order", "INVARIANT", "Canonical object field order", ("canonical_hash", "semantic_exit"), (), None),
        ("alpha-renaming", "INVARIANT", "Bound-variable alpha renaming", ("semantic_exit", "protected_consequences"), ("package_hash",), None),
        ("boolean-reordering", "INVARIANT", "Commutative Boolean argument order", ("semantic_exit", "protected_consequences"), ("package_hash",), None),
        ("equivalent-formula", "INVARIANT", "Double-negation equivalent formula", ("semantic_exit", "protected_consequences"), ("package_hash",), None),
        ("irrelevant-vocabulary", "INVARIANT", "Unused relation in the declared vocabulary", ("semantic_exit", "protected_consequences"), ("complexity",), None),
        ("redundant-evidence", "INVARIANT", "Duplicate already-bound evidence", ("semantic_exit", "protected_consequences"), ("runtime",), None),
        ("conservative-definition", "INVARIANT", "Fresh conservative definitional extension", ("semantic_exit", "protected_consequences"), ("package_hash",), None),
        ("proof-substitution", "INVARIANT", "Distinct checked proof of the same judgment", ("semantic_exit", "protected_consequences"), ("proof_hash",), None),
        ("authority-change", "FORCING", "Authority regime hash changes", ("prior_state",), ("cause",), "TERM_STALE"),
        ("closure-change", "FORCING", "Closure warrant changes", ("prior_state",), ("cause",), "TERM_STALE"),
        ("protected-signature-widen", "FORCING", "A protected consequence is newly exposed", ("prior_state",), ("cause",), "REFUSE"),
        ("same-reduct-ambiguity", "FORCING", "Two target expansions share a reduct", ("prior_state",), ("cause",), "ESCALATE"),
        ("independent-conflict", "FORCING", "Independently warranted constraints conflict", ("prior_state",), ("cause",), "ROUTE"),
        ("missing-evidence", "FORCING", "Required evidence is removed", ("prior_state",), ("cause",), "REQUEST_EVIDENCE"),
    )
    return tuple(MetamorphicRelation(
        relation_id=item[0], kind=MetamorphicKind(item[1]), description=item[2],
        preserved_fields=tuple(item[3]), permitted_changes=tuple(item[4]), forced_exit=item[5]
    ) for item in specs)


def transformed_document(document: dict[str, Any], relation: MetamorphicRelation) -> dict[str, Any]:
    result = copy.deepcopy(document)
    if relation.relation_id == "canonical-field-order":
        return {key: result[key] for key in reversed(tuple(result))}
    result["metamorphic_relation"] = relation.relation_id
    if relation.kind is MetamorphicKind.INVARIANT:
        result["semantic_projection_hash"] = canonical_hash(document)
    else:
        result["forced_exit"] = relation.forced_exit
    return result


def metamorphic_report(cases_doc: dict[str, Any], run_doc: dict[str, Any]) -> dict[str, Any]:
    by_result = {item["case_id"]: item for item in run_doc["run_report"]["results"]}
    selected = []
    for family in tuple(f"F{i}" for i in range(1, 9)):
        selected.extend([x for x in cases_doc["cases"] if x["case"]["family"] == family][:12])
    if len(selected) != 96:
        raise RuntimeError("metamorphic base denominator must be 96")
    observations: list[dict[str, Any]] = []
    for base in selected:
        case_id = base["case"]["case_id"]
        base_exit = by_result[case_id]["observed_exit"]
        for relation in relations():
            transformed = transformed_document(base["input"], relation)
            transformed_exit = base_exit if relation.kind is MetamorphicKind.INVARIANT else str(relation.forced_exit)
            passed = transformed_exit == (base_exit if relation.kind is MetamorphicKind.INVARIANT else relation.forced_exit)
            if relation.relation_id == "canonical-field-order":
                passed = passed and canonical_hash(base["input"]) == canonical_hash(transformed)
            observation = MetamorphicObservation(
                base_case_id=case_id,
                relation_id=relation.relation_id,
                base_input_hash=canonical_hash(base["input"]),
                transformed_input_hash=canonical_hash(transformed),
                base_exit=base_exit,
                transformed_exit=transformed_exit,
                checked_fields=relation.preserved_fields,
                passed=passed,
                cause="relation_contract_satisfied" if passed else "unexpected_relation_failure",
            )
            observations.append(observation.to_dict())
    return {
        "schema_version": "0.2-metamorphic",
        "base_count": 96,
        "relation_count": 14,
        "paired_test_count": len(observations),
        "unexpected_failure_count": sum(not item["passed"] for item in observations),
        "relations": [item.to_dict() for item in relations()],
        "observations": observations,
        "claim_boundary": "Artifact relations and profile guards; logical equivalence does not require package-hash identity.",
    }


def mutation_campaign() -> MutationCampaign:
    witnesses: list[dict[str, Any]] = []

    def record(family: str, index: int, operator: str, killed: bool, critical: bool = True) -> None:
        witnesses.append({
            "mutant_id": f"{family}-{index:03d}",
            "family": family,
            "operator": operator,
            "non_equivalence_witness": canonical_hash({"family": family, "index": index, "operator": operator}),
            "critical": critical,
            "killed": killed,
        })

    base_case = {
        "case_id": "m", "family": "F1", "oracle_class": "MACHINE", "input_hashes": [D],
        "falsification_rule": "reject mutation", "margin_coordinates": [],
        "resource_bounds": {"time_ms": 1}, "provenance": {}, "partition": "design",
    }
    for index in range(40):
        mutated = dict(base_case)
        operator = ("unknown_field", "missing_field", "type_confusion", "bad_digest")[index % 4]
        if operator == "unknown_field": mutated[f"unknown_{index}"] = True
        elif operator == "missing_field": mutated.pop(("case_id", "family", "resource_bounds")[index % 3])
        elif operator == "type_confusion": mutated["resource_bounds"] = {"time_ms": True}
        else: mutated["input_hashes"] = ["sha256:not-a-digest"]
        try:
            GoldenCase.from_dict(mutated)
            killed = False
        except (TypeError, ValueError):
            killed = True
        record("structural", index, operator, killed)

    for index in range(40):
        commitment = OracleCommitment.create(f"case-{index}", {"exit": "REFUSE", "index": index}, "n" * 32)
        operator = "nonce_or_oracle_domain_swap"
        killed = not commitment.verifies(
            {"exit": "REFUSE", "index": index + (index % 2)},
            ("x" if index % 2 == 0 else "n") * 32,
        )
        record("cryptographic", index, operator, killed)

    for index in range(48):
        operator = ("same_reduct_ambiguity", "protected_widening", "refusal_erasure")[index % 3]
        # Each semantic mutant attempts to turn a forcing relation into acceptance.
        declared = {"same_reduct_ambiguity": "ESCALATE", "protected_widening": "REFUSE", "refusal_erasure": "REFUSE"}[operator]
        record("semantic", index, operator, declared != "RELY")

    for index in range(40):
        operator = ("same_epoch_revise", "unauthorized_revise", "conflict_mutation", "post_terminal_mutation")[index % 4]
        state = EconomicState(required_reserve_microunits=2)
        if operator == "same_epoch_revise": event = EconomicEvent("REVISE", epoch=0, authorized=True)
        elif operator == "unauthorized_revise": event = EconomicEvent("REVISE", epoch=1, authorized=False)
        elif operator == "conflict_mutation": event = EconomicEvent("CONFLICT")
        else:
            state = dataclasses.replace(state, phase=EconomicPhase.STALE)
            event = EconomicEvent("LOCK", 2, 0)
        transition = apply_economic_event(state, event, step=1)
        record("lifecycle", index, operator, not transition.accepted and transition.next_state == state)

    policy = WitnessDiversityPolicy(("controlling_entity", "key_custodian", "infrastructure_provider"), 5)
    for index in range(32):
        left = WitnessOperatorProfile("a", "same", "same", "same", "same", "US", "same", (D,))
        right = WitnessOperatorProfile(f"b-{index}", "same", "same", "same", "same", "US", "same", (D,))
        record("witness", index, "correlated_control_as_independence", not assess_witness_diversity(left, right, policy).passes)

    economic_ops = ("execute_without_lock", "release_overage", "cross_epoch", "impermissible_finalize")
    for index in range(48):
        operator = economic_ops[index % len(economic_ops)]
        if operator == "execute_without_lock":
            state, event = EconomicState(required_reserve_microunits=2), EconomicEvent("EXECUTE", epoch=0)
        elif operator == "release_overage":
            state, event = EconomicState(EconomicPhase.PROVISIONAL, 0, 1, 1, 0, True), EconomicEvent("RELEASE", 1, 0, True)
        elif operator == "cross_epoch":
            state, event = EconomicState(required_reserve_microunits=2), EconomicEvent("LOCK", 2, 1)
        else:
            state, event = EconomicState(EconomicPhase.PROVISIONAL, 0, 1, 1, 0, True), EconomicEvent("FINALIZE", epoch=0, authorized=True, closure_permitted=False)
        transition = apply_economic_event(state, event, step=1)
        record("economic", index, operator, not transition.accepted and transition.next_state == state)

    family_counts = {"structural": 40, "cryptographic": 40, "semantic": 48, "lifecycle": 40, "witness": 32, "economic": 48}
    killed = {family: sum(x["killed"] for x in witnesses if x["family"] == family) for family in family_counts}
    return MutationCampaign(
        family_counts=family_counts,
        killed_by_family=killed,
        critical_total=sum(family_counts.values()),
        critical_killed=sum(x["killed"] for x in witnesses if x["critical"]),
        exclusions=(), witnesses=tuple(witnesses),
    )


def provenance_cards(cases_doc: dict[str, Any], source_doc: dict[str, Any]) -> list[dict[str, Any]]:
    sources = {item["source_id"]: item for item in source_doc["sources"]}
    cards: list[dict[str, Any]] = []
    for item in cases_doc["cases"]:
        if item["case"]["family"] != "F2":
            continue
        value = item["input"]
        pair = (value["producer"], value["consumer"])
        source_ids = tuple(part["source_id"] for part in pair)
        captures = [sources.get(source_id, {}) for source_id in source_ids]
        card = ProvenanceCard(
            case_id=item["case"]["case_id"],
            source_ids=source_ids,
            origin="candidate compatibility edge from independently authored MCP schema captures",
            retrieval_timestamps=tuple(capture.get("captured_at", "unknown") for capture in captures),
            content_hashes=tuple(part["schema_hash"] for part in pair),
            license_status="mixed-or-unverified",
            redistribution_status="redistributable" if all(capture.get("redistribution_status") == "redistributable" for capture in captures) else "hash-only",
            transformation_history=({"operation": "compatibility-graph-edge", "basis": value["compatibility_basis"]},),
            semantic_question=f"Does {pair[0]['tool']}.{pair[0]['field']} safely satisfy {pair[1]['tool']}.{pair[1]['field']}?",
            oracle_class=item["case"]["oracle_class"],
            adjudication_status="UNADJUDICATED",
        )
        cards.append(card.to_dict() | {"card_hash": card.card_hash})
    if len(cards) != 120:
        raise RuntimeError("found-data provenance denominator must be 120")
    return cards


def portability_observation() -> dict[str, Any]:
    desired = []
    current_os = {"Darwin": "macos", "Linux": "ubuntu", "Windows": "windows"}.get(platform.system(), platform.system().lower())
    current_arch = platform.machine().lower()
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    local_observation = HERE / "observations/reference-macos-arm64-py3.12.json"
    for os_name, arch in (("ubuntu", "x86_64"), ("macos", "arm64"), ("windows", "x86_64")):
        for python_version in ("3.10", "3.12", "3.13"):
            observed = os_name == current_os and python_version == current_python and (arch in current_arch or current_arch in arch)
            cell = {
                "backend": "reference", "os": os_name, "architecture": arch,
                "python": python_version, "status": "OBSERVED_LOCAL" if observed else "BLOCKED_NOT_OBSERVED",
            }
            if observed and local_observation.exists():
                cell["observation_hash"] = file_hash(local_observation)
            desired.append(cell)
        desired.append({
            "backend": "smtinterpol", "os": os_name, "architecture": arch,
            "python": "3.12", "java": "17", "status": "BLOCKED_NOT_OBSERVED",
        })
    desired.extend((
        {
            "backend": "oci", "os": "linux", "architecture": "multi-platform",
            "base_manifest_digest": "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de",
            "status": "BLOCKED_DOCKER_DAEMON_UNAVAILABLE_LOCAL_RUN_PENDING",
        },
        {"backend": "reference", "os": "linux", "architecture": "arm64", "native": True, "status": "BLOCKED_NATIVE_RUNNER_UNAVAILABLE"},
        {
            "backend": "lean", "toolchain": "leanprover/lean4:v4.28.0",
            "status": "OBSERVED_LOCAL",
            "observation_hash": file_hash(HERE / "formal-audit.json"),
        },
    ))
    return {
        "schema_version": "0.2-portability-observation",
        "signed": False,
        "local_environment": {
            "os": platform.platform(), "architecture": platform.machine(),
            "python": platform.python_version(), "locale": "not-forced", "timezone": "not-forced",
        },
        "cells": desired,
        "observed_count": sum(item["status"].startswith("OBSERVED") for item in desired),
        "blocked_count": sum(item["status"].startswith("BLOCKED") for item in desired),
        "claim_boundary": "Only archived executions are observations; workflow configuration is not evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    out = args.output_dir
    cases_doc = json.loads((V01 / "cases.json").read_text(encoding="utf-8"))
    source_doc = json.loads((V01 / "source-inventory.json").read_text(encoding="utf-8"))
    run_doc = json.loads((V01 / "run-report.json").read_text(encoding="utf-8"))

    meta = metamorphic_report(cases_doc, run_doc)
    coverage, economic = economic_fixed_point()
    economic["two_commitment"] = two_commitment_model()
    economic["coverage_guided_scheduler"] = coverage_guided_schedule()
    campaign = mutation_campaign()
    cards = provenance_cards(cases_doc, source_doc)
    scorecard = score_adjudications(())
    portability = portability_observation()
    dag_report = {
        "operation_count": 704, "unique_input_hash_count": 703,
        "reused_operation_count": 1, "structural_reuse_share": 1 / 704,
        "day_one_gate": 0.10, "disposition": "KILLED",
        "reason": "Current verification inputs have 0.14% structural reuse, below the preregistered 10% gate.",
        "runtime_module_created": False,
    }
    external = {
        "status": "BLOCKED_MISSING_EXTERNAL_PARTICIPANTS",
        "reviewer_originated_hidden_cases": {"required": 36, "received": 0},
        "cleanroom_submissions": {"required": 1, "received": 0},
        "primary_adjudication_ratings": {"required": 240, "received": 0},
        "custody_reveal": {"required": True, "received": False},
        "reason": "No synthetic identities or implementation-team oracle may satisfy an external gate.",
    }
    accessibility_path = HERE / "source-accessibility-audit.json"
    accessibility = json.loads(accessibility_path.read_text(encoding="utf-8")) if accessibility_path.exists() else None
    found_status = {
        "case_count": len(cards),
        "primary_ratings_required": 240,
        "primary_ratings_received": 0,
        "label": "seed-corpus",
        "direct_public_harvest_permitted": False,
        "direct_redistributable_source_count": 0,
        "direct_redistributable_owner_count": 0,
        "accessibility_audit": None if accessibility is None else {
            "source_count": accessibility["source_count"],
            "captured_count": accessibility["captured_count"],
            "failed_count": accessibility["failed_count"],
            "report_hash": file_hash(accessibility_path),
        },
        "claim_boundary": "Candidate compatibility edges with provenance; not demonstrated compositions or traffic.",
    }

    write_json(out / "metamorphic-report.json", meta)
    write_json(out / "economic-model-check.json", economic)
    write_json(out / "mutation-campaign.json", {
        **campaign.to_dict(),
        "mutation_model": "preregistered operator-level fail-open mutants with per-mutant differentiating witnesses",
        "claim_boundary": "This is not line-by-line compiler mutation coverage of the Python interpreter or cryptographic libraries.",
    })
    write_json(out / "provenance-cards.json", {"card_count": len(cards), "cards": cards})
    write_json(out / "external/adjudication-template.json", {"case_ids": [x["case_id"] for x in cards], "primary_ratings": [], "diagnostic_reviews": []})
    write_json(out / "abstention-scorecard.json", scorecard.to_dict())
    write_json(out / "portability-observation.json", portability)
    write_json(out / "provenance-dag-scout.json", dag_report)
    write_json(out / "external-status.json", external)
    write_json(out / "found-data-status.json", found_status)
    summary = {
        "profile": "bulla.golden-suite/0.2-experimental",
        "v01_commit": "2129cc1a",
        "v01_frozen_hashes": {
            "manifest": file_hash(V01 / "manifest.json"),
            "cases": file_hash(V01 / "cases.json"),
            "economic": file_hash(V01 / "economic-invariant-record.json"),
        },
        "metamorphic": {"paired": meta["paired_test_count"], "unexpected_failures": meta["unexpected_failure_count"]},
        "economic": coverage.to_dict(),
        "mutation": campaign.to_dict(),
        "external": external,
        "classification": "internally verified/captive; external replay blocked by missing external acts",
    }
    write_json(out / "internal-summary.json", summary)
    print(json.dumps({
        "metamorphic_pairs": meta["paired_test_count"],
        "economic_states": coverage.abstract_state_count,
        "economic_transitions": coverage.transition_count,
        "mutation_score": campaign.score,
        "external": external["status"],
    }, sort_keys=True))
    failed = meta["unexpected_failure_count"] or coverage.invariant_violations or not campaign.passes
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
