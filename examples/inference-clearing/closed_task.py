"""Closed synthetic task and runtime receipt helpers for Inference Clearing."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04, verify_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.checkpoint import verify_checkpoint
from bulla.experimental.inference_clearing import (
    CAPITAL_PROFILE,
    COVERAGE_PROFILE,
    EXECUTION_PROFILE,
    PROFILE,
    SETTLEMENT_PROFILE,
    canonical_hash,
    run_reference_model,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, verify_inclusion_record


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
    {"task": "fixed-telemetry-routing", "input": INPUT, "output": "BACKUP"}
)
OUTPUT_BYTES = b"BACKUP\n"
ROLE_NAMES = (
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
RUNTIME_SEQUENCE = (
    ("inference.order", "buyer"),
    ("inference.route", "router"),
    ("inference.accept", "provider"),
    ("inference.delivery", "receiver"),
    ("assurance.collateral.bind", "rail_observer"),
    ("inference.coverage.checkpoint", "receiver"),
)


def signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(hashlib.sha256(f"inference-clearing:{role}".encode()).digest())


def provider_role(scenario: str) -> str:
    return "provider_opaque" if scenario == "opaque" else "provider_reproducible"


def role_issuer_name(role: str) -> str:
    return {
        "provider-opaque": "provider_opaque",
        "provider-reproducible": "provider_reproducible",
        "settlement-authority": "settlement_authority",
        "rail-observer": "rail_observer",
    }.get(role, role)


def hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def receipt_ref(receipt: Mapping[str, Any]) -> dict[str, str]:
    return {
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    }


def term_document(scenario: str) -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "transaction_id": f"inference-{scenario}-runtime-001",
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


def relation_evidence(scenario: str) -> tuple[dict[str, Any], dict[str, Any] | None, bytes]:
    reproducible = scenario != "opaque"
    if reproducible:
        output, trace = run_reference_model(MODEL, INPUT)
        model: dict[str, Any] | None = MODEL
    else:
        output, trace, model = "BACKUP", {"provider_statement": "fixed output returned"}, None
    output_bytes = (output + "\n").encode()
    return (
        {
            "profile": EXECUTION_PROFILE,
            "adapter": "bulla.int8-mlp-recompute/1" if reproducible else "provider-assertion/1",
            "model_id": "bulla-fixed-int-model-001" if reproducible else "opaque-model-unavailable",
            "model_hash": canonical_hash(MODEL) if reproducible else hash_bytes(b"opaque-model-unavailable"),
            "model_available": reproducible,
            "input": INPUT,
            "input_hash": canonical_hash(INPUT),
            "output": output,
            "output_hash": hash_bytes(output_bytes),
            "trace": trace,
            "trace_hash": canonical_hash(trace),
            "provider_process_claim": "self_asserted",
        },
        model,
        output_bytes,
    )


def coverage_record(scenario: str, transaction_id: str) -> dict[str, Any]:
    return {
        "profile": COVERAGE_PROFILE,
        "transaction_id": transaction_id,
        "anchor_id": "receiver-effect-log-001",
        "provenance": "PATH_SEPARATE_TEAM_CONTROLLED",
        "denominator_ids": ["effect-mediated-001"]
        + (["effect-bypass-001"] if scenario == "bypass" else []),
        "receipted_ids": ["effect-mediated-001"],
    }


def rail_evidence(scenario: str) -> dict[str, Any]:
    return {
        "profile": "bulla.fixture-escrow-evidence/1",
        "binding_id": f"fixture-lock-{scenario}-runtime-001",
        "locked_amount": 200000,
        "unit": "sat",
        "reported_execution": "NOT_ATTEMPTED",
        "synthetic": True,
    }


def capital_record(scenario: str, transaction_id: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile": CAPITAL_PROFILE,
        "transaction_id": transaction_id,
        "binding_id": f"fixture-lock-{scenario}-runtime-001",
        "unit": "sat",
        "locked_amount": 200000,
        "allocated_amount": 200000,
        "external_encumbrance": "NOT_COMPUTED",
        "rail_adapter": "fixture-escrow/1",
        "rail_evidence_hash": canonical_hash(evidence),
    }


def settlement_record(
    transaction_id: str,
    evidence: Mapping[str, Any],
    authorization_ref: Mapping[str, str] | None,
) -> dict[str, Any]:
    return {
        "profile": SETTLEMENT_PROFILE,
        "transaction_id": transaction_id,
        "rail_adapter": "fixture-escrow/1",
        "status": "EXECUTED" if authorization_ref else "NOT_ATTEMPTED",
        "amount": 125000,
        "unit": "sat",
        "synthetic": True,
        "authorization_ref": dict(authorization_ref) if authorization_ref else None,
        "rail_evidence_hash": canonical_hash(evidence),
    }


def external_context(*roots: str) -> dict[str, Any]:
    return {
        "accepted_issuers_by_role": {
            "buyer": [signer("buyer").issuer],
            "router": [signer("router").issuer],
            "provider": [signer("provider_opaque").issuer, signer("provider_reproducible").issuer],
            "receiver": [signer("receiver").issuer],
            "witness": [signer("witness").issuer],
            "relier": [signer("relier").issuer],
            "settlement_authority": [signer("settlement_authority").issuer],
            "rail_observer": [signer("rail_observer").issuer],
            "guarantor": [signer("guarantor").issuer],
            "publisher": [signer("publisher").issuer],
        },
        "accepted_execution_adapters": ["bulla.int8-mlp-recompute/1", "provider-assertion/1"],
        "accepted_rail_adapters": ["fixture-escrow/1"],
        "accepted_policy_hash": POLICY_HASH,
        "accepted_evidence_requirements": POLICY["evidence_requirements"],
        "trusted_witness_roots": list(roots),
        "team_controlled_roles": [
            "buyer", "guarantor", "provider", "publisher", "rail_observer", "receiver",
            "relier", "router", "settlement_authority", "witness",
        ],
    }


def _envelope(role: str, action_type: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal=signer(role).issuer, policy=POLICY_HASH),
        bounds=Bounds(scope=f"profile:{PROFILE};action:{action_type}"),
        recourse=Recourse(
            challenge_window="checkpoint:inference-clearing-challenge-001",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/bulla/experimental/inference-clearing/reference",
                trusted_root_ref=hash_bytes(b"inference-clearing-forum-root"),
            ),
            remedies=(Remedy("challenge", "verify retained clearing bundle", "forum:inference-clearing-alpha"),),
        ),
        retention_class="operational",
        disclosure_class="public",
    )


def issue_receipt(
    scenario: str,
    role: str,
    action_type: str,
    subject: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    transaction_id = f"inference-{scenario}-runtime-001"
    issuer_role = "provider" if role.startswith("provider_") else role
    unsigned = build_action_receipt_v04(
        action={
            "type": action_type,
            "subject": {
                "profile": PROFILE,
                "issuer_role": issuer_role,
                "transaction_id": transaction_id,
                **dict(subject),
            },
        },
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(role, action_type),
        event_id=str(uuid.UUID(bytes=hashlib.sha256(f"runtime:{scenario}:{index}:{role}:{action_type}".encode()).digest()[:16], version=4)),
        claimed_at=f"2026-08-04T13:{index:02d}:00Z",
        producer={"bulla_version": "source", "runtime": "inference-clearing-localhost"},
    )
    return sign_action_receipt_v04(unsigned, signer(role)).to_dict()


def issue_publish_receipt(scenario: str, core: Mapping[str, Any], component_hashes: Mapping[str, str]) -> dict[str, Any]:
    subject = {
        "profile": PROFILE,
        "issuer_role": "publisher",
        "transaction_id": core["transaction_id"],
        "clearing_core_hash": canonical_hash(core),
        "component_hashes": dict(component_hashes),
    }
    unsigned = build_action_receipt_v04(
        action={"type": "inference.clearing.publish", "subject": subject},
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope("publisher", "inference.clearing.publish"),
        event_id=str(uuid.UUID(bytes=hashlib.sha256(f"runtime:{scenario}:publisher".encode()).digest()[:16], version=4)),
        claimed_at="2026-08-04T13:15:00Z",
        producer={"bulla_version": "source", "runtime": "inference-clearing-localhost"},
    )
    return sign_action_receipt_v04(unsigned, signer("publisher")).to_dict()


def verify_direct_receipt(receipt: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> None:
    verdict = verify_receipt(receipt)
    issuer = (receipt.get("signature") or {}).get("issuer")
    mandate = receipt.get("mandate") or {}
    authority = mandate.get("authority") or {}
    if (
        not verdict.ok
        or verdict.verified_to != "attestation"
        or verdict.authority_authentic != "verified"
        or authority.get("principal") != issuer
        or authority.get("delegation") != []
        or issuer not in context["accepted_issuers_by_role"][role]
    ):
        raise ValueError(f"{role} receipt failed direct authority and signature verification")


def evaluate_pre_reliance(facts: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Recompute the fixed reliance policy from retained pre-reliance evidence."""

    term = facts["term"]
    execution = facts["execution"]
    model = facts["model"]
    output_bytes = bytes(facts["output_bytes"])
    coverage = facts["coverage"]
    capital = facts["capital"]
    rail = facts["rail_evidence"]
    checkpoint = facts["checkpoint"]
    inclusions = facts["inclusions"]
    context = facts["context"]
    receipt_entries = facts["receipts"]
    transaction_id = term["transaction_id"]
    term_root = canonical_hash(term)

    if context["accepted_policy_hash"] != term["policy_hash"] or term["input_hash"] != canonical_hash(INPUT):
        raise ValueError("term and relying context do not bind the fixed task")
    if [(entry["action_type"], entry["role"]) for entry in receipt_entries] != list(RUNTIME_SEQUENCE):
        raise ValueError("pre-reliance receipt sequence is incomplete")
    previous: dict[str, str] | None = None
    for entry in receipt_entries:
        receipt = entry["receipt"]
        role = entry["role"]
        verify_direct_receipt(receipt, role, context)
        subject = receipt["action"]["subject"]
        if (
            receipt["action"]["type"] != entry["action_type"]
            or subject["transaction_id"] != transaction_id
            or subject["term_root"] != term_root
            or subject["comparison_group"] != term["comparison_group"]
            or (previous is None and "parent_ref" in subject)
            or (previous is not None and subject.get("parent_ref") != previous)
        ):
            raise ValueError("pre-reliance receipt lineage or repeated fields differ")
        previous = receipt_ref(receipt)

    subjects = {entry["action_type"]: entry["receipt"]["action"]["subject"] for entry in receipt_entries}
    if subjects["inference.order"]["input_hash"] != term["input_hash"] or subjects["inference.order"]["price"] != term["price"]:
        raise ValueError("order differs from the accepted terms")
    if (
        subjects["inference.delivery"]["receiver_anchor"] != term["coverage_anchor"]
        or subjects["inference.delivery"]["output_hash"] != execution["output_hash"]
    ):
        raise ValueError("delivery names the wrong receiver anchor or output")
    if (
        subjects["inference.accept"]["accepted_model_hash"]
        != term["expected_model_hash"]
        or subjects["inference.accept"]["execution_report_hash"]
        != canonical_hash(execution)
        or subjects["inference.accept"]["provider_process_claim"]
        != execution["provider_process_claim"]
    ):
        raise ValueError(
            "provider acceptance does not bind the term-selected model and execution report"
        )
    if (
        output_bytes != OUTPUT_BYTES
        or execution["output_hash"] != hash_bytes(output_bytes)
        or execution["input_hash"] != term["input_hash"]
    ):
        raise ValueError("returned output bytes differ")

    model_binding = "UNAVAILABLE"
    relation_reproduction = "UNAVAILABLE"
    if execution["adapter"] == "bulla.int8-mlp-recompute/1":
        output, trace = run_reference_model(model, execution["input"])
        if (
            canonical_hash(model) != execution["model_hash"]
            or canonical_hash(execution["input"]) != execution["input_hash"]
            or output != execution["output"]
            or trace != execution["trace"]
            or canonical_hash(trace) != execution["trace_hash"]
        ):
            raise ValueError("execution recomputation differs")
        model_binding = (
            "TERM_BOUND"
            if canonical_hash(model) == execution["model_hash"] == term["expected_model_hash"]
            else "UNBOUND"
        )
        relation_reproduction = "REPRODUCED" if model_binding == "TERM_BOUND" else "FAILED"
    elif execution["adapter"] != "provider-assertion/1" or model is not None:
        raise ValueError("unsupported execution evidence adapter")
    expected_kind = "REPRODUCIBLE" if model is not None else "OPAQUE"
    if subjects["inference.route"]["provider_kind"] != expected_kind:
        raise ValueError("route does not bind the selected evidence path")

    denominator = coverage["denominator_ids"]
    receipted = coverage["receipted_ids"]
    if (
        coverage["profile"] != COVERAGE_PROFILE
        or coverage["transaction_id"] != transaction_id
        or coverage["anchor_id"] != term["coverage_anchor"]
        or len(denominator) != len(set(denominator))
        or len(receipted) != len(set(receipted))
        or not set(receipted).issubset(denominator)
        or subjects["inference.delivery"]["effect_id"] not in receipted
    ):
        raise ValueError("receiver coverage record is malformed")
    coverage_subject = subjects["inference.coverage.checkpoint"]
    if (
        coverage_subject["coverage_hash"] != canonical_hash(coverage)
        or coverage_subject["anchor_id"] != coverage["anchor_id"]
        or coverage_subject["denominator_count"] != len(denominator)
        or coverage_subject["receipted_count"] != len(receipted)
    ):
        raise ValueError("receiver coverage checkpoint differs")

    checkpoint_verdict = verify_checkpoint(checkpoint)
    if (
        not checkpoint_verdict.ok
        or checkpoint["root"] not in context["trusted_witness_roots"]
        or checkpoint["operator"] not in context["accepted_issuers_by_role"]["witness"]
        or checkpoint["tree_size"] != len(receipt_entries)
        or len(inclusions) != len(receipt_entries)
    ):
        raise ValueError("witness checkpoint is not accepted")
    inclusion_by_attestation = {item["attestation"]: item for item in inclusions}
    if len(inclusion_by_attestation) != len(receipt_entries):
        raise ValueError("witness inclusions are duplicated or incomplete")
    for entry in receipt_entries:
        receipt = entry["receipt"]
        attestation = receipt["hashes"]["attestation"]
        expected_leaf = "sha256:" + Deed(
            receipt["signature"]["issuer"], receipt["hashes"]["content"], attestation
        ).leaf().hex()
        inclusion = inclusion_by_attestation.get(attestation)
        if (
            inclusion is None
            or inclusion.get("tree_size") != checkpoint["tree_size"]
            or not verify_inclusion_record(inclusion, trusted_root=checkpoint["root"], expected_leaf=expected_leaf)
        ):
            raise ValueError("receipt lacks exact witness inclusion")

    if (
        capital["profile"] != CAPITAL_PROFILE
        or capital["transaction_id"] != transaction_id
        or capital["rail_evidence_hash"] != canonical_hash(rail)
        or subjects["assurance.collateral.bind"]["capital_hash"] != canonical_hash(capital)
    ):
        raise ValueError("capital evidence is not bound")
    adequate = (
        capital["unit"] == term["capital_requirement"]["unit"]
        and capital["allocated_amount"] >= term["capital_requirement"]["amount"]
        and capital["allocated_amount"] <= capital["locked_amount"]
        and capital["rail_adapter"] in context["accepted_rail_adapters"]
    )

    unmet: list[str] = []
    requirements = context["accepted_evidence_requirements"]
    if model_binding != requirements["model_binding"]:
        unmet.append("required term-bound model was not established")
    if relation_reproduction != requirements["relation_reproduction"]:
        unmet.append("required computational relation was not reproduced")
    if execution["adapter"] not in context["accepted_execution_adapters"]:
        unmet.append("execution adapter was not accepted")
    if set(denominator) != set(receipted):
        unmet.append("required receiver coverage was not complete")
    if not adequate:
        unmet.append("required capital allocation was not established")
    return ("REFUSE" if unmet else "RELY"), unmet
