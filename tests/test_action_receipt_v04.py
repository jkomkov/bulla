from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from bulla._canonical import CanonicalizationError, canonical_jcs_int
from bulla.action_receipt import (
    ActionReceipt,
    ActionReceiptError,
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.receipt_parser import ReceiptParseError, ReceiptParseLimits, parse_action_receipt_json

try:
    from bulla.identity import LocalEd25519Signer
    LocalEd25519Signer.generate()
    HAS_IDENTITY = True
except ImportError:  # pragma: no cover
    HAS_IDENTITY = False


EVENT_ID = "2f5fe4bd-386d-4d5a-96e8-47ca2c156f25"
CLAIMED_AT = "2026-07-21T22:00:00Z"


def envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal="did:key:zExample", policy="policy://payments"),
        bounds=Bounds(scope="payments.charge:usd-micros"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://forum.example", trusted_root_ref="sha256:root"),
            remedies=(Remedy("challenge", "bulla challenge replay", "forum:payments"),),
        ),
        retention_class="operational",
    )


def unsigned(**changes) -> ActionReceipt:
    args = {
        "action": {"type": "payments.charge", "subject": {"amount_micros": 12500000, "currency": "USD"}},
        "diagnostic_ref": {"status": "not_applicable"},
        "envelope": envelope(),
        "event_id": EVENT_ID,
        "claimed_at": CLAIMED_AT,
        "producer": {"bulla_version": "source"},
    }
    args.update(changes)
    return build_action_receipt_v04(**args)


def test_v04_unsigned_round_trip_is_digest_valid():
    receipt = unsigned()
    document = receipt.to_dict()
    assert document["canonicalization"] == "bulla-jcs-int/1"
    assert "timestamp" not in document
    assert ActionReceipt.from_dict(document).to_dict() == document
    verification = verify_receipt(document)
    assert verification.ok and verification.verified_to == "digest"


@pytest.mark.skipif(not HAS_IDENTITY, reason="needs bulla[identity]")
def test_v04_three_proofs_verify_and_share_signer():
    signer = LocalEd25519Signer.generate()
    receipt = sign_action_receipt_v04(unsigned(), signer)
    verification = verify_receipt(receipt.to_dict())
    assert verification.ok and verification.verified_to == "attestation"
    assert verification.checks["signature"]
    assert verification.checks["occurrence"]
    assert verification.checks["authorization"]
    assert verification.checks["occurrence_same_signer"]
    assert verification.checks["authorization_same_signer"]


@pytest.mark.skipif(not HAS_IDENTITY, reason="needs bulla[identity]")
def test_v04_claimed_at_tamper_survives_digest_but_fails_occurrence_proof():
    signer = LocalEd25519Signer.generate()
    document = sign_action_receipt_v04(unsigned(), signer).to_dict()
    document["claimed_at"] = "2036-01-01T00:00:00Z"
    document["hashes"] = ActionReceipt.from_dict(document).hashes()
    verification = verify_receipt(document)
    assert not verification.ok
    assert verification.checks["occurrence"] is False


@pytest.mark.skipif(not HAS_IDENTITY, reason="needs bulla[identity]")
def test_v04_event_id_tamper_and_proof_transplant_fail():
    signer = LocalEd25519Signer.generate()
    first = sign_action_receipt_v04(unsigned(), signer)
    second = sign_action_receipt_v04(
        unsigned(event_id="38d86f96-7a4d-4a61-a489-a5bcb75a0e70"), signer,
    )
    transplanted = second.to_dict()
    transplanted["occurrence"] = first.occurrence
    transplanted["hashes"] = ActionReceipt.from_dict(transplanted).hashes()
    verification = verify_receipt(transplanted)
    assert not verification.ok and verification.checks["occurrence"] is False


@pytest.mark.skipif(not HAS_IDENTITY, reason="needs bulla[identity]")
def test_v04_occurrence_key_substitution_fails_same_signer():
    honest = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()
    receipt = sign_action_receipt_v04(unsigned(), honest)
    forged = dataclasses.replace(
        receipt,
        occurrence=attacker.sign_domain("occurrence", receipt.event_hash, schema="0.4"),
    )
    verification = verify_receipt(forged.to_dict())
    assert not verification.ok
    assert verification.checks["occurrence"] is True
    assert verification.checks["occurrence_same_signer"] is False


@pytest.mark.parametrize("value", [1.5, float("inf"), 2**53])
def test_v04_portable_canonical_domain_rejects_numbers(value):
    with pytest.raises((ActionReceiptError, CanonicalizationError)):
        unsigned(action={"type": "x", "subject": {"value": value}})


def test_jcs_int_utf16_key_order_is_deterministic():
    left = {"\U0001f600": 1, "\uffff": 2, "a": 3}
    right = {"a": 3, "\uffff": 2, "\U0001f600": 1}
    assert canonical_jcs_int(left) == canonical_jcs_int(right)


def test_strict_parser_rejects_duplicate_unknown_missing_and_nonfinite():
    valid = json.dumps(unsigned().to_dict(), separators=(",", ":"))
    duplicate = valid.replace('"schema_version":"0.4"', '"schema_version":"0.4","schema_version":"0.4"', 1)
    with pytest.raises(ReceiptParseError, match="duplicate"):
        parse_action_receipt_json(duplicate)

    unknown = unsigned().to_dict()
    unknown["ambient_authority"] = True
    with pytest.raises(ReceiptParseError, match="unknown"):
        parse_action_receipt_json(json.dumps(unknown))

    missing = unsigned().to_dict()
    missing.pop("event_id")
    with pytest.raises(ReceiptParseError, match="missing"):
        parse_action_receipt_json(json.dumps(missing))

    with pytest.raises(ReceiptParseError, match="non-finite"):
        parse_action_receipt_json(valid[:-1] + ',"x":NaN}')

    nested = unsigned().to_dict()
    nested["remedy"]["ambient_default"] = "allow"
    with pytest.raises(ReceiptParseError, match="remedy has unknown"):
        parse_action_receipt_json(json.dumps(nested))
    with pytest.raises(ActionReceiptError, match="remedy has unknown"):
        ActionReceipt.from_dict(nested)


def test_strict_parser_preserves_conforming_historical_vector():
    vector = Path(__file__).resolve().parents[1] / "spec/vectors/valid-release.json"
    parsed = parse_action_receipt_json(vector.read_bytes())
    assert parsed.schema_version == "0.1"


def test_strict_parser_resource_limits_precede_verification():
    raw = json.dumps(unsigned().to_dict())
    with pytest.raises(ReceiptParseError, match="exceeds 32 bytes"):
        parse_action_receipt_json(raw, ReceiptParseLimits(max_bytes=32))
    with pytest.raises(ReceiptParseError, match="depth"):
        parse_action_receipt_json(raw, ReceiptParseLimits(max_depth=2))
    with pytest.raises(ReceiptParseError, match="aggregate"):
        parse_action_receipt_json(raw, ReceiptParseLimits(max_nodes=10))


def test_strict_parser_never_normalizes_away_a_served_hash_mismatch():
    document = unsigned().to_dict()
    document["hashes"]["event"] = "sha256:" + "00" * 32
    with pytest.raises(ReceiptParseError, match="served receipt hashes.*event"):
        parse_action_receipt_json(json.dumps(document))


def test_v04_rejects_noncanonical_uuid_and_historical_field_mix():
    with pytest.raises(ActionReceiptError, match="UUIDv4"):
        unsigned(event_id=EVENT_ID.upper())
    document = unsigned().to_dict()
    document["timestamp"] = CLAIMED_AT
    with pytest.raises(ActionReceiptError, match="unknown"):
        ActionReceipt.from_dict(document)
