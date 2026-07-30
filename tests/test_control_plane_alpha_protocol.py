from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bulla.experimental.control_plane_alpha as control_plane_alpha
from bulla.action_receipt import ActionReceipt, verify_receipt
from bulla.experimental.checkpoint import (
    WitnessCheckpoint,
    issue_checkpoint,
    verify_checkpoint,
    verify_checkpoint_extension,
)
from bulla.experimental.control_plane_alpha import (
    APPROVED_TIMELINE,
    DEPLOYMENT_GATE,
    PROFILE,
    RUN_STATUSES,
    ControlPlaneLimits,
    ControlPlaneProtocolError,
    ControlPlaneVerificationContext,
    validate_authzen_decision,
    validate_authzen_request,
    validate_run_transition,
    verify_control_plane_fixture,
)
from bulla.experimental.incident_packet import (
    PROFILE as INCIDENT_PROFILE,
    IncidentVerificationContext,
    build_publish_receipt,
    parse_incident_packet,
    verify_incident_packet,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog, deed_leaf, verify_inclusion_record


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "control-plane-alpha"
VECTOR = SPEC / "vectors" / "full-loop"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _context_document() -> dict:
    return _load(SPEC / "contexts" / "alpha-context.json")


def _context() -> ControlPlaneVerificationContext:
    return ControlPlaneVerificationContext.from_dict(_context_document())


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(
        seed=hashlib.sha256(f"bulla-control-plane-alpha:{role}".encode()).digest()
    )


def _republish_changed_artifact(packet: Path, relative: str, value: dict) -> None:
    _republish_changed_artifact_bytes(packet, relative, _json_bytes(value))


def _republish_changed_artifact_bytes(
    packet: Path,
    relative: str,
    content: bytes,
) -> None:
    (packet / relative).write_bytes(content)
    core_path = packet / "packet-core.json"
    core = _load(core_path)
    artifact = next(item for item in core["artifacts"] if item["path"] == relative)
    artifact["byte_length"] = len(content)
    artifact["sha256"] = _sha256(content)
    old_publish = ActionReceipt.from_dict(_load(packet / "publish-receipt.json"))
    new_publish = build_publish_receipt(
        core,
        signer=_signer("publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    core_path.write_bytes(_json_bytes(core))
    (packet / "publish-receipt.json").write_bytes(_json_bytes(new_publish))


def test_full_loop_reproduces_expected_multidimensional_verdict() -> None:
    expected = _load(SPEC / "expected-verdict.json")["vectors"]["full-loop"][
        "result"
    ]
    result = verify_control_plane_fixture(VECTOR, _context())

    assert result.to_dict() == expected
    assert result.exit_code == 0
    assert result.run_status == "FINALIZED"
    assert result.incident_packet_integrity == "VERIFIED"
    assert result.witness_inclusion == "VERIFIED"
    assert result.witness_root_trust == "TRUSTED"
    assert result.retained_receipts == "VERIFIED"
    assert result.service_witness_inclusion == "VERIFIED"
    assert result.service_witness_consistency == "VERIFIED"
    assert (
        result.decision_coverage.covered,
        result.decision_coverage.total,
    ) == (2, 2)
    assert (
        result.effect_coverage.covered,
        result.effect_coverage.total,
        result.effect_coverage.uncovered_ids,
    ) == (1, 2, ("effect-bypass-001",))
    assert result.reliance == "NOT_COMPUTED"
    with pytest.raises(TypeError, match="ambiguous"):
        bool(result)
    with pytest.raises(TypeError, match="ambiguous"):
        bool(result.effect_coverage)


def test_generator_check_is_byte_identical() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SPEC / "generate_vectors.py"),
            "--check",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "byte-identical" in completed.stdout


def test_clean_index_collects_every_full_loop_member(tmp_path: Path) -> None:
    repository = ROOT.parent
    vector_relative = VECTOR.relative_to(repository).as_posix()
    index_path = tmp_path / "clean-index"
    environment = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
    for arguments in (
        ["read-tree", "--empty"],
        ["add", "--", vector_relative],
    ):
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    indexed = subprocess.run(
        ["git", "ls-files", "--", vector_relative],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert indexed.returncode == 0, indexed.stdout + indexed.stderr
    expected = {
        path.relative_to(repository).as_posix()
        for path in VECTOR.rglob("*")
        if path.is_file()
    }
    assert set(indexed.stdout.splitlines()) == expected
    assert {
        f"{vector_relative}/coverage/decisions.json",
        f"{vector_relative}/coverage/effects.json",
    } <= expected
    for relative, ignored in (
        (f"{vector_relative}/coverage/decisions.json", False),
        (f"{vector_relative}/coverage/effects.json", False),
        (f"{vector_relative}/coverage/not-normative.json", True),
        (f"{vector_relative}/coverage/deeper/not-normative.json", True),
    ):
        completed = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", relative],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == (0 if ignored else 1), relative


def test_profile_separates_hosted_packet_from_deterministic_outer_fixture() -> None:
    profile = (SPEC / "PROFILE.md").read_text(encoding="utf-8")
    assert (
        "A hosted export does not claim deterministic outer-archive closure"
        in profile
    )
    assert "is not an input to `verify_control_plane_fixture`" in profile
    assert (
        "The standalone Python and Node incident-packet checkers verify that packet."
        in profile
    )
    assert (
        "secret-equivalent, one-time\ncreation capability" in profile
    )


def test_packet_uses_only_approved_v04_actions_roles_and_profiles() -> None:
    packet = parse_incident_packet(VECTOR)
    assert tuple(item["action_type"] for item in packet.core["timeline"]) == (
        APPROVED_TIMELINE
    )
    documents = [
        ActionReceipt.from_dict(_load(VECTOR / item["receipt_path"]))
        for item in packet.core["timeline"]
    ]
    for receipt in documents:
        assert receipt.schema_version == "0.4"
        assert receipt.action["subject"]["profile"] == INCIDENT_PROFILE
        assert verify_receipt(receipt.to_dict()).verified_to == "attestation"
    assert documents[3].action["type"] == "capability.observe"
    assert documents[3].action["subject"]["issuer_role"] == "boundary"
    assert documents[4].action["subject"]["issuer_role"] == "boundary"
    assert documents[5].action["subject"]["issuer_role"] == "target"
    assert documents[6].action["subject"]["claim"] == (
        "EFFECT_RECEIPT_GAP_CAUSE_UNRESOLVED"
    )
    assert documents[6].action["subject"]["issuer_role"] == "incident_commander"
    assert documents[6].action["subject"]["topic"] == "effect-coverage"
    assert documents[6].action["subject"]["epistemic_status"] == "observed"
    assert documents[6].action["subject"]["evidence_refs"] == [
        next(
            item["sha256"]
            for item in packet.core["artifacts"]
            if item["artifact_id"] == "coverage-effects"
        )
    ]
    assert documents[6].evidence_refs[0]["name"] == (
        "recomputation:effect-coverage-report"
    )
    assert documents[6].evidence_refs[0]["grounding"] == "execution_verified"
    publish = ActionReceipt.from_dict(_load(VECTOR / "publish-receipt.json"))
    assert publish.action["type"] == "incident.packet.publish"
    assert publish.action["subject"]["profile"] == INCIDENT_PROFILE
    assert publish.action["subject"]["issuer_role"] == "publisher"
    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (VECTOR / "receipts").glob("*.json")
    )
    assert "control.authzen" not in corpus
    assert "control.effect.observe" not in corpus
    assert '"decision": "DENY"' not in corpus


def test_authzen_mapping_is_closed_and_uses_exact_sandbox_scenarios() -> None:
    permit_request = _load(VECTOR / "authzen/requests/01-permit.json")
    refusal_request = _load(VECTOR / "authzen/requests/03-refuse.json")
    permit_decision = _load(VECTOR / "authzen/decisions/02-permit.json")
    refusal_decision = _load(VECTOR / "authzen/decisions/04-refuse.json")

    validate_authzen_request(permit_request)
    validate_authzen_request(refusal_request)
    validate_authzen_decision(permit_decision)
    validate_authzen_decision(refusal_decision)
    assert permit_request["subject"] == {
        "type": "principal",
        "id": "agent:synthetic-alpha",
    }
    assert permit_request["resource"] == {
        "type": "mcp-tool",
        "id": "mcp://synthetic-receiver/tools/sandbox.append",
    }
    mandate = _load(VECTOR / "receipts/00-mandate.json")
    assert mandate["action"]["subject"]["authorized_targets"] == [
        permit_request["resource"]["id"],
    ]
    assert refusal_request["resource"]["id"] in mandate["action"]["subject"][
        "authorized_targets"
    ]
    assert permit_request["context"]["tool_name"] == "sandbox.append"
    assert permit_request["context"]["arguments_hash"] != refusal_request[
        "context"
    ]["arguments_hash"]
    assert permit_decision["decision"] is True
    assert refusal_decision["decision"] is False

    unknown = copy.deepcopy(permit_request)
    unknown["extension"] = {}
    with pytest.raises(ControlPlaneProtocolError, match="unknown fields"):
        validate_authzen_request(unknown)
    wrong_principal = copy.deepcopy(permit_request)
    wrong_principal["subject"]["id"] = _context_document()[
        "accepted_issuers_by_role"
    ]["evaluation_authority"][0]
    with pytest.raises(ControlPlaneProtocolError, match="constants"):
        validate_authzen_request(wrong_principal)
    wrong_input = copy.deepcopy(permit_request)
    wrong_input["context"]["arguments_hash"] = "sha256:" + "00" * 32
    with pytest.raises(ControlPlaneProtocolError, match="arguments"):
        validate_authzen_request(wrong_input)
    wrong_reason = copy.deepcopy(refusal_decision)
    wrong_reason["context"]["reason_code"] = "FIXED_OPERATION_ALLOWED"
    with pytest.raises(ControlPlaneProtocolError, match="reason_code"):
        validate_authzen_decision(wrong_reason)


def test_authzen_transaction_slots_reject_swaps_and_a_second_permit() -> None:
    permit_request = _load(VECTOR / "authzen/requests/01-permit.json")
    refusal_request = _load(VECTOR / "authzen/requests/03-refuse.json")
    permit_decision = _load(VECTOR / "authzen/decisions/02-permit.json")
    refusal_decision = _load(VECTOR / "authzen/decisions/04-refuse.json")

    control_plane_alpha._validate_authzen_slot(
        permit_request, permit_decision, 0
    )
    control_plane_alpha._validate_authzen_slot(
        refusal_request, refusal_decision, 1
    )

    with pytest.raises(ControlPlaneProtocolError, match="frozen mediated_append"):
        control_plane_alpha._validate_authzen_slot(
            refusal_request, permit_decision, 0
        )
    with pytest.raises(ControlPlaneProtocolError, match="frozen refused_append"):
        control_plane_alpha._validate_authzen_slot(
            permit_request, refusal_decision, 1
        )

    second_permit = copy.deepcopy(refusal_decision)
    second_permit["decision"] = True
    second_permit["context"]["reason_code"] = "FIXED_OPERATION_ALLOWED"
    validate_authzen_decision(second_permit)
    with pytest.raises(ControlPlaneProtocolError, match="frozen refused_append"):
        control_plane_alpha._validate_authzen_slot(
            refusal_request, second_permit, 1
        )


def test_run_state_materializes_exact_progression_and_two_transactions() -> None:
    run = _load(VECTOR / "run-state.json")
    assert run["status"] == "FINALIZED"
    assert [item["state"] for item in run["transitions"]] == list(RUN_STATUSES)
    assert [item["sequence"] for item in run["transitions"]] == [1, 2, 3, 4, 5]
    assert len(run["transactions"]) == 2
    for previous, current in zip(RUN_STATUSES, RUN_STATUSES[1:]):
        validate_run_transition(previous, current)
    with pytest.raises(ControlPlaneProtocolError, match="invalid run transition"):
        validate_run_transition("CREATED", "FINALIZED")
    assert ControlPlaneLimits().max_transactions == 2
    assert ControlPlaneLimits().max_files == 32
    assert ControlPlaneLimits().max_file_bytes == 65_536
    assert ControlPlaneLimits().max_total_bytes == 2_097_152


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effect_receipt_paths", []),
        (
            "effect_receipt_paths",
            ["receipts/02-decision-refuse.json"],
        ),
        (
            "decision_checkpoint_path",
            "receipts/not-a-packet-member.json",
        ),
        (
            "effect_checkpoint_path",
            "receipts/04-denominator-decision-checkpoint.json",
        ),
        (
            "handoff_receipt_path",
            "receipts/06-effect-coverage-gap-unresolved.json",
        ),
    ],
)
def test_run_state_receipt_paths_must_resolve_to_exact_packet_receipts(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    run = _load(packet / "run-state.json")
    run[field] = value
    _republish_changed_artifact(packet, "run-state.json", run)

    assert verify_incident_packet(
        packet, _context().incident_context()
    ).exit_code == 0
    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "RUN_STATE_INVALID"
    assert "exact accepted packet receipts" in result.errors[0]["detail"]


@pytest.mark.parametrize("transition_index", range(len(RUN_STATUSES)))
def test_run_state_transition_rejects_arbitrary_well_formed_evidence(
    tmp_path: Path,
    transition_index: int,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    run = _load(packet / "run-state.json")
    run["transitions"][transition_index]["evidence_ref"] = (
        "sha256:" + f"{transition_index + 1:02x}" * 32
    )
    _republish_changed_artifact(packet, "run-state.json", run)

    assert verify_incident_packet(
        packet, _context().incident_context()
    ).exit_code == 0
    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "RUN_STATE_INVALID"
    assert "exact generated evidence" in result.errors[0]["detail"]


def test_retained_receipt_archive_rejects_noncanonical_base64_pad_bits(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    manifest = _load(packet / "service-manifest.json")
    archive_path = manifest["service_witness"]["retained_archive_path"]
    archive = _load(packet / archive_path)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    changed = False
    for record in archive["records"]:
        encoded = record["bytes_base64"]
        if encoded.endswith("=="):
            index = alphabet.index(encoded[-3])
            assert index & 0x0F == 0
            record["bytes_base64"] = (
                encoded[:-3] + alphabet[index + 1] + encoded[-2:]
            )
        elif encoded.endswith("="):
            index = alphabet.index(encoded[-2])
            assert index & 0x03 == 0
            record["bytes_base64"] = (
                encoded[:-2] + alphabet[index + 1] + encoded[-1:]
            )
        else:
            continue
        assert base64.b64decode(
            record["bytes_base64"], validate=True
        ) == base64.b64decode(encoded, validate=True)
        changed = True
        break
    assert changed
    _republish_changed_artifact(packet, archive_path, archive)
    archive_bytes = (packet / archive_path).read_bytes()
    manifest_artifact = next(
        item
        for item in manifest["service_witness"]["artifacts"]
        if item["path"] == archive_path
    )
    manifest_artifact["sha256"] = _sha256(archive_bytes)
    _republish_changed_artifact(
        packet, "service-manifest.json", manifest
    )

    assert verify_incident_packet(
        packet, _context().incident_context()
    ).exit_code == 0
    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "INCIDENT_PACKET_FAILED"
    assert "noncanonical base64" in result.errors[0]["detail"]


def test_standards_pin_binds_authzen_and_mcp_finals_separately() -> None:
    pin = _load(VECTOR / "standards-pin.json")
    assert pin["authzen"] == {
        "standard": "OpenID AuthZEN Authorization API",
        "version": "1.0",
        "status": "FINAL",
        "published": "2026-01-11",
        "official_url": "https://openid.net/specs/authorization-api-1_0.html",
        "document_sha256": (
            "sha256:f0ee89cc4a688f9f409dc323c8342579d74c45dc776a6b04a622ba9738de82bc"
        ),
        "context_note": (
            "The decision response context is permitted by Authorization API "
            "1.0; keys inside it are specific to the Bulla profile."
        ),
    }
    assert pin["source_status"] == "FINAL"
    assert pin["target_protocol_revision"] == "2026-07-28"
    assert pin["snapshot_commit"] == "271ecc9accafdd9b83a3c869fa67c22953b2af80"
    assert pin["snapshot_sha256"] == (
        "sha256:ef70b61f99b6d2e5e3b46863822eab08dff6a45bedc7a08914e0e5b133f40203"
    )
    assert pin["final_document_digest"] == pin["snapshot_sha256"]
    assert pin["deployment_gate"] == DEPLOYMENT_GATE
    assert "schema/draft/schema.json" in pin["official_draft_url"]
    assert pin["rc_comparison"] == {
        "snapshot_commit": "71e306956a4959c9655e5036be215d41986596e6",
        "snapshot_sha256": (
            "sha256:9281c4890630e2d1e61792fa23b4084c4ea360cd58519610cd050545ab7b8708"
        ),
        "added_definitions": [
            "SubscriptionsListenResultMetaObject",
            "SubscriptionsListenResultResponse",
        ],
        "removed_definitions": ["SubscriptionsListenResultMeta"],
        "changed_definitions": ["SubscriptionsListenResult"],
        "implemented_subset_impact": "NO_CHANGE_SUBSCRIPTIONS_LISTEN_RESULT_ONLY",
    }


def test_outer_and_incident_contexts_have_exact_parity_and_six_keys() -> None:
    outer = _context_document()
    incident = _load(SPEC / "contexts" / "incident-context.json")
    assert incident == {
        key: outer[key]
        for key in (
            "accepted_issuers_by_role",
            "team_controlled_roles",
            "trusted_witness_roots",
        )
    }
    assert set(outer["accepted_issuers_by_role"]) == {
        "evaluation_authority",
        "boundary",
        "target",
        "witness",
        "incident_commander",
        "publisher",
    }
    issuers = [
        values[0] for values in outer["accepted_issuers_by_role"].values()
    ]
    assert len(set(issuers)) == 6
    assert set(outer["role_identities"]) == set(
        outer["accepted_issuers_by_role"]
    )
    assert IncidentVerificationContext.from_dict(incident)
    assert verify_incident_packet(
        VECTOR, IncidentVerificationContext.from_dict(incident)
    ).exit_code == 0


def test_rich_trust_context_requires_one_exact_issuer_per_role() -> None:
    schema = _load(SPEC / "control-plane-alpha.schema.json")
    assert schema["$defs"]["issuerList"]["minItems"] == 1
    assert schema["$defs"]["issuerList"]["maxItems"] == 1
    context = _context_document()
    attacker = LocalEd25519Signer(
        seed=hashlib.sha256(b"second-boundary-issuer").digest()
    )
    context["accepted_issuers_by_role"]["boundary"].append(attacker.issuer)
    with pytest.raises(ValueError, match="exactly one issuer per role"):
        ControlPlaneVerificationContext.from_dict(context)


def test_trust_substitution_does_not_bootstrap_from_packet() -> None:
    substituted = _context_document()
    attacker = LocalEd25519Signer(seed=hashlib.sha256(b"attacker-boundary").digest())
    substituted["accepted_issuers_by_role"]["boundary"] = [attacker.issuer]
    substituted["role_identities"]["boundary"] = {
        "issuer": attacker.issuer,
        "verification_method": attacker.verification_method,
        "public_key_sha256": _sha256(attacker.public_key),
    }
    result = verify_control_plane_fixture(
        VECTOR, ControlPlaneVerificationContext.from_dict(substituted)
    )
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "INCIDENT_PACKET_FAILED"
    assert "not accepted" in result.errors[0]["detail"]


def test_public_key_fingerprint_substitution_is_rejected_at_context_boundary() -> None:
    context = _context_document()
    context["role_identities"]["witness"]["public_key_sha256"] = (
        "sha256:" + "00" * 32
    )
    with pytest.raises(ValueError, match="does not match did:key"):
        ControlPlaneVerificationContext.from_dict(context)


def test_untrusted_policy_fails_independently_of_signature_integrity() -> None:
    context = _context_document()
    context["trusted_policy_hashes"] = ["sha256:" + "00" * 32]
    result = verify_control_plane_fixture(
        VECTOR, ControlPlaneVerificationContext.from_dict(context)
    )
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "UNTRUSTED_POLICY"


def test_request_mapping_tamper_fails_after_packet_is_validly_republished(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    request = _load(packet / "authzen/requests/01-permit.json")
    request["context"]["arguments_hash"] = "sha256:" + "00" * 32
    _republish_changed_artifact(
        packet, "authzen/requests/01-permit.json", request
    )

    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "MAPPING_MISMATCH"


@pytest.mark.parametrize("malicious_first", [True, False])
def test_duplicate_authzen_members_fail_before_mapping_semantics(
    tmp_path: Path,
    malicious_first: bool,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    relative = "authzen/requests/01-permit.json"
    original = (packet / relative).read_bytes()
    legitimate = b'    "audience": "synthetic-receiver",'
    malicious = b'    "audience": "attacker-first-wins",'
    duplicate = (
        malicious + b"\n" + legitimate
        if malicious_first
        else legitimate + b"\n" + malicious
    )
    assert original.count(legitimate) == 1
    _republish_changed_artifact_bytes(
        packet,
        relative,
        original.replace(legitimate, duplicate),
    )

    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 2
    assert result.errors[0]["code"] == "MALFORMED_FIXTURE"
    assert "duplicate JSON member 'audience'" in result.errors[0]["detail"]


def test_mcp_final_pin_tamper_is_rejected(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    pin = _load(packet / "standards-pin.json")
    pin["final_document_digest"] = "sha256:" + "11" * 32
    _republish_changed_artifact(packet, "standards-pin.json", pin)

    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "STANDARDS_GATE_INVALID"


def test_service_witness_retains_bytes_and_proves_inclusion_and_consistency() -> None:
    manifest = _load(VECTOR / "service-manifest.json")["service_witness"]
    retained = _load(VECTOR / manifest["retained_index_path"])
    archive = _load(VECTOR / manifest["retained_archive_path"])
    assert archive["encoding"] == "base64"
    assert len(retained["records"]) == len(archive["records"]) == 8
    for sequence, (record, archived) in enumerate(
        zip(retained["records"], archive["records"], strict=True), start=1
    ):
        exact_bytes = base64.b64decode(
            archived["bytes_base64"], validate=True
        )
        assert archived["source_path"] == record["source_path"]
        assert record["archive_sequence"] == sequence
        assert (VECTOR / record["source_path"]).read_bytes() == exact_bytes
        assert record["byte_length"] == len(exact_bytes)
        assert record["sha256"] == _sha256(exact_bytes)
    prefix = WitnessCheckpoint.from_dict(
        _load(VECTOR / manifest["prefix_checkpoint_path"])
    )
    final = WitnessCheckpoint.from_dict(
        _load(VECTOR / manifest["final_checkpoint_path"])
    )
    consistency = _load(VECTOR / manifest["consistency_path"])
    inclusion = _load(VECTOR / manifest["inclusion_path"])
    deeds = _load(VECTOR / manifest["deeds_path"])["records"]
    assert verify_checkpoint(prefix).ok
    assert verify_checkpoint(final).ok
    assert verify_checkpoint_extension(prefix, final, consistency).ok
    assert verify_inclusion_record(
        inclusion,
        trusted_root=final.root,
        expected_leaf=deed_leaf(deeds[1]),
    )
    assert prefix.tree_size == 3
    assert final.tree_size == 8


def test_service_witness_rejects_signed_consistent_roots_for_fabricated_leaves(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(VECTOR, packet)
    service_manifest = _load(packet / "service-manifest.json")
    service = service_manifest["service_witness"]
    records = _load(packet / service["deeds_path"])["records"]

    fake_log = DeedLog(tmp_path / "fabricated-deeds.jsonl")
    fabricated: list[Deed] = []
    for index, record in enumerate(records):
        fabricated.append(
            Deed(**record)
            if index == 1
            else Deed(
                issuer=f"did:key:fabricated-{index}",
                content_hash=_sha256(f"fabricated-content-{index}".encode()),
                attestation_hash=_sha256(
                    f"fabricated-attestation-{index}".encode()
                ),
            )
        )
    for deed in fabricated[:3]:
        fake_log.append(deed)
    prefix = issue_checkpoint(
        fake_log,
        _signer("witness"),
        log_id=service["log_id"],
        issued_at="2026-07-26T12:07:00Z",
    )
    for deed in fabricated[3:]:
        fake_log.append(deed)
    final = issue_checkpoint(
        fake_log,
        _signer("witness"),
        log_id=service["log_id"],
        previous=prefix,
        issued_at="2026-07-26T12:08:00Z",
    )
    replacements = {
        service["prefix_checkpoint_path"]: prefix.to_dict(),
        service["final_checkpoint_path"]: final.to_dict(),
        service["consistency_path"]: fake_log.consistency(prefix.tree_size),
        service["inclusion_path"]: fake_log.inclusion_by_attestation(
            records[1]["attestation_hash"]
        ),
    }
    for relative, value in replacements.items():
        assert value is not None
        _republish_changed_artifact(packet, relative, value)

    for artifact in service["artifacts"]:
        if artifact["path"] in replacements:
            artifact["sha256"] = _sha256(
                (packet / artifact["path"]).read_bytes()
            )
    _republish_changed_artifact(
        packet, "service-manifest.json", service_manifest
    )
    run = _load(packet / "run-state.json")
    run["transitions"][-1]["evidence_ref"] = final.checkpoint_hash
    _republish_changed_artifact(packet, "run-state.json", run)

    assert verify_checkpoint(prefix).ok
    assert verify_checkpoint(final).ok
    assert verify_checkpoint_extension(
        prefix,
        final,
        replacements[service["consistency_path"]],
    ).ok
    assert verify_inclusion_record(
        replacements[service["inclusion_path"]],
        trusted_root=final.root,
        expected_leaf=deed_leaf(records[1]),
    )
    assert verify_incident_packet(
        packet, _context().incident_context()
    ).exit_code == 0

    result = verify_control_plane_fixture(packet, _context())
    assert result.exit_code == 1
    assert result.errors[0]["code"] == "INCIDENT_PACKET_FAILED"
    assert "exact ordered deeds" in result.errors[0]["detail"]
    assert result.retained_receipts == "FAILED"
    assert result.service_witness_inclusion == "FAILED"
    assert result.service_witness_consistency == "FAILED"
    assert result.decision_coverage.to_dict() == {
        "covered": 0,
        "total": 0,
        "covered_ids": [],
        "uncovered_ids": [],
    }


def test_file_count_limit_fails_before_semantic_verification() -> None:
    file_count = len([path for path in VECTOR.rglob("*") if path.is_file()])
    assert file_count <= ControlPlaneLimits().max_files
    result = verify_control_plane_fixture(
        VECTOR, _context(), limits=ControlPlaneLimits(max_files=file_count - 1)
    )
    assert result.exit_code == 2
    assert result.errors[0]["code"] == "MALFORMED_FIXTURE"


@pytest.mark.parametrize(
    "command",
    [
        [
            sys.executable,
            "-I",
            "spec/agent-incident-packet/verify_packet.py",
            "spec/control-plane-alpha/vectors/full-loop",
            "--context",
            "spec/control-plane-alpha/contexts/incident-context.json",
        ],
        [
            "node",
            "spec/agent-incident-packet/verify_packet.mjs",
            "spec/control-plane-alpha/vectors/full-loop",
            "--context",
            "spec/control-plane-alpha/contexts/incident-context.json",
        ],
    ],
)
def test_incident_packet_passes_each_standalone_checker(
    command: list[str],
) -> None:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
