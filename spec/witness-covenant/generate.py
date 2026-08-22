#!/usr/bin/env python3
"""Generate deterministic vectors for bulla.witness-covenant/0.1-experimental."""

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
from bulla.experimental.witness_covenant import (
    BOND,
    CONTEXT_PROFILE,
    MAX_REMEDY,
    PROFILE,
    PREDICATE_PROFILE,
    RAIL,
    ROLE_ACTIONS,
    UNIT,
    canonical_hash,
    verify_witness_covenant,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


HERE = Path(__file__).resolve().parent
GENERATED = ("contexts", "vectors", "reports")
SOURCE_DOCUMENTS = (
    "PROFILE.md", "PREREGISTRATION.md", "CORRECTION-001.md",
    "THREAT-MODEL.md", "DESIGN-BASIS.md", "WIRE-FORMAT.md",
    "formal-fixture-map.json",
)
SCENARIOS = (
    "consistent", "fork-open", "fork-closed", "fork-closed-no-bond",
    "fork-authorized", "model-dispute",
)
AUTHORITY_EPOCH = "witness-epoch-2026-08"
AUTHORITY_GRANT = "sha256:" + hashlib.sha256(b"witness-covenant-authority-grant").hexdigest()
CAPITAL_CHECKPOINT = "sha256:" + hashlib.sha256(b"test-ledger:witness-covenant").hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _hash_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _uuid(label: str) -> str:
    raw = bytearray(hashlib.sha256(label.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(seed=hashlib.sha256(f"witness-covenant:{role}".encode()).digest())


def _envelope(role: str, action_type: str) -> RecourseEnvelope:
    issuer = _signer(role).issuer
    return RecourseEnvelope(
        authority=Authority(
            principal=issuer,
            policy=f"policy://witness-covenant/{action_type}",
        ),
        bounds=Bounds(scope=f"profile:{PROFILE}"),
        recourse=Recourse(
            challenge_window="checkpoint:witness-covenant-5",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/bulla/answerable-computing",
                trusted_root_ref=_hash_bytes(b"witness-covenant-forum-root"),
            ),
            remedies=(Remedy(
                "challenge",
                "verify the covenant dossier under separately supplied trust roots",
                "forum:witness-covenant",
            ),),
        ),
        retention_class="operational",
        disclosure_class="public",
    )


def _receipt(role: str, subject: dict[str, Any], index: int) -> dict[str, Any]:
    action_type = ROLE_ACTIONS[role]
    unsigned = build_action_receipt_v04(
        action={
            "type": action_type,
            "subject": {"profile": PROFILE, "issuer_role": role, **subject},
        },
        diagnostic_ref={"status": "not_applicable"},
        envelope=_envelope(role, action_type),
        event_id=_uuid(f"{index}:{role}:{canonical_hash(subject)}"),
        claimed_at=f"2026-08-21T12:{index:02d}:00Z",
        producer={"bulla_version": "source", "fixture": "witness-covenant-v0.1"},
    )
    return sign_action_receipt_v04(unsigned, _signer(role)).to_dict()


def _covenant() -> dict[str, Any]:
    body = {
        "operator": _signer("witness_operator").issuer,
        "key_identifier": _signer("witness_operator").issuer,
        "log_id": "answerability-network:receipt-history",
        "authority_epoch": AUTHORITY_EPOCH,
        "checkpoint_profile": "bulla.witness-checkpoint/0.1-draft",
        "covered_duty": "sign no different roots for one log, epoch, and tree size",
        "fault_predicate": PREDICATE_PROFILE,
        "challenge_checkpoint": {"domain": "accepted-checkpoint-height", "value": 5},
        "correction_path": "append a signed covenant correction under a later authority epoch",
        "beneficiary": "fixture:buyer",
        "settlement_authority": _signer("settlement_authority").issuer,
        "destination": "test-ledger:buyer-remedy",
        "maximum_remedy": MAX_REMEDY,
        "capital": {
            "binding_id": "witness-bond-001",
            "unit": UNIT,
            "required_allocation": BOND,
            "binding_mode": "DEDICATED",
            "rail_adapter": RAIL,
        },
    }
    return {"covenant_hash": canonical_hash(body), **body}


def _artifact(path: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": _hash_bytes(raw),
        "byte_length": len(raw),
        "media_type": "application/json",
    }


def _receipt_manifest(path: str, role: str, receipt: dict[str, Any], raw: bytes) -> dict[str, Any]:
    return {
        **_artifact(path, raw),
        "role": role,
        "action_type": receipt["action"]["type"],
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    }


def _heads(
    covenant: dict[str, Any], *, fork: bool, provider_claim: dict[str, Any] | None = None,
    link_external_prefix: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    anchor = {
        "authority_epoch": covenant["authority_epoch"],
        "covenant_hash": covenant["covenant_hash"],
    }
    with tempfile.TemporaryDirectory(prefix="witness-covenant-heads-") as raw_dir:
        root = Path(raw_dir)
        log_a = DeedLog(root / "a.jsonl")
        log_b = DeedLog(root / "b.jsonl")
        start = 0
        if provider_claim is not None:
            claim_deed = Deed(
                provider_claim["signature"]["issuer"],
                provider_claim["hashes"]["content"],
                provider_claim["hashes"]["attestation"],
            )
            log_a.append(claim_deed)
            log_b.append(claim_deed)
            start = 1
        prefix = None
        if link_external_prefix:
            prefix = issue_checkpoint(
                log_a, _signer("witness_operator"), log_id=covenant["log_id"],
                issued_at="2026-08-21T12:05:00Z", anchor_evidence=anchor,
            )
        for index in range(start, 4):
            shared = index < 3 or not fork
            suffix = "shared" if shared else "fork-b"
            deed = Deed(
                _signer("witness_operator").issuer,
                _hash_bytes(f"content:{index}:{suffix}".encode()),
                _hash_bytes(f"attestation:{index}:{suffix}".encode()),
            )
            log_a.append(Deed(
                _signer("witness_operator").issuer,
                _hash_bytes(f"content:{index}:shared".encode()),
                _hash_bytes(f"attestation:{index}:shared".encode()),
            ))
            log_b.append(deed)
        head_a = issue_checkpoint(
            log_a,
            _signer("witness_operator"),
            log_id=covenant["log_id"],
            issued_at="2026-08-21T12:10:00Z",
            anchor_evidence=anchor,
            previous=prefix,
        )
        head_b = issue_checkpoint(
            log_b,
            _signer("witness_operator"),
            log_id=covenant["log_id"],
            issued_at="2026-08-21T12:10:01Z",
            anchor_evidence=anchor,
            previous=prefix,
        )
        inclusion = None
        if provider_claim is not None:
            inclusion = log_a.inclusion(0)
            inclusion["attestation"] = provider_claim["hashes"]["attestation"]
        consistency = log_a.consistency(1) if link_external_prefix else None
    return head_a.to_dict(), head_b.to_dict(), inclusion, prefix.to_dict() if prefix else None, consistency


def _build_scenario(
    root: Path,
    scenario: str,
    *,
    external_history_receipt: dict[str, Any] | None = None,
) -> Path:
    covenant = _covenant()
    covenant_id = "witness-covenant-001"
    common = {"covenant_id": covenant_id, "covenant_hash": covenant["covenant_hash"]}
    provider_claim = None
    if scenario == "model-dispute":
        provider_claim = _receipt("provider", {
            **common,
            "proposition_digest": _hash_bytes(b"model-family-is-synthetic-v1"),
            "claim_class": "MODEL_IDENTITY_P3",
            "claim_value": "model-family:synthetic-v1",
        }, 3)
    fork = scenario not in {"consistent", "model-dispute"}
    history_receipt = provider_claim or external_history_receipt
    head_a, head_b, history_inclusion, prefix_head, history_consistency = _heads(
        covenant, fork=fork, provider_claim=history_receipt,
        link_external_prefix=external_history_receipt is not None,
    )
    equivocation_finding_ref = canonical_hash({
        "predicate": PREDICATE_PROFILE,
        "head_a": head_a["checkpoint_hash"],
        "head_b": head_b["checkpoint_hash"],
    })
    finding_ref = equivocation_finding_ref
    if provider_claim is not None:
        finding_ref = canonical_hash({
            "claim_attestation": provider_claim["hashes"]["attestation"],
            "proposition_digest": provider_claim["action"]["subject"]["proposition_digest"],
            "claim_class": provider_claim["action"]["subject"]["claim_class"],
        })

    receipts: list[tuple[str, dict[str, Any]]] = []
    receipts.append(("witness_operator", _receipt("witness_operator", common, 1)))
    if scenario != "fork-closed-no-bond":
        receipts.append(("rail_observer", _receipt("rail_observer", {
            **common,
            "binding_id": covenant["capital"]["binding_id"],
            "unit": UNIT,
            "locked_amount": BOND,
            "allocation_manifest": [{
                "covenant_hash": covenant["covenant_hash"], "amount": BOND, "active": True,
            }],
            "reported_unspent": True,
            "checkpoint_ref": CAPITAL_CHECKPOINT,
            "rail_adapter": RAIL,
        }, 2)))
    if provider_claim is not None:
        receipts.append(("provider", provider_claim))

    finding_class = "MODEL_IDENTITY_P3" if scenario == "model-dispute" else "SAME_SIZE_LOG_EQUIVOCATION"
    if scenario != "consistent":
        opened = _receipt("challenge_authority", {
            **common,
            "finding_ref": finding_ref,
            "finding_class": finding_class,
            "challenge_state": "OPEN",
            "challenge_checkpoint": covenant["challenge_checkpoint"],
            "observed_checkpoint": 4,
            "prior_challenge_ref": None,
        }, len(receipts) + 1)
        receipts.append(("challenge_authority", opened))
        if scenario in {"fork-closed", "fork-closed-no-bond", "fork-authorized"}:
            closed = _receipt("challenge_authority", {
                **common,
                "finding_ref": finding_ref,
                "finding_class": finding_class,
                "challenge_state": "EXPIRED",
                "challenge_checkpoint": covenant["challenge_checkpoint"],
                "observed_checkpoint": 5,
                "prior_challenge_ref": opened["hashes"]["attestation"],
            }, len(receipts) + 1)
            receipts.append(("challenge_authority", closed))

    files: dict[str, bytes] = {
        "evidence/head-a.json": _json(head_a),
        "evidence/head-b.json": _json(head_b),
    }
    if provider_claim is not None and history_inclusion is not None:
        files["evidence/model-claim-inclusion.json"] = _json(history_inclusion)
    if external_history_receipt is not None and history_inclusion is not None:
        files["evidence/external-receipt-inclusion.json"] = _json(history_inclusion)
        files["evidence/external-receipt-checkpoint.json"] = _json(prefix_head)
        files["evidence/external-receipt-consistency.json"] = _json(history_consistency)
    if scenario == "fork-authorized":
        challenge = receipts[-1][1]
        authorization = _receipt("settlement_authority", {
            **common,
            "finding_ref": finding_ref,
            "finding_class": "SAME_SIZE_LOG_EQUIVOCATION",
            "challenge_attestation": challenge["hashes"]["attestation"],
            "consequence": "TRANSFER_COLLATERAL",
            "amount": MAX_REMEDY,
            "unit": UNIT,
            "destination": covenant["destination"],
            "capital_binding_id": covenant["capital"]["binding_id"],
            "challenge_state": "EXPIRED",
            "rail_checkpoint": CAPITAL_CHECKPOINT,
            "authority_grant": AUTHORITY_GRANT,
        }, 5)
        receipts.append(("settlement_authority", authorization))
        ledger = {
            "profile": "bulla.test-ledger/1",
            "covenant_hash": covenant["covenant_hash"],
            "authorization_attestation": authorization["hashes"]["attestation"],
            "status": "ATTEMPT_REPORTED",
            "amount": MAX_REMEDY,
            "unit": UNIT,
            "destination": covenant["destination"],
            "synthetic": True,
            "actual_funds": False,
        }
        ledger_raw = _json(ledger)
        files["settlement/test-ledger-report.json"] = ledger_raw
        observer = _receipt("settlement_observer", {
            **common,
            "fixture_report_hash": _hash_bytes(ledger_raw),
            "authorization_attestation": authorization["hashes"]["attestation"],
            "amount": MAX_REMEDY,
            "unit": UNIT,
            "destination": covenant["destination"],
            "status": "ATTEMPT_REPORTED",
            "synthetic": True,
            "actual_funds": False,
        }, 6)
        receipts.append(("settlement_observer", observer))

    receipt_manifest = []
    for index, (role, receipt) in enumerate(receipts, 1):
        path = f"receipts/{index:02d}-{role}.json"
        raw = _json(receipt)
        files[path] = raw
        receipt_manifest.append(_receipt_manifest(path, role, receipt, raw))

    core = {
        "profile": PROFILE,
        "revision": 1,
        "scenario": scenario,
        "covenant_id": covenant_id,
        "covenant": covenant,
        "role_issuers": {role: _signer(role).issuer for role in sorted(ROLE_ACTIONS)},
        "receipt_manifest": receipt_manifest,
        "artifact_manifest": [
            _artifact(path, raw)
            for path, raw in sorted(files.items())
            if not path.startswith("receipts/")
        ],
    }
    files["covenant-core.json"] = _json(core)
    dossier = root / "vectors" / scenario
    for path, raw in files.items():
        target = dossier / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    context = {
        "profile": CONTEXT_PROFILE,
        "accepted_operator": covenant["operator"],
        "accepted_log": covenant["log_id"],
        "authority_epoch": covenant["authority_epoch"],
        "accepted_covenant_hash": covenant["covenant_hash"],
        "accepted_issuers_by_role": {
            role: [_signer(role).issuer] for role in sorted(ROLE_ACTIONS)
        },
        "accepted_authority_grants": [AUTHORITY_GRANT],
        "accepted_rail_adapters": [RAIL],
        "accepted_capital_checkpoint": CAPITAL_CHECKPOINT,
        "controlled_roles": sorted(ROLE_ACTIONS),
    }
    context_path = root / "contexts" / f"{scenario}.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_bytes(_json(context))
    return context_path


def _tree_digest(root: Path) -> tuple[str, list[dict[str, Any]]]:
    entries = []
    for name in GENERATED:
        for path in sorted(item for item in (root / name).rglob("*") if item.is_file()):
            raw = path.read_bytes()
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": _hash_bytes(raw),
                "byte_length": len(raw),
            })
    return canonical_hash(entries), entries


def build(root: Path) -> None:
    for name in GENERATED:
        shutil.rmtree(root / name, ignore_errors=True)
    contexts = {scenario: _build_scenario(root, scenario) for scenario in SCENARIOS}
    (root / "reports").mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        report = verify_witness_covenant(
            root / "vectors" / scenario,
            contexts[scenario].read_bytes(),
        ).to_dict()
        (root / "reports" / f"{scenario}.json").write_bytes(_json(report))
    root_hash, entries = _tree_digest(root)
    manifest = {
        "profile": PROFILE,
        "generated_root": root_hash,
        "entries": entries,
        "source_entries": [
            _artifact(name, (root / name).read_bytes())
            for name in SOURCE_DOCUMENTS
            if (root / name).is_file()
        ],
    }
    (root / "manifest.json").write_bytes(_json(manifest))
    expected = {
        scenario: json.loads((root / "reports" / f"{scenario}.json").read_bytes())
        for scenario in SCENARIOS
    }
    (root / "expected-verdicts.json").write_bytes(_json(expected))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        build(HERE)
        return
    with tempfile.TemporaryDirectory(prefix="witness-covenant-check-") as raw:
        candidate = Path(raw)
        for name in SOURCE_DOCUMENTS:
            source = HERE / name
            if source.exists():
                shutil.copy2(source, candidate / name)
        build(candidate)
        for name in (*GENERATED, "manifest.json", "expected-verdicts.json"):
            left = HERE / name
            right = candidate / name
            if left.is_dir():
                left_files = {p.relative_to(left): p.read_bytes() for p in left.rglob("*") if p.is_file()}
                right_files = {p.relative_to(right): p.read_bytes() for p in right.rglob("*") if p.is_file()}
                if left_files != right_files:
                    raise SystemExit(f"generated {name} differs")
            elif left.read_bytes() != right.read_bytes():
                raise SystemExit(f"generated {name} differs")
    print("witness covenant generation is deterministic")


if __name__ == "__main__":
    main()
