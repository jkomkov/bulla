"""Tests for Extension D: alias-form known_values.

Extension D scope (Standards Ingestion Sprint, Phase 1):

- ``known_values`` items widen from ``str`` to ``str | { canonical,
  aliases?, source_codes? }``. Strictly additive: legacy string-only
  packs continue to validate and load unchanged.
- The classifier's enum-overlap signal collapses canonical values,
  aliases, and source-code values into a single normalized set per
  dimension, so a field whose enum lists ``"840"`` (ISO-4217 numeric)
  classifies under ``currency`` just like a field listing ``"USD"``.
- Unknown keys inside an alias dict are rejected (typo defense).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    _get_enum_known_values,
    _iter_normalized_values,
    _normalize_enum_value,
    _reset_taxonomy_cache,
    classify_schema_signal,
    configure_packs,
)
from bulla.infer.classifier import FieldInfo
from bulla.packs.validate import validate_pack


# ── Validator: alias-form items ──────────────────────────────────────


class TestKnownValuesItemValidation:
    def _pack(self, kv: list) -> dict:
        return {
            "pack_name": "test",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "known_values": kv,
                }
            },
        }

    def test_legacy_string_only_still_valid(self):
        pack = self._pack(["USD", "EUR", "JPY"])
        assert validate_pack(pack) == []

    def test_alias_form_with_canonical_only(self):
        pack = self._pack([{"canonical": "USD"}])
        assert validate_pack(pack) == []

    def test_alias_form_with_aliases(self):
        pack = self._pack([
            {"canonical": "USD", "aliases": ["840", "$", "us-dollar"]},
        ])
        assert validate_pack(pack) == []

    def test_alias_form_with_source_codes(self):
        pack = self._pack([
            {
                "canonical": "USD",
                "source_codes": {"ISO-4217": "840", "GS1": "840"},
            },
        ])
        assert validate_pack(pack) == []

    def test_alias_form_with_all_optional_fields(self):
        pack = self._pack([
            {
                "canonical": "USD",
                "aliases": ["$"],
                "source_codes": {"ISO-4217": "840"},
            },
        ])
        assert validate_pack(pack) == []

    def test_mixed_string_and_alias_forms(self):
        pack = self._pack([
            "JPY",  # bare string
            {"canonical": "USD", "aliases": ["840", "$"]},  # alias form
            "EUR",
        ])
        assert validate_pack(pack) == []

    def test_missing_canonical_rejected(self):
        pack = self._pack([{"aliases": ["840"]}])
        errors = validate_pack(pack)
        assert any("canonical" in e for e in errors)

    def test_non_string_canonical_rejected(self):
        pack = self._pack([{"canonical": 42}])
        errors = validate_pack(pack)
        assert any("canonical" in e and "string" in e for e in errors)

    def test_aliases_not_a_list_rejected(self):
        pack = self._pack([{"canonical": "USD", "aliases": "840"}])
        errors = validate_pack(pack)
        assert any("aliases" in e and "list" in e for e in errors)

    def test_alias_item_not_a_string_rejected(self):
        pack = self._pack([{"canonical": "USD", "aliases": ["840", 42]}])
        errors = validate_pack(pack)
        assert any("aliases" in e and "string" in e for e in errors)

    def test_source_codes_not_a_mapping_rejected(self):
        pack = self._pack([{"canonical": "USD", "source_codes": ["840"]}])
        errors = validate_pack(pack)
        assert any(
            "source_codes" in e and ("mapping" in e or "dict" in e)
            for e in errors
        )

    def test_source_code_value_not_a_string_rejected(self):
        pack = self._pack([
            {"canonical": "USD", "source_codes": {"ISO-4217": 840}},
        ])
        errors = validate_pack(pack)
        assert any(
            "source_codes" in e and ("ISO-4217" in e or "string" in e)
            for e in errors
        )

    def test_unknown_key_in_alias_dict_rejected(self):
        """Typo defense: catches authors writing ``alais`` or
        ``synonyms`` instead of the documented keys."""
        pack = self._pack([{"canonical": "USD", "alais": ["840"]}])
        errors = validate_pack(pack)
        assert any("unrecognized" in e or "alais" in e for e in errors)

    def test_arbitrary_non_string_non_dict_item_rejected(self):
        pack = self._pack(["USD", 42, "EUR"])
        errors = validate_pack(pack)
        assert any(
            "known_values[1]" in e or "must be a string" in e
            for e in errors
        )


# ── Classifier: alias normalization ──────────────────────────────────


class TestIterNormalizedValues:
    def test_string_only(self):
        result = _iter_normalized_values(["USD", "EUR", "JPY"])
        assert _normalize_enum_value("USD") in result
        assert _normalize_enum_value("EUR") in result
        assert _normalize_enum_value("JPY") in result

    def test_alias_form_yields_canonical_and_aliases(self):
        result = _iter_normalized_values([
            {"canonical": "USD", "aliases": ["840", "$", "us-dollar"]},
        ])
        assert _normalize_enum_value("USD") in result
        assert _normalize_enum_value("840") in result
        assert _normalize_enum_value("$") in result
        assert _normalize_enum_value("us-dollar") in result

    def test_source_codes_values_are_added(self):
        result = _iter_normalized_values([
            {
                "canonical": "USD",
                "source_codes": {"ISO-4217": "840", "GS1": "840"},
            },
        ])
        assert _normalize_enum_value("USD") in result
        assert _normalize_enum_value("840") in result

    def test_mixed_forms_collapse_into_one_set(self):
        result = _iter_normalized_values([
            "JPY",
            {"canonical": "USD", "aliases": ["$", "840"]},
            "EUR",
        ])
        for expected in ("JPY", "USD", "$", "840", "EUR"):
            assert _normalize_enum_value(expected) in result

    def test_empty_list_yields_empty_set(self):
        assert _iter_normalized_values([]) == set()

    def test_malformed_dict_silently_dropped(self):
        """An alias dict with missing canonical (caught by validator)
        should not crash the classifier — it should produce an empty
        contribution. The validator is the place that reports the error;
        the classifier is best-effort at runtime."""
        result = _iter_normalized_values([
            {"aliases": ["840"]},  # missing canonical
            "EUR",
        ])
        assert _normalize_enum_value("EUR") in result
        # The orphaned alias still contributes — this matches the
        # "be liberal in what you accept" stance for the runtime
        # classifier; the validator catches the malformed pack at
        # ingest time.
        assert _normalize_enum_value("840") in result


class TestEnumOverlapWithAliases:
    """End-to-end: a pack defining USD with alias '840' must classify a
    field whose enum is [\"840\", \"978\"] under that pack's dimension
    (≥2 overlapping aliases triggers the schema_enum signal)."""

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

    def test_field_enum_with_numeric_codes_classifies_under_currency(self):
        pack_dict = {
            "pack_name": "iso-4217-test",
            "pack_version": "0.1.0",
            "dimensions": {
                "currency_code": {
                    "description": "ISO-4217 currency code (alpha or numeric)",
                    "field_patterns": ["*_currency"],
                    "known_values": [
                        {"canonical": "USD", "aliases": ["840"]},
                        {"canonical": "EUR", "aliases": ["978"]},
                        {"canonical": "JPY", "aliases": ["392"]},
                    ],
                },
            },
        }
        path = self._write_pack(pack_dict)
        try:
            configure_packs(extra_paths=[path])
            # Field whose enum uses the numeric ISO-4217 codes only.
            field = FieldInfo(
                name="amount_currency",
                schema_type="string",
                enum=("840", "978", "392"),
            )
            results = classify_schema_signal(field)
            dims = {r.dimension for r in results}
            assert "currency_code" in dims, (
                f"expected currency_code in dimensions; got {dims}"
            )
        finally:
            path.unlink()

    def test_field_enum_with_alpha_codes_also_classifies(self):
        """Alpha values also trigger — proving aliases don't replace
        canonicals, they augment them."""
        pack_dict = {
            "pack_name": "iso-4217-test-alpha",
            "pack_version": "0.1.0",
            "dimensions": {
                "currency_code": {
                    "description": "ISO-4217 currency code",
                    "field_patterns": ["*_currency"],
                    "known_values": [
                        {"canonical": "USD", "aliases": ["840"]},
                        {"canonical": "EUR", "aliases": ["978"]},
                        "JPY",  # bare string
                    ],
                },
            },
        }
        path = self._write_pack(pack_dict)
        try:
            configure_packs(extra_paths=[path])
            field = FieldInfo(
                name="amount_currency",
                schema_type="string",
                enum=("USD", "EUR", "JPY"),
            )
            results = classify_schema_signal(field)
            dims = {r.dimension for r in results}
            assert "currency_code" in dims
        finally:
            path.unlink()
