"""Tests for Extension E: passive mappings block + bulla.mappings.translate.

Extension E scope (Standards Ingestion Sprint, Phase 1):

- ``mappings: { target_pack: { target_dim: [{from, to, equivalence}] }}``
  block at pack level. Validator-only; not consumed by the measurement
  layer (the coboundary is value-blind).
- ``bulla.mappings.translate(value, from_pack=..., to_pack_name=...,
  to_dimension=...)`` walks a single pack's mappings block and returns
  the translated value(s) with their equivalence class.
- ``bulla.mappings.list_mappings(parsed_pack)`` summarizes a pack's
  mapping coverage as (target_pack, target_dim, row_count) triples.
- Invariant: loading a pack with mappings doesn't change ANY coboundary
  / fee output for any composition (mappings are passive data).
"""

from __future__ import annotations

import pytest

from bulla.mappings import TranslationResult, list_mappings, translate
from bulla.packs.validate import validate_pack


# ── Validator: mappings block ────────────────────────────────────────


class TestMappingsValidation:
    def _pack(self, mappings: object) -> dict:
        return {
            "pack_name": "test",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
            "mappings": mappings,
        }

    def test_pack_without_mappings_valid(self):
        pack = {
            "pack_name": "test",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        assert validate_pack(pack) == []

    def test_minimal_mappings_block(self):
        pack = self._pack({
            "iso-4217": {
                "currency_code": [
                    {"from": "USD", "to": "840", "equivalence": "exact"},
                    {"from": "EUR", "to": "978", "equivalence": "exact"},
                ],
            },
        })
        assert validate_pack(pack) == []

    def test_default_equivalence_omitted_is_valid(self):
        pack = self._pack({
            "iso-4217": {
                "currency_code": [
                    {"from": "USD", "to": "840"},  # no equivalence key
                ],
            },
        })
        assert validate_pack(pack) == []

    def test_lossy_forward_equivalence(self):
        pack = self._pack({
            "snomed": {
                "concept_id": [
                    {
                        "from": "ICD-10:A00",
                        "to": "SNOMED:63650001",
                        "equivalence": "lossy_forward",
                        "note": "ICD-10 A00 maps to multiple SNOMED concepts",
                    },
                ],
            },
        })
        assert validate_pack(pack) == []

    def test_invalid_equivalence_class_rejected(self):
        pack = self._pack({
            "x": {"y": [{"from": "a", "to": "b", "equivalence": "fuzzy"}]},
        })
        errors = validate_pack(pack)
        assert any(
            "equivalence" in e and "must be one of" in e
            for e in errors
        )

    def test_missing_from_rejected(self):
        pack = self._pack({"x": {"y": [{"to": "b"}]}})
        errors = validate_pack(pack)
        assert any("from" in e for e in errors)

    def test_missing_to_rejected(self):
        pack = self._pack({"x": {"y": [{"from": "a"}]}})
        errors = validate_pack(pack)
        assert any("to" in e for e in errors)

    def test_unknown_row_key_rejected(self):
        pack = self._pack({
            "x": {"y": [{"from": "a", "to": "b", "weight": 0.9}]},
        })
        errors = validate_pack(pack)
        assert any(
            "unrecognized" in e or "weight" in e
            for e in errors
        )

    def test_rows_must_be_a_list(self):
        pack = self._pack({"x": {"y": "not-a-list"}})
        errors = validate_pack(pack)
        assert any("must be a list" in e for e in errors)

    def test_target_dim_table_must_be_a_mapping(self):
        pack = self._pack({"x": "not-a-mapping"})
        errors = validate_pack(pack)
        assert any(
            "mapping" in e or "must be a mapping" in e
            for e in errors
        )

    def test_mappings_block_must_be_a_mapping(self):
        pack = self._pack("not-a-mapping")
        errors = validate_pack(pack)
        assert any("mappings" in e and "mapping" in e for e in errors)

    def test_non_string_from_rejected(self):
        pack = self._pack({"x": {"y": [{"from": 42, "to": "b"}]}})
        errors = validate_pack(pack)
        assert any("from" in e and "string" in e for e in errors)

    def test_note_field_must_be_string(self):
        pack = self._pack({
            "x": {"y": [{"from": "a", "to": "b", "note": ["wrong"]}]},
        })
        errors = validate_pack(pack)
        assert any("note" in e and "string" in e for e in errors)


# ── translate() ──────────────────────────────────────────────────────


class TestTranslate:
    def _currency_pack(self) -> dict:
        return {
            "pack_name": "iso-4217-alpha",
            "dimensions": {
                "currency_alpha": {
                    "description": "ISO-4217 alpha-3 currency code",
                    "field_patterns": ["*_currency"],
                    "known_values": ["USD", "EUR", "JPY"],
                },
            },
            "mappings": {
                "iso-4217-numeric": {
                    "currency_numeric": [
                        {"from": "USD", "to": "840", "equivalence": "exact"},
                        {"from": "EUR", "to": "978", "equivalence": "exact"},
                        {"from": "JPY", "to": "392", "equivalence": "exact"},
                    ],
                },
            },
        }

    def test_simple_forward_translation(self):
        pack = self._currency_pack()
        result = translate(
            "USD",
            from_pack=pack,
            to_pack_name="iso-4217-numeric",
            to_dimension="currency_numeric",
        )
        assert result.found
        assert result.values == ("840",)
        assert result.equivalence == "exact"

    def test_simple_reverse_translation(self):
        pack = self._currency_pack()
        result = translate(
            "840",
            from_pack=pack,
            to_pack_name="iso-4217-numeric",
            to_dimension="currency_numeric",
            direction="reverse",
        )
        assert result.found
        assert result.values == ("USD",)

    def test_unknown_source_value_returns_not_found(self):
        pack = self._currency_pack()
        result = translate(
            "XXX",
            from_pack=pack,
            to_pack_name="iso-4217-numeric",
            to_dimension="currency_numeric",
        )
        assert not result.found
        assert result.values == ()
        assert result.equivalence is None

    def test_unknown_target_pack_returns_not_found(self):
        pack = self._currency_pack()
        result = translate(
            "USD",
            from_pack=pack,
            to_pack_name="nonexistent",
            to_dimension="currency_numeric",
        )
        assert not result.found

    def test_unknown_target_dimension_returns_not_found(self):
        pack = self._currency_pack()
        result = translate(
            "USD",
            from_pack=pack,
            to_pack_name="iso-4217-numeric",
            to_dimension="nonexistent",
        )
        assert not result.found

    def test_pack_without_mappings_returns_not_found(self):
        pack = {
            "pack_name": "no_mappings",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        result = translate(
            "USD",
            from_pack=pack,
            to_pack_name="any",
            to_dimension="any",
        )
        assert not result.found

    def test_invalid_direction_raises(self):
        pack = self._currency_pack()
        with pytest.raises(ValueError):
            translate(
                "USD",
                from_pack=pack,
                to_pack_name="iso-4217-numeric",
                to_dimension="currency_numeric",
                direction="sideways",
            )

    def test_multiple_target_values_collapse_into_tuple(self):
        """If a source value maps to multiple targets (e.g. contextual
        ICD-10 → SNOMED with several candidate concepts), all targets
        survive in result.values."""
        pack = {
            "pack_name": "icd-10-to-snomed",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*"]}},
            "mappings": {
                "snomed": {
                    "concept_id": [
                        {
                            "from": "A00",
                            "to": "63650001",
                            "equivalence": "contextual",
                        },
                        {
                            "from": "A00",
                            "to": "63650002",
                            "equivalence": "contextual",
                        },
                    ],
                },
            },
        }
        result = translate(
            "A00",
            from_pack=pack,
            to_pack_name="snomed",
            to_dimension="concept_id",
        )
        assert result.found
        assert sorted(result.values) == ["63650001", "63650002"]

    def test_strongest_equivalence_wins_when_multiple_match(self):
        pack = {
            "pack_name": "p",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*"]}},
            "mappings": {
                "q": {
                    "r": [
                        {"from": "src", "to": "a", "equivalence": "lossy_forward"},
                        {"from": "src", "to": "b", "equivalence": "exact"},
                    ],
                },
            },
        }
        result = translate(
            "src", from_pack=pack, to_pack_name="q", to_dimension="r"
        )
        assert result.found
        assert result.equivalence == "exact"

    def test_note_propagates(self):
        pack = {
            "pack_name": "p",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*"]}},
            "mappings": {
                "q": {
                    "r": [
                        {
                            "from": "src",
                            "to": "tgt",
                            "equivalence": "lossy_forward",
                            "note": "ambiguous; verify clinical context",
                        },
                    ],
                },
            },
        }
        result = translate(
            "src", from_pack=pack, to_pack_name="q", to_dimension="r"
        )
        assert result.note == "ambiguous; verify clinical context"


# ── list_mappings() ──────────────────────────────────────────────────


class TestListMappings:
    def test_no_mappings_returns_empty(self):
        pack = {
            "pack_name": "p",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*"]}},
        }
        assert list_mappings(pack) == []

    def test_summarizes_mapping_coverage(self):
        pack = {
            "pack_name": "p",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*"]}},
            "mappings": {
                "iso-4217-numeric": {
                    "currency_numeric": [
                        {"from": "USD", "to": "840"},
                        {"from": "EUR", "to": "978"},
                        {"from": "JPY", "to": "392"},
                    ],
                },
                "snomed": {
                    "concept_id": [{"from": "A00", "to": "636"}],
                },
            },
        }
        summary = list_mappings(pack)
        assert ("iso-4217-numeric", "currency_numeric", 3) in summary
        assert ("snomed", "concept_id", 1) in summary
        assert len(summary) == 2


# ── Measurement-layer invariance (Extension E's load-bearing claim) ──


class TestMappingsAreValueBlind:
    """Loading a pack with a mappings block must NOT change any
    coboundary, fee, or H¹ output. Mappings are receipt-side data;
    the measurement layer ignores them. Document this by verifying
    that ``_hash_pack`` of an otherwise-identical pack with-and-without
    mappings produces DIFFERENT pack hashes (mappings are content)
    but the *measurement* output is unaffected (covered by the
    coboundary tests in test_diagnostic.py and test_invariants.py)."""

    def test_mappings_participate_in_pack_hash(self):
        from bulla.infer.classifier import _hash_pack
        without = {
            "pack_name": "p",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*"]},
            },
        }
        with_m = dict(without)
        with_m["mappings"] = {
            "q": {"r": [{"from": "a", "to": "b"}]},
        }
        # The mappings block is content; pack hash MUST differ.
        # (We deliberately do not strip it from the canonical hash —
        # it carries semantically meaningful translation rules.)
        assert _hash_pack(without) != _hash_pack(with_m)
