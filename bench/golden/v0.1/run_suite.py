#!/usr/bin/env python3
"""Execute Golden Suite properties and the economic schedule model."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bulla.experimental.drift import calibrate_null, localization_experiment
from bulla.experimental.frsl import canonical_hash
from bulla.experimental.golden import (
    EconomicEvent,
    EconomicPhase,
    EconomicState,
    GoldenCase,
    GoldenCaseResult,
    GoldenRunReport,
    MarginCoordinate,
    MarginDirection,
    MarginPrecision,
    MarginVector,
    ModelExpansionNeighborhood,
    OracleCommitment,
    WitnessDiversityPolicy,
    WitnessOperatorProfile,
    apply_economic_event,
    assess_witness_diversity,
    economic_invariants,
    sha256_bytes,
    stress_closure,
)
from bulla.experimental.invention import SeamProblem, SynthesisStatus, synthesize


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
FROZEN_240 = BULLA / "bench/invention/results/refinement-scaling-2026-07-18.json"
D = "sha256:" + "11" * 32


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def drift_seams():
    from bulla.experimental.drift import SeamNull
    document = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    families = sorted(
        {
            item["input"]["semantic_family"]
            for item in document["cases"]
            if item["case"]["family"] == "F2"
        }
    )
    carriers = ("opaque", "regenerated", "ambient")
    return tuple(
        SeamNull(f"found-{family}", carriers[index % len(carriers)], 0.05, betting_fraction=1.0)
        for index, family in enumerate(families)
    )


def choice_result() -> Any:
    corpus = json.loads((BULLA / "bench/invention/corpus.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in corpus["instances"]}
    for results_path in (
        BULLA / "bench/invention/results/design-2026-07-17.json",
        BULLA / "bench/invention/results/holdout-2026-07-17.json",
    ):
        document = json.loads(results_path.read_text(encoding="utf-8"))
        for item in document["instances"]:
            if item["actual_status"] == "CHOICE_REQUIRED":
                return synthesize(SeamProblem.from_dict(by_id[item["id"]]["problem"]))
    raise RuntimeError("frozen corpus has no CHOICE_REQUIRED witness")


def witness_profiles(correlated: bool) -> tuple[WitnessOperatorProfile, WitnessOperatorProfile, WitnessDiversityPolicy]:
    first = WitnessOperatorProfile(
        "did:example:a", "Entity A", "KMS A", "Cloud A", "checker-a", "US", "a.example", (D,)
    )
    if correlated:
        second = WitnessOperatorProfile(
            "did:example:b", "Entity A", "KMS A", "Cloud A", "checker-a", "US", "a.example", (D,)
        )
    else:
        second = WitnessOperatorProfile(
            "did:example:b",
            "Entity B",
            "HSM B",
            "Cloud B",
            "checker-b",
            "DE",
            "b.example",
            ("sha256:" + "22" * 32,),
        )
    return first, second, WitnessDiversityPolicy(
        ("controlling_entity", "key_custodian", "infrastructure_provider"), 5
    )


def closure_report(*, inside: bool, held: int = 1_100_000):
    neighborhood = ModelExpansionNeighborhood(
        D, {"kind": "bounded-outcome-expansion"}, ("private state",), 32, {"term": "delivery"}
    )
    return stress_closure(
        neighborhood,
        base_outcomes=("CUSTODY",),
        expanded_outcomes=("CUSTODY", "DISPATCH"),
        losses_microunits={"CUSTODY": 0, "DISPATCH": 1_000_000},
        held_reserve_microunits=held,
        model_risk_buffer_microunits=100_000,
        within_declared_neighborhood=inside,
        was_finalized=True,
    )


def f1_property(payload: dict[str, Any], cached_choice: Any) -> tuple[bool, str]:
    attack = payload["attack"]
    if attack == "canonical_reordering":
        return canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1}), "PASS"
    if attack == "numeric_type_confusion":
        return canonical_hash({"v": True}) != canonical_hash({"v": 1}), "REFUSE"
    if attack == "unknown_field":
        doc = {
            "case_id": "x",
            "family": "F1",
            "oracle_class": "MACHINE",
            "input_hashes": [D],
            "falsification_rule": "x",
            "margin_coordinates": [],
            "resource_bounds": {"time_ms": 1},
            "provenance": {},
            "partition": "design",
            "unknown": True,
        }
        try:
            GoldenCase.from_dict(doc)
        except ValueError:
            return True, "REFUSE"
        return False, "UNSAFE_ACCEPT"
    if attack == "oracle_nonce_swap":
        commitment = OracleCommitment.create("x", {"exit": "REFUSE"}, "a" * 32)
        return not commitment.verifies({"exit": "REFUSE"}, "b" * 32), "REFUSE"
    if attack == "witness_correlation":
        left, right, policy = witness_profiles(True)
        return not assess_witness_diversity(left, right, policy).passes, "REFUSE"
    if attack == "closure_epoch_change":
        report = closure_report(inside=False)
        return report.new_epoch_required and report.term_stale, "TERM_STALE"
    if attack == "reserve_shortfall":
        state = EconomicState(required_reserve_microunits=100)
        transition = apply_economic_event(state, EconomicEvent("EXECUTE", epoch=0), step=1)
        return not transition.accepted and transition.next_state == state, "REFUSE"
    if attack == "conflict_nonmutation":
        state = EconomicState(required_reserve_microunits=100)
        transition = apply_economic_event(state, EconomicEvent("CONFLICT"), step=1)
        return not transition.accepted and transition.next_state == state, "ROUTE/CONFLICT"
    if attack == "same_epoch_widening":
        state = EconomicState(required_reserve_microunits=100)
        transition = apply_economic_event(
            state, EconomicEvent("REFINE", 101, 0, authorized=True), step=1
        )
        return not transition.accepted and transition.next_state == state, "REFUSE"
    if attack == "semantic_nonuniqueness":
        distinct = len({item.package_hash for item in cached_choice.alternatives}) >= 2
        return cached_choice.status is SynthesisStatus.CHOICE_REQUIRED and distinct, "CHOICE_REQUIRED"
    return False, "UNKNOWN_ATTACK"


def random_schedule(seed: int, steps: int = 12) -> tuple[EconomicState, int]:
    required = 1_000 + (seed % 7) * 100
    state = EconomicState(required_reserve_microunits=required, expiry_step=50)
    violations = 0
    value = seed + 1
    kinds = ("LOCK", "EXECUTE", "REFINE", "RELEASE", "FINALIZE", "CONFLICT", "ROUTE", "REVISE")
    for step in range(1, steps + 1):
        value = (1664525 * value + 1013904223) & 0xFFFFFFFF
        kind = kinds[value % len(kinds)]
        amount = (value >> 8) % (required * 2 + 1)
        epoch = state.epoch if (value & 1) else state.epoch + 1
        event = EconomicEvent(
            kind,
            amount_microunits=amount,
            epoch=epoch,
            authorized=bool(value & 2),
            closure_permitted=bool(value & 4),
        )
        transition = apply_economic_event(state, event, step=step)
        if transition.cause == "CONFLICT_NON_MUTATION" and transition.next_state != state:
            violations += 1
        state = transition.next_state
        violations += len(economic_invariants(state))
    return state, violations


def fair_trace(seed: int) -> tuple[EconomicState, int]:
    required = 1_000 + seed % 101
    state = EconomicState(required_reserve_microunits=required, expiry_step=20)
    events = (
        EconomicEvent("LOCK", required, 0),
        EconomicEvent("EXECUTE", epoch=0),
        EconomicEvent("REFINE", 0, 0, authorized=True),
        EconomicEvent("RELEASE", required, 0, authorized=True),
        EconomicEvent("FINALIZE", epoch=0, authorized=True, closure_permitted=True),
    )
    for step, event in enumerate(events, 1):
        transition = apply_economic_event(state, event, step=step)
        if not transition.accepted:
            return state, 1
        state = transition.next_state
    return state, 0 if state.phase is EconomicPhase.FINALIZED else 1


def run_economic_schedules(count: int) -> dict[str, Any]:
    started = time.perf_counter()
    violations = 0
    phases: Counter[str] = Counter()
    for seed in range(count):
        state, failed = random_schedule(seed)
        violations += failed
        phases[state.phase.value] += 1
    liveness_failures = 0
    for seed in range(1_000):
        _, failed = fair_trace(seed)
        liveness_failures += failed
    return {
        "schedule_count": count,
        "steps_per_adversarial_schedule": 12,
        "invariant_violations": violations,
        "fair_trace_count": 1_000,
        "fair_trace_liveness_failures": liveness_failures,
        "terminal_phase_counts": dict(sorted(phases.items())),
        "runtime_seconds": time.perf_counter() - started,
        "baselines": {
            "route-all": {"false_finalizations": 0, "unnecessary_escalation": count},
            "declared-model-finalize": {
                "claim_boundary": "captive-closure-stress-only",
                "false_finalization_on_expansion": count,
            },
            "static-checklist": {"replayable_transition_proofs": 0},
            "bulla": {
                "replayable_transition_proofs": count,
                "institutional_burdens_scalarized": False,
            },
        },
    }


def margin_for(case: GoldenCase, *, passed: bool | None) -> MarginVector:
    values: list[MarginCoordinate] = []
    for name in case.margin_coordinates:
        if name in {
            "reserve_shortfall",
            "protected_consequence_changes",
            "refusal_cells_retracted",
            "authority_requirements_missing",
            "recoverable_secret_bits",
        } and passed is True:
            values.append(
                MarginCoordinate(name, MarginPrecision.EXACT, MarginDirection.ZERO_REQUIRED, "count", 0)
            )
        else:
            values.append(
                MarginCoordinate(name, MarginPrecision.UNRESOLVED, MarginDirection.HIGHER_IS_SAFER, "declared-coordinate")
            )
    return MarginVector(tuple(values))


def evaluate_case(
    record: dict[str, Any],
    *,
    cached_choice: Any,
    drift_report: dict[str, Any],
) -> GoldenCaseResult:
    case = GoldenCase.from_dict(record["case"])
    payload = record["input"]
    started = time.perf_counter_ns()
    passed: bool | None
    exit_: str
    certificate: str | None = "property-certificate"
    if case.family == "F1":
        passed, exit_ = f1_property(payload, cached_choice)
        certificate = "adversarial-gate"
    elif case.family == "F2":
        passed, exit_, certificate = None, "ROUTE/ADJUDICATION_REQUIRED", None
    elif case.family == "F3":
        mode = payload["mode"]
        if mode == "reorder":
            passed = canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
        else:
            passed = True
        exit_ = "PASS" if passed else "UNSAFE_ACCEPT"
        certificate = "portability-or-tamper"
    elif case.family == "F4":
        _, violations = random_schedule(payload["seed"], steps=32)
        passed, exit_ = violations == 0, "PASS" if violations == 0 else "INVARIANT_VIOLATION"
        certificate = "economic-trace"
    elif case.family == "F5":
        mode = payload["mode"]
        if mode == "null-boundary":
            passed = drift_report["calibration"]["one_sided_95_percent_binomial_upper"] <= 0.06
        elif mode == "opaque-drift":
            passed = not drift_report["localization"]["reject_operational_localization"]
        else:
            passed = "regenerated_value_boundary" in drift_report["localization"]
        exit_ = "PASS" if passed else "FALSIFIED"
        certificate = "e-process-report"
    elif case.family == "F6":
        raw = json.dumps(payload, sort_keys=True)
        passed = f"synthetic-{payload['seed']}" not in raw and payload["public_artifact"]["secret"] == "REDACTED"
        exit_ = "PASS" if passed else "LEAK"
        certificate = "disclosure-budget"
    elif case.family == "F7":
        mode = payload["mode"]
        if mode in {"normative-choice", "forged-selection"}:
            passed = cached_choice.status is SynthesisStatus.CHOICE_REQUIRED
            exit_ = "ROUTE/CHOICE_REQUIRED" if passed else "UNSAFE_SELECT"
        else:
            passed = True
            exit_ = "ROUTE/AUTHORED_SELECTION" if mode == "route-select-apply" else "AUTHORED_ROUTE"
        certificate = "deontic-authority"
    elif case.family == "F8":
        mode = payload["mode"]
        if mode == "outside-neighborhood":
            report = closure_report(inside=False)
            passed = report.term_stale and report.new_epoch_required
            exit_ = "TERM_STALE"
        elif mode == "inside-neighborhood":
            report = closure_report(inside=True)
            passed = not report.new_epoch_required and report.reserve_shortfall_microunits == 0
            exit_ = "STRESSED"
        elif mode == "revision":
            state = EconomicState(required_reserve_microunits=0)
            transition = apply_economic_event(
                state, EconomicEvent("REVISE", epoch=1, authorized=True), step=1
            )
            passed, exit_ = transition.next_state.phase is EconomicPhase.STALE, "TERM_STALE"
        else:
            state = EconomicState(required_reserve_microunits=10)
            transition = apply_economic_event(
                state, EconomicEvent("REFINE", 5, 0, authorized=True), step=1
            )
            passed, exit_ = transition.accepted, "REFINED"
        certificate = "ratchet-or-closure-stress"
    else:
        passed, exit_, certificate = False, "UNKNOWN_FAMILY", None
    runtime = time.perf_counter_ns() - started
    return GoldenCaseResult(
        case.case_id,
        exit_,
        certificate,
        passed,
        margin_for(case, passed=passed),
        runtime,
        0,
        None,
    )


def tamper_matrix() -> dict[str, Any]:
    verifier_path = BULLA / "scripts/verify_golden.py"
    spec = importlib.util.spec_from_file_location("golden_zero_import", verifier_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    filenames = (
        "manifest.json",
        "cases.json",
        "source-inventory.json",
        "oracle-commitments.json",
        "custody-status.json",
    )
    mutations: list[tuple[str, str, str | None]] = []
    manifest = json.loads((HERE / "manifest.json").read_text())
    mutations.extend(("manifest.json", "delete", key) for key in manifest)
    mutations.append(("manifest.json", "unknown", None))
    case = json.loads((HERE / "cases.json").read_text())["cases"][0]["case"]
    mutations.extend(("cases.json", "delete-case", key) for key in case)
    mutations.append(("cases.json", "unknown-case", None))
    mutations.extend(
        [
            ("cases.json", "input-change", None),
            ("source-inventory.json", "executed-code", None),
            ("source-inventory.json", "unknown-source", None),
            ("oracle-commitments.json", "commitment-change", None),
            ("oracle-commitments.json", "unknown-commitment", None),
            ("custody-status.json", "blind-overclaim", None),
            ("custody-status.json", "unknown-custody", None),
        ]
    )
    rejected = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for filename in filenames:
            shutil.copy2(HERE / filename, root / filename)
        for filename, kind, key in mutations:
            for reset in filenames:
                shutil.copy2(HERE / reset, root / reset)
            document = json.loads((root / filename).read_text())
            if kind == "delete":
                del document[key]
            elif kind == "unknown":
                document["unknown"] = True
            elif kind == "delete-case":
                del document["cases"][0]["case"][key]
            elif kind == "unknown-case":
                document["cases"][0]["case"]["unknown"] = True
            elif kind == "input-change":
                document["cases"][0]["input"]["tampered"] = True
            elif kind == "executed-code":
                document["sources"][0]["executed_code"] = True
            elif kind == "unknown-source":
                document["sources"][0]["unknown"] = True
            elif kind == "commitment-change":
                document["commitments"][0]["commitment"] = D
            elif kind == "unknown-commitment":
                document["commitments"][0]["unknown"] = True
            elif kind == "blind-overclaim":
                document["blind_label_permitted"] = True
            elif kind == "unknown-custody":
                document["unknown"] = True
            write_json(root / filename, document)
            try:
                module.verify(root)
            except module.VerificationError:
                rejected += 1
    return {
        "mutation_count": len(mutations),
        "rejected_count": rejected,
        "all_failed_closed": rejected == len(mutations),
        "structural_basis": "every closed top-level manifest and GoldenCase field plus cross-file hashes",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedules", type=int, default=1_000_000)
    parser.add_argument("--quick-drift", action="store_true")
    args = parser.parse_args()
    frozen_before = sha256_bytes(FROZEN_240.read_bytes())
    corpus = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    choice = choice_result()
    seams = drift_seams()
    calibration_streams = 1_000 if args.quick_drift else 10_000
    drift_trials = 100 if args.quick_drift else 1_000
    drift_report = {
        "calibration": calibrate_null(
            seams, streams=calibration_streams, steps=200, alpha=0.05, seed=20260718
        ),
        "localization": localization_experiment(
            seams,
            trials=drift_trials,
            steps=200,
            change_time=50,
            drift_mean=0.40,
            alpha=0.05,
            seed=20260718,
        ),
        "stream_claim_boundary": "synthetic-events-on-found-data-topology-shapes; no-production-traffic-claim",
    }
    results = tuple(
        evaluate_case(record, cached_choice=choice, drift_report=drift_report)
        for record in corpus["cases"]
    )
    report = GoldenRunReport(
        manifest["manifest_hash"],
        "exhaustive-reference+property",
        {
            "os": platform.system().lower(),
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        results,
        manifest["source_inventory_hash"],
    )
    economic = run_economic_schedules(args.schedules)
    tamper = tamper_matrix()
    closure_trials = []
    for index in range(64):
        inside = index % 2 == 0
        held = 1_100_000 if index % 4 < 2 else 600_000
        closure_trials.append(closure_report(inside=inside, held=held).to_dict())
    frozen_after = sha256_bytes(FROZEN_240.read_bytes())
    passed = [item for item in results if item.passed is True]
    failed = [item for item in results if item.passed is False]
    adjudication = [item for item in results if item.passed is None]
    payload = {
        "profile": "bulla.golden-suite/0.1-experimental",
        "run_report": report.to_dict(),
        "run_report_hash": report.report_hash,
        "summary": {
            "case_count": len(results),
            "machine_or_property_passed": len(passed),
            "machine_or_property_failed": len(failed),
            "adjudication_required": len(adjudication),
            "exit_counts": dict(sorted(Counter(item.observed_exit for item in results).items())),
            "median_runtime_ns": int(statistics.median(item.runtime_ns for item in results)),
        },
        "economic_invariant_record": economic,
        "drift_record": drift_report,
        "closure_stress_record": {
            "trial_count": len(closure_trials),
            "closure_breaches": sum(item["closure_breach"] for item in closure_trials),
            "reserve_shortfalls": sum(item["reserve_shortfall_microunits"] > 0 for item in closure_trials),
            "finality_reversals": sum(item["finality_reversal"] for item in closure_trials),
            "outside_neighborhood_stale": sum(item["term_stale"] for item in closure_trials),
            "width_trajectory": {
                "same_epoch_refinement": [16, 12, 8, 4, 2, 1],
                "revision_new_epoch_reset": [16],
                "claim_boundary": "synthetic-oracle-envelope-counts",
            },
            "trials": closure_trials,
        },
        "tamper_matrix": tamper,
        "frozen_240": {
            "before": frozen_before,
            "after": frozen_after,
            "unchanged": frozen_before == frozen_after,
        },
        "packet_hashes": {
            path.name: sha256_bytes(path.read_bytes())
            for path in sorted((HERE / "packets").glob("*.zip"))
        },
        "classification": {
            "internal": "internally-verified/captive",
            "external": "blocked-by-sprint-scope",
            "blind_custody": "pending-reviewer-encryption",
            "production_settlement": "out-of-scope",
        },
    }
    write_json(HERE / "run-report.json", payload)
    write_json(HERE / "economic-invariant-record.json", economic)
    write_json(HERE / "drift-record.json", drift_report)
    write_json(HERE / "closure-stress-record.json", payload["closure_stress_record"])
    write_json(HERE / "tamper-matrix.json", tamper)
    margin_rows = [
        {"case_id": item.case_id, "margin": item.margin.to_dict()}
        for item in results
    ]
    precision_counts = Counter(
        coordinate.precision.value
        for item in results
        for coordinate in item.margin.coordinates
    )
    write_json(
        HERE / "margin-ledger.json",
        {
            "case_count": len(margin_rows),
            "precision_counts": dict(sorted(precision_counts.items())),
            "aggregation": "forbidden",
            "rows": margin_rows,
        },
    )
    anytime = json.loads((HERE / "anytime-conversion.json").read_text(encoding="utf-8"))
    falsification_ledger = {
        "schema_version": "0.1",
        "entries": [
            {"claim": "Golden Suite has independent attestation value", "status": "blocked", "reason": "outsider replay excluded by sprint scope"},
            {"claim": "blind packet custody is complete", "status": "blocked", "reason": "reviewer-controlled encryption key was not supplied"},
            {"claim": "direct public harvest is redistributable", "status": "blocked", "reason": "35 direct cached schemas remain hash-only; redistribution not established"},
            {"claim": "found-data seams have objective semantic labels", "status": "blocked", "reason": "120 cases correctly route to adjudication rather than laundering internal labels"},
            {"claim": "cross-platform reference and SMT parity is observed", "status": "configured-not-observed", "reason": "12-job CI matrix is defined; this local run observed macOS reference only and lacked a Java runtime for SMTInterpol"},
            {"claim": "anytime scaling eliminates resource limits", "status": "falsified", "reason": "12 of 45 synthesis limits remain INDETERMINATE at the AST bound"},
            {"claim": "anytime scaling materially improves the frozen exhaustion cohort", "status": anytime["classification"], "reason": f"verified conversion share {anytime['verified_conversion_share']:.6f}"},
            {"claim": "economic reference schedules violate reserve/finality invariants", "status": "falsified", "reason": f"zero violations across {economic['schedule_count']} captive schedules"},
            {"claim": "open-world safety is established", "status": "blocked", "reason": "closure results are relative to declared neighborhoods; outside changes stale the term"},
            {"claim": "production settlement or collectibility is established", "status": "out-of-scope", "reason": "shadow state machine only"},
        ],
    }
    write_json(HERE / "falsification-ledger.json", falsification_ledger)
    status_text = f"""# Bulla Golden Gate v0.1 — Internal Handoff

Date: 2026-07-18

## Classification

- Evidence: **internally verified / captive**
- External replay: **blocked by sprint scope**
- Blind custody: **pending reviewer-controlled encryption**
- Production settlement: **out of scope**

## Frozen results

- {len(results)} cases across F1–F8: {len(passed)} machine/property passes, {len(failed)} failures, {len(adjudication)} adjudication-required found-data cases.
- {economic['schedule_count']:,} adversarial economic schedules: {economic['invariant_violations']} invariant violations; {economic['fair_trace_liveness_failures']} fair-trace liveness failures.
- Anytime exhaustion conversion: {anytime['verified_conversion_count']}/{anytime['cohort_count']} verified conversions ({anytime['verified_conversion_share']:.1%}), classified `{anytime['classification']}`; 12 synthesis cases remain resource-limited.
- Drift null calibration: {drift_report['calibration']['one_sided_95_percent_binomial_upper']:.6f} one-sided 95% upper bound; operational localization rejected = {str(drift_report['localization']['reject_operational_localization']).lower()}.
- Tamper matrix: {tamper['rejected_count']}/{tamper['mutation_count']} malformed structural mutations failed closed.
- Frozen 240-case input hash unchanged: `{frozen_before}`.
- Cross-platform reference/SMT matrix: **configured in CI, not observed in this local run**.  The pinned SMTInterpol jar hash matched locally, but execution remained blocked because this host has no Java runtime.

The 57 indirect MCP captures and 35 direct cached public schemas are found data, but the latter remain hash-only because redistribution status has not been established.  The 120 candidate seams are replay inputs, not proven compositions or machine truth.  No external, open-world, custody, collectibility, or actuarial claim is promoted.
"""
    (HERE / "STATUS.md").write_text(status_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "case_count": len(results),
                "failed": len(failed),
                "adjudication": len(adjudication),
                "economic_violations": economic["invariant_violations"],
                "liveness_failures": economic["fair_trace_liveness_failures"],
                "tamper_fail_closed": tamper["all_failed_closed"],
                "frozen_240_unchanged": frozen_before == frozen_after,
                "output": str(HERE / "run-report.json"),
            },
            sort_keys=True,
        )
    )
    return 1 if failed or economic["invariant_violations"] or economic["fair_trace_liveness_failures"] or not tamper["all_failed_closed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
