"""Tests for ``bulla.translate`` runtime and the five canonical bridges.

Covers:

  - All five canonical translators round-trip cleanly.
  - Registry behavior: registration, replacement, listing.
  - Auto ``from_convention`` discovery when not supplied.
  - Mapping-derived path through Extension E ``mappings:`` blocks.
  - Restricted-pack invariant: licensed values are never surfaced raw.
  - ``TranslationUnavailable`` carries structured metadata.
  - Receipt is a real ``WitnessReceipt`` and chains via
    ``parent_receipt_hashes``.
"""

from __future__ import annotations

import json

import pytest

from bulla.bridges import (
    TranslationEvidence,
    TranslationResult,
    TranslationUnavailable,
    register,
    registered_pairs,
    translate,
)
from bulla.model import Disposition, WitnessReceipt


# ── Five canonical bridges ───────────────────────────────────────────


class TestCurrencyCodeBridge:
    def test_usd_to_stripe_lower(self):
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        assert r.value == "usd"
        assert r.evidence.equivalence == "exact"
        assert r.evidence.source == "registry"

    def test_stripe_lower_to_iso(self):
        r = translate(
            "currency_code",
            value="usd",
            to_convention="iso-4217",
            from_convention="stripe-lower",
        )
        assert r.value == "USD"
        assert r.evidence.equivalence == "exact"

    def test_alpha_to_numeric(self):
        r = translate(
            "currency_code",
            value="EUR",
            to_convention="iso-4217-numeric",
            from_convention="iso-4217",
        )
        assert r.value == "978"
        assert r.evidence.equivalence == "exact"

    def test_numeric_to_alpha(self):
        r = translate(
            "currency_code",
            value="392",
            to_convention="iso-4217",
            from_convention="iso-4217-numeric",
        )
        assert r.value == "JPY"

    def test_unknown_currency_raises(self):
        with pytest.raises(TranslationUnavailable) as exc:
            translate(
                "currency_code",
                value="ZZZ",
                to_convention="iso-4217-numeric",
                from_convention="iso-4217",
            )
        assert "ZZZ" in exc.value.suggestion or "20-currency" in exc.value.suggestion


class TestCountryCodeBridge:
    @pytest.mark.parametrize(
        "value,from_c,to_c,expected",
        [
            ("US", "iso-3166-alpha2", "iso-3166-alpha3", "USA"),
            ("USA", "iso-3166-alpha3", "iso-3166-alpha2", "US"),
            ("US", "iso-3166-alpha2", "iso-3166-numeric", "840"),
            ("840", "iso-3166-numeric", "iso-3166-alpha2", "US"),
            ("DE", "iso-3166-alpha2", "iso-3166-alpha3", "DEU"),
            ("DEU", "iso-3166-alpha3", "iso-3166-alpha2", "DE"),
        ],
    )
    def test_country_pairs(self, value, from_c, to_c, expected):
        r = translate(
            "country_code",
            value=value,
            to_convention=to_c,
            from_convention=from_c,
        )
        assert r.value == expected
        assert r.evidence.equivalence == "exact"

    def test_unknown_country_raises(self):
        with pytest.raises(TranslationUnavailable):
            translate(
                "country_code",
                value="ZZ",
                to_convention="iso-3166-alpha3",
                from_convention="iso-3166-alpha2",
            )


class TestLanguageCodeBridge:
    @pytest.mark.parametrize(
        "value,from_c,to_c,expected",
        [
            ("en", "iso-639-1", "iso-639-3", "eng"),
            ("eng", "iso-639-3", "iso-639-1", "en"),
            ("ja", "iso-639-1", "iso-639-3", "jpn"),
            ("zho", "iso-639-3", "iso-639-1", "zh"),
        ],
    )
    def test_iso_639_pairs(self, value, from_c, to_c, expected):
        r = translate(
            "language_code",
            value=value,
            to_convention=to_c,
            from_convention=from_c,
        )
        assert r.value == expected

    def test_bcp47_normalization_drops_region(self):
        r = translate(
            "language_code",
            value="en-US",
            to_convention="iso-639-1",
            from_convention="bcp-47",
        )
        assert r.value == "en"
        assert r.evidence.equivalence == "lossy_forward"

    def test_invalid_bcp47_raises(self):
        with pytest.raises(TranslationUnavailable):
            translate(
                "language_code",
                value="not-a-valid-tag-xyz123",
                to_convention="iso-639-1",
                from_convention="bcp-47",
            )


class TestTemporalFormatBridge:
    def test_iso_to_unix_seconds(self):
        # 2026-05-02T12:00:00Z = 1777723200 seconds
        r = translate(
            "temporal_format",
            value="2026-05-02T12:00:00Z",
            to_convention="unix-seconds",
            from_convention="iso-8601",
        )
        assert int(r.value) == 1777723200

    def test_unix_seconds_to_iso_round_trip(self):
        r = translate(
            "temporal_format",
            value="1777723200",
            to_convention="iso-8601",
            from_convention="unix-seconds",
        )
        assert r.value == "2026-05-02T12:00:00Z"
        assert r.evidence.equivalence == "exact"

    def test_iso_to_unix_millis(self):
        r = translate(
            "temporal_format",
            value="2026-05-02T12:00:00.123Z",
            to_convention="unix-millis",
            from_convention="iso-8601",
        )
        assert r.value == "1777723200123"

    def test_unparseable_iso_raises(self):
        with pytest.raises(TranslationUnavailable):
            translate(
                "temporal_format",
                value="not a date",
                to_convention="unix-seconds",
                from_convention="iso-8601",
            )


class TestFhirResourceTypeBridge:
    def test_renamed_resource_r4_to_r5(self):
        r = translate(
            "fhir_resource_type",
            value="ImagingManifest",
            to_convention="fhir-r5",
            from_convention="fhir-r4",
        )
        assert r.value == "ImagingSelection"
        assert r.evidence.equivalence == "lossy_bidirectional"

    def test_renamed_resource_r5_to_r4(self):
        r = translate(
            "fhir_resource_type",
            value="ImagingSelection",
            to_convention="fhir-r4",
            from_convention="fhir-r5",
        )
        assert r.value == "ImagingManifest"

    def test_stable_resource_passes_through(self):
        # Most resource types are unchanged R4 → R5
        r = translate(
            "fhir_resource_type",
            value="Patient",
            to_convention="fhir-r5",
            from_convention="fhir-r4",
        )
        assert r.value == "Patient"
        assert r.evidence.equivalence == "exact"


# ── Registry mechanics ───────────────────────────────────────────────


class TestRegistry:
    def test_registry_has_at_least_18_pairs(self):
        # 4 currency + 4 country + 4 language + 4 temporal + 2 fhir = 18
        pairs = registered_pairs()
        assert len(pairs) >= 18

    def test_register_decorator_is_callable(self):
        @register("test_dim_unique", "from_X", "to_Y")
        def _t(v: str) -> tuple[str, str]:
            return v.upper(), "exact"

        r = translate(
            "test_dim_unique",
            value="hello",
            to_convention="to_Y",
            from_convention="from_X",
        )
        assert r.value == "HELLO"

    def test_registry_replacement_keeps_latest(self):
        @register("test_dim_replace", "a", "b")
        def _v1(v: str) -> tuple[str, str]:
            return "v1", "exact"

        @register("test_dim_replace", "a", "b")
        def _v2(v: str) -> tuple[str, str]:
            return "v2", "exact"

        r = translate(
            "test_dim_replace", value="x", to_convention="b", from_convention="a"
        )
        assert r.value == "v2"


# ── Auto-discovery (no from_convention) ──────────────────────────────


class TestAutoFromConvention:
    def test_auto_finds_only_match(self):
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
        )
        assert r.value == "usd"
        assert r.evidence.from_convention == "iso-4217"

    def test_auto_propagates_failure_to_unavailable(self):
        with pytest.raises(TranslationUnavailable):
            translate(
                "currency_code",
                value="ZZZ",
                to_convention="iso-4217-numeric",  # no auto match for ZZZ
            )


# ── Receipt shape and chaining ───────────────────────────────────────


class TestReceiptShape:
    def test_receipt_is_witness_receipt(self):
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        assert isinstance(r.receipt, WitnessReceipt)
        assert r.receipt.disposition == Disposition.PROCEED
        assert r.receipt.fee == 0
        assert r.receipt.blind_spots_count == 0

    def test_receipt_inline_dimensions_are_load_bearing(self):
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        d = r.receipt.inline_dimensions
        assert d is not None
        assert d["kind"] == "translate"
        assert d["dimension"] == "currency_code"
        assert d["from_convention"] == "iso-4217"
        assert d["to_convention"] == "stripe-lower"
        assert d["value_in"] == "USD"
        assert d["value_out"] == "usd"
        assert d["equivalence"] == "exact"

    def test_two_independent_calls_have_distinct_composition_hashes(self):
        r1 = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        r2 = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        # Same logical translation, but different sessions → different hashes
        assert r1.receipt.composition_hash != r2.receipt.composition_hash
        assert r1.receipt.receipt_hash != r2.receipt.receipt_hash

    def test_receipt_round_trips_through_to_dict(self):
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        # Round trip via JSON to confirm serializable.
        serialized = json.dumps(r.receipt.to_dict(), sort_keys=True)
        loaded = json.loads(serialized)
        assert loaded["fee"] == 0
        assert loaded["disposition"] == "proceed"
        assert loaded["inline_dimensions"]["kind"] == "translate"

    def test_parent_receipt_hashes_threads_through(self):
        parent_hashes = ("parent_receipt_hash_abc",)
        r = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
            parent_receipt_hashes=parent_hashes,
        )
        assert r.receipt.parent_receipt_hashes == parent_hashes

    def test_session_id_makes_composition_hash_deterministic(self):
        r1 = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
            session_id="fixed-session-1",
        )
        r2 = translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
            session_id="fixed-session-1",
        )
        # Same session_id + same payload → bitwise identical composition hash
        assert r1.receipt.composition_hash == r2.receipt.composition_hash


# ── Mapping-derived path ─────────────────────────────────────────────


class TestMappingDerivedPath:
    """When no hand-written translator covers (dim, from, to), the
    runtime falls back to walking Extension E ``mappings:`` blocks on
    supplied or active packs."""

    def _icd_pack_with_mapping(self) -> dict:
        # Mirror the icd-10-cm.yaml seed-pack mappings: block.
        return {
            "pack_name": "icd-10-cm",
            "pack_version": "0.1.1",
            "license": {
                "spdx_id": "Public-Domain",
                "registry_license": "open",
            },
            "dimensions": {
                "icd_10_cm_code": {
                    "description": "ICD-10-CM",
                    "field_patterns": ["*_icd"],
                },
            },
            "mappings": {
                "icd-9-cm": {
                    "icd_9_cm_code": [
                        {"from": "001.0", "to": "A00.0", "equivalence": "exact"},
                        {"from": "250.00", "to": "E11.9", "equivalence": "exact"},
                    ],
                },
            },
        }

    def test_extension_e_walker_resolves_translation(self):
        # ICD mapping rows are in `from`/`to` direction:
        # from=icd-9 (e.g. "001.0") to=icd-10 ("A00.0"). Our pack lives
        # on the icd-10-cm pack but its mappings: block targets
        # "icd-9-cm" / "icd_9_cm_code" — i.e. the rows let us
        # translate icd-10 codes back to icd-9 codes (reverse) or
        # icd-9 codes to icd-10 codes (forward).
        # Convention here: ``to_convention=icd-9-cm`` reads the pack's
        # mappings block and walks forward from the listed `from` field.
        # The runtime's forward direction means: input value matches
        # `from`, output is `to`.
        r = translate(
            "icd_9_cm_code",
            value="001.0",
            to_convention="icd-9-cm",
            extra_packs=[self._icd_pack_with_mapping()],
        )
        assert r.value == "A00.0"
        assert r.evidence.source == "mappings"
        assert r.evidence.equivalence == "exact"
        assert r.evidence.from_convention == "icd-10-cm"

    def test_unmatched_value_falls_through_to_unavailable(self):
        with pytest.raises(TranslationUnavailable):
            translate(
                "icd_9_cm_code",
                value="999.99",  # not in our 2-row mapping
                to_convention="icd-9-cm",
                extra_packs=[self._icd_pack_with_mapping()],
            )


# ── Restricted-pack invariant ────────────────────────────────────────


class TestRestrictedPackInvariant:
    """Mapping-derived translations through restricted/research-only
    packs MUST NOT surface raw values. Instead, the runtime raises
    TranslationUnavailable with license_required set, so the caller
    knows what credential to obtain."""

    def _restricted_pack_with_mapping(self) -> dict:
        return {
            "pack_name": "umls-mappings",
            "pack_version": "0.1.0",
            "license": {
                "spdx_id": "NLM-UMLS-License",
                "registry_license": "restricted",
            },
            "dimensions": {
                "umls_concept_id": {
                    "description": "UMLS CUI",
                    "field_patterns": ["*_cui"],
                },
            },
            "mappings": {
                "snomed-ct": {
                    "snomed_ct_code": [
                        {"from": "CUI001", "to": "12345", "equivalence": "exact"},
                    ],
                },
            },
        }

    def test_restricted_pack_blocks_translation(self):
        with pytest.raises(TranslationUnavailable) as exc:
            translate(
                "snomed_ct_code",
                value="CUI001",
                to_convention="snomed-ct",
                extra_packs=[self._restricted_pack_with_mapping()],
            )
        # The caller should be told a license is required.
        assert exc.value.license_required == "umls-mappings"

    def test_restricted_then_open_open_wins(self):
        """If both a restricted AND an open pack resolve the same
        translation, the open one wins — restricted is recorded as a
        license-required option but the translation succeeds."""
        open_pack = {
            "pack_name": "snomed-open-overlay",
            "license": {
                "spdx_id": "CC-BY",
                "registry_license": "open",
            },
            "dimensions": {
                "umls_concept_id": {
                    "description": "Open overlay",
                    "field_patterns": ["*"],
                },
            },
            "mappings": {
                "snomed-ct": {
                    "snomed_ct_code": [
                        {"from": "CUI001", "to": "12345-open", "equivalence": "exact"},
                    ],
                },
            },
        }
        # Order matters: restricted scanned first, open second.
        r = translate(
            "snomed_ct_code",
            value="CUI001",
            to_convention="snomed-ct",
            extra_packs=[
                self._restricted_pack_with_mapping(),
                open_pack,
            ],
        )
        assert r.value == "12345-open"
        assert r.evidence.is_redacted is False


# ── TranslationUnavailable structure ─────────────────────────────────


class TestTranslationUnavailable:
    def test_carries_dimension_from_to(self):
        with pytest.raises(TranslationUnavailable) as exc:
            translate(
                "currency_code",
                value="x",
                to_convention="quantum-ledger",
                from_convention="iso-4217",
            )
        e = exc.value
        assert e.dimension == "currency_code"
        assert e.from_convention == "iso-4217"
        assert e.to_convention == "quantum-ledger"

    def test_suggestion_lists_known_pairs(self):
        with pytest.raises(TranslationUnavailable) as exc:
            translate(
                "currency_code",
                value="x",
                to_convention="quantum-ledger",
                from_convention="iso-4217",
            )
        # The suggestion lists known conventions for currency_code.
        assert "iso-4217" in exc.value.suggestion

    def test_unknown_dimension_says_so(self):
        with pytest.raises(TranslationUnavailable) as exc:
            translate(
                "completely_unknown_dimension_xyz",
                value="x",
                to_convention="any",
            )
        assert "no translators registered" in exc.value.suggestion or \
            "completely_unknown_dimension_xyz" in exc.value.suggestion


# ── Naming discipline (the post-feedback rename guard) ───────────────


class TestNamingDiscipline:
    """The diagnostic-side ``Bridge`` dataclass and the runtime
    ``translate`` function are intentionally separate verbs for
    separate operations. Pin the import surface so a future refactor
    can't silently re-introduce the case-collision."""

    def test_bulla_translate_is_the_function(self):
        import bulla
        # Top-level export.
        assert callable(bulla.translate)

    def test_bulla_bridge_is_not_exported_as_function(self):
        import bulla
        # No lowercase `bridge` function — the verb is `translate`.
        assert not (
            hasattr(bulla, "bridge") and callable(getattr(bulla, "bridge"))
            and not isinstance(getattr(bulla, "bridge"), type)
        )

    def test_bulla_Bridge_class_still_exists(self):
        import bulla
        # The diagnostic-side dataclass survives unchanged.
        assert isinstance(bulla.Bridge, type)
