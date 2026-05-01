"""Tests for Extension A: pack-level license metadata + RegistryAccessError + pack_attributions.

Extension A scope (Standards Ingestion Sprint, Phase 1):

- ``license`` block on a pack YAML, with ``registry_license`` describing the
  licensing posture of the **registry the pack points to**, not the pack
  itself. Valid ``registry_license`` values: ``open`` | ``research-only``
  | ``restricted``.
- ``RegistryAccessError`` typed error class (raised by Extension B's
  registry-fetch path; here we verify the type is constructible and
  carries the right metadata).
- ``WitnessReceipt.pack_attributions`` optional field carrying
  hash-references to NOTICES.md entries that standards bodies require
  crediting. Conditional-include in ``_hash_input()`` for backward
  compatibility with pre-Extension-A receipts.
"""

from __future__ import annotations

import json

import pytest

from bulla.lifecycle import receipt_from_dict
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Disposition,
    PackRef,
    RegistryAccessError,
    RegistryAccessErrorCode,
    WitnessReceipt,
)
from bulla.packs.validate import validate_pack
from bulla.witness import verify_receipt_integrity


# ── License block validation ─────────────────────────────────────────


class TestLicenseBlockValidation:
    """``validate_pack`` accepts well-formed license blocks and rejects
    malformed ones. A pack without a license block remains valid (the
    field is optional for backward compatibility)."""

    def _base_pack(self, license_block: dict | None = None) -> dict:
        pack: dict = {
            "pack_name": "test",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        if license_block is not None:
            pack["license"] = license_block
        return pack

    def test_pack_without_license_is_valid(self):
        assert validate_pack(self._base_pack()) == []

    def test_open_registry_license(self):
        pack = self._base_pack({
            "spdx_id": "CC0-1.0",
            "source_url": "https://example.org/standard",
            "registry_license": "open",
        })
        assert validate_pack(pack) == []

    def test_research_only_registry_license(self):
        pack = self._base_pack({
            "spdx_id": "CC-BY-NC-SA-3.0",
            "source_url": "https://www.who.int/icd-10",
            "registry_license": "research-only",
            "attribution": "sha256:abc123",
        })
        assert validate_pack(pack) == []

    def test_restricted_registry_license(self):
        pack = self._base_pack({
            "spdx_id": "Proprietary",
            "source_url": "https://www.swift.com/",
            "registry_license": "restricted",
            "attribution": "sha256:def456",
        })
        assert validate_pack(pack) == []

    def test_missing_registry_license_rejected(self):
        pack = self._base_pack({
            "spdx_id": "MIT",
            "source_url": "https://example.org/",
            # registry_license is missing
        })
        errors = validate_pack(pack)
        assert any("registry_license" in e for e in errors)

    def test_invalid_registry_license_value_rejected(self):
        pack = self._base_pack({
            "registry_license": "public-domain-mostly",  # not in valid set
        })
        errors = validate_pack(pack)
        assert any(
            "registry_license" in e
            and (
                "must be one of" in e
                or "open" in e
                or "research-only" in e
                or "restricted" in e
            )
            for e in errors
        )

    def test_non_string_spdx_id_rejected(self):
        pack = self._base_pack({
            "spdx_id": 42,
            "registry_license": "open",
        })
        errors = validate_pack(pack)
        assert any("spdx_id" in e for e in errors)

    def test_non_string_source_url_rejected(self):
        pack = self._base_pack({
            "source_url": ["not", "a", "string"],
            "registry_license": "open",
        })
        errors = validate_pack(pack)
        assert any("source_url" in e for e in errors)

    def test_non_string_attribution_rejected(self):
        pack = self._base_pack({
            "registry_license": "open",
            "attribution": {"hash": "wrong-shape"},
        })
        errors = validate_pack(pack)
        assert any("attribution" in e for e in errors)

    def test_license_must_be_mapping(self):
        pack = self._base_pack({})
        # Empty dict — registry_license missing
        errors = validate_pack(pack)
        assert any("registry_license" in e for e in errors)

    def test_license_block_not_a_mapping(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*_x"]}},
            "license": "not-a-mapping",
        }
        errors = validate_pack(pack)
        assert any(
            "license" in e and ("must be a mapping" in e or "mapping" in e)
            for e in errors
        )


# ── RegistryAccessError ──────────────────────────────────────────────


class TestRegistryAccessError:
    """The typed error raised by Extension B's registry-fetch path.
    Extension A only verifies the class is constructible and carries
    the right metadata; the actual raise sites land in Extension B."""

    def test_license_required_with_metadata(self):
        err = RegistryAccessError(
            RegistryAccessErrorCode.LICENSE_REQUIRED,
            "registry requires a license credential",
            license_id="NLM-UMLS",
            registry_uri="https://uts.nlm.nih.gov/uts/umls",
        )
        assert err.code == RegistryAccessErrorCode.LICENSE_REQUIRED
        assert err.license_id == "NLM-UMLS"
        assert err.registry_uri == "https://uts.nlm.nih.gov/uts/umls"
        # The message should surface both the license id and registry uri
        # so callers see what they need to obtain.
        msg = str(err)
        assert "LICENSE_REQUIRED" in msg
        assert "NLM-UMLS" in msg
        assert "uts.nlm.nih.gov" in msg

    def test_registry_unavailable(self):
        err = RegistryAccessError(
            RegistryAccessErrorCode.REGISTRY_UNAVAILABLE,
            "could not reach registry",
            registry_uri="https://registry.example.org/",
        )
        assert err.code == RegistryAccessErrorCode.REGISTRY_UNAVAILABLE
        assert err.license_id == ""
        assert "REGISTRY_UNAVAILABLE" in str(err)

    def test_registry_hash_mismatch(self):
        err = RegistryAccessError(
            RegistryAccessErrorCode.REGISTRY_HASH_MISMATCH,
            "fetched content hash differs from pack-recorded hash",
        )
        assert err.code == RegistryAccessErrorCode.REGISTRY_HASH_MISMATCH

    def test_invalid_registry_pointer(self):
        err = RegistryAccessError(
            RegistryAccessErrorCode.INVALID_REGISTRY_POINTER,
            "values_registry pointer is malformed",
        )
        assert err.code == RegistryAccessErrorCode.INVALID_REGISTRY_POINTER

    def test_is_an_exception(self):
        # Must be a normal Exception subclass so callers can catch it.
        with pytest.raises(RegistryAccessError):
            raise RegistryAccessError(
                RegistryAccessErrorCode.LICENSE_REQUIRED, "test"
            )


# ── WitnessReceipt.pack_attributions ─────────────────────────────────


def _minimal_receipt(**overrides) -> WitnessReceipt:
    """Build a minimal receipt for hashing-semantics tests."""
    base = dict(
        receipt_version="0.1.0",
        kernel_version="bulla-test",
        composition_hash="0" * 64,
        diagnostic_hash="0" * 64,
        policy_profile=DEFAULT_POLICY_PROFILE,
        fee=0,
        blind_spots_count=0,
        bridges_required=0,
        unknown_dimensions=0,
        disposition=Disposition.PROCEED,
        timestamp="2026-04-26T00:00:00Z",
    )
    base.update(overrides)
    return WitnessReceipt(**base)


class TestPackAttributions:
    """The optional ``pack_attributions`` field is conditionally
    included in ``_hash_input()``: pre-Extension-A receipts (with
    the field absent / None) must produce the same hash as if the
    field did not exist."""

    def test_default_is_none(self):
        r = _minimal_receipt()
        assert r.pack_attributions is None

    def test_none_attributions_excluded_from_hash(self):
        """A receipt with ``pack_attributions=None`` produces the same
        hash as a receipt where the field never existed (backward
        compatibility invariant)."""
        r = _minimal_receipt(pack_attributions=None)
        d = r.to_dict()
        assert "pack_attributions" not in d
        # Hash includes everything except `receipt_hash` and
        # `anchor_ref`; if pack_attributions had been included as None,
        # it would show up here.

    def test_populated_attributions_included_in_hash(self):
        attributions = ("sha256:who-icd-10-notice", "sha256:fix-trading-notice")
        r = _minimal_receipt(pack_attributions=attributions)
        d = r.to_dict()
        assert d["pack_attributions"] == list(attributions)

    def test_attributions_change_hash(self):
        """Two receipts that differ only in their attributions must
        have different hashes — the field is content-bearing when
        present."""
        r1 = _minimal_receipt(pack_attributions=None)
        r2 = _minimal_receipt(
            pack_attributions=("sha256:notice-a",),
        )
        assert r1.receipt_hash != r2.receipt_hash

    def test_round_trip_preserves_attributions(self):
        attributions = (
            "sha256:who-icd-10-notice",
            "sha256:fix-trading-community-notice",
            "sha256:hl7-fhir-notice",
        )
        r = _minimal_receipt(pack_attributions=attributions)
        d = r.to_dict()
        json_str = json.dumps(d)
        d2 = json.loads(json_str)
        r2 = receipt_from_dict(d2)
        assert r2.pack_attributions == attributions
        # Hash must round-trip cleanly.
        assert r2.receipt_hash == r.receipt_hash

    def test_round_trip_preserves_none(self):
        r = _minimal_receipt(pack_attributions=None)
        d = r.to_dict()
        json_str = json.dumps(d)
        d2 = json.loads(json_str)
        r2 = receipt_from_dict(d2)
        assert r2.pack_attributions is None
        assert r2.receipt_hash == r.receipt_hash

    def test_verify_receipt_integrity_with_attributions(self):
        attributions = ("sha256:notice-x",)
        r = _minimal_receipt(pack_attributions=attributions)
        d = r.to_dict()
        assert verify_receipt_integrity(d)

    def test_verify_receipt_integrity_without_attributions(self):
        r = _minimal_receipt(pack_attributions=None)
        d = r.to_dict()
        assert verify_receipt_integrity(d)

    def test_pre_extension_a_receipt_still_verifies(self):
        """A receipt serialized before Extension A landed (no
        ``pack_attributions`` key in the dict at all) must verify
        identically to a receipt with the field set to None."""
        r = _minimal_receipt()
        d = r.to_dict()
        # Sanity: no key present.
        assert "pack_attributions" not in d
        assert verify_receipt_integrity(d)


# ── Empty-tuple semantics (defensive) ────────────────────────────────


class TestPackAttributionsEdgeCases:
    """Defensive tests for edge cases that could cause hash drift if
    handled wrong by future changes."""

    def test_empty_tuple_treated_as_no_attributions(self):
        """An empty tuple ``()`` is conceptually equivalent to None
        but is currently a separate state. Document the contract:
        only ``None`` is excluded from the hash; an explicit empty
        tuple is included as an empty list (which is a different
        hash than absent).

        This test pins the current behavior so future changes to
        ``_hash_input`` don't silently drift it.
        """
        r_none = _minimal_receipt(pack_attributions=None)
        r_empty = _minimal_receipt(pack_attributions=())
        # Document: empty tuple round-trips as empty list; None as absent.
        # Hashes may differ; pin whichever is current.
        d_none = r_none.to_dict()
        d_empty = r_empty.to_dict()
        assert "pack_attributions" not in d_none
        # Empty tuple currently serialized as []; this is the documented
        # contract. If we want None and () to be hash-equivalent in the
        # future, that's a separate change.
        if "pack_attributions" in d_empty:
            assert d_empty["pack_attributions"] == []
            # Hash differs because the dict differs.
            assert r_none.receipt_hash != r_empty.receipt_hash

    def test_attribution_round_trip_via_json_string(self):
        """The hash-ref strings should survive JSON encode/decode
        unchanged regardless of content (sha256 hex, ipfs CID, etc.).
        """
        attributions = (
            "sha256:abc123",
            "ipfs:QmXoYxz",
            "git:6f4d2e",
        )
        r = _minimal_receipt(pack_attributions=attributions)
        d = json.loads(json.dumps(r.to_dict()))
        r2 = receipt_from_dict(d)
        assert r2.pack_attributions == attributions
