"""The stable first-action product demonstration.

The scenario is deliberately constructed and local.  It demonstrates the
causal path from an action boundary to a retained ActionReceipt, then keeps
alteration detection separate from omission detection.  Nothing in this
module claims that a payment network moved funds.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from bulla.action_receipt import verify_receipt
from bulla.coverage import event_coverage
from bulla.receipt_drill import ReceiptDrillError, run_receipt_drill
from bulla.wrap import wrap_action


PRIMARY_ACTION_ID = "pay_demo_042"
BYPASS_ACTION_ID = "pay_demo_043"
DEMO_VERSION = "1"


class FirstActionDemoError(RuntimeError):
    """The demonstration could not complete safely or consistently."""


def _json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FirstActionDemoError(f"artifact is not canonical JSON: {exc}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_new(path: Path, payload: bytes) -> None:
    """Write one artifact without following or replacing an existing path."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise FirstActionDemoError(f"artifact parent must not be a symlink: {path.parent}")
        with path.open("xb") as stream:
            stream.write(payload)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except FileExistsError as exc:
        raise FirstActionDemoError(f"refusing to overwrite existing artifact: {path}") from exc
    except OSError as exc:
        raise FirstActionDemoError(f"cannot write artifact {path}: {exc}") from exc


def _create_output_root(requested: Path | None) -> Path:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="bulla-first-action-"))
    requested = Path(os.path.abspath(requested))
    if requested.is_symlink() or requested.exists():
        raise FirstActionDemoError(f"--out must name a nonexistent path: {requested}")
    for ancestor in (requested.parent, *requested.parent.parents):
        if ancestor.is_symlink():
            raise FirstActionDemoError(f"--out must not traverse a symlink: {ancestor}")
    if not requested.parent.exists() or not requested.parent.is_dir():
        raise FirstActionDemoError(f"--out parent must be an existing directory: {requested.parent}")
    try:
        requested.mkdir(mode=0o700)
    except OSError as exc:
        raise FirstActionDemoError(f"cannot create --out directory {requested}: {exc}") from exc
    return requested


def _payment_convention() -> dict:
    return {
        "name": "payment-within-declared-limit",
        "scope": "seam:constructed-receiver->payments.charge",
        "kind": "executable",
        "definition": {
            "form": "jsonschema+quantum/1",
            "schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "amount_minor": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20000,
                    },
                    "currency": {"type": "string", "const": "USD"},
                },
                "required": ["event_id", "amount_minor", "currency"],
                "additionalProperties": False,
            },
            "quantum": {
                "amount_minor": {"unit": "USD_minor", "multipleOf": 1},
            },
        },
    }


def _receiver_record(event_id: str, amount_minor: int) -> dict:
    return {
        "id": event_id,
        "kind": "payments.charge",
        "amount_minor": amount_minor,
        "currency": "USD",
        "receiver": "constructed-local-receiver",
    }


def _strip_generated_at(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "generated_at"}


def _verification_report(receipt: dict) -> dict:
    verdict = verify_receipt(receipt)
    remedy = receipt.get("remedy") if isinstance(receipt.get("remedy"), dict) else {}
    forum = remedy.get("forum") if isinstance(remedy.get("forum"), dict) else {}
    convention_results = list(verdict.conventions.values())
    if not verdict.ok:
        declared_bounds = "NOT_EVALUATED"
    elif "violates" in convention_results or verdict.bounds_conformance == "violates":
        declared_bounds = "VIOLATES"
    elif "conforms" in convention_results or verdict.bounds_conformance == "conforms":
        declared_bounds = "CONFORMS"
    elif "pinned" in convention_results:
        declared_bounds = "PINNED"
    else:
        declared_bounds = verdict.bounds_conformance.upper()
    return {
        "record_integrity": "VERIFIED" if verdict.ok else "FAILED",
        "verified_to": verdict.verified_to.upper(),
        "declared_bounds": declared_bounds,
        "evidence_grounding": (
            (verdict.effective_grounding or "NO_EVIDENCE_DECLARED").upper()
            if verdict.ok
            else "NOT_EVALUATED"
        ),
        "authority": verdict.authority_authentic.upper() if verdict.ok else "NOT_EVALUATED",
        "recourse": "DECLARED" if verdict.ok and forum else "NOT_EVALUATED",
        "underlying_event_occurrence": "NOT_ESTABLISHED",
        "receipt_coverage": "REQUIRES_SEPARATE_ACTION_RECORD",
        "reliance": "NOT_COMPUTED",
        "reasons": list(verdict.reasons),
    }


def _artifact_paths() -> dict[str, str]:
    return {
        "receiver_actions": "receiver-actions.json",
        "receipt": f"receipts/{PRIMARY_ACTION_ID}.json",
        "tamper_control": f"controls/{PRIMARY_ACTION_ID}.amount-changed.json",
        "verification": "reports/verification.json",
        "coverage_before": "reports/coverage-before.json",
        "coverage_after": "reports/coverage-after.json",
        "drill": "reports/drill.json",
        "report": "report.json",
    }


def run_first_action_demo(output: Path | None = None) -> tuple[Path, dict]:
    """Run the fixed demonstration and retain every inspectable artifact."""
    root = _create_output_root(output)
    paths = _artifact_paths()
    observed: list[dict] = []
    receipts: list[dict] = []

    primary = _receiver_record(PRIMARY_ACTION_ID, 12500)
    subject = {
        "event_id": PRIMARY_ACTION_ID,
        "amount_minor": 12500,
        "currency": "USD",
    }
    scope = wrap_action(
        "payments.charge",
        subject,
        principal="did:web:example.test#agent",
        policy="policy://constructed-payment-v1",
        scope="payments.charge amount_minor<=20000 currency=USD",
        diagnostic_ref={"status": "not_applicable"},
        conventions=(_payment_convention(),),
        timestamp="2026-08-04T00:00:00Z",
    )
    with scope as action:
        # The receiver record is the separate coverage denominator.  In this
        # constructed scenario the local call is the action being wrapped.
        observed.append(primary)
        receiver_payload = _json_bytes(primary)
        receiver_digest = "sha256:" + _sha256(receiver_payload)
        action.add_evidence("constructed_receiver_record", receiver_digest, "self_asserted")
        action.set_result(receiver_digest)
    if scope.receipt is None:
        raise FirstActionDemoError("the wrapped action did not emit a receipt")
    receipt = scope.receipt
    receipts.append(receipt)

    receipt_payload = _json_bytes(receipt)
    receipt_digest = _sha256(receipt_payload)
    _write_new(root / paths["receipt"], receipt_payload)

    verification = _verification_report(receipt)
    if verification["record_integrity"] != "VERIFIED":
        raise FirstActionDemoError("the emitted receipt did not pass record-integrity verification")
    if verification["declared_bounds"] != "CONFORMS":
        raise FirstActionDemoError("the fixed payment did not conform to its USD 200 bound")
    if verification["evidence_grounding"] != "SELF_ASSERTED":
        raise FirstActionDemoError("the constructed receiver evidence lost its grounding label")

    tampered = copy.deepcopy(receipt)
    tampered["action"]["subject"]["amount_minor"] = 12501
    tampered_payload = _json_bytes(tampered)
    tamper_verification = _verification_report(tampered)
    if tamper_verification["record_integrity"] != "FAILED":
        raise FirstActionDemoError("the deterministic amount-change control was not rejected")
    _write_new(root / paths["tamper_control"], tampered_payload)

    coverage_before = _strip_generated_at(
        event_coverage(observed, receipts, anchor="constructed-receiver-record")
    )
    if (
        coverage_before.get("receipted") != 1
        or coverage_before.get("total_anchored") != 1
        or coverage_before.get("unreceipted_delta") != []
    ):
        raise FirstActionDemoError("the intact scenario did not produce coverage 1/1")

    observed.append(_receiver_record(BYPASS_ACTION_ID, 5000))
    coverage_after = _strip_generated_at(
        event_coverage(observed, receipts, anchor="constructed-receiver-record")
    )
    if (
        coverage_after.get("receipted") != 1
        or coverage_after.get("total_anchored") != 2
        or coverage_after.get("unreceipted_delta") != [BYPASS_ACTION_ID]
    ):
        raise FirstActionDemoError("the bypass scenario did not identify pay_demo_043 at coverage 1/2")

    if _sha256((root / paths["receipt"]).read_bytes()) != receipt_digest:
        raise FirstActionDemoError("the original receipt changed during the demonstration")

    try:
        drill, drill_exit = run_receipt_drill(root / paths["receipt"])
    except ReceiptDrillError as exc:
        raise FirstActionDemoError(str(exc)) from exc
    if drill_exit != 0 or drill.get("verifier", {}).get("checker_agreement") != "MATCH":
        raise FirstActionDemoError("the retained-receipt drill did not complete with checker agreement")

    _write_new(root / paths["receiver_actions"], _json_bytes(observed))
    _write_new(root / paths["verification"], _json_bytes(verification))
    _write_new(root / paths["coverage_before"], _json_bytes(coverage_before))
    _write_new(root / paths["coverage_after"], _json_bytes(coverage_after))
    _write_new(root / paths["drill"], _json_bytes(drill))

    report = {
        "demo_version": DEMO_VERSION,
        "constructed": True,
        "scenario": "first-action-to-missing-action",
        "artifacts": paths,
        "action": {
            "id": PRIMARY_ACTION_ID,
            "type": "payments.charge",
            "amount": "USD 125.00",
            "declared_limit": "USD 200.00",
            "receiver_record": "constructed-local-receiver",
        },
        "receipt": {
            "path": paths["receipt"],
            "sha256": receipt_digest,
            "schema_version": receipt.get("schema_version"),
            "automatically_emitted": True,
        },
        "verification": verification,
        "tamper_control": {
            "path": paths["tamper_control"],
            "changed_field": "action.subject.amount_minor",
            "changed_value": 12501,
            "record_integrity": tamper_verification["record_integrity"],
            "original_receipt_unchanged": True,
        },
        "coverage": {
            "before": coverage_before,
            "after": coverage_after,
        },
        "drill": drill,
        "boundaries": [
            "the receiver action record is constructed",
            "receipt integrity does not establish that funds moved",
            "coverage is relative to the supplied receiver action record",
            "reliance is not computed",
        ],
    }
    _write_new(root / paths["report"], _json_bytes(report))
    return root, report


def print_first_action_demo(root: Path, report: dict) -> None:
    verification = report["verification"]
    before = report["coverage"]["before"]
    after = report["coverage"]["after"]
    print("FIRST ACTION DEMO · CONSTRUCTED LOCAL SCENARIO")
    print("\nACTION")
    print(f"recorded action       {report['action']['type']}")
    print(f"action id             {report['action']['id']}")
    print(f"amount                {report['action']['amount']}")
    print(f"declared limit        {report['action']['declared_limit']}")
    print(f"receipt               {report['receipt']['path']}")
    print("\nLOCAL RECEIPT CHECKS")
    print(f"record integrity      {verification['record_integrity']}")
    print(f"declared bounds       {verification['declared_bounds']}")
    print(f"evidence grounding    {verification['evidence_grounding']}")
    print(f"authority             {verification['authority']}")
    print(f"event occurrence      {verification['underlying_event_occurrence']}")
    print("\nALTERATION CONTROL")
    print(f"changed amount        USD 125.01")
    print(f"record integrity      {report['tamper_control']['record_integrity']}")
    print("original receipt      UNCHANGED")
    print("\nOMISSION CONTROL")
    print(f"coverage before       {before['receipted']}/{before['total_anchored']}")
    print(f"coverage after        {after['receipted']}/{after['total_anchored']}")
    print(f"unreceipted action    {after['unreceipted_delta'][0]}")
    print("original integrity    VERIFIED")
    print("\nOFFLINE DRILL")
    print(f"checker agreement     {report['drill']['verifier']['checker_agreement']}")
    print(f"network guard         {report['drill']['verifier']['network_guard']}")
    print("\nThe receipt caught alteration. The receiver's action record caught omission.")
    print("This is a constructed receiver record. Neither record establishes that funds moved.")
    print(f"\nartifacts              {root.resolve()}")


__all__ = [
    "BYPASS_ACTION_ID",
    "DEMO_VERSION",
    "FirstActionDemoError",
    "PRIMARY_ACTION_ID",
    "print_first_action_demo",
    "run_first_action_demo",
]
