from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.experimental.checkpoint import ordering_domain
from bulla.experimental.witness_covenant import (
    verify_witness_covenant,
    verify_witness_covenant_report,
)


BULLA = Path(__file__).resolve().parents[1]
SPEC = BULLA / "spec" / "witness-covenant"
SCENARIOS = (
    "consistent", "fork-open", "fork-closed", "fork-closed-no-bond",
    "fork-authorized", "model-dispute",
)
EXPECTED = json.loads((SPEC / "expected-verdicts.json").read_text())
_GENERATOR_SPEC = importlib.util.spec_from_file_location("witness_covenant_generator", SPEC / "generate.py")
assert _GENERATOR_SPEC and _GENERATOR_SPEC.loader
GENERATOR = importlib.util.module_from_spec(_GENERATOR_SPEC)
_GENERATOR_SPEC.loader.exec_module(GENERATOR)


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _copy(tmp_path: Path, scenario: str) -> tuple[Path, Path]:
    dossier = tmp_path / scenario
    shutil.copytree(SPEC / "vectors" / scenario, dossier)
    context = tmp_path / f"{scenario}-context.json"
    shutil.copy2(SPEC / "contexts" / f"{scenario}.json", context)
    return dossier, context


def _refresh(core: dict, dossier: Path, relative: str) -> None:
    raw = (dossier / relative).read_bytes()
    for section in ("receipt_manifest", "artifact_manifest"):
        for entry in core[section]:
            if entry["path"] == relative:
                entry["sha256"] = _sha(raw)
                entry["byte_length"] = len(raw)
                return
    raise AssertionError(relative)


def _replace_role_receipt(dossier: Path, role: str, subject: dict, index: int) -> None:
    core = _json(dossier / "covenant-core.json")
    receipt = GENERATOR._receipt(role, subject, index)
    for entry in core["receipt_manifest"]:
        if entry["role"] == role:
            path = dossier / entry["path"]
            _write(path, receipt)
            raw = path.read_bytes()
            entry.update({
                "sha256": _sha(raw),
                "byte_length": len(raw),
                "event": receipt["hashes"]["event"],
                "attestation": receipt["hashes"]["attestation"],
            })
            _write(dossier / "covenant-core.json", core)
            return
    raise AssertionError(role)


def _replace_receipt_path(
    dossier: Path, relative: str, role: str, receipt: dict
) -> None:
    core = _json(dossier / "covenant-core.json")
    for entry in core["receipt_manifest"]:
        if entry["path"] == relative:
            _write(dossier / relative, receipt)
            raw = (dossier / relative).read_bytes()
            entry.update({
                "sha256": _sha(raw), "byte_length": len(raw),
                "role": role, "action_type": receipt["action"]["type"],
                "event": receipt["hashes"]["event"],
                "attestation": receipt["hashes"]["attestation"],
            })
            _write(dossier / "covenant-core.json", core)
            return
    raise AssertionError(relative)


def _custom_receipt(
    role: str,
    subject: dict,
    index: int,
    *,
    action_extra: dict | None = None,
    claimed_at: str | None = None,
    signer_role: str | None = None,
) -> dict:
    action = {
        "type": GENERATOR.ROLE_ACTIONS[role],
        "subject": {"profile": GENERATOR.PROFILE, "issuer_role": role, **subject},
        **(action_extra or {}),
    }
    signing_role = signer_role or role
    unsigned = build_action_receipt_v04(
        action=action,
        diagnostic_ref={"status": "not_applicable"},
        envelope=GENERATOR._envelope(signing_role, GENERATOR.ROLE_ACTIONS[role]),
        event_id=GENERATOR._uuid(f"custom:{index}:{role}:{GENERATOR.canonical_hash(subject)}"),
        claimed_at=claimed_at or f"2026-08-21T13:{index:02d}:00Z",
        producer={"bulla_version": "source", "fixture": "hostile"},
    )
    return sign_action_receipt_v04(unsigned, GENERATOR._signer(signing_role)).to_dict()


def _replace_checkpoint(
    dossier: Path,
    relative: str,
    change: dict,
    *,
    signer_role: str = "witness_operator",
) -> None:
    core = _json(dossier / "covenant-core.json")
    head = _json(dossier / relative)
    head.update(change)
    if "operator" in change or "log_id" in change:
        head["ordering_domain"] = ordering_domain(head["log_id"], head["operator"])
    unsigned = {
        key: value
        for key, value in head.items()
        if key not in {"checkpoint_hash", "proof"}
    }
    head["checkpoint_hash"] = GENERATOR.canonical_hash(unsigned)
    head["proof"] = GENERATOR._signer(signer_role).sign_domain(
        "witness-checkpoint", head["checkpoint_hash"], schema="0.3",
    )
    _write(dossier / relative, head)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)


def _node(dossier: Path, context: Path) -> dict:
    completed = subprocess.run(
        ["node", str(SPEC / "check.mjs"), str(dossier), "--context", str(context)],
        check=False,
        capture_output=True,
        text=True,
    )
    if not completed.stdout:
        return {"exit_code": completed.returncode, "errors": [completed.stderr.strip()]}
    return json.loads(completed.stdout)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_python_node_and_frozen_report_agree(scenario: str) -> None:
    dossier = SPEC / "vectors" / scenario
    context = SPEC / "contexts" / f"{scenario}.json"
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    assert python_report == EXPECTED[scenario]
    assert _node(dossier, context) == python_report


def test_report_verification_recomputes_exact_result() -> None:
    scenario = "fork-authorized"
    report = verify_witness_covenant_report(
        (SPEC / "reports" / f"{scenario}.json").read_bytes(),
        SPEC / "vectors" / scenario,
        (SPEC / "contexts" / f"{scenario}.json").read_bytes(),
    )
    assert report.exit_code == 0
    assert report.test_ledger_attempt == "REPORTED"
    assert report.actual_funds == "NOT_ESTABLISHED"


def test_report_verification_requires_type_exact_equality() -> None:
    scenario = "consistent"
    supplied = _json(SPEC / "reports" / f"{scenario}.json")
    supplied["exit_code"] = False
    report = verify_witness_covenant_report(
        json.dumps(supplied).encode(),
        SPEC / "vectors" / scenario,
        (SPEC / "contexts" / f"{scenario}.json").read_bytes(),
    )
    assert report.exit_code == 1
    assert "supplied report differs" in report.errors[0]


def test_single_view_cannot_establish_fault(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "evidence/head-b.json"
    (dossier / relative).unlink()
    core["artifact_manifest"] = [entry for entry in core["artifact_manifest"] if entry["path"] != relative]
    _write(dossier / "covenant-core.json", core)
    report = verify_witness_covenant(dossier, context.read_bytes())
    assert report.exit_code == 1
    assert report.same_size_equivocation == "NOT_COMPUTED"
    assert report.witness_remedy == "NOT_COMPUTED"
    assert _node(dossier, context)["exit_code"] != 0


def test_packet_carried_trust_cannot_replace_context(tmp_path: Path) -> None:
    dossier, context_path = _copy(tmp_path, "fork-closed")
    context = _json(context_path)
    context["accepted_operator"] = context["accepted_issuers_by_role"]["rail_observer"][0]
    _write(context_path, context)
    report = verify_witness_covenant(dossier, context_path.read_bytes())
    assert report.exit_code == 1
    assert report.witness_trust == "NOT_COMPUTED"
    assert _node(dossier, context_path)["exit_code"] == 1


def test_controlled_roles_requires_an_exact_array(tmp_path: Path) -> None:
    dossier, context_path = _copy(tmp_path, "fork-closed")
    context = _json(context_path)
    context["controlled_roles"] = {
        role: 0 for role in context["controlled_roles"]
    }
    _write(context_path, context)
    python_report = verify_witness_covenant(dossier, context_path.read_bytes()).to_dict()
    node_report = _node(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == node_report["witness_remedy"] == "NOT_COMPUTED"


def test_context_must_accept_named_authority_before_remedy_eligibility(
    tmp_path: Path,
) -> None:
    dossier, context_path = _copy(tmp_path, "fork-closed")
    context = _json(context_path)
    context["accepted_issuers_by_role"]["settlement_authority"] = [
        GENERATOR._signer("alternate_settlement_authority").issuer
    ]
    _write(context_path, context)
    python_report = verify_witness_covenant(dossier, context_path.read_bytes()).to_dict()
    node_report = _node(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["issuer_role_status"] == "NOT_COMPUTED"
    assert python_report["witness_remedy"] == "NOT_COMPUTED"


def test_invalid_checkpoint_signature_fails_closed_in_both_kernels(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "evidence/head-b.json"
    head = _json(dossier / relative)
    proof = head["proof"]["proofValue"]
    head["proof"]["proofValue"] = ("A" if proof[0] != "A" else "B") + proof[1:]
    _write(dossier / relative, head)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["checkpoint_authenticity"] == "NOT_COMPUTED"
    assert python_report["exit_code"] == node_report["exit_code"] == 1


@pytest.mark.parametrize(
    ("change", "signer_role"),
    (
        ({"log_id": "other-log"}, "witness_operator"),
        ({"anchor_evidence": {"authority_epoch": "other-epoch", "covenant_hash": "sha256:" + "a" * 64}}, "witness_operator"),
        ({"tree_size": 5, "position": 5}, "witness_operator"),
        ({"operator": GENERATOR._signer("alternate_witness_operator").issuer}, "alternate_witness_operator"),
        ({"previous_checkpoint_hash": "garbage"}, "witness_operator"),
    ),
)
def test_validly_signed_incomparable_or_malformed_head_is_rejected(
    tmp_path: Path, change: dict, signer_role: str
) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    _replace_checkpoint(dossier, "evidence/head-b.json", change, signer_role=signer_role)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_checkpoint_position_rejects_boolean_coercion(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "consistent")
    for relative in ("evidence/head-a.json", "evidence/head-b.json"):
        _replace_checkpoint(
            dossier, relative, {"tree_size": 1, "position": True},
        )
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["checkpoint_authenticity"] == "NOT_COMPUTED"


def test_borrowed_receipt_proof_is_rejected(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "receipts/01-witness_operator.json"
    receipt = _json(dossier / relative)
    borrowed = _json(dossier / "receipts/02-rail_observer.json")
    receipt["signature"] = borrowed["signature"]
    _write(dossier / relative, receipt)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_open_challenge_never_reaches_remedy_or_authorization() -> None:
    report = EXPECTED["fork-open"]
    assert report["same_size_equivocation"] == "ESTABLISHED"
    assert report["challenge"] == "OPEN"
    assert report["witness_remedy"] == "CHALLENGE_REQUIRED"
    assert report["settlement_authorization"] == "NOT_ISSUED"


def test_open_challenge_rejects_even_an_authentic_authorization(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-open")
    core = _json(dossier / "covenant-core.json")
    challenge = _json(dossier / "receipts/03-challenge_authority.json")
    covenant = core["covenant"]
    head_a = _json(dossier / "evidence/head-a.json")
    head_b = _json(dossier / "evidence/head-b.json")
    finding_ref = GENERATOR.canonical_hash({
        "predicate": GENERATOR.PREDICATE_PROFILE,
        "head_a": head_a["checkpoint_hash"],
        "head_b": head_b["checkpoint_hash"],
    })
    subject = {
        "covenant_id": core["covenant_id"],
        "covenant_hash": covenant["covenant_hash"],
        "finding_ref": finding_ref,
        "finding_class": "SAME_SIZE_LOG_EQUIVOCATION",
        "challenge_attestation": challenge["hashes"]["attestation"],
        "consequence": "TRANSFER_COLLATERAL",
        "amount": GENERATOR.MAX_REMEDY,
        "unit": GENERATOR.UNIT,
        "destination": covenant["destination"],
        "capital_binding_id": covenant["capital"]["binding_id"],
        "challenge_state": "OPEN",
        "rail_checkpoint": GENERATOR.CAPITAL_CHECKPOINT,
        "authority_grant": GENERATOR.AUTHORITY_GRANT,
    }
    receipt = GENERATOR._receipt("settlement_authority", subject, 4)
    relative = "receipts/04-settlement_authority.json"
    _write(dossier / relative, receipt)
    raw = (dossier / relative).read_bytes()
    core["receipt_manifest"].append({
        **GENERATOR._artifact(relative, raw),
        "role": "settlement_authority",
        "action_type": receipt["action"]["type"],
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    })
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_model_identity_cannot_use_objective_witness_remedy() -> None:
    report = EXPECTED["model-dispute"]
    assert report["claim_inclusion"] == "VERIFIED"
    assert report["same_size_equivocation"] == "NOT_ESTABLISHED"
    assert report["challenge"] == "CHALLENGE_REQUIRED"
    assert report["witness_remedy"] == "CHALLENGE_REQUIRED"
    assert report["settlement_authorization"] == "NOT_ISSUED"


def test_bond_does_not_upgrade_truth_or_witness_control() -> None:
    for scenario in ("consistent", "fork-open", "fork-closed", "fork-authorized", "model-dispute"):
        report = EXPECTED[scenario]
        assert report["capital_allocation"] == "ADEQUATE_DEDICATED"
        assert report["substantive_truth"] == "NOT_ESTABLISHED"
        assert report["occurrence"] == "NOT_ESTABLISHED"
        assert report["witness_control"] == "PROJECT_OPERATED"
        assert report["custody"] == "NOT_COMPUTED"
        assert report["collectibility"] == "NOT_COMPUTED"

    bonded = EXPECTED["fork-closed"]
    unbonded = EXPECTED["fork-closed-no-bond"]
    for field in (
        "dossier_integrity", "receipt_integrity", "signature_status",
        "covenant_binding", "checkpoint_authenticity", "joint_observation",
        "history_consistency", "same_size_equivocation", "witness_trust",
        "witness_control", "substantive_truth", "occurrence",
    ):
        assert bonded[field] == unbonded[field]
    assert bonded["capital_allocation"] == "ADEQUATE_DEDICATED"
    assert unbonded["capital_allocation"] == "INADEQUATE"
    assert bonded["witness_remedy"] == "ELIGIBLE"
    assert unbonded["witness_remedy"] == "INELIGIBLE"


@pytest.mark.parametrize(
    "change",
    (
        {"locked_amount": 10_000},
        {"unit": "SAT"},
        {"binding_id": "wrong-bond"},
        {
            "allocation_manifest": [
                {"covenant_hash": "sha256:" + "1" * 64, "amount": 12_500, "active": True},
                {"covenant_hash": "sha256:" + "2" * 64, "amount": 12_500, "active": True},
            ]
        },
    ),
)
def test_shortfall_wrong_unit_wrong_binding_and_double_pledge_fail_closed(
    tmp_path: Path, change: dict
) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    receipt = _json(dossier / "receipts/02-rail_observer.json")
    subject = {
        key: value
        for key, value in receipt["action"]["subject"].items()
        if key not in {"profile", "issuer_role"}
    }
    subject.update(change)
    _replace_role_receipt(dossier, "rail_observer", subject, 2)
    python_report = verify_witness_covenant(dossier, context.read_bytes())
    assert python_report.exit_code == 1
    assert python_report.witness_remedy == "NOT_COMPUTED"
    assert _node(dossier, context)["exit_code"] == 1


def test_allocation_active_rejects_integer_boolean_coercion(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    receipt = _json(dossier / "receipts/02-rail_observer.json")
    subject = {
        key: value
        for key, value in receipt["action"]["subject"].items()
        if key not in {"profile", "issuer_role"}
    }
    subject["allocation_manifest"][0]["active"] = 1
    _replace_role_receipt(dossier, "rail_observer", subject, 2)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_prototype_key_is_rejected_by_both_strict_parsers(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "consistent")
    core = _json(dossier / "covenant-core.json")
    core["__proto__"] = {"attacker": True}
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_receipt_scalar_limit_is_identical_in_python_and_node(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "receipts/01-witness_operator.json"
    receipt = _json(dossier / relative)
    receipt["producer"]["pad"] = "a" * 5_000
    _write(dossier / relative, receipt)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == node_report["witness_remedy"] == "NOT_COMPUTED"


def test_receipt_node_limit_counts_object_keys_in_both_kernels(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "receipts/01-witness_operator.json"
    receipt = _json(dossier / relative)
    receipt["producer"] = {f"k{index:04d}": 0 for index in range(5_000)}
    _write(dossier / relative, receipt)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == node_report["witness_remedy"] == "NOT_COMPUTED"


def test_every_dossier_member_obeys_the_per_member_limit(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "evidence/padded.json"
    raw = b'{"pad":null}' + b" " * 300_000
    (dossier / relative).write_bytes(raw)
    core["artifact_manifest"].append({
        "path": relative,
        "sha256": _sha(raw),
        "byte_length": len(raw),
        "media_type": "application/json",
    })
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_scenario_label_cannot_select_protected_result(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-open")
    core = _json(dossier / "covenant-core.json")
    core["scenario"] = "fork-authorized"
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == "NOT_COMPUTED"


def test_missing_settlement_report_cannot_preserve_attempt(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-authorized")
    core = _json(dossier / "covenant-core.json")
    relative = "settlement/test-ledger-report.json"
    (dossier / relative).unlink()
    core["artifact_manifest"] = [entry for entry in core["artifact_manifest"] if entry["path"] != relative]
    _write(dossier / "covenant-core.json", core)
    report = verify_witness_covenant(dossier, context.read_bytes())
    assert report.exit_code == 1
    assert report.test_ledger_attempt == "NOT_COMPUTED"


def test_symlink_directory_is_rejected(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "consistent")
    outside = tmp_path / "outside"
    outside.mkdir()
    (dossier / "packet-trust").symlink_to(outside, target_is_directory=True)
    report = verify_witness_covenant(dossier, context.read_bytes())
    assert report.exit_code == 1
    assert _node(dossier, context)["exit_code"] != 0


def test_symlink_dossier_root_is_rejected_by_both_checkers(tmp_path: Path) -> None:
    context = SPEC / "contexts" / "fork-closed.json"
    linked = tmp_path / "linked-dossier"
    linked.symlink_to(SPEC / "vectors" / "fork-closed", target_is_directory=True)
    assert verify_witness_covenant(linked, context.read_bytes()).exit_code == 1
    assert _node(linked, context)["exit_code"] != 0


def test_boolean_coercion_is_rejected() -> None:
    report = verify_witness_covenant(
        SPEC / "vectors" / "consistent",
        (SPEC / "contexts" / "consistent.json").read_bytes(),
    )
    with pytest.raises(TypeError):
        bool(report)


def test_generation_is_deterministic() -> None:
    subprocess.run([sys.executable, str(SPEC / "generate.py"), "--check"], check=True)


def test_formal_fixture_map_matches_committed_reports_and_theorems() -> None:
    mapping = _json(SPEC / "formal-fixture-map.json")
    formal = (Path(__file__).parents[1] / mapping["formal_source"]).read_text()
    for item in mapping["mappings"]:
        assert f"theorem {item['theorem']}" in formal
        if "fixture" not in item:
            assert item["hostile_test"] in globals()
            continue
        report = EXPECTED[item["fixture"]]
        for field, value in {**item["premises"], **item["conclusions"]}.items():
            assert report[field] == value


def test_remedy_eligibility_does_not_imply_authorization_or_attempt() -> None:
    eligible = EXPECTED["fork-closed"]
    authorized = EXPECTED["fork-authorized"]
    for field in (
        "same_size_equivocation", "challenge", "capital_allocation",
        "witness_remedy",
    ):
        assert eligible[field] == authorized[field]
    assert eligible["settlement_authorization"] == "NOT_ISSUED"
    assert eligible["test_ledger_attempt"] == "NOT_REPORTED"
    assert authorized["settlement_authorization"] == "VERIFIED"
    assert authorized["test_ledger_attempt"] == "REPORTED"


def test_revision_boolean_cannot_dispatch_profile(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    core["revision"] = True
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_manifest_byte_length_rejects_boolean_coercion(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    relative = "evidence/one-byte.json"
    raw = b"0"
    (dossier / relative).write_bytes(raw)
    core["artifact_manifest"].append({
        "path": relative,
        "sha256": _sha(raw),
        "byte_length": True,
        "media_type": "application/json",
    })
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_cross_covenant_replay_is_rejected(tmp_path: Path) -> None:
    dossier, context_path = _copy(tmp_path, "fork-closed")
    context = _json(context_path)
    context["accepted_covenant_hash"] = "sha256:" + "c" * 64
    _write(context_path, context)
    assert verify_witness_covenant(dossier, context_path.read_bytes()).exit_code == 1
    assert _node(dossier, context_path)["exit_code"] == 1


@pytest.mark.parametrize("observed", ("5", True, -1, 4))
def test_challenge_checkpoint_type_and_range_are_closed(
    tmp_path: Path, observed: object
) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    relative = "receipts/04-challenge_authority.json"
    current = _json(dossier / relative)
    subject = {key: value for key, value in current["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    subject["observed_checkpoint"] = observed
    replacement = GENERATOR._receipt("challenge_authority", subject, 4)
    _replace_receipt_path(dossier, relative, "challenge_authority", replacement)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


@pytest.mark.parametrize(
    ("action_extra", "claimed_at"),
    (
        ({"extra": "not-closed"}, None),
        (None, "x"),
        (None, "２０２６-０８-２１T１２:０１:００Z"),
    ),
)
def test_closed_action_and_timestamp_rules_match(
    tmp_path: Path, action_extra: dict | None, claimed_at: str | None
) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    relative = "receipts/01-witness_operator.json"
    current = _json(dossier / relative)
    subject = {key: value for key, value in current["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    replacement = _custom_receipt(
        "witness_operator", subject, 1,
        action_extra=action_extra, claimed_at=claimed_at,
    )
    _replace_receipt_path(dossier, relative, "witness_operator", replacement)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_unknown_semantic_class_is_rejected_not_collapsed_to_p3(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "model-dispute")
    relative = "receipts/04-challenge_authority.json"
    current = _json(dossier / relative)
    subject = {key: value for key, value in current["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    subject["finding_class"] = "SEMANTIC_P4"
    replacement = GENERATOR._receipt("challenge_authority", subject, 4)
    _replace_receipt_path(dossier, relative, "challenge_authority", replacement)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == node_report["witness_remedy"] == "NOT_COMPUTED"


def test_witness_operator_must_accept_its_own_covenant(tmp_path: Path) -> None:
    dossier, context_path = _copy(tmp_path, "fork-closed")
    relative = "receipts/01-witness_operator.json"
    current = _json(dossier / relative)
    subject = {key: value for key, value in current["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    replacement = _custom_receipt(
        "witness_operator", subject, 1, signer_role="alternate_witness_operator",
    )
    _replace_receipt_path(dossier, relative, "witness_operator", replacement)
    core = _json(dossier / "covenant-core.json")
    core["role_issuers"]["witness_operator"] = replacement["signature"]["issuer"]
    _write(dossier / "covenant-core.json", core)
    context = _json(context_path)
    context["accepted_issuers_by_role"]["witness_operator"] = [replacement["signature"]["issuer"]]
    _write(context_path, context)
    assert verify_witness_covenant(dossier, context_path.read_bytes()).exit_code == 1
    assert _node(dossier, context_path)["exit_code"] == 1


def test_model_inclusion_index_rejects_boolean_coercion(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "model-dispute")
    core = _json(dossier / "covenant-core.json")
    relative = "evidence/model-claim-inclusion.json"
    inclusion = _json(dossier / relative)
    inclusion["index"] = False
    _write(dossier / relative, inclusion)
    _refresh(core, dossier, relative)
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["claim_inclusion"] == node_report["claim_inclusion"] == "NOT_COMPUTED"


@pytest.mark.parametrize(
    "change",
    (
        {"amount": 1},
        {"consequence": "RETURN_COLLATERAL"},
        {"unit": "SAT"},
        {"destination": "test-ledger:attacker"},
        {"capital_binding_id": "wrong-bond"},
        {"challenge_attestation": "sha256:" + "b" * 64},
        {"challenge_state": "OPEN"},
        {"authority_grant": "sha256:" + "f" * 64},
        {"rail_checkpoint": "sha256:" + "e" * 64},
        {"covenant_hash": "sha256:" + "d" * 64},
    ),
)
def test_authorization_exact_binding_hostiles(tmp_path: Path, change: dict) -> None:
    dossier, context = _copy(tmp_path, "fork-authorized")
    relative = "receipts/05-settlement_authority.json"
    current = _json(dossier / relative)
    subject = {key: value for key, value in current["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    subject.update(change)
    replacement = GENERATOR._receipt("settlement_authority", subject, 5)
    _replace_receipt_path(dossier, relative, "settlement_authority", replacement)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_different_settlement_signer_cannot_be_laundered_by_context(tmp_path: Path) -> None:
    dossier, context_path = _copy(tmp_path, "fork-authorized")
    relative = "receipts/05-settlement_authority.json"
    current = _json(dossier / relative)
    subject = {
        key: value
        for key, value in current["action"]["subject"].items()
        if key not in {"profile", "issuer_role"}
    }
    replacement = _custom_receipt(
        "settlement_authority", subject, 5,
        signer_role="alternate_settlement_authority",
    )
    _replace_receipt_path(dossier, relative, "settlement_authority", replacement)
    core = _json(dossier / "covenant-core.json")
    core["role_issuers"]["settlement_authority"] = replacement["signature"]["issuer"]
    _write(dossier / "covenant-core.json", core)
    context = _json(context_path)
    context["accepted_issuers_by_role"]["settlement_authority"] = [
        replacement["signature"]["issuer"]
    ]
    _write(context_path, context)
    assert verify_witness_covenant(dossier, context_path.read_bytes()).exit_code == 1
    assert _node(dossier, context_path)["exit_code"] == 1


def test_contradictory_test_ledger_real_funds_language_is_rejected(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-authorized")
    report_path = dossier / "settlement/test-ledger-report.json"
    fixture = _json(report_path)
    fixture["actual_funds"] = True
    _write(report_path, fixture)
    observer_path = "receipts/06-settlement_observer.json"
    observer = _json(dossier / observer_path)
    subject = {key: value for key, value in observer["action"]["subject"].items() if key not in {"profile", "issuer_role"}}
    subject["fixture_report_hash"] = _sha(report_path.read_bytes())
    replacement = GENERATOR._receipt("settlement_observer", subject, 6)
    _replace_receipt_path(dossier, observer_path, "settlement_observer", replacement)
    core = _json(dossier / "covenant-core.json")
    _refresh(core, dossier, "settlement/test-ledger-report.json")
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] == 1


def test_test_ledger_boolean_fields_reject_integer_coercion(tmp_path: Path) -> None:
    dossier, context = _copy(tmp_path, "fork-authorized")
    report_path = dossier / "settlement/test-ledger-report.json"
    fixture = _json(report_path)
    fixture["synthetic"] = 1
    fixture["actual_funds"] = 0
    _write(report_path, fixture)
    observer_path = "receipts/06-settlement_observer.json"
    observer = _json(dossier / observer_path)
    subject = {
        key: value
        for key, value in observer["action"]["subject"].items()
        if key not in {"profile", "issuer_role"}
    }
    subject["fixture_report_hash"] = _sha(report_path.read_bytes())
    replacement = GENERATOR._receipt("settlement_observer", subject, 6)
    _replace_receipt_path(dossier, observer_path, "settlement_observer", replacement)
    core = _json(dossier / "covenant-core.json")
    _refresh(core, dossier, "settlement/test-ledger-report.json")
    _write(dossier / "covenant-core.json", core)
    python_report = verify_witness_covenant(dossier, context.read_bytes()).to_dict()
    node_report = _node(dossier, context)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["test_ledger_attempt"] == "NOT_COMPUTED"


@pytest.mark.parametrize(
    ("relative", "raw"),
    (
        ("evidence/duplicate.json", b'{"x":1,"x":2}'),
        ("evidence\\extra.json", b'{"x":1}'),
    ),
)
def test_every_declared_json_member_and_posix_path_is_strict(
    tmp_path: Path, relative: str, raw: bytes
) -> None:
    dossier, context = _copy(tmp_path, "fork-closed")
    core = _json(dossier / "covenant-core.json")
    (dossier / relative).write_bytes(raw)
    core["artifact_manifest"].append({
        "path": relative, "sha256": _sha(raw), "byte_length": len(raw),
        "media_type": "application/json",
    })
    _write(dossier / "covenant-core.json", core)
    assert verify_witness_covenant(dossier, context.read_bytes()).exit_code == 1
    assert _node(dossier, context)["exit_code"] != 0
