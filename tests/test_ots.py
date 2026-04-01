"""Tests for OpenTimestamps integration."""

import base64
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from opentimestamps.core.notary import PendingAttestation
from opentimestamps.core.timestamp import Timestamp


SAMPLE_MANIFEST = {
    "bulla_manifest": "0.1",
    "tool": {"name": "test-tool"},
    "conventions": {
        "amount_unit": {"value": "dollars", "confidence": "declared"},
    },
}


@pytest.fixture
def manifest_path():
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False
    ) as f:
        yaml.dump(SAMPLE_MANIFEST, f)
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


def _mock_submit(calendar_url, msg, q, timeout):
    """Mock _submit_to_calendar that injects a pending attestation."""
    ts = Timestamp(msg)
    ts.attestations.add(
        PendingAttestation("https://mock.calendar.example")
    )
    q.put(ts)


def _mock_submit_fail(calendar_url, msg, q, timeout):
    """Mock _submit_to_calendar that simulates network failure."""
    q.put(ConnectionError("No network"))


class TestCommitmentHash:
    def test_deterministic(self, manifest_path):
        from bulla.ots import commitment_hash

        h1 = commitment_hash(manifest_path)
        h2 = commitment_hash(manifest_path)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_excludes_ots_fields(self, manifest_path):
        from bulla.ots import commitment_hash

        h_before = commitment_hash(manifest_path)

        # Add OTS fields
        data = yaml.safe_load(manifest_path.read_text())
        data["commitment_hash"] = "abc123"
        data["ots_proof"] = "deadbeef"
        manifest_path.write_text(yaml.dump(data))

        h_after = commitment_hash(manifest_path)
        assert h_before == h_after

    def test_changes_with_content(self, manifest_path):
        from bulla.ots import commitment_hash

        h1 = commitment_hash(manifest_path)

        data = yaml.safe_load(manifest_path.read_text())
        data["conventions"]["timezone"] = {"confidence": "unknown"}
        manifest_path.write_text(yaml.dump(data))

        h2 = commitment_hash(manifest_path)
        assert h1 != h2


class TestOtsAvailability:
    def test_check_passes_when_installed(self):
        from bulla.ots import _check_ots_available

        _check_ots_available()


class TestStampManifest:
    def test_stamp_returns_bytes(self, manifest_path):
        from bulla.ots import stamp_manifest

        with patch("bulla.ots._submit_to_calendar", _mock_submit):
            proof = stamp_manifest(
                manifest_path,
                calendar_urls=["https://mock.calendar.example"],
            )
        assert isinstance(proof, bytes)
        assert len(proof) > 0

    def test_stamp_fails_without_calendars(self, manifest_path):
        from bulla.ots import stamp_manifest

        with patch("bulla.ots._submit_to_calendar", _mock_submit_fail):
            with pytest.raises(RuntimeError, match="Failed to get"):
                stamp_manifest(
                    manifest_path,
                    calendar_urls=["https://mock.calendar.example"],
                    timeout=1,
                )


class TestPublishAndVerify:
    def test_publish_embeds_fields(self, manifest_path):
        from bulla.ots import publish_manifest

        with patch("bulla.ots._submit_to_calendar", _mock_submit):
            publish_manifest(
                manifest_path,
                calendar_urls=["https://mock.calendar.example"],
            )

        data = yaml.safe_load(manifest_path.read_text())
        assert "commitment_hash" in data
        assert "ots_proof" in data
        assert len(data["commitment_hash"]) == 64
        proof_bytes = base64.b64decode(data["ots_proof"])
        assert len(proof_bytes) > 0

    def test_verify_pending(self, manifest_path):
        from bulla.ots import publish_manifest, verify_manifest

        with patch("bulla.ots._submit_to_calendar", _mock_submit):
            publish_manifest(
                manifest_path,
                calendar_urls=["https://mock.calendar.example"],
            )

        result = verify_manifest(manifest_path)
        assert result["valid"] is True
        assert result["status"] == "pending"
        assert "commitment_hash" in result

    def test_verify_no_proof(self, manifest_path):
        from bulla.ots import verify_manifest

        result = verify_manifest(manifest_path)
        assert result["valid"] is False
        assert "No OTS proof" in result["error"]

    def test_verify_tampered_manifest(self, manifest_path):
        from bulla.ots import publish_manifest, verify_manifest

        with patch("bulla.ots._submit_to_calendar", _mock_submit):
            publish_manifest(
                manifest_path,
                calendar_urls=["https://mock.calendar.example"],
            )

        # Tamper with manifest
        data = yaml.safe_load(manifest_path.read_text())
        data["conventions"]["timezone"] = {"confidence": "unknown"}
        manifest_path.write_text(yaml.dump(data))

        result = verify_manifest(manifest_path)
        assert result["valid"] is False
        assert "mismatch" in result["error"]

    def test_roundtrip_hash_stability(self, manifest_path):
        from bulla.ots import commitment_hash, publish_manifest

        h_before = commitment_hash(manifest_path)

        with patch("bulla.ots._submit_to_calendar", _mock_submit):
            publish_manifest(
                manifest_path,
                calendar_urls=["https://mock.calendar.example"],
            )

        h_after = commitment_hash(manifest_path)
        assert h_before == h_after
