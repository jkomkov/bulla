#!/usr/bin/env python3
"""Generate deterministic inference-clearing fixtures and checked projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.checkpoint import issue_checkpoint
from bulla.experimental.inference_clearing import (
    CAPITAL_PROFILE,
    COVERAGE_PROFILE,
    EXECUTION_PROFILE,
    PROFILE,
    SETTLEMENT_PROFILE,
    InferenceClearingContext,
    canonical_hash,
    component_hashes,
    run_reference_model,
    verify_inference_clearing_bundle,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


HERE = Path(__file__).resolve().parent
VECTORS = HERE / "vectors"
CONTEXTS = HERE / "contexts"
EXPECTED = HERE / "expected-verdict.json"
SITE_PROJECTION = HERE / "site-projection.json"

SCENARIOS = ("opaque", "recheckable", "bypass")
HOSTILE_REGISTRY = json.loads((HERE / "hostile-cases.json").read_text(encoding="utf-8"))
HOSTILE_MUTATIONS = {
    item["fixture"]: item["semantic_mutation"]
    for item in HOSTILE_REGISTRY["cases"]
    if "fixture" in item
}
ROLES = (
    "buyer",
    "router",
    "provider_opaque",
    "provider_reproducible",
    "receiver",
    "witness",
    "relier",
    "settlement_authority",
    "rail_observer",
    "guarantor",
    "publisher",
)
INPUT = [12, -4, 7, 3, -8, 5, 2, 9]
MODEL = {
    "profile": "bulla.int8-mlp/1",
    "input_width": 8,
    "hidden_weights": [
        [2, -1, 1, 0, 1, 0, -1, 1],
        [-1, 2, 0, 1, -1, 1, 0, 2],
        [0, 1, -2, 2, 0, -1, 1, 1],
    ],
    "hidden_bias": [3, 1, 4],
    "output_weights": [[1, -2, 1], [2, 1, 3]],
    "output_bias": [0, 5],
    "labels": ["PRIMARY", "BACKUP"],
}
OUTPUT, TRACE = run_reference_model(MODEL, INPUT)
assert OUTPUT == "BACKUP"
OUTPUT_BYTES = b"BACKUP\n"
POLICY = {
    "profile": "bulla.inference-reliance-policy/0.1-experimental",
    "evidence_requirements": {
        "output_binding": "VERIFIED",
        "model_binding": "TERM_BOUND",
        "relation_reproduction": "REPRODUCED",
        "provider_execution_occurrence": "NOT_REQUIRED",
    },
    "requires": [
        "accepted_authority",
        "exact_terms",
        "receiver_coverage",
        "witness_inclusion",
        "adequate_capital",
    ],
    "eligible_consequence": "RELEASE_PAYMENT",
}
POLICY_HASH = canonical_hash(POLICY)
COMPARISON_GROUP = canonical_hash(
    {"task": "fixed-telemetry-routing", "input": INPUT, "output": OUTPUT}
)


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _uuid(label: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(label.encode()).digest()[:16], version=4))


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(hashlib.sha256(f"inference-clearing:{role}".encode()).digest())


def _provider_role(scenario: str) -> str:
    return "provider_opaque" if scenario in {"opaque", "guarantee"} else "provider_reproducible"


def _envelope(
    role: str,
    action_type: str,
    *,
    principal_override: str | None = None,
) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal=principal_override or _signer(role).issuer,
            policy=POLICY_HASH,
        ),
        bounds=Bounds(scope=f"profile:{PROFILE};action:{action_type}"),
        recourse=Recourse(
            challenge_window="checkpoint:inference-clearing-challenge-001",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/bulla/experimental/inference-clearing/reference",
                trusted_root_ref=_hash_bytes(b"inference-clearing-forum-root"),
            ),
            remedies=(
                Remedy(
                    "challenge",
                    "verify retained clearing bundle",
                    "forum:inference-clearing-alpha",
                ),
            ),
        ),
        retention_class="operational",
        disclosure_class="public",
    )


def _receipt(
    scenario: str,
    role: str,
    action_type: str,
    subject: dict[str, Any],
    index: int,
    *,
    principal_override: str | None = None,
) -> dict[str, Any]:
    unsigned = build_action_receipt_v04(
        action={
            "type": action_type,
            "subject": {
                "profile": PROFILE,
                "issuer_role": "provider" if role.startswith("provider_") else role,
                "transaction_id": f"inference-{scenario}-001",
                **subject,
            },
        },
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(role, action_type, principal_override=principal_override),
        event_id=_uuid(f"{scenario}:{index}:{role}:{action_type}"),
        claimed_at=f"2026-08-04T12:{index:02d}:00Z",
        producer={"bulla_version": "source", "fixture": scenario},
    )
    return sign_action_receipt_v04(unsigned, _signer(role)).to_dict()


def _ref(receipt: dict[str, Any]) -> dict[str, str]:
    return {
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    }


def _artifact(path: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "media_type": "application/json" if path.endswith(".json") else "application/octet-stream",
        "byte_length": len(raw),
        "sha256": _hash_bytes(raw),
    }


def _execution(scenario: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    reproducible = scenario not in {"opaque", "guarantee"}
    trace = TRACE if reproducible else {"provider_statement": "fixed output returned"}
    report = {
        "profile": EXECUTION_PROFILE,
        "adapter": "bulla.int8-mlp-recompute/1" if reproducible else "provider-assertion/1",
        "model_id": "bulla-fixed-int-model-001" if reproducible else "opaque-model-unavailable",
        "model_hash": canonical_hash(MODEL) if reproducible else _hash_bytes(b"opaque-model-unavailable"),
        "model_available": reproducible,
        "input": INPUT,
        "input_hash": canonical_hash(INPUT),
        "output": OUTPUT,
        "output_hash": _hash_bytes(OUTPUT_BYTES),
        "trace": trace,
        "trace_hash": canonical_hash(trace),
        "provider_process_claim": "self_asserted",
    }
    return report, MODEL if reproducible else None


def _term(scenario: str) -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "transaction_id": f"inference-{scenario}-001",
        "comparison_group": COMPARISON_GROUP,
        "input_hash": canonical_hash(INPUT),
        "expected_model_hash": canonical_hash(MODEL),
        "output_schema": {"type": "enum", "values": ["PRIMARY", "BACKUP"]},
        "required_evidence": POLICY["evidence_requirements"],
        "policy_hash": POLICY_HASH,
        "price": {"amount": 125000, "unit": "sat"},
        "capital_requirement": {"amount": 200000, "unit": "sat"},
        "coverage_anchor": "receiver-effect-log-001",
        "witness_policy": "accepted-signed-checkpoint-and-inclusion",
        "recourse": {
            "challenge_window": "checkpoint:inference-clearing-challenge-001",
            "forum": "forum:inference-clearing-alpha",
        },
    }


def _context(*roots: str) -> InferenceClearingContext:
    return InferenceClearingContext(
        accepted_issuers_by_role={
            "buyer": {_signer("buyer").issuer},
            "router": {_signer("router").issuer},
            "provider": {_signer("provider_opaque").issuer, _signer("provider_reproducible").issuer},
            "receiver": {_signer("receiver").issuer},
            "witness": {_signer("witness").issuer},
            "relier": {_signer("relier").issuer},
            "settlement_authority": {_signer("settlement_authority").issuer},
            "rail_observer": {_signer("rail_observer").issuer},
            "guarantor": {_signer("guarantor").issuer},
            "publisher": {_signer("publisher").issuer},
        },
        accepted_execution_adapters={"provider-assertion/1", "bulla.int8-mlp-recompute/1"},
        accepted_rail_adapters={"fixture-escrow/1"},
        accepted_policy_hash=POLICY_HASH,
        accepted_evidence_requirements=POLICY["evidence_requirements"],
        trusted_witness_roots=set(roots),
        team_controlled_roles={
            "buyer", "router", "provider", "receiver", "witness", "relier",
            "settlement_authority", "rail_observer", "guarantor", "publisher",
        },
    )


def _scenario(
    scenario: str,
    destination: Path,
    *,
    verify_result: bool = True,
    semantic_mutation: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    transaction_id = f"inference-{scenario}-001"
    provider_role = _provider_role(scenario)
    execution, model = _execution(scenario)
    if semantic_mutation == "input-mismatch":
        mutated_input = [13, *INPUT[1:]]
        mutated_output, mutated_trace = run_reference_model(MODEL, mutated_input)
        execution = {
            **execution,
            "input": mutated_input,
            "input_hash": canonical_hash(mutated_input),
            "output": mutated_output,
            "output_hash": _hash_bytes((mutated_output + "\n").encode()),
            "trace": mutated_trace,
            "trace_hash": canonical_hash(mutated_trace),
        }
    if semantic_mutation == "model-mismatch" and model is not None:
        model = {**model, "output_bias": [0, 6]}
    if semantic_mutation == "post-hoc-equivalent-model" and model is not None:
        # This different model coherently maps the retained input to the same
        # BACKUP bytes. It must still fail because it was not selected in the
        # terms accepted before delivery.
        model = {
            **MODEL,
            "hidden_weights": [[0] * 8, [0] * 8, [0] * 8],
            "hidden_bias": [0, 0, 0],
            "output_weights": [[0, 0, 0], [0, 0, 0]],
            "output_bias": [0, 1],
        }
        post_hoc_output, post_hoc_trace = run_reference_model(model, INPUT)
        assert post_hoc_output == OUTPUT
        execution = {
            **execution,
            "model_hash": canonical_hash(model),
            "trace": post_hoc_trace,
            "trace_hash": canonical_hash(post_hoc_trace),
        }
    if semantic_mutation == "model-string-coercion" and model is not None:
        model = {
            **model,
            "hidden_weights": [["2", *model["hidden_weights"][0][1:]], *model["hidden_weights"][1:]],
        }
        execution = {**execution, "model_hash": canonical_hash(model)}
    if semantic_mutation == "unsafe-arithmetic":
        unsafe_input = [9_007_199_254_740_991, 9_007_199_254_740_990, 0, 0, 0, 0, 0, 0]
        model = {
            **MODEL,
            "hidden_weights": [[3, -3, 0, 0, 0, 0, 0, 0], [0] * 8, [0] * 8],
            "hidden_bias": [0, 0, 0],
            "output_weights": [[1, 0, 0], [0, 0, 0]],
            "output_bias": [0, 3],
        }
        unsafe_trace = {"hidden": [3, 0, 0], "scores": [3, 3], "selected_index": 1, "output": "BACKUP"}
        execution = {
            **execution,
            "input": unsafe_input,
            "input_hash": canonical_hash(unsafe_input),
            "model_hash": canonical_hash(model),
            "trace": unsafe_trace,
            "trace_hash": canonical_hash(unsafe_trace),
        }
    output_bytes = (execution["output"] + "\n").encode()
    term = _term(scenario)
    if semantic_mutation == "unsafe-arithmetic":
        term = {**term, "input_hash": execution["input_hash"]}
    term_root = canonical_hash(term)
    effect_id = "effect-mediated-001"

    common = {"term_root": term_root, "comparison_group": COMPARISON_GROUP}
    receipts: list[tuple[str, str, str, dict[str, Any]]] = []
    order = _receipt(scenario, "buyer", "inference.order", {
        **common,
        "input_hash": (
            "sha256:" + "0" * 64
            if semantic_mutation == "order-term-mismatch"
            else term["input_hash"]
        ),
        "price": term["price"],
    }, 1)
    receipts.append(("inference.order", "buyer", "receipts/01-order.json", order))
    route = _receipt(scenario, "router", "inference.route", {
        **common, "parent_ref": _ref(order), "provider_kind": "OPAQUE" if scenario in {"opaque", "guarantee"} else "REPRODUCIBLE"
    }, 2)
    receipts.append(("inference.route", "router", "receipts/02-route.json", route))
    accept = _receipt(scenario, provider_role, "inference.accept", {
        **common,
        "comparison_group": (
            "sha256:" + "0" * 64
            if semantic_mutation == "comparison-mismatch"
            else common["comparison_group"]
        ),
        "parent_ref": _ref(route),
        "accepted_model_hash": term["expected_model_hash"],
        "execution_report_hash": canonical_hash(execution),
        "provider_process_claim": execution["provider_process_claim"],
    }, 3, principal_override=(_signer("buyer").issuer if semantic_mutation == "wrong-authority" else None))
    receipts.append(("inference.accept", "provider", "receipts/03-accept.json", accept))
    delivery = _receipt(scenario, "receiver", "inference.delivery", {
        **common, "parent_ref": _ref(accept), "effect_id": effect_id,
        "output_hash": execution["output_hash"],
        "receiver_anchor": (
            "receiver-effect-log-wrong"
            if semantic_mutation == "delivery-anchor-mismatch"
            else term["coverage_anchor"]
        ),
    }, 4)
    receipts.append(("inference.delivery", "receiver", "receipts/04-delivery.json", delivery))
    capital_shortfall = scenario == "capital-shortfall"
    has_guarantee = scenario == "guarantee"
    unsafe_settlement = scenario == "unsafe-settlement"
    missing_witness = scenario == "missing-witness"
    has_bypass = scenario in {"bypass", "unsafe-settlement"} or semantic_mutation == "coverage-unbound"
    settlement_requested = unsafe_settlement or semantic_mutation == "settlement-unbound"
    rail_evidence_value = {
        "profile": "bulla.fixture-escrow-evidence/1",
        "binding_id": f"fixture-lock-{scenario}-001",
        "locked_amount": 200000,
        "unit": "sat",
        "reported_execution": "EXECUTED" if settlement_requested else "NOT_ATTEMPTED",
        "synthetic": True,
    }
    capital = {
        "profile": CAPITAL_PROFILE,
        "transaction_id": transaction_id,
        "binding_id": f"fixture-lock-{scenario}-001",
        "unit": "sat",
        "locked_amount": 100000 if capital_shortfall else 200000,
        "allocated_amount": 100000 if capital_shortfall else 200000,
        "external_encumbrance": "NOT_COMPUTED",
        "rail_adapter": "fixture-escrow/1",
        "rail_evidence_hash": canonical_hash(rail_evidence_value),
    }
    capital_receipt = _receipt(scenario, "rail_observer", "assurance.collateral.bind", {
        **common, "parent_ref": _ref(delivery), "capital_hash": canonical_hash(capital)
    }, 5)
    receipts.append(("assurance.collateral.bind", "rail_observer", "receipts/05-capital-bind.json", capital_receipt))

    coverage = {
        "profile": COVERAGE_PROFILE,
        "transaction_id": transaction_id,
        "anchor_id": term["coverage_anchor"],
        "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
        "denominator_ids": [effect_id] + (["effect-bypass-001"] if has_bypass else []),
        "receipted_ids": [effect_id],
    }

    decision_parent = capital_receipt
    if has_guarantee:
        guarantee = _receipt(scenario, "guarantor", "assurance.guarantee.issue", {
            **common,
            "parent_ref": _ref(capital_receipt),
            "guaranteed_claim": "model-identity",
            "exposure": 200000,
            "unit": "sat",
            "capital_hash": canonical_hash(capital),
        }, 6)
        receipts.append(("assurance.guarantee.issue", "guarantor", "receipts/06-guarantee.json", guarantee))
        decision_parent = guarantee

    coverage_index = len(receipts) + 1
    coverage_receipt = _receipt(scenario, "receiver", "inference.coverage.checkpoint", {
        **common,
        "parent_ref": _ref(decision_parent),
        "coverage_hash": canonical_hash(coverage),
        "anchor_id": coverage["anchor_id"],
        "denominator_count": len(coverage["denominator_ids"]),
        "receipted_count": len(coverage["receipted_ids"]),
    }, coverage_index)
    receipts.append((
        "inference.coverage.checkpoint",
        "receiver",
        f"receipts/{coverage_index:02d}-coverage-checkpoint.json",
        coverage_receipt,
    ))
    decision_parent = coverage_receipt

    with tempfile.TemporaryDirectory(prefix=f"inference-witness-{scenario}-") as raw:
        log = DeedLog(Path(raw) / "witness.jsonl")
        for _, _, _, receipt in receipts:
            log.append(Deed(
                receipt["signature"]["issuer"],
                receipt["hashes"]["content"],
                receipt["hashes"]["attestation"],
            ))
        decision_checkpoint_record = issue_checkpoint(
            log,
            _signer("witness"),
            log_id=f"inference-clearing:{scenario}",
            issued_at="2026-08-04T12:06:00Z",
        )
        checkpoint = decision_checkpoint_record.to_dict()
        inclusions = []
        for index, (_, _, _, receipt) in enumerate(receipts):
            inclusion = log.inclusion(index)
            inclusion["attestation"] = receipt["hashes"]["attestation"]
            inclusions.append(inclusion)
    if missing_witness:
        inclusions = inclusions[:-1]

    complete = scenario not in {"opaque", "guarantee", "capital-shortfall", "unsafe-settlement", "missing-witness"} and not has_bypass
    decision = "RELY" if complete else "REFUSE"
    unmet = []
    if scenario in {"opaque", "guarantee"}:
        unmet.extend((
            "required term-bound model was not established",
            "required computational relation was not reproduced",
        ))
    if has_bypass:
        unmet.append("required receiver coverage was not complete")
    if capital_shortfall:
        unmet.append("required capital allocation was not established")
    if missing_witness:
        unmet.append("required witness evidence was not established")
    rely_index = len(receipts) + 1
    rely = _receipt(scenario, "relier", "bulla.rely", {
        **common, "parent_ref": _ref(decision_parent), "policy_hash": POLICY_HASH,
        "decision": decision, "unmet_requirements": unmet,
        "named_consequence": "RELEASE_PAYMENT", "witness_root": checkpoint["root"],
        "coverage_hash": canonical_hash(coverage), "coverage_ref": _ref(coverage_receipt),
    }, rely_index)
    receipts.append(("bulla.rely", "relier", f"receipts/{rely_index:02d}-rely.json", rely))

    authorization_ref: dict[str, str] | None = None
    if settlement_requested:
        authorize_index = len(receipts) + 1
        authorize = _receipt(scenario, "settlement_authority", "assurance.settlement.authorize", {
            **common, "parent_ref": _ref(rely), "eligibility": "ELIGIBLE",
            "destination": "synthetic:provider-reproducible", "amount": 125000, "unit": "sat",
            "rail_adapter": "fixture-escrow/1",
        }, authorize_index)
        receipts.append(("assurance.settlement.authorize", "settlement_authority", f"receipts/{authorize_index:02d}-settlement-authorize.json", authorize))
        authorization_ref = _ref(authorize)

    settlement = {
        "profile": SETTLEMENT_PROFILE,
        "transaction_id": transaction_id,
        "rail_adapter": "fixture-escrow/1",
        "status": "EXECUTED" if settlement_requested else "NOT_ATTEMPTED",
        "amount": 125000,
        "unit": "sat",
        "synthetic": True,
        "authorization_ref": authorization_ref,
        "rail_evidence_hash": canonical_hash(rail_evidence_value),
    }

    if settlement_requested:
        rail_index = len(receipts) + 1
        rail = _receipt(scenario, "rail_observer", "rail.settlement.report", {
            **common, "parent_ref": _ref(authorize), "status": "EXECUTED",
            "authorization_ref": _ref(authorize), "amount": 125000, "unit": "sat",
            "rail_adapter": "fixture-escrow/1", "settlement_hash": canonical_hash(settlement),
        }, rail_index)
        receipts.append(("rail.settlement.report", "rail_observer", f"receipts/{rail_index:02d}-settlement-report.json", rail))

    with tempfile.TemporaryDirectory(prefix=f"inference-final-witness-{scenario}-") as raw:
        final_log = DeedLog(Path(raw) / "witness.jsonl")
        for _, _, _, receipt in receipts:
            final_log.append(Deed(
                receipt["signature"]["issuer"],
                receipt["hashes"]["content"],
                receipt["hashes"]["attestation"],
            ))
        final_checkpoint = issue_checkpoint(
            final_log,
            _signer("witness"),
            log_id=f"inference-clearing:{scenario}",
            previous=decision_checkpoint_record,
            issued_at="2026-08-04T12:10:00Z",
        ).to_dict()
        final_inclusions = []
        for index, (_, _, _, receipt) in enumerate(receipts):
            inclusion = final_log.inclusion(index)
            inclusion["attestation"] = receipt["hashes"]["attestation"]
            final_inclusions.append(inclusion)

    # Signed hostile variants rehash and republish the outer bundle while
    # preserving the original role receipt that should catch the contradiction.
    if semantic_mutation == "coverage-unbound":
        coverage = {**coverage, "denominator_ids": [effect_id]}
    elif semantic_mutation == "capital-unbound":
        capital = {**capital, "allocated_amount": capital["allocated_amount"] - 1}
    elif semantic_mutation == "settlement-unbound":
        settlement = {**settlement, "authorization_ref": None}
    elif semantic_mutation == "inclusion-size-mismatch":
        inclusions = [{**inclusions[0], "tree_size": inclusions[0]["tree_size"] + 1}, *inclusions[1:]]
    elif semantic_mutation == "witness-proof-substitution":
        checkpoint = {
            **checkpoint,
            "proof": _signer("publisher").sign_domain(
                "witness-checkpoint",
                checkpoint["checkpoint_hash"],
                schema="0.3",
            ),
        }
    elif semantic_mutation == "provider-claim-substitution":
        execution = {
            **execution,
            "provider_process_claim": (
                "absent"
                if execution["provider_process_claim"] == "self_asserted"
                else "self_asserted"
            ),
        }
    elif semantic_mutation == "malformed-settlement-object":
        settlement = []

    files: dict[str, bytes] = {
        "terms/term-document.json": _json(term),
        "execution/output.bin": output_bytes,
        "execution/report.json": _json(execution),
        "receiver/coverage.json": _json(coverage),
        "witness/decision-checkpoint.json": _json(checkpoint),
        "witness/decision-inclusions.json": _json(inclusions),
        "witness/final-checkpoint.json": _json(final_checkpoint),
        "witness/final-inclusions.json": _json(final_inclusions),
        "assurance/capital.json": _json(capital),
        "settlement/report.json": _json(settlement),
        "settlement/rail-evidence.json": _json(rail_evidence_value),
    }
    if model is not None and semantic_mutation != "missing-required-model":
        files["execution/model.json"] = _json(model)
    for _, _, path, receipt in receipts:
        files[path] = _json(receipt)
    artifacts = [_artifact(path, files[path]) for path in sorted(files)]
    core = {
        "profile": PROFILE,
        "transaction_id": transaction_id,
        "revision": 1,
        "provider_kind": "OPAQUE" if scenario in {"opaque", "guarantee"} else "REPRODUCIBLE",
        "comparison_group": COMPARISON_GROUP,
        "role_issuers": {
            role: _signer(role).issuer for role in ROLES
        },
        "term_root": term_root,
        "ordered_receipts": [
            {
                "action_type": action,
                "role": role,
                "path": path,
                "event": receipt["hashes"]["event"],
                "attestation": receipt["hashes"]["attestation"],
            }
            for action, role, path, receipt in receipts
        ],
        "artifacts": artifacts,
    }
    publish = _receipt(scenario, "publisher", "inference.clearing.publish", {
        "profile": PROFILE,
        "issuer_role": "publisher",
        "transaction_id": transaction_id,
        "clearing_core_hash": canonical_hash(core),
        "component_hashes": component_hashes(core),
    }, 9)
    # Publication subjects are deliberately non-lineage records.
    publish["action"]["subject"] = {
        "profile": PROFILE,
        "issuer_role": "publisher",
        "transaction_id": transaction_id,
        "clearing_core_hash": canonical_hash(core),
        "component_hashes": component_hashes(core),
    }
    # Re-sign after replacing the closed publication subject.
    unsigned_publish = build_action_receipt_v04(
        action={"type": "inference.clearing.publish", "subject": publish["action"]["subject"]},
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope("publisher", "inference.clearing.publish"),
        event_id=_uuid(f"{scenario}:9:publisher:inference.clearing.publish"),
        claimed_at="2026-08-04T12:09:00Z",
        producer={"bulla_version": "source", "fixture": scenario},
    )
    publish = sign_action_receipt_v04(unsigned_publish, _signer("publisher")).to_dict()
    files["clearing-core.json"] = _json(core)
    files["publish-receipt.json"] = _json(publish)
    for path, raw in files.items():
        target = destination / scenario / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    context = _context(checkpoint["root"], final_checkpoint["root"])
    report = (
        verify_inference_clearing_bundle(destination / scenario, context).to_dict()
        if verify_result
        else {}
    )
    return context.to_dict(), report


def _projection(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    opaque = reports["opaque"]
    recheckable = reports["recheckable"]
    bypass = reports["bypass"]
    opaque_term = _term("opaque")
    recheckable_term = _term("recheckable")
    opaque_template = {key: value for key, value in opaque_term.items() if key != "transaction_id"}
    recheckable_template = {
        key: value for key, value in recheckable_term.items() if key != "transaction_id"
    }
    return {
        "profile": PROFILE,
        "maturity": "experimental · SOURCE_ONLY · synthetic-public · team-operated · A0/J0/I0/W0 · r0",
        "promise": {
            "input": INPUT,
            "output": OUTPUT,
            "output_schema": ["PRIMARY", "BACKUP"],
            "price": {"amount": 125000, "unit": "sat"},
            "required_allocation": {"amount": 200000, "unit": "sat"},
            "required_relation_evidence": dict(POLICY["evidence_requirements"]),
        },
        "provider_substitution": {
            "same_term_template": opaque_template == recheckable_template,
            "same_buyer_policy": opaque_term["policy_hash"] == recheckable_term["policy_hash"],
            "separate_transaction_instances": (
                opaque_term["transaction_id"] != recheckable_term["transaction_id"]
            ),
            "verifier": "PROJECT_AUTHORED_LOCAL",
        },
        "providers": {
            "opaque": {
                "transaction_id": opaque["transaction_id"],
                "output": opaque["output"],
                "receipt_integrity": opaque["receipt_integrity"],
                "retained_model": opaque["relation_evidence"]["model_binding"],
                "relation_rerun": opaque["relation_evidence"]["relation_reproduction"],
                "historical_execution": opaque["relation_evidence"]["provider_execution_occurrence"],
                "reliance": opaque["reliance_decision"],
                "payment": opaque["payment_eligibility"],
                "settlement_authorization": opaque["settlement_authorization"],
                "settlement_execution": opaque["settlement_execution"],
                "settlement_attempt": opaque["settlement_execution"],
                "funds_movement": "NOT_ESTABLISHED",
            },
            "recheckable": {
                "transaction_id": recheckable["transaction_id"],
                "output": recheckable["output"],
                "receipt_integrity": recheckable["receipt_integrity"],
                "retained_model": recheckable["relation_evidence"]["model_binding"],
                "relation_rerun": recheckable["relation_evidence"]["relation_reproduction"],
                "historical_execution": recheckable["relation_evidence"]["provider_execution_occurrence"],
                "reliance": recheckable["reliance_decision"],
                "payment": recheckable["payment_eligibility"],
                "settlement_authorization": recheckable["settlement_authorization"],
                "settlement_execution": recheckable["settlement_execution"],
                "settlement_attempt": recheckable["settlement_execution"],
                "funds_movement": "NOT_ESTABLISHED",
            },
        },
        "bypass": {
            "receipt_integrity": bypass["receipt_integrity"],
            "receiver_effects": bypass["receiver_total"],
            "receipted_effects": bypass["receiver_receipted"],
            "coverage": f"{bypass['receiver_receipted']}/{bypass['receiver_total']} · {bypass['receiver_coverage']}",
            "uncovered_effects": bypass["uncovered_effects"],
            "reliance": bypass["reliance_decision"],
            "payment": bypass["payment_eligibility"],
            "settlement_authorization": bypass["settlement_authorization"],
            "settlement_execution": bypass["settlement_execution"],
            "settlement_attempt": bypass["settlement_execution"],
            "funds_movement": "NOT_ESTABLISHED",
        },
        "reproduction": {
            "commands": [
                'VENV_DIR="$(mktemp -d)"',
                'RUN_DIR="$(mktemp -d)"',
                'python3 -m venv "$VENV_DIR"',
                '"$VENV_DIR/bin/python" -m pip install \'./bulla[identity]\'',
                'PYTHONPATH=bulla/src "$VENV_DIR/bin/python" bulla/examples/inference-clearing/run_demo.py --story --out "$RUN_DIR"',
                '"$VENV_DIR/bin/python" -I bulla/spec/inference-clearing/check.py "$RUN_DIR/recheckable" --context "$RUN_DIR/contexts/recheckable.json" > "$RUN_DIR/python-report.json"',
                'node bulla/spec/inference-clearing/check.mjs "$RUN_DIR/recheckable" --context "$RUN_DIR/contexts/recheckable.json" > "$RUN_DIR/node-report.json"',
                '"$VENV_DIR/bin/python" bulla/spec/inference-clearing/compare_reports.py "$RUN_DIR/python-report.json" "$RUN_DIR/node-report.json"',
            ],
        },
        "limitations": {
            "provider_execution_occurrence": "NOT_ESTABLISHED",
            "denominator_completeness": bypass["denominator_completeness"],
            "custody": bypass["custody"],
            "collectibility": bypass["collectibility"],
            "worldly_truth": bypass["worldly_truth"],
            "external_reproduction": "BLOCKED",
            "human_comprehension": "BLOCKED",
        },
    }


def materialize(root: Path, *, include_hostile: bool = False) -> None:
    reports: dict[str, dict[str, Any]] = {}
    for scenario in SCENARIOS:
        context, report = _scenario(scenario, root / "vectors")
        (root / "contexts").mkdir(parents=True, exist_ok=True)
        (root / "contexts" / f"{scenario}.json").write_bytes(_json(context))
        reports[scenario] = report
    if include_hostile:
        for scenario, mutation in HOSTILE_MUTATIONS.items():
            context, _ = _scenario(
                scenario,
                root / "hostile-vectors",
                verify_result=False,
                semantic_mutation=mutation,
            )
            (root / "hostile-contexts").mkdir(parents=True, exist_ok=True)
            (root / "hostile-contexts" / f"{scenario}.json").write_bytes(_json(context))
    (root / "expected-verdict.json").write_bytes(_json({"profile": PROFILE, "reports": reports}))
    (root / "site-projection.json").write_bytes(_json(_projection(reports)))


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def write() -> None:
    with tempfile.TemporaryDirectory(prefix="bulla-inference-clearing-") as raw:
        generated = Path(raw)
        materialize(generated)
        for target in (VECTORS, CONTEXTS):
            if target.exists():
                shutil.rmtree(target)
        shutil.copytree(generated / "vectors", VECTORS)
        shutil.copytree(generated / "contexts", CONTEXTS)
        EXPECTED.write_bytes((generated / "expected-verdict.json").read_bytes())
        SITE_PROJECTION.write_bytes((generated / "site-projection.json").read_bytes())


def check() -> None:
    with tempfile.TemporaryDirectory(prefix="bulla-inference-clearing-check-") as raw:
        generated = Path(raw)
        materialize(generated)
        expected = _tree(generated)
        actual = {
            **{f"vectors/{key}": value for key, value in _tree(VECTORS).items()},
            **{f"contexts/{key}": value for key, value in _tree(CONTEXTS).items()},
            "expected-verdict.json": EXPECTED.read_bytes() if EXPECTED.exists() else b"",
            "site-projection.json": SITE_PROJECTION.read_bytes() if SITE_PROJECTION.exists() else b"",
        }
        if actual != expected:
            changed = sorted(key for key in set(actual) & set(expected) if actual[key] != expected[key])
            raise SystemExit(
                f"inference-clearing fixtures drifted; missing={sorted(set(expected)-set(actual))}; "
                f"unexpected={sorted(set(actual)-set(expected))}; changed={changed}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--hostile-out", type=Path)
    args = parser.parse_args()
    if args.check and args.hostile_out is not None:
        raise SystemExit("--check and --hostile-out are mutually exclusive")
    if args.hostile_out is not None:
        materialize(args.hostile_out, include_hostile=True)
        print(f"generated transient hostile inference-clearing fixtures in {args.hostile_out}")
    elif args.check:
        check()
        print("inference-clearing fixtures are deterministic")
    else:
        write()
        print("generated opaque, recheckable, and bypass clearing fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
