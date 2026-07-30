from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import bulla.experimental.incident_packet as incident_packet
from bulla.action_receipt import ActionReceipt, sign_action_receipt_v04
from bulla.experimental.incident_packet import (
    IncidentPacketError,
    IncidentVerificationContext,
    _is_symlink_or_reparse_point,
    _parse_instant,
    _validate_profile_receipt,
    build_publish_receipt,
    canonical_hash,
    parse_incident_packet,
    verify_incident_packet,
)
from bulla.identity import LocalEd25519Signer


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "spec" / "agent-incident-packet"


def _context(protocol: str) -> IncidentVerificationContext:
    value = json.loads(
        (PROFILE / "contexts" / f"{protocol}-context.json").read_text(encoding="utf-8")
    )
    return IncidentVerificationContext.from_dict(value)


def _fixture_signer(protocol: str, role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(
        seed=hashlib.sha256(
            f"glyph-incident-packet:{protocol}:{role}".encode("utf-8")
        ).digest()
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _republish_fixture(packet: Path, core: dict) -> None:
    publish_path = packet / "publish-receipt.json"
    old_publish = ActionReceipt.from_dict(
        json.loads(publish_path.read_text(encoding="utf-8"))
    )
    new_publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    (packet / "packet-core.json").write_bytes(_json_bytes(core))
    publish_path.write_bytes(_json_bytes(new_publish))


def _replace_effect_checkpoint(
    packet: Path,
    *,
    subject_changes: dict[str, str] | None = None,
    signer_role: str = "target",
) -> None:
    checkpoint_path = (
        packet / "receipts" / "06-denominator-effect-checkpoint.json"
    )
    old = ActionReceipt.from_dict(
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
    )
    changed = sign_action_receipt_v04(
        dataclasses.replace(
            old,
            action={
                "type": "incident.statement",
                "subject": {
                    **old.action["subject"],
                    **(subject_changes or {}),
                },
            },
            signature=None,
            occurrence=None,
            authorization=None,
        ),
        _fixture_signer("http", signer_role),
    ).to_dict()
    changed_bytes = _json_bytes(changed)
    checkpoint_path.write_bytes(changed_bytes)

    core = json.loads(
        (packet / "packet-core.json").read_text(encoding="utf-8")
    )
    timeline = next(
        item for item in core["timeline"]
        if item["receipt_path"]
        == "receipts/06-denominator-effect-checkpoint.json"
    )
    old_attestation = timeline["attestation_hash"]
    new_attestation = changed["hashes"]["attestation"]
    timeline["byte_length"] = len(changed_bytes)
    timeline["sha256"] = _sha256(changed_bytes)
    timeline["attestation_hash"] = new_attestation
    next(
        item for item in core["denominators"] if item["phase"] == "effect"
    )["checkpoint_attestation"] = new_attestation
    next(
        item for item in core["statements"]
        if item["attestation_hash"] == old_attestation
    )["attestation_hash"] = new_attestation
    _republish_fixture(packet, core)


@pytest.mark.parametrize("protocol", ["http", "mcp"])
def test_clean_vectors_reproduce_the_checked_multidimensional_result(protocol: str) -> None:
    vector = PROFILE / "vectors" / f"{protocol}-clean"
    expected = json.loads(
        (PROFILE / "expected-verdict.json").read_text(encoding="utf-8")
    )["vectors"][f"{protocol}-clean"]["result"]
    result = verify_incident_packet(vector, _context(protocol))

    assert result.to_dict() == expected
    assert result.exit_code == 0
    assert result.packet_integrity == "VERIFIED"
    assert result.trace_integrity == "VERIFIED"
    assert result.disclosure_safety == "NOT_COMPUTED"
    assert result.reliance == "NOT_COMPUTED"
    assert result.witness_inclusion == "VERIFIED"
    assert result.witness_root_trust == "TRUSTED"
    assert result.unavailable_artifacts == ("trace-source-controlled",)

    decision, effect = result.coverage
    assert (decision.receipted, decision.total, decision.uncovered_ids) == (2, 2, ())
    assert (effect.receipted, effect.total) == (1, 2)
    assert effect.uncovered_ids == (f"{protocol}-effect-bypass-001",)


def test_post_window_observation_and_handoff_remain_evidentiary() -> None:
    vector = PROFILE / "vectors" / "http-clean"
    mandate = json.loads(
        (vector / "receipts" / "00-mandate.json").read_text(encoding="utf-8")
    )
    observation = json.loads(
        (vector / "receipts" / "03-observation-mediated.json").read_text(
            encoding="utf-8"
        )
    )
    handoff = json.loads(
        (vector / "receipts" / "10-handoff.json").read_text(encoding="utf-8")
    )

    assert observation["claimed_at"] > mandate["action"]["subject"]["valid_until"]
    assert handoff["claimed_at"] > mandate["action"]["subject"]["valid_until"]
    assert verify_incident_packet(vector, _context("http")).exit_code == 0


@pytest.mark.parametrize("kind", ["executable", "semantic"])
def test_profile_receipts_reject_nonempty_conventions(
    tmp_path: Path,
    kind: str,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    publish_path = packet / "publish-receipt.json"
    original = ActionReceipt.from_dict(
        json.loads(publish_path.read_text(encoding="utf-8"))
    )
    if kind == "executable":
        definition: object = {
            "form": "jsonschema+quantum/1",
            "schema": {},
        }
        convention = {
            "name": "incident-profile-executable",
            "scope": "incident.packet.publish",
            "kind": "executable",
            "definition": definition,
            "definition_hash": canonical_hash(definition),
        }
    else:
        definition = "A semantic convention outside the incident profile."
        convention = {
            "name": "incident-profile-semantic",
            "scope": "incident.packet.publish",
            "kind": "semantic",
            "definition": definition,
            "definition_hash": _sha256(definition.encode("utf-8")),
            "forum": {
                "log_endpoint": "https://example.invalid/convention-log",
                "trusted_root_ref": "sha256:" + "44" * 32,
            },
        }
    changed = sign_action_receipt_v04(
        dataclasses.replace(
            original,
            conventions=(convention,),
            signature=None,
            occurrence=None,
            authorization=None,
        ),
        _fixture_signer("http", "publisher"),
    )
    publish_path.write_bytes(_json_bytes(changed.to_dict()))

    result = verify_incident_packet(packet, _context("http"))

    assert result.exit_code == 1
    assert result.receipt_integrity == "FAILED"
    assert any(
        "glyph.agent-incident-packet/0.1-draft requires conventions to be empty"
        in error
        for error in result.errors
    )


def test_verification_objects_reject_boolean_coercion() -> None:
    result = verify_incident_packet(
        PROFILE / "vectors" / "http-clean", _context("http")
    )
    with pytest.raises(TypeError, match="ambiguous"):
        bool(result)
    with pytest.raises(TypeError, match="ambiguous"):
        bool(result.coverage[0])


def test_packet_declared_roles_cannot_bootstrap_trust() -> None:
    result = verify_incident_packet(
        PROFILE / "vectors" / "http-clean",
        IncidentVerificationContext(accepted_issuers_by_role={}),
    )
    assert result.issuer_authenticity == "FAILED"
    assert result.exit_code == 1
    assert any("not accepted" in reason for reason in result.errors)


def test_counterparty_confirmation_requires_affected_party_signer() -> None:
    vector = PROFILE / "vectors" / "http-clean"
    source = json.loads(
        (vector / "receipts" / "09-statement-redaction-review.json").read_text(
            encoding="utf-8"
        )
    )
    receipt = ActionReceipt.from_dict(source)
    action = {
        "type": "incident.statement",
        "subject": {
            **receipt.action["subject"],
            "epistemic_status": "counterparty_confirmed",
        },
    }
    unsigned = dataclasses.replace(
        receipt,
        action=action,
        signature=None,
        occurrence=None,
        authorization=None,
    )
    reviewer_signer = _fixture_signer("http", "reviewer")
    forged = sign_action_receipt_v04(unsigned, reviewer_signer).to_dict()
    core = json.loads((vector / "packet-core.json").read_text(encoding="utf-8"))
    declared_roles = {
        item["role"]: item["issuer"] for item in core["roles"]
    }

    document, errors = _validate_profile_receipt(
        (json.dumps(forged, sort_keys=True) + "\n").encode("utf-8"),
        context=_context("http"),
        label="unauthorized-counterparty-confirmation",
        expected_action="incident.statement",
        declared_roles=declared_roles,
    )

    assert document is not None
    assert any(
        "counterparty_confirmed requires issuer_role 'affected_party'" in error
        for error in errors
    )


def test_packet_correction_cannot_target_statement_attestations(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)

    correction_path = packet / "receipts" / "11-correction.json"
    correction = ActionReceipt.from_dict(
        json.loads(correction_path.read_text(encoding="utf-8"))
    )
    changed_subject = {
        **correction.action["subject"],
        "supersedes_kind": "packet",
    }
    changed_correction = sign_action_receipt_v04(
        dataclasses.replace(
            correction,
            action={"type": "incident.correct", "subject": changed_subject},
            signature=None,
            occurrence=None,
            authorization=None,
        ),
        _fixture_signer("http", "correction_authority"),
    ).to_dict()
    correction_bytes = _json_bytes(changed_correction)
    correction_path.write_bytes(correction_bytes)

    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    correction_timeline = core["timeline"][-1]
    old_attestation = correction_timeline["attestation_hash"]
    new_attestation = changed_correction["hashes"]["attestation"]
    correction_timeline["byte_length"] = len(correction_bytes)
    correction_timeline["sha256"] = _sha256(correction_bytes)
    correction_timeline["attestation_hash"] = new_attestation
    core["corrections"][0]["attestation_hash"] = new_attestation

    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["included_attestation_hashes"] = [
        new_attestation if value == old_attestation else value
        for value in witness["included_attestation_hashes"]
    ]
    checkpoint = {
        "witness_id": witness["witness_id"],
        "root_ref": witness["root_ref"],
        "received_at": witness["received_at"],
        "witnessed_at": witness["witnessed_at"],
        "included_attestation_hashes": witness["included_attestation_hashes"],
    }
    witness["checkpoint_hash"] = canonical_hash(checkpoint)
    witness["proof"] = _fixture_signer("http", "witness").sign_domain(
        "witness-checkpoint",
        witness["checkpoint_hash"],
        schema="0.4",
    )
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)
    witness_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == "witness-checkpoint"
    )
    witness_artifact["byte_length"] = len(witness_bytes)
    witness_artifact["sha256"] = _sha256(witness_bytes)

    publish_path = packet / "publish-receipt.json"
    old_publish = ActionReceipt.from_dict(
        json.loads(publish_path.read_text(encoding="utf-8"))
    )
    new_publish = build_publish_receipt(
        core,
        signer=_fixture_signer("http", "publisher"),
        envelope=old_publish.envelope,
        event_id=old_publish.event_id,
        claimed_at=old_publish.claimed_at,
        producer=old_publish.producer,
    )
    core_path.write_bytes(_json_bytes(core))
    publish_path.write_bytes(_json_bytes(new_publish))

    result = verify_incident_packet(packet, _context("http"))

    assert result.correction_status == "FAILED"
    assert result.exit_code == 1
    assert "reliance" in result.suppressed_conclusions
    assert "correction resolution" in result.suppressed_conclusions
    assert any(
        "packet supersedes_ref must resolve" in error
        for error in result.errors
    )


def test_publisher_cannot_self_shorten_authenticated_denominator(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    denominator_path = packet / "indexes" / "http-target-effects.json"
    denominator = json.loads(denominator_path.read_text(encoding="utf-8"))
    denominator["observations"].pop()
    denominator_bytes = _json_bytes(denominator)
    denominator_path.write_bytes(denominator_bytes)

    coverage_path = packet / "coverage" / "http-effects.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["denominator_sha256"] = _sha256(denominator_bytes)
    coverage["total"] = 1
    coverage["receipted"] = 1
    coverage["uncovered_ids"] = []
    coverage_bytes = _json_bytes(coverage)
    coverage_path.write_bytes(coverage_bytes)

    core = json.loads(
        (packet / "packet-core.json").read_text(encoding="utf-8")
    )
    denominator_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == "denominator-effects"
    )
    denominator_artifact["byte_length"] = len(denominator_bytes)
    denominator_artifact["sha256"] = _sha256(denominator_bytes)
    coverage_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == "coverage-effects"
    )
    coverage_artifact["byte_length"] = len(coverage_bytes)
    coverage_artifact["sha256"] = _sha256(coverage_bytes)
    _republish_fixture(packet, core)

    result = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in result.coverage if item.phase == "effect")

    assert effect.status == "NOT_COMPUTED"
    assert effect.total == effect.receipted == 0
    assert result.exit_code == 1
    assert any(
        "exact denominator artifact byte SHA" in error
        for error in result.errors
    )


def test_denominator_checkpoint_rejects_completeness_prose(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    _replace_effect_checkpoint(
        packet,
        subject_changes={"claim": "complete event log"},
    )

    result = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in result.coverage if item.phase == "effect")

    assert effect.status == "NOT_COMPUTED"
    assert result.exit_code == 1
    assert "reliance" in result.suppressed_conclusions
    assert "coverage conclusions" in result.suppressed_conclusions
    assert any(
        "claim must be 'ORDERED_DENOMINATOR_SNAPSHOT'" in error
        for error in result.errors
    )


def test_denominator_checkpoint_must_pass_accepted_attestation(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    _replace_effect_checkpoint(packet, signer_role="publisher")

    result = verify_incident_packet(packet, _context("http"))
    effect = next(item for item in result.coverage if item.phase == "effect")

    assert effect.status == "NOT_COMPUTED"
    assert result.exit_code == 1
    assert any(
        "did not pass accepted attestation verification" in error
        for error in result.errors
    )


def test_publisher_cannot_repackage_redaction_without_reviewer_binding(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    released_path = packet / "traces" / "http-released.jsonl"
    released_bytes = (
        b'{"classification":"publisher-repacked","events":[],"protocol":"http"}\n'
    )
    released_path.write_bytes(released_bytes)

    core = json.loads(
        (packet / "packet-core.json").read_text(encoding="utf-8")
    )
    source_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == "trace-source-controlled"
    )
    source_artifact["sha256"] = _sha256(b"publisher-repacked-source")
    source_artifact["byte_length"] = len(b"publisher-repacked-source")
    released_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == "trace-released"
    )
    released_artifact["sha256"] = _sha256(released_bytes)
    released_artifact["byte_length"] = len(released_bytes)

    redaction = core["redactions"][0]
    redaction["tool"] = "publisher-repacker"
    redaction["tool_version"] = "2"
    redaction["rules_hash"] = _sha256(b"remove-everything")
    record_path = packet / "redactions" / "http-trace.json"
    record = {
        "schema_version": 1,
        "redaction_id": redaction["redaction_id"],
        "source_sha256": source_artifact["sha256"],
        "released_sha256": released_artifact["sha256"],
        "tool": redaction["tool"],
        "tool_version": redaction["tool_version"],
        "rules_hash": redaction["rules_hash"],
        "disclosure_safety": "NOT_COMPUTED",
    }
    record_bytes = _json_bytes(record)
    record_path.write_bytes(record_bytes)
    record_artifact = next(
        item for item in core["artifacts"]
        if item["artifact_id"] == redaction["record_artifact_id"]
    )
    record_artifact["sha256"] = _sha256(record_bytes)
    record_artifact["byte_length"] = len(record_bytes)
    _republish_fixture(packet, core)

    result = verify_incident_packet(packet, _context("http"))

    assert result.redaction_binding == "FAILED"
    assert result.exit_code == 1
    assert "reliance" in result.suppressed_conclusions
    assert (
        "redaction and disclosure conclusions"
        in result.suppressed_conclusions
    )
    assert any(
        "sole self_asserted evidence binding the redaction record" in error
        for error in result.errors
    )


def test_publish_receipt_detects_changed_core_without_republication(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["packet_id"] = "packet-http-synthetic-tampered"
    core_path.write_text(json.dumps(core, indent=2, sort_keys=True) + "\n")

    result = verify_incident_packet(packet, _context("http"))
    assert result.packet_integrity == "FAILED"
    assert result.exit_code == 1
    assert "reliance" in result.suppressed_conclusions


def test_timeline_commits_to_exact_receipt_bytes(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    receipt_path = packet / "receipts" / "01-decision-permit.json"
    receipt_path.write_bytes(receipt_path.read_bytes() + b" ")

    result = verify_incident_packet(packet, _context("http"))

    assert result.timeline_integrity == "FAILED"
    assert result.receipt_integrity == "FAILED"
    assert result.exit_code == 1
    assert any("exact bytes" in reason for reason in result.errors)


def test_denominator_boolean_is_not_integer_one(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    denominator = packet / "indexes" / "http-gateway-ingress.json"
    value = json.loads(denominator.read_text(encoding="utf-8"))
    value["schema_version"] = True
    denominator.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    result = verify_incident_packet(packet, _context("http"))
    decision = next(item for item in result.coverage if item.phase == "decision")
    assert decision.status == "NOT_COMPUTED"
    assert result.artifact_integrity == "FAILED"
    assert result.exit_code == 1


def test_timeline_boolean_is_not_sequence_one(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["timeline"][0]["sequence"] = True
    core_path.write_text(json.dumps(core, indent=2, sort_keys=True) + "\n")

    with pytest.raises(IncidentPacketError, match="sequence"):
        parse_incident_packet(packet)


def test_empty_coverage_anchor_set_is_not_vacuously_computed(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["denominators"] = []
    core["coverage"] = []
    core_path.write_text(json.dumps(core, indent=2, sort_keys=True) + "\n")

    with pytest.raises(IncidentPacketError, match="at least one coverage anchor"):
        parse_incident_packet(packet)


def test_protocol_cannot_omit_an_entire_effect_phase(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["denominators"] = [
        item for item in core["denominators"] if item["phase"] != "effect"
    ]
    core["coverage"] = [
        item for item in core["coverage"] if item["phase"] != "effect"
    ]
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(
        IncidentPacketError,
        match="exactly one decision denominator and one effect denominator",
    ):
        parse_incident_packet(packet)


def test_protocol_cannot_duplicate_one_phase_in_place_of_the_other(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    next(
        item for item in core["denominators"] if item["phase"] == "effect"
    )["phase"] = "decision"
    core_path.write_bytes(_json_bytes(core))

    with pytest.raises(
        IncidentPacketError,
        match="exactly one decision denominator and one effect denominator",
    ):
        parse_incident_packet(packet)


def test_strict_packet_parser_rejects_duplicate_members(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core = packet / "packet-core.json"
    served = core.read_text(encoding="utf-8")
    core.write_text(served.replace('"profile":', '"profile":"duplicate","profile":', 1))
    with pytest.raises(IncidentPacketError, match="duplicate JSON member"):
        parse_incident_packet(packet)


def test_strict_packet_parser_rejects_undeclared_and_symlink_members(tmp_path: Path) -> None:
    undeclared = tmp_path / "undeclared"
    shutil.copytree(PROFILE / "vectors" / "http-clean", undeclared)
    (undeclared / "ambient-secret.txt").write_text("not allowed")
    with pytest.raises(IncidentPacketError, match="undeclared"):
        parse_incident_packet(undeclared)

    symlinked = tmp_path / "symlinked"
    shutil.copytree(PROFILE / "vectors" / "http-clean", symlinked)
    (symlinked / "extra-link").symlink_to(tmp_path / "outside")
    with pytest.raises(IncidentPacketError, match="non-regular|symlink"):
        parse_incident_packet(symlinked)


@pytest.mark.skipif(not getattr(os, "O_NOFOLLOW", 0), reason="platform has no O_NOFOLLOW")
def test_packet_reader_opens_members_with_no_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.open
    observed_flags: list[int] = []

    def recording_open(path, flags, *args, **kwargs):
        observed_flags.append(flags)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    parse_incident_packet(PROFILE / "vectors" / "http-clean")
    assert observed_flags
    assert all(flags & os.O_NOFOLLOW for flags in observed_flags)


def test_packet_reader_regular_files_request_binary_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary_flag = 0x40000000
    monkeypatch.setattr(os, "O_BINARY", binary_flag, raising=False)
    standalone = runpy.run_path(str(PROFILE / "verify_packet.py"))

    implementations = (
        incident_packet._packet_read_open_flags,
        standalone["packet_read_open_flags"],
    )
    for open_flags in implementations:
        regular_flags = open_flags(require_directory=False)
        directory_flags = open_flags(require_directory=True)

        assert regular_flags & binary_flag
        assert not directory_flags & binary_flag
        if no_follow := getattr(os, "O_NOFOLLOW", 0):
            assert regular_flags & no_follow
            assert directory_flags & no_follow

    original_open = os.open
    observed_flags: list[int] = []

    def recording_open(path, flags, *args, **kwargs):
        observed_flags.append(flags)
        return original_open(path, flags & ~binary_flag, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    readers = (
        lambda: incident_packet._walk_packet_path_fallback(
            PROFILE / "vectors" / "http-clean",
            incident_packet.IncidentParseLimits(),
        ),
        lambda: standalone["read_packet_path_fallback"](
            PROFILE / "vectors" / "http-clean"
        ),
    )
    for read_packet in readers:
        observed_flags.clear()
        assert read_packet()["packet-core.json"]
        assert observed_flags
        assert all(flags & binary_flag for flags in observed_flags)


def test_incident_packet_checkout_attributes_preserve_exact_bytes() -> None:
    if (ROOT.parent / "glyph").is_dir():
        repository = ROOT.parent
        prefixes = (
            "bulla/spec/agent-incident-packet",
            "glyph/public/examples/agent-incident-packet",
        )
        representative_members = {
            "bulla/spec/agent-incident-packet/vectors/http-clean/packet-core.json",
            "bulla/spec/agent-incident-packet/vectors/http-clean/receipts/00-mandate.json",
            "bulla/spec/agent-incident-packet/vectors/http-clean/coverage/http-decisions.json",
            "bulla/spec/agent-incident-packet/vectors/http-clean/traces/http-released.jsonl",
            "bulla/spec/agent-incident-packet/vectors/http-clean/redactions/http-trace.json",
            "bulla/spec/agent-incident-packet/vectors/http-clean/witness/http-checkpoint.json",
            "glyph/public/examples/agent-incident-packet/http/packet-core.json",
            "glyph/public/examples/agent-incident-packet/contexts/http.json",
            "glyph/public/examples/agent-incident-packet/reports/http.json",
        }
    else:
        repository = ROOT
        prefixes = ("spec/agent-incident-packet",)
        representative_members = {
            "spec/agent-incident-packet/vectors/http-clean/packet-core.json",
            "spec/agent-incident-packet/vectors/http-clean/receipts/00-mandate.json",
            "spec/agent-incident-packet/vectors/http-clean/coverage/http-decisions.json",
            "spec/agent-incident-packet/vectors/http-clean/traces/http-released.jsonl",
            "spec/agent-incident-packet/vectors/http-clean/redactions/http-trace.json",
            "spec/agent-incident-packet/vectors/http-clean/witness/http-checkpoint.json",
        }
    tracked = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "--", *prefixes],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked

    assert representative_members <= set(tracked)

    result = subprocess.run(
        ["git", "-C", str(repository), "check-attr", "-z", "text", "--", *tracked],
        check=True,
        capture_output=True,
    )
    fields = result.stdout.decode().split("\0")
    assert fields[-1] == ""
    attributes = {
        fields[index]: fields[index + 2]
        for index in range(0, len(fields) - 1, 3)
    }
    assert attributes == {path: "unset" for path in tracked}

    staged = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "--stage", "--", *tracked],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    staged_hashes = {
        path: metadata.split()[1]
        for line in staged
        for metadata, path in (line.split("\t", 1),)
    }
    worktree_hashes = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "hash-object",
            "--no-filters",
            "--",
            *tracked,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert dict(zip(tracked, worktree_hashes, strict=True)) == staged_hashes


def test_packet_reader_rejects_an_intermediate_directory_symlink(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    outside = tmp_path / "outside-traces"
    (packet / "traces").rename(outside)
    (packet / "traces").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IncidentPacketError, match="symlink"):
        parse_incident_packet(packet)


@pytest.mark.skipif(
    not (
        os.name == "posix"
        and bool(getattr(os, "O_DIRECTORY", 0))
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.listdir in os.supports_fd
    ),
    reason="platform lacks descriptor-relative directory traversal",
)
def test_packet_reader_does_not_follow_an_intermediate_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    safe_trace = (
        packet / "traces" / "http-released.jsonl"
    ).read_bytes()
    outside = tmp_path / "outside-traces"
    outside.mkdir()
    hostile_trace = b'{"source":"outside-path-escape"}\n'
    (outside / "http-released.jsonl").write_bytes(hostile_trace)
    displaced = tmp_path / "displaced-traces"

    original_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path).name == "http-released.jsonl":
            (packet / "traces").rename(displaced)
            (packet / "traces").symlink_to(
                outside,
                target_is_directory=True,
            )
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    parsed = parse_incident_packet(packet)

    assert swapped
    assert parsed.files["traces/http-released.jsonl"] == safe_trace
    assert parsed.files["traces/http-released.jsonl"] != hostile_trace


def test_packet_reader_path_fallback_parses_clean_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        incident_packet,
        "_DESCRIPTOR_RELATIVE_WALK_SUPPORTED",
        False,
    )
    parsed = parse_incident_packet(PROFILE / "vectors" / "http-clean")
    assert parsed.core["profile"] == "glyph.agent-incident-packet/0.1-draft"
    assert parsed.files["packet-core.json"]


def test_packet_reader_rejects_windows_reparse_metadata() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=0x400,
    )
    assert _is_symlink_or_reparse_point(metadata)


def test_witnessed_at_requires_an_authentic_checkpoint(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["witnessed_at"] = "2026-07-26T12:11:01Z"
    witness_path.write_text(json.dumps(witness, indent=2, sort_keys=True) + "\n")

    result = verify_incident_packet(packet, _context("http"))
    assert result.witness_inclusion == "FAILED"
    assert result.temporal_evidence["witnessed_at"] == ()
    assert result.exit_code == 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("20260726T120000Z", "must be an RFC 3339 UTC instant ending in Z"),
        ("2026-07-26T12:00:00+00:00", "must be an RFC 3339 UTC instant ending in Z"),
        ("2026-02-30T12:00:00Z", "is not a valid RFC 3339 instant"),
    ],
)
def test_incident_instants_require_extended_utc_form(
    value: str,
    message: str,
) -> None:
    with pytest.raises(IncidentPacketError, match=message):
        _parse_instant(value, "test.instant")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("received_at", "20260726T121000Z"),
        ("received_at", "2026-07-26T12:10:00+00:00"),
        ("witnessed_at", "2026-02-30T12:11:00Z"),
    ],
)
def test_witness_times_require_extended_valid_utc_instants(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    witness_path = packet / "witness" / "http-checkpoint.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness[field] = value
    witness_bytes = _json_bytes(witness)
    witness_path.write_bytes(witness_bytes)

    core = json.loads(
        (packet / "packet-core.json").read_text(encoding="utf-8")
    )
    witness_artifact = next(
        artifact
        for artifact in core["artifacts"]
        if artifact["path"] == "witness/http-checkpoint.json"
    )
    witness_artifact["byte_length"] = len(witness_bytes)
    witness_artifact["sha256"] = _sha256(witness_bytes)
    _republish_fixture(packet, core)

    result = verify_incident_packet(packet, _context("http"))
    assert result.witness_inclusion == "FAILED"
    assert result.temporal_evidence["received_at"] == ()
    assert result.temporal_evidence["witnessed_at"] == ()
    assert result.exit_code == 1
    assert any("RFC 3339" in error for error in result.errors)


def test_unsafe_manifest_path_is_rejected_before_file_access(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["artifacts"][0]["path"] = "../outside.json"
    core_path.write_text(json.dumps(core, indent=2, sort_keys=True) + "\n")
    with pytest.raises(IncidentPacketError, match="normalized relative path"):
        parse_incident_packet(packet)


def test_drive_qualified_manifest_path_is_rejected_on_every_platform(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PROFILE / "vectors" / "http-clean", packet)
    core_path = packet / "packet-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core["artifacts"][0]["path"] = "C:/outside.json"
    core_path.write_text(json.dumps(core, indent=2, sort_keys=True) + "\n")

    with pytest.raises(IncidentPacketError, match="normalized relative path"):
        parse_incident_packet(packet)
