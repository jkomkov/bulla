"""General event-denominator coverage: the unreceipted-action detector."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from bulla.coverage import (
    event_coverage,
    observed_record_sha256,
    receipt_attested_action_ids,
)
from bulla.wrap import receipt_for


def _receipt(action_id: str) -> dict:
    return receipt_for("network.egress", {"event_id": action_id})


def test_all_covered() -> None:
    observed = [{"id": "act-1"}, {"id": "act-2"}]
    receipts = [_receipt("act-1"), _receipt("act-2")]
    report = event_coverage(observed, receipts)
    assert report["coverage"] == 1.0
    assert report["unreceipted_delta"] == []
    assert report["unreceipted"] == []
    assert report["total_anchored"] == 2


def test_injected_unreceipted_action_is_flagged() -> None:
    observed = [
        {"id": "act-1"},
        {"id": "act-bypass"},
        {"id": "act-3"},
    ]
    # The bypass emitted no receipt.
    receipts = [_receipt("act-1"), _receipt("act-3")]
    report = event_coverage(observed, receipts)
    assert report["unreceipted_delta"] == ["act-bypass"]
    assert report["unreceipted"][0]["id"] == "act-bypass"
    assert report["receipted"] == 2
    assert report["coverage"] == round(2 / 3, 4)


def test_empty_observed_is_full_coverage() -> None:
    report = event_coverage([], [])
    assert report["coverage"] == 1.0
    assert report["total_anchored"] == 0
    assert report["unreceipted"] == []


def test_phantom_receipt_id_detected() -> None:
    observed = [{"id": "act-1"}]
    receipts = [_receipt("act-1"), _receipt("act-ghost")]
    report = event_coverage(observed, receipts)
    assert report["coverage"] == 1.0
    assert report["phantom_receipt_ids"] == ["act-ghost"]


def test_reads_receipts_from_directory(tmp_path: Path) -> None:
    (tmp_path / "r1.json").write_text(json.dumps(_receipt("act-1")))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "notjson.txt").write_text("ignored")
    observed = [{"id": "act-1"}, {"id": "act-2"}]
    report = event_coverage(observed, tmp_path)
    assert report["unreceipted_delta"] == ["act-2"]
    assert len(report["invalid_receipts"]) == 1
    assert "invalid JSON" in report["invalid_receipts"][0]["reason"]


@pytest.mark.parametrize(
    ("name", "raw", "reason"),
    [
        (
            "duplicate.json",
            lambda valid: valid.replace(
                '"schema_version": "0.2"',
                '"schema_version": "0.2", "schema_version": "0.2"',
                1,
            ),
            "duplicate JSON member",
        ),
        (
            "unknown.json",
            lambda valid: valid[:-1] + ', "ambient_authority": true}',
            "unknown fields",
        ),
        (
            "nonfinite.json",
            lambda valid: valid[:-1] + ', "ambient_authority": NaN}',
            "non-finite JSON number",
        ),
        (
            "surrogate.json",
            lambda valid: valid.replace(
                '"network.egress"', '"network.\\ud800egress"', 1
            ),
            "lone Unicode surrogates",
        ),
    ],
)
def test_receipt_directory_uses_strict_byte_parser(
    tmp_path: Path,
    name: str,
    raw,
    reason: str,
) -> None:
    valid = json.dumps(_receipt("act-1"))
    (tmp_path / name).write_text(raw(valid))

    report = event_coverage([{"id": "act-1"}], tmp_path)

    assert report["coverage"] == 0.0
    assert report["unreceipted_delta"] == ["act-1"]
    assert len(report["invalid_receipts"]) == 1
    assert reason in report["invalid_receipts"][0]["reason"]


def test_receipt_directory_enforces_resource_limits(tmp_path: Path) -> None:
    (tmp_path / "oversized.json").write_bytes(b" " * 1_048_577)

    report = event_coverage([{"id": "act-1"}], tmp_path)

    assert report["coverage"] == 0.0
    assert report["unreceipted_delta"] == ["act-1"]
    assert "exceeds 1048576 bytes" in report["invalid_receipts"][0]["reason"]


def test_receipt_directory_rejects_symlink_root_and_members(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "valid.json").write_text(json.dumps(_receipt("act-1")))
    linked_root = tmp_path / "linked-receipts"
    linked_root.symlink_to(receipts, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        event_coverage([{"id": "act-1"}], linked_root)

    target = tmp_path / "outside.json"
    target.write_text(json.dumps(_receipt("act-1")))
    (receipts / "linked.json").symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        event_coverage([{"id": "act-1"}], receipts)


def test_receipt_directory_enforces_file_count_limit(tmp_path: Path) -> None:
    for index in range(513):
        (tmp_path / f"{index:03d}.json").write_text("{}")
    with pytest.raises(ValueError, match="exceeds 512 JSON files"):
        event_coverage([], tmp_path)


def test_receipt_directory_enforces_aggregate_byte_limit(tmp_path: Path) -> None:
    for index in range(17):
        (tmp_path / f"{index:02d}.json").write_bytes(b" " * 1_048_576)
    with pytest.raises(ValueError, match="exceeds 16777216 aggregate bytes"):
        event_coverage([], tmp_path)


def test_attested_ids_read_from_known_paths() -> None:
    assert receipt_attested_action_ids(_receipt("x")) == {"x"}
    assert receipt_attested_action_ids({"producer": {"observed_action_id": "y"}}) == set()
    assert receipt_attested_action_ids({"action": {"subject": {}}}) == set()


def test_duplicate_observed_ids_fail_closed() -> None:
    observed = [{"id": "act-1"}, {"id": "act-1"}]
    with pytest.raises(ValueError, match="duplicate observed action id"):
        event_coverage(observed, [_receipt("act-1")])


@pytest.mark.parametrize("observed", [[{}], [{"id": ""}], ["not-an-object"]])
def test_malformed_observed_denominator_fails_closed(observed: list[object]) -> None:
    with pytest.raises(ValueError):
        event_coverage(observed, [])


def test_fabricated_or_tampered_receipt_cannot_erase_a_finding() -> None:
    fabricated = {"action": {"type": "network.egress", "subject": {"event_id": "act-1"}}}
    tampered = deepcopy(_receipt("act-1"))
    tampered["action"]["subject"]["event_id"] = "act-2"

    report = event_coverage([{"id": "act-1"}, {"id": "act-2"}], [fabricated, tampered])

    assert report["coverage"] == 0.0
    assert report["unreceipted_delta"] == ["act-1", "act-2"]
    assert len(report["invalid_receipts"]) == 2


def test_security_boundary_can_require_attestation() -> None:
    observed = [{"id": "act-1"}]
    report = event_coverage(
        observed,
        [_receipt("act-1")],
        minimum_verification_depth="attestation",
    )
    assert report["coverage"] == 0.0
    assert report["invalid_receipts"][0]["verified_to"] == "digest"


def test_attestation_verified_receipt_covers_security_boundary() -> None:
    identity = pytest.importorskip("bulla.identity")
    signer = identity.LocalEd25519Signer.generate()
    receipt = receipt_for(
        "network.egress",
        {"event_id": "act-1"},
        principal=signer.issuer,
        policy="policy://gateway@sha256:aa",
        signer=signer,
    )
    report = event_coverage(
        [{"id": "act-1"}],
        [receipt],
        minimum_verification_depth="attestation",
        accepted_issuers={signer.issuer},
    )
    assert report["coverage"] == 1.0
    assert report["invalid_receipts"] == []


def test_attested_receipt_from_unaccepted_issuer_does_not_cover() -> None:
    identity = pytest.importorskip("bulla.identity")
    signer = identity.LocalEd25519Signer.generate()
    receipt = receipt_for(
        "network.egress",
        {"event_id": "act-1"},
        principal=signer.issuer,
        policy="policy://gateway@sha256:aa",
        signer=signer,
    )
    report = event_coverage(
        [{"id": "act-1"}],
        [receipt],
        minimum_verification_depth="attestation",
        accepted_issuers={"did:key:zDesignatedGateway"},
    )
    assert report["coverage"] == 0.0
    assert "not accepted" in report["invalid_receipts"][0]["reason"]


def test_conflicting_bound_ids_do_not_cover_multiple_actions() -> None:
    # Rebuild the receipt so its hashes are valid; ambiguity itself is the fault.
    receipt = receipt_for(
        "network.egress", {"event_id": "act-1", "action_id": "act-2"}
    )
    report = event_coverage([{"id": "act-1"}, {"id": "act-2"}], [receipt])
    assert report["coverage"] == 0.0
    assert "exactly one" in report["invalid_receipts"][0]["reason"]


def _digest_bound_pair() -> tuple[dict, dict]:
    observed = {
        "id": "act-1",
        "kind": "network.egress",
        "destination": "example.test",
        "receiver": "constructed-gateway",
    }
    observed["record_sha256"] = observed_record_sha256(observed)
    receipt = receipt_for(
        "network.egress",
        {"event_id": "act-1"},
        evidence_refs=[{
            "name": "observed_action_record",
            "hash": observed["record_sha256"],
            "grounding": "self_asserted",
        }],
    )
    return observed, receipt


def test_record_digest_binds_receipt_to_observed_action_facts() -> None:
    observed, receipt = _digest_bound_pair()
    report = event_coverage([observed], [receipt])
    assert report["coverage"] == 1.0
    assert report["binding_mismatches"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination", "different.test"),
        ("kind", "credential.use"),
        ("receiver", "different-gateway"),
    ],
)
def test_changed_observed_facts_cannot_clear_by_reused_id(field: str, value: str) -> None:
    observed, receipt = _digest_bound_pair()
    observed[field] = value

    with pytest.raises(ValueError, match="record_sha256 does not match"):
        event_coverage([observed], [receipt])

    observed["record_sha256"] = observed_record_sha256(observed)
    report = event_coverage([observed], [receipt])
    assert report["coverage"] == 0.0
    assert report["unreceipted_delta"] == ["act-1"]
    assert report["binding_mismatches"][0]["id"] == "act-1"


def test_fact_bearing_observed_record_requires_digest_binding() -> None:
    with pytest.raises(ValueError, match="action facts without record_sha256"):
        event_coverage(
            [{"id": "act-1", "amount_minor": 12500}],
            [_receipt("act-1")],
        )
