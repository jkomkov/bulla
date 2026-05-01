"""Tests for Extension C: derives_from on PackRef + StandardProvenance.

Extension C scope (Standards Ingestion Sprint, Phase 1):

- ``StandardProvenance`` dataclass: ``standard``, ``version``,
  ``source_uri``, ``source_hash``.
- ``PackRef.derives_from: StandardProvenance | None`` field; lives on
  the ref so multi-pack receipts naturally carry per-standard
  provenance.
- ``derives_from`` block on a pack YAML is validated (required keys
  ``standard``, ``version`` are strings).
- Pack loaders (base, community, extra) extract the block at load
  time and attach it to the resulting PackRef.
- Pack hash binds the underlying-standard revision transitively
  (``derives_from`` is part of the parsed dict, so the hash already
  changes when standard or version changes — no special handling
  needed).
- Receipt round-trip preserves ``derives_from`` on every active pack.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    _hash_pack,
    _load_single_pack,
    _reset_taxonomy_cache,
    load_pack_stack,
)
from bulla.lifecycle import receipt_from_dict
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Disposition,
    PackRef,
    StandardProvenance,
    WitnessReceipt,
)
from bulla.packs.validate import validate_pack


# ── StandardProvenance shape ─────────────────────────────────────────


class TestStandardProvenance:
    def test_minimal_construction(self):
        p = StandardProvenance(standard="ISO-4217", version="2024")
        assert p.standard == "ISO-4217"
        assert p.version == "2024"
        assert p.source_uri == ""
        assert p.source_hash == ""

    def test_full_construction(self):
        p = StandardProvenance(
            standard="FHIR",
            version="R4",
            source_uri="https://hl7.org/fhir/R4/definitions.json.zip",
            source_hash="sha256:" + "1" * 64,
        )
        assert p.standard == "FHIR"
        assert p.version == "R4"
        assert p.source_uri.endswith(".zip")
        assert p.source_hash.startswith("sha256:")

    def test_to_dict_minimal(self):
        p = StandardProvenance(standard="ISO-4217", version="2024")
        d = p.to_dict()
        assert d == {"standard": "ISO-4217", "version": "2024"}
        # Empty optional fields are omitted, not serialized as "".
        assert "source_uri" not in d
        assert "source_hash" not in d

    def test_to_dict_full(self):
        p = StandardProvenance(
            standard="FHIR",
            version="R4",
            source_uri="https://hl7.org/fhir/R4/",
            source_hash="sha256:" + "1" * 64,
        )
        d = p.to_dict()
        assert d["source_uri"] == "https://hl7.org/fhir/R4/"
        assert d["source_hash"].startswith("sha256:")

    def test_round_trip_minimal(self):
        p1 = StandardProvenance(standard="ISO-4217", version="2024")
        p2 = StandardProvenance.from_dict(p1.to_dict())
        assert p2 == p1

    def test_round_trip_full(self):
        p1 = StandardProvenance(
            standard="ICD-10-CM",
            version="2024.10",
            source_uri="https://www.cms.gov/medicare/coding-billing/icd-10-codes",
            source_hash="sha256:" + "2" * 64,
        )
        p2 = StandardProvenance.from_dict(p1.to_dict())
        assert p2 == p1


# ── PackRef integration ──────────────────────────────────────────────


class TestPackRefDerivesFrom:
    def test_default_is_none(self):
        ref = PackRef(name="base", version="0.1.0", hash="0" * 64)
        assert ref.derives_from is None

    def test_to_dict_omits_none(self):
        ref = PackRef(name="base", version="0.1.0", hash="0" * 64)
        d = ref.to_dict()
        assert "derives_from" not in d

    def test_to_dict_includes_provenance(self):
        ref = PackRef(
            name="iso-4217",
            version="0.1.0",
            hash="0" * 64,
            derives_from=StandardProvenance(
                standard="ISO-4217",
                version="2024",
                source_uri="https://www.six-group.com/",
            ),
        )
        d = ref.to_dict()
        assert d["derives_from"]["standard"] == "ISO-4217"
        assert d["derives_from"]["version"] == "2024"
        assert d["derives_from"]["source_uri"] == "https://www.six-group.com/"


# ── derives_from validation ──────────────────────────────────────────


class TestDerivesFromValidation:
    def _pack(self, derives_block: object | None) -> dict:
        pack: dict = {
            "pack_name": "test",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        if derives_block is not None:
            pack["derives_from"] = derives_block
        return pack

    def test_pack_without_derives_from_is_valid(self):
        assert validate_pack(self._pack(None)) == []

    def test_minimal_derives_from(self):
        pack = self._pack({"standard": "ISO-4217", "version": "2024"})
        assert validate_pack(pack) == []

    def test_full_derives_from(self):
        pack = self._pack({
            "standard": "FHIR",
            "version": "R4",
            "source_uri": "https://hl7.org/fhir/R4/",
            "source_hash": "sha256:" + "1" * 64,
        })
        assert validate_pack(pack) == []

    def test_missing_standard_rejected(self):
        pack = self._pack({"version": "2024"})
        errors = validate_pack(pack)
        assert any(
            "derives_from" in e and "standard" in e for e in errors
        )

    def test_missing_version_rejected(self):
        pack = self._pack({"standard": "ISO-4217"})
        errors = validate_pack(pack)
        assert any(
            "derives_from" in e and "version" in e for e in errors
        )

    def test_non_string_standard_rejected(self):
        pack = self._pack({"standard": 42, "version": "2024"})
        errors = validate_pack(pack)
        assert any(
            "derives_from" in e and "standard" in e and "string" in e
            for e in errors
        )

    def test_block_not_a_mapping(self):
        pack = self._pack("just-a-string")
        errors = validate_pack(pack)
        assert any(
            "derives_from" in e and "mapping" in e for e in errors
        )


# ── Pack loading attaches derives_from ───────────────────────────────


class TestPackLoadingExtractsProvenance:
    def setup_method(self):
        _reset_taxonomy_cache()

    def teardown_method(self):
        _reset_taxonomy_cache()

    def _write_pack(self, pack_dict: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        )
        yaml.dump(pack_dict, tmp, default_flow_style=False)
        tmp.flush()
        return Path(tmp.name)

    def test_loaded_pack_carries_provenance(self):
        pack_dict = {
            "pack_name": "iso-4217",
            "pack_version": "0.1.0",
            "derives_from": {
                "standard": "ISO-4217",
                "version": "2024",
                "source_uri": "https://www.six-group.com/",
                "source_hash": "sha256:" + "1" * 64,
            },
            "dimensions": {
                "currency": {
                    "description": "Currency code",
                    "field_patterns": ["*_currency"],
                    "known_values": ["USD", "EUR", "JPY"],
                },
            },
        }
        path = self._write_pack(pack_dict)
        try:
            _, refs = load_pack_stack(extra_paths=[path])
            iso_ref = next(r for r in refs if r.name == "iso-4217")
            assert iso_ref.derives_from is not None
            assert iso_ref.derives_from.standard == "ISO-4217"
            assert iso_ref.derives_from.version == "2024"
            assert iso_ref.derives_from.source_uri.startswith("https://")
        finally:
            path.unlink()

    def test_pack_without_derives_from_has_none(self):
        pack_dict = {
            "pack_name": "no_provenance",
            "pack_version": "0.1.0",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        path = self._write_pack(pack_dict)
        try:
            _, refs = load_pack_stack(extra_paths=[path])
            ref = next(r for r in refs if r.name == "no_provenance")
            assert ref.derives_from is None
        finally:
            path.unlink()

    def test_malformed_provenance_loads_as_none(self):
        """The classifier's ``_extract_provenance`` returns None for
        malformed blocks; the validator surfaces the error separately.
        Loading a malformed pack via load_pack_stack should not crash —
        it should just produce a None provenance."""
        pack_dict = {
            "pack_name": "bad_provenance",
            "pack_version": "0.1.0",
            "derives_from": {"standard": "ISO-4217"},  # missing version
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        path = self._write_pack(pack_dict)
        try:
            _, refs = load_pack_stack(extra_paths=[path])
            ref = next(r for r in refs if r.name == "bad_provenance")
            assert ref.derives_from is None
        finally:
            path.unlink()


# ── Pack hash binds underlying standard ──────────────────────────────


class TestPackHashBindsStandardVersion:
    """``derives_from`` is part of the parsed dict, so the existing
    ``_hash_pack`` already binds the underlying-standard revision
    transitively. Document the invariant with explicit tests."""

    def test_different_standard_versions_produce_different_hashes(self):
        a = {
            "pack_name": "icd",
            "derives_from": {"standard": "ICD-10-CM", "version": "2024"},
            "dimensions": {
                "code": {"description": "x", "field_patterns": ["*_code"]},
            },
        }
        b = dict(a)
        b["derives_from"] = {"standard": "ICD-10-CM", "version": "2025"}
        assert _hash_pack(a) != _hash_pack(b)

    def test_different_source_hashes_produce_different_pack_hashes(self):
        a = {
            "pack_name": "icd",
            "derives_from": {
                "standard": "ICD-10-CM",
                "version": "2024",
                "source_hash": "sha256:aaaa",
            },
            "dimensions": {
                "code": {"description": "x", "field_patterns": ["*_code"]},
            },
        }
        b = dict(a)
        b["derives_from"] = dict(a["derives_from"])
        b["derives_from"]["source_hash"] = "sha256:bbbb"
        assert _hash_pack(a) != _hash_pack(b)

    def test_pack_with_provenance_and_without_have_different_hashes(self):
        with_p = {
            "pack_name": "p",
            "derives_from": {"standard": "X", "version": "1"},
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*"]},
            },
        }
        without_p = {
            "pack_name": "p",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*"]},
            },
        }
        assert _hash_pack(with_p) != _hash_pack(without_p)


# ── Receipt round-trip ───────────────────────────────────────────────


def _minimal_receipt(active_packs: tuple[PackRef, ...]) -> WitnessReceipt:
    return WitnessReceipt(
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
        active_packs=active_packs,
    )


class TestReceiptRoundTripWithProvenance:
    def test_receipt_records_provenance_on_packref(self):
        ref = PackRef(
            name="iso-4217",
            version="0.1.0",
            hash="0" * 64,
            derives_from=StandardProvenance(
                standard="ISO-4217",
                version="2024",
                source_hash="sha256:" + "1" * 64,
            ),
        )
        r = _minimal_receipt(active_packs=(ref,))
        d = r.to_dict()
        assert d["active_packs"][0]["derives_from"]["standard"] == "ISO-4217"

    def test_round_trip_preserves_provenance(self):
        ref = PackRef(
            name="fhir-r4",
            version="0.1.0",
            hash="0" * 64,
            derives_from=StandardProvenance(
                standard="FHIR",
                version="R4",
                source_uri="https://hl7.org/fhir/R4/",
            ),
        )
        r = _minimal_receipt(active_packs=(ref,))
        d2 = json.loads(json.dumps(r.to_dict()))
        r2 = receipt_from_dict(d2)
        assert r2.active_packs[0].derives_from is not None
        assert r2.active_packs[0].derives_from.standard == "FHIR"
        assert r2.active_packs[0].derives_from.version == "R4"
        assert r2.active_packs[0].derives_from.source_uri == "https://hl7.org/fhir/R4/"
        # Hash round-trip cleanly.
        assert r2.receipt_hash == r.receipt_hash

    def test_legacy_receipt_without_derives_from_still_loads(self):
        """A receipt serialized before Extension C landed has no
        ``derives_from`` key on its active_packs entries. The loader
        must tolerate that."""
        legacy_dict = {
            "receipt_version": "0.1.0",
            "kernel_version": "bulla-test",
            "composition_hash": "0" * 64,
            "diagnostic_hash": "0" * 64,
            "policy_profile": DEFAULT_POLICY_PROFILE.to_dict(),
            "fee": 0,
            "blind_spots_count": 0,
            "bridges_required": 0,
            "unknown_dimensions": 0,
            "disposition": "proceed",
            "timestamp": "2025-01-01T00:00:00Z",
            "patches": [],
            "active_packs": [
                {"name": "base", "version": "0.1.0", "hash": "abc"}
                # ← no derives_from key
            ],
            "witness_basis": None,
        }
        r = receipt_from_dict(legacy_dict)
        assert r.active_packs[0].derives_from is None

    def test_multi_pack_carries_per_standard_provenance(self):
        """A receipt with two active packs carries provenance per pack
        — the design that motivated putting ``derives_from`` on
        PackRef rather than on the receipt."""
        refs = (
            PackRef(
                name="iso-4217", version="0.1.0", hash="a" * 64,
                derives_from=StandardProvenance(
                    standard="ISO-4217", version="2024",
                ),
            ),
            PackRef(
                name="fhir-r4", version="0.1.0", hash="b" * 64,
                derives_from=StandardProvenance(
                    standard="FHIR", version="R4",
                ),
            ),
        )
        r = _minimal_receipt(active_packs=refs)
        r2 = receipt_from_dict(json.loads(json.dumps(r.to_dict())))
        standards = [
            ref.derives_from.standard
            for ref in r2.active_packs
            if ref.derives_from is not None
        ]
        assert standards == ["ISO-4217", "FHIR"]
