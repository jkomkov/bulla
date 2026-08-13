#!/usr/bin/env python3
"""Generate the compact Acceptance Contract alpha corpus and checked projection."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.acceptance_contract import (
    CONTEXT_PROFILE,
    CORE_PROFILE,
    EVIDENCE_REQUEST_PROFILE,
    PROFILE,
    AcceptanceContext,
    canonical_hash,
    evaluate_acceptance_bundle,
)
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
SCENARIOS = ("missing", "passing", "failing")
TRANSACTION_ID = "release-acceptance-alpha-001"
CONTRACT_ID = "release-policy-alpha-001"
BUILD_HASH = "sha256:" + hashlib.sha256(b"build:release-candidate-42").hexdigest()
GENESIS_REF = {
    "event": "sha256:" + hashlib.sha256(b"acceptance-genesis-event").hexdigest(),
    "attestation": "sha256:" + hashlib.sha256(b"acceptance-genesis-attestation").hexdigest(),
}


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _uuid(label: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(label.encode()).digest()[:16], version=4))


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(
        seed=hashlib.sha256(f"acceptance-contract:{role}".encode()).digest()
    )


ROLES = {
    "release_buyer": _signer("release_buyer"),
    "deploy_agent": _signer("deploy_agent"),
    "staging_controller": _signer("staging_controller"),
    "rollback_test_runner": _signer("rollback_test_runner"),
    "release_authority": _signer("release_authority"),
    "publisher": _signer("publisher"),
}


def _terms() -> dict[str, Any]:
    return {
        "build_hash": BUILD_HASH,
        "environment": "staging",
        "claim": "STAGING_READY",
        "consequence": "release.promote",
    }


def _request(requirement_id: str) -> dict[str, Any]:
    terms = _terms()
    return {
        "profile": EVIDENCE_REQUEST_PROFILE,
        "request_id": "rollback-test-001",
        "requirement_id": requirement_id,
        "artifact_class": "rollback-test-record",
        "accepted_adapter": "bulla.rollback-test/0.1",
        "accepted_values": ["PASS"],
        "required_bindings": {
            "transaction_id": TRANSACTION_ID,
            "term_root": canonical_hash(terms),
            "contract_id": CONTRACT_ID,
            "contract_revision": 1,
            "build_hash": BUILD_HASH,
            "environment": "staging",
            "issuer_role": "rollback_test_runner",
        },
        "challenge_path": "challenge:release-policy-alpha-001",
        "conditional_statement": (
            "If a newly supplied and verified artifact is bound to this transaction, "
            "contract, build, environment, and accepted rollback-test authority, and "
            "its rollback_status is PASS, this requirement will be satisfied."
        ),
    }


def _requirement(
    requirement_id: str,
    path: str,
    action: str,
    role: str,
    adapter: str,
    field: str,
    accepted: str,
    negative: str,
    *,
    absence: str,
    evidence_request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "source_path": path,
        "source_action": action,
        "source_role": role,
        "adapter": adapter,
        "result_field": field,
        "accepted_values": [accepted],
        "negative_values": [negative],
        "absence_route": absence,
        "negative_route": "REFUSE",
        "ambiguity_route": "ESCALATE",
        "evidence_request_id": evidence_request_id,
    }


def contract() -> dict[str, Any]:
    terms = _terms()
    requirements = [
        _requirement(
            "buyer-request",
            "records/01-request.json",
            "release.stage.request",
            "release_buyer",
            "bulla.action-receipt-v0.4",
            "request_status",
            "REQUESTED",
            "CANCELLED",
            absence="REFUSE",
        ),
        _requirement(
            "provider-acceptance",
            "records/02-accept.json",
            "release.stage.accept",
            "deploy_agent",
            "bulla.action-receipt-v0.4",
            "acceptance_status",
            "ACCEPTED",
            "DECLINED",
            absence="REFUSE",
        ),
        _requirement(
            "staging-ready-claim",
            "records/03-staging-ready.json",
            "release.stage.report",
            "deploy_agent",
            "bulla.action-receipt-v0.4",
            "deployment_claim",
            "STAGING_READY",
            "STAGING_FAILED",
            absence="REFUSE",
        ),
        _requirement(
            "receiver-observation",
            "reports/receiver-observation.json",
            "release.stage.observe",
            "staging_controller",
            "bulla.receiver-observation/0.1",
            "observation_status",
            "OBSERVED",
            "MISMATCH",
            absence="ESCALATE",
        ),
        _requirement(
            "receiver-coverage",
            "reports/receiver-coverage.json",
            "release.coverage.checkpoint",
            "staging_controller",
            "bulla.receiver-coverage/0.1",
            "coverage_status",
            "COVERED",
            "UNCOVERED",
            absence="ESCALATE",
        ),
        _requirement(
            "correction-current",
            "reports/correction-state.json",
            "release.correction.checkpoint",
            "release_authority",
            "bulla.correction-state/0.1",
            "correction_state",
            "CURRENT",
            "STALE",
            absence="ESCALATE",
        ),
        _requirement(
            "rollback-test",
            "evidence/rollback-test.json",
            "release.rollback.report",
            "rollback_test_runner",
            "bulla.rollback-test/0.1",
            "rollback_status",
            "PASS",
            "FAIL",
            absence="HOLD_FOR_EVIDENCE",
            evidence_request_id="rollback-test-001",
        ),
    ]
    return {
        "profile": PROFILE,
        "contract_id": CONTRACT_ID,
        "revision": 1,
        "semantic_epoch": 1,
        "authority_epoch": 1,
        "policy_authority_ref": ROLES["release_authority"].issuer,
        "transaction_id": TRANSACTION_ID,
        "terms": terms,
        "term_root": canonical_hash(terms),
        "contemplated_consequence": {
            "action_type": "release.promote",
            "subject_hash": canonical_hash(
                {"build_hash": BUILD_HASH, "environment": "production"}
            ),
            "authority_ref": ROLES["release_authority"].issuer,
        },
        "requirements": requirements,
        "receiver_coverage_requirement": {
            "required": True,
            "anchor_id": "staging-controller:release-acceptance-alpha-001",
            "accepted_status": "COVERED",
        },
        "correction_policy": {
            "required_state": "CURRENT",
            "new_revision_required": True,
        },
        "challenge_policy": {
            "path": "challenge:release-policy-alpha-001",
            "semantic_route": "ESCALATE",
        },
        "evidence_requests": [_request("rollback-test")],
        "authorization_policy": {"eligible_state": "NOT_ISSUED"},
        "execution_policy": {"eligible_state": "NOT_ATTEMPTED"},
    }


def _envelope(role: str, action: str, contract_hash: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal=ROLES[role].issuer, policy=contract_hash),
        bounds=Bounds(scope=f"profile:{PROFILE};action:{action}"),
        recourse=Recourse(
            challenge_window="checkpoint:release-policy-alpha-001",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/bulla/experimental/acceptance-contract",
                trusted_root_ref=canonical_hash({"forum": "acceptance-contract-alpha"}),
            ),
            remedies=(
                Remedy(
                    "challenge",
                    "reevaluate the retained transaction under the accepted contract",
                    "challenge:release-policy-alpha-001",
                ),
            ),
        ),
        retention_class="operational",
        disclosure_class="public",
    )


def _receipt(
    role: str,
    action: str,
    adapter: str,
    result: dict[str, str],
    parent_ref: dict[str, str],
    index: int,
    contract_value: dict[str, Any],
) -> dict[str, Any]:
    contract_hash = canonical_hash(contract_value)
    subject = {
        "profile": PROFILE,
        "issuer_role": role,
        "transaction_id": TRANSACTION_ID,
        "contract_hash": contract_hash,
        "term_root": contract_value["term_root"],
        "build_hash": BUILD_HASH,
        "environment": "staging",
        "adapter": adapter,
        "result": result,
        "parent_ref": parent_ref,
        "authority_epoch": 1,
        "semantic_epoch": 1,
    }
    receipt = build_action_receipt_v04(
        action={"type": action, "subject": subject},
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(role, action, contract_hash),
        event_id=_uuid(f"acceptance:{index}:{role}:{action}:{json.dumps(result, sort_keys=True)}"),
        claimed_at=f"2026-08-13T12:{index:02d}:00Z",
        producer={"bulla_version": "source", "fixture": "acceptance-contract-alpha"},
    )
    return sign_action_receipt_v04(receipt, ROLES[role]).to_dict()


def _ref(receipt: dict[str, Any]) -> dict[str, str]:
    return {
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    }


def _artifact(path: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "media_type": "application/json",
        "byte_length": len(raw),
        "sha256": _sha(raw),
    }


def _bundle(scenario: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    value = contract()
    contract_hash = canonical_hash(value)
    request = _receipt(
        "release_buyer",
        "release.stage.request",
        "bulla.action-receipt-v0.4",
        {"request_status": "REQUESTED"},
        GENESIS_REF,
        1,
        value,
    )
    accept = _receipt(
        "deploy_agent",
        "release.stage.accept",
        "bulla.action-receipt-v0.4",
        {"acceptance_status": "ACCEPTED"},
        _ref(request),
        2,
        value,
    )
    ready = _receipt(
        "deploy_agent",
        "release.stage.report",
        "bulla.action-receipt-v0.4",
        {"deployment_claim": "STAGING_READY"},
        _ref(accept),
        3,
        value,
    )
    observation = _receipt(
        "staging_controller",
        "release.stage.observe",
        "bulla.receiver-observation/0.1",
        {
            "observation_status": (
                "MISMATCH" if scenario == "receiver-mismatch" else "OBSERVED"
            ),
            "observed_build_hash": BUILD_HASH,
        },
        _ref(ready),
        4,
        value,
    )
    coverage = _receipt(
        "staging_controller",
        "release.coverage.checkpoint",
        "bulla.receiver-coverage/0.1",
        {
            "coverage_status": "UNCOVERED" if scenario == "uncovered" else "COVERED",
            "anchor_id": "staging-controller:release-acceptance-alpha-001",
            "observed_effect_ids": (
                ["stage-build-001", "effect-bypass-001"]
                if scenario == "uncovered"
                else ["stage-build-001"]
            ),
            "receipted_effect_ids": ["stage-build-001"],
            "unmatched_effect_ids": (
                ["effect-bypass-001"] if scenario == "uncovered" else []
            ),
        },
        _ref(observation),
        5,
        value,
    )
    correction = _receipt(
        "release_authority",
        "release.correction.checkpoint",
        "bulla.correction-state/0.1",
        {
            "correction_state": "STALE" if scenario == "stale" else "CURRENT",
            "evaluated_contract_hash": contract_hash,
        },
        _ref(coverage),
        6,
        value,
    )
    files = {
        "contract.json": _json(value),
        "records/01-request.json": _json(request),
        "records/02-accept.json": _json(accept),
        "records/03-staging-ready.json": _json(ready),
        "reports/receiver-observation.json": _json(observation),
        "reports/receiver-coverage.json": _json(coverage),
        "reports/correction-state.json": _json(correction),
    }
    if scenario != "missing":
        rollback_status = {
            "passing": "PASS",
            "failing": "FAIL",
            "ambiguous": "UNKNOWN",
            "uncovered": "PASS",
            "stale": "PASS",
            "receiver-mismatch": "PASS",
        }.get(scenario, "FAIL")
        rollback = _receipt(
            "rollback_test_runner",
            "release.rollback.report",
            "bulla.rollback-test/0.1",
            {
                "rollback_status": rollback_status,
                "tested_build_hash": BUILD_HASH,
                "tested_environment": "staging",
            },
            _ref(correction),
            7,
            value,
        )
        files["evidence/rollback-test.json"] = _json(rollback)
    core = {
        "profile": CORE_PROFILE,
        "transaction_id": TRANSACTION_ID,
        "contract_hash": contract_hash,
        "term_root": value["term_root"],
        "roles": {role: signer.issuer for role, signer in sorted(ROLES.items())},
        "artifacts": [_artifact(path, files[path]) for path in sorted(files)],
    }
    files["acceptance-core.json"] = _json(core)
    publish = sign_action_receipt_v04(
        build_action_receipt_v04(
            action={
                "type": "acceptance.contract.publish",
                "subject": {
                    "profile": PROFILE,
                    "issuer_role": "publisher",
                    "transaction_id": TRANSACTION_ID,
                    "acceptance_core_hash": canonical_hash(core),
                    "contract_hash": contract_hash,
                },
            },
            diagnostic_ref={"status": "not_applicable"},
            envelope=_envelope("publisher", "acceptance.contract.publish", contract_hash),
            event_id=_uuid(f"acceptance:publish:{scenario}"),
            claimed_at="2026-08-13T12:08:00Z",
            producer={"bulla_version": "source", "fixture": scenario},
        ),
        ROLES["publisher"],
    ).to_dict()
    files["publish-receipt.json"] = _json(publish)
    context = AcceptanceContext(
        accepted_issuers_by_role={role: [signer.issuer] for role, signer in ROLES.items()},
        accepted_contract_hash=contract_hash,
        accepted_policy_authorities=[ROLES["release_authority"].issuer],
        accepted_adapters=[item["adapter"] for item in value["requirements"]],
        authority_epoch=1,
        semantic_epoch=1,
    ).to_dict()
    return files, context


def _write_tree(root: Path) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    context_value: dict[str, Any] | None = None
    for scenario in SCENARIOS:
        files, context = _bundle(scenario)
        context_value = context
        target = root / scenario
        for path, raw in files.items():
            destination = target / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        report = evaluate_acceptance_bundle(target, AcceptanceContext.from_dict(context))
        expected[scenario] = report.to_dict()
        expected_path = root / "expected" / f"{scenario}.json"
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_bytes(_json(report.to_dict()))
    assert context_value is not None
    (root / "context.json").write_bytes(_json(context_value))
    projection = {
        "profile": "bulla.acceptance-site-projection/0.1",
        "label": "CHECKED SYNTHETIC TRANSACTION",
        "claim": "Staging ready",
        "build": BUILD_HASH,
        "environment": "staging",
        "missing": expected["missing"],
        "passing": expected["passing"],
        "failing": expected["failing"],
        "plain_language": {
            "missing": {
                "deploy_agent_claim": "Staging ready",
                "requested_build": "Matches",
                "staging_controller_observation": "Present",
                "rollback_test_result": "Missing",
                "release_agent_decision": "Wait for evidence",
                "next_required_record": "Signed rollback-test result",
                "production_promotion": "Not eligible",
                "authorization": "Not issued",
                "promotion_attempt": "Not attempted",
            },
            "passing": {
                "deploy_agent_claim": "Staging ready",
                "requested_build": "Matches",
                "staging_controller_observation": "Present",
                "rollback_test_result": "Pass",
                "release_agent_decision": "Proceed under its policy",
                "next_required_record": "None",
                "production_promotion": "Eligible",
                "authorization": "Not issued",
                "promotion_attempt": "Not attempted",
            },
        },
    }
    (root / "site-projection.json").write_bytes(_json(projection))
    return projection


def generate(*, check: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="acceptance-contract-") as tmp:
        temporary = Path(tmp) / "generated"
        temporary.mkdir()
        _write_tree(temporary)
        if check:
            current = {
                path.relative_to(GENERATED).as_posix(): path.read_bytes()
                for path in GENERATED.rglob("*")
                if path.is_file()
            } if GENERATED.exists() else {}
            proposed = {
                path.relative_to(temporary).as_posix(): path.read_bytes()
                for path in temporary.rglob("*")
                if path.is_file()
            }
            if current != proposed:
                missing = sorted(set(proposed) - set(current))
                extra = sorted(set(current) - set(proposed))
                changed = sorted(
                    path
                    for path in set(current) & set(proposed)
                    if current[path] != proposed[path]
                )
                raise SystemExit(
                    "generated acceptance-contract corpus drifted: "
                    f"missing={missing} extra={extra} changed={changed}"
                )
            return
        if GENERATED.exists():
            shutil.rmtree(GENERATED)
        shutil.copytree(temporary, GENERATED)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)


if __name__ == "__main__":
    main()
