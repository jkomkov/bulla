#!/usr/bin/env python3
"""Generate deterministic routed-inference profile trace bundles.

This generator imports Bulla to mint and sign ordinary ActionReceipts. The sibling
``check.py`` deliberately does not: it is the independent consumer contract.
"""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

from bulla.action_receipt import build_action_receipt, sign_action_receipt
from bulla.envelope import Authority, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.executable_form import definition_hash
from bulla.identity import LocalEd25519Signer
from bulla.reliance import (
    PRAGMATIC_RELIANCE_POLICY,
    ReceiptRef,
    build_reliance_receipt,
)


HERE = Path(__file__).resolve().parent
PROFILE = "bulla.routed-inference/0.1-draft"
SLOT_ID = "slot:routed-inference-0001"
REMEDY_REF = "remedy://routed-inference-v0.1"
WITNESS_REF = "witness-policy://two-independent@sha256:demo"
UNIT = "usd_micros"
TS = "2026-07-17T00:00:0{}+00:00"

HARNESS = LocalEd25519Signer(seed=bytes([11]) + bytes(31))
ROUTER = LocalEd25519Signer(seed=bytes([12]) + bytes(31))
PROVIDER_A = LocalEd25519Signer(seed=bytes([13]) + bytes(31))
PROVIDER_B = LocalEd25519Signer(seed=bytes([14]) + bytes(31))
RELIER = LocalEd25519Signer(seed=bytes([15]) + bytes(31))
WITNESS = LocalEd25519Signer(seed=bytes([16]) + bytes(31))


def _hash(label: str) -> str:
    return definition_hash({"fixture": label})


def _terms() -> dict:
    return {
        "profile": PROFILE,
        "route_topology": "single_route_single_provider",
        "term_disclosure": "full",
        "request_ref": _hash("request"),
        "process_constraints": {
            "permitted_providers": ["provider-a", "provider-b"],
            "permitted_models": ["hermes-4-70b", "hermes-4-405b"],
            "min_precision_bits": 8,
            "approved_hardware_classes": ["h100", "b200"],
            "randomness_policy": "declared-seed",
            "max_route_depth": 1,
            "resource_ceilings": {"input_tokens": 20000, "output_tokens": 4000},
        },
        "evidence_policy": {
            "minimum_process_grounding": "third_party_anchored",
            "appraisal_policy_ref": "appraisal://process-evidence-v1",
        },
        "budget_policy": {
            "mode": "disclosed_components",
            "unit": UNIT,
            "ceiling": 1000,
        },
        "deadline": {"domain": "fixture-log", "value": 100},
        "witness_policy_ref": WITNESS_REF,
        "remedy_adapter_ref": REMEDY_REF,
        "forum_ref": "forum://routed-inference-review",
        "reliance_policy_ref": (
            f"{PRAGMATIC_RELIANCE_POLICY.name}@"
            f"{PRAGMATIC_RELIANCE_POLICY.policy_hash}"
        ),
    }


def _envelope(signer: LocalEd25519Signer) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal=signer.verification_method,
            policy="policy://routed-inference@sha256:demo",
        ),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(
                log_endpoint="https://witness.example",
                trusted_root_ref="fixture:independently-pinned-root",
            ),
            remedies=(
                Remedy("recompute", "python3 check.py", "hashes.content"),
                Remedy("escalate", "named routed-inference forum", REMEDY_REF),
            ),
        ),
    )


def _ref(receipt: dict) -> dict:
    return ReceiptRef.from_receipt(receipt).to_dict()


def _receipt(
    action_type: str,
    signer: LocalEd25519Signer,
    subject: dict,
    *,
    parents: list[dict],
    index: int,
    evidence_refs: tuple[dict, ...] = (),
    discharges: list[dict] | None = None,
) -> dict:
    action = {
        "type": action_type,
        "profile": PROFILE,
        "parents": parents,
        "slot_id": subject.get("slot_id"),
        "term_root": subject.get("term_root"),
        "subject": subject,
    }
    if discharges is not None:
        action["discharges"] = discharges
    receipt = build_action_receipt(
        action=action,
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(signer),
        evidence_refs=evidence_refs,
        timestamp=TS.format(index),
        producer={"bulla_version": "0.44.0", "fixture": PROFILE},
    )
    return sign_action_receipt(receipt, signer).to_dict()


def _selection(provider: str = "provider-a") -> dict:
    return {
        "provider": provider,
        "model": "hermes-4-70b",
        "precision_bits": 8,
        "hardware_class": "h100",
        "randomness_policy": "declared-seed",
        "route_depth": 1,
    }


def _ledger(upstream: int, downstream: int, retained: int) -> dict:
    return {
        "unit": UNIT,
        "charge_to_upstream": upstream,
        "charge_from_downstream": downstream,
        "retained_amount": retained,
    }


def _build_bundle(
    trace_id: str,
    *,
    route_provider: str = "provider-a",
    delivery_provider: str | None = None,
    accept_term_override: str | None = None,
    accept_remedy_override: str | None = None,
    accept_witness_override: str | None = None,
    route_ledger: dict | None = None,
    provider_ledger: dict | None = None,
    process_grounding: str = "third_party_anchored",
    omit_delivery: bool = False,
    conflicting_delivery: bool = False,
    log_equivocation: bool = False,
    orphan_route: bool = False,
    attempted_discharge: bool = False,
) -> dict:
    terms = _terms()
    term_root = definition_hash(terms)
    route_ledger = route_ledger or _ledger(900, 700, 200)
    provider_ledger = provider_ledger or _ledger(700, 0, 700)
    route_selection = _selection(route_provider)
    actual_selection = _selection(delivery_provider or route_provider)

    order = _receipt(
        "inference.order",
        HARNESS,
        {
            "slot_id": SLOT_ID,
            "term_root": term_root,
            "request_ref": terms["request_ref"],
            "budget_ceiling": terms["budget_policy"]["ceiling"],
            "budget_unit": UNIT,
            "remedy_adapter_ref": REMEDY_REF,
            "witness_policy_ref": WITNESS_REF,
        },
        parents=[],
        index=1,
    )
    route_parents = [] if orphan_route else [_ref(order)]
    route = _receipt(
        "inference.route",
        ROUTER,
        {
            "slot_id": SLOT_ID,
            "term_root": term_root,
            "selection": route_selection,
            "budget_ledger": route_ledger,
        },
        parents=route_parents,
        index=2,
        discharges=[_ref(order)] if attempted_discharge else None,
    )
    accept = _receipt(
        "inference.accept",
        PROVIDER_B if route_provider == "provider-b" else PROVIDER_A,
        {
            "slot_id": SLOT_ID,
            "term_root": accept_term_override or term_root,
            "accepted_route": _ref(route),
            "accepted_selection": route_selection,
            "remedy_adapter_ref": accept_remedy_override or REMEDY_REF,
            "witness_policy_ref": accept_witness_override or WITNESS_REF,
            "budget_ledger": provider_ledger,
        },
        parents=[_ref(route)],
        index=3,
    )

    receipts = [order, route, accept]
    deliveries: list[dict] = []
    if not omit_delivery:
        provider_signer = PROVIDER_B if actual_selection["provider"] == "provider-b" else PROVIDER_A
        if actual_selection["provider"] not in ("provider-a", "provider-b"):
            provider_signer = PROVIDER_A
        delivery = _receipt(
            "inference.delivery",
            provider_signer,
            {
                "slot_id": SLOT_ID,
                "term_root": term_root,
                "selection": actual_selection,
                "artifact_ref": _hash(f"artifact:{trace_id}:a"),
                "resource_usage": {
                    "deltas": {"input_tokens": 1000, "output_tokens": 250},
                    "grounding": process_grounding,
                },
            },
            parents=[_ref(accept)],
            index=4,
            evidence_refs=({
                "name": "process_evidence",
                "hash": _hash(f"process:{trace_id}:a"),
                "grounding": process_grounding,
            },),
        )
        receipts.append(delivery)
        deliveries.append(delivery)
        if conflicting_delivery:
            second = _receipt(
                "inference.delivery",
                provider_signer,
                {
                    "slot_id": SLOT_ID,
                    "term_root": term_root,
                    "selection": actual_selection,
                    "artifact_ref": _hash(f"artifact:{trace_id}:b"),
                    "resource_usage": {
                        "deltas": {"input_tokens": 1200, "output_tokens": 300},
                        "grounding": process_grounding,
                    },
                },
                parents=[_ref(accept)],
                index=5,
                evidence_refs=({
                    "name": "process_evidence",
                    "hash": _hash(f"process:{trace_id}:b"),
                    "grounding": process_grounding,
                },),
            )
            receipts.append(second)
            deliveries.append(second)

        reliance = build_reliance_receipt(
            relied_on=delivery,
            policy=PRAGMATIC_RELIANCE_POLICY,
            envelope=_envelope(RELIER),
            timestamp=TS.format(6),
            producer={"bulla_version": "0.44.0", "fixture": PROFILE},
        )
        reliance = replace(
            reliance,
            action={
                **reliance.action,
                "profile": PROFILE,
                "parents": [_ref(delivery)],
                "slot_id": SLOT_ID,
                "term_root": term_root,
            },
        )
        receipts.append(sign_action_receipt(reliance, RELIER).to_dict())

    witness = {
        "status": "unavailable" if omit_delivery else "not_exercised",
        "heads": [],
    }
    if log_equivocation:
        def _signed_head(root: str) -> dict:
            statement = {
                "operator": WITNESS.verification_method,
                "tree_size": 10,
                "root": root,
            }
            return {**statement, "signature": WITNESS.sign(definition_hash(statement))}

        witness = {
            "status": "equivocated",
            "heads": [
                _signed_head(_hash("root:a")),
                _signed_head(_hash("root:b")),
            ],
        }

    return {
        "profile": PROFILE,
        "trace_id": trace_id,
        "term_document": terms,
        "term_root": term_root,
        "receipts": receipts,
        "witness": witness,
        "settlement_evidence": [],
    }


def main() -> int:
    common = {
        "answerability_coverage": "COVERED",
        "binding_state": "RETAINED",
        "recourse_conveyance": "CONFORMS",
        "recourse_reachability": "UNVERIFIED",
        "process_grounding": "third_party_anchored",
        "accounting_depth": "ACCOUNTING_CONFORMS",
        "settlement_depth": "SETTLEMENT_UNVERIFIED",
    }

    def expected(outcome: str, faults: list[str], **overrides: object) -> dict:
        return {**common, "outcome": outcome, "fault_codes": faults, **overrides}

    traces: list[tuple[str, dict, dict]] = [
        (
            "01-honest-balanced",
            _build_bundle("01-honest-balanced"),
            expected("CONFORMS", []),
        ),
        (
            "02-permitted-provider-selection",
            _build_bundle("02-permitted-provider-selection", route_provider="provider-b"),
            expected("CONFORMS", []),
        ),
        (
            "03-out-of-policy-delivery-substitution",
            _build_bundle("03-out-of-policy-delivery-substitution", delivery_provider="provider-x"),
            expected("VIOLATES", ["DELIVERY_ROUTE_SUBSTITUTION", "PROVIDER_NOT_PERMITTED"]),
        ),
        (
            "04-altered-term-commitment",
            _build_bundle("04-altered-term-commitment", accept_term_override=_hash("wrong-terms")),
            expected(
                "VIOLATES", ["TERM_ROOT_CHANGED"],
                answerability_coverage="BROKEN",
            ),
        ),
        (
            "05-partial-recourse-acceptance",
            _build_bundle(
                "05-partial-recourse-acceptance",
                accept_remedy_override="remedy://substituted",
                accept_witness_override="witness-policy://substituted",
            ),
            expected(
                "VIOLATES", ["REMEDY_NOT_ACCEPTED", "WITNESS_POLICY_NOT_ACCEPTED"],
                answerability_coverage="BROKEN", recourse_conveyance="VIOLATES",
            ),
        ),
        (
            "06-unbalanced-ledger",
            _build_bundle("06-unbalanced-ledger", route_ledger=_ledger(900, 700, 100)),
            expected(
                "VIOLATES", ["BUDGET_LEDGER_UNBALANCED"],
                accounting_depth="ACCOUNTING_VIOLATES",
            ),
        ),
        (
            "07-downstream-charge-mismatch",
            _build_bundle("07-downstream-charge-mismatch", provider_ledger=_ledger(600, 0, 600)),
            expected(
                "VIOLATES", ["DOWNSTREAM_CHARGE_MISMATCH"],
                accounting_depth="ACCOUNTING_VIOLATES",
            ),
        ),
        (
            "08-root-budget-overrun",
            _build_bundle("08-root-budget-overrun", route_ledger=_ledger(1100, 700, 400)),
            expected(
                "VIOLATES", ["BUDGET_CEILING_EXCEEDED"],
                accounting_depth="ACCOUNTING_VIOLATES",
            ),
        ),
        (
            "09-process-grounding-below-floor",
            _build_bundle("09-process-grounding-below-floor", process_grounding="self_asserted"),
            expected(
                "VIOLATES", ["PROCESS_GROUNDING_BELOW_FLOOR"],
                process_grounding="self_asserted",
            ),
        ),
        (
            "10-delivery-and-witness-unavailable",
            _build_bundle("10-delivery-and-witness-unavailable", omit_delivery=True),
            expected(
                "UNDETERMINED", ["DELIVERY_UNAVAILABLE", "WITNESS_UNAVAILABLE"],
                process_grounding=None,
            ),
        ),
        (
            "11-conflicting-deliveries",
            _build_bundle("11-conflicting-deliveries", conflicting_delivery=True),
            expected(
                "VIOLATES", ["CONFLICTING_DELIVERIES"],
                answerability_coverage="BROKEN",
            ),
        ),
        (
            "12-same-size-log-equivocation",
            _build_bundle("12-same-size-log-equivocation", log_equivocation=True),
            expected("VIOLATES", ["LOG_EQUIVOCATION"]),
        ),
        (
            "13-severed-parent-occurrence",
            _build_bundle("13-severed-parent-occurrence", orphan_route=True),
            expected(
                "VIOLATES", ["ORPHANED_TRANSITION"],
                answerability_coverage="BROKEN",
            ),
        ),
        (
            "14-attempted-discharge",
            _build_bundle("14-attempted-discharge", attempted_discharge=True),
            expected(
                "VIOLATES", ["DISCHARGE_UNSUPPORTED"],
                answerability_coverage="BROKEN",
            ),
        ),
    ]
    for old in HERE.glob("[0-9][0-9]-*.json"):
        old.unlink()
    expected: dict[str, dict] = {}
    for stem, bundle, want in traces:
        path = HERE / f"{stem}.json"
        path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        expected[path.name] = {**want, "verification_depth": "identity"}
        print(f"wrote {path.name}")
    (HERE / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("wrote expected.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
