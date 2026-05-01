"""End-to-end integration tests for Tier A seed packs (Phase 2B–2F).

Covers:
  - iso-8601.yaml (date/time formats)
  - iso-3166.yaml (country codes; alpha-2 / alpha-3 / numeric aliases)
  - iso-639.yaml  (language codes; alpha-2 / alpha-3 aliases)
  - iana-media-types.yaml (MIME types — values_registry + inline seed)
  - naics-2022.yaml (industry codes — values_registry + sector seed)

Each pack is verified for: schema validity, load via load_pack_stack,
provenance attachment, and at least one positive classification case
exercising the pack's intended dimension. Hash stability is checked
in a single bulk test that loads every Tier A pack twice.

The ISO 4217 pack has its own dedicated test file
(test_seed_iso_4217.py) because it was the first ingest and got
correspondingly more attention.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    FieldInfo,
    _reset_taxonomy_cache,
    classify_schema_signal,
    configure_packs,
    get_active_pack_refs,
)
from bulla.packs.validate import validate_pack
from bulla.packs.verify import inspect_registries


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


# ── Per-pack: validation + provenance ────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected_pack_name,expected_standard",
    [
        ("iso-8601.yaml",          "iso-8601",         "ISO-8601 / RFC-3339"),
        ("iso-3166.yaml",          "iso-3166",         "ISO-3166-1"),
        ("iso-639.yaml",           "iso-639",          "ISO-639-3"),
        ("iana-media-types.yaml",  "iana-media-types", "IANA-Media-Types"),
        ("naics-2022.yaml",        "naics-2022",       "NAICS"),
    ],
)
def test_tier_a_pack_validates_and_carries_provenance(
    filename: str, expected_pack_name: str, expected_standard: str
):
    path = _seed_dir() / filename
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert validate_pack(parsed) == [], (
        f"{filename} failed validation"
    )
    assert parsed["pack_name"] == expected_pack_name
    assert parsed["derives_from"]["standard"] == expected_standard


# ── ISO 8601: date_format-related classification ─────────────────────


class TestIso8601Pack:
    def test_field_with_iso_format_classifies(self):
        path = _seed_dir() / "iso-8601.yaml"
        configure_packs(extra_paths=[path])
        # The pack defines the temporal_format dimension which refines
        # the base pack's date_format.  A field with format=date-time
        # should pick up the base date_format signal; a field whose
        # description mentions "iso 8601" should pick up the
        # temporal_format keyword signal.
        field = FieldInfo(
            name="created_at",
            schema_type="string",
            format="date-time",
            description="ISO 8601 timestamp with timezone",
        )
        results = classify_schema_signal(field)
        dims = {r.dimension for r in results}
        # base pack already classifies format=date-time as date_format;
        # confirm that signal still fires with the new pack loaded.
        assert "date_format" in dims or "temporal_format" in dims

    def test_known_values_include_canonical_iso_forms(self):
        path = _seed_dir() / "iso-8601.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        kv = parsed["dimensions"]["temporal_format"]["known_values"]
        canonicals = {item["canonical"] for item in kv if isinstance(item, dict)}
        for required in {
            "iso-8601-datetime",
            "iso-8601-date",
            "unix-epoch-seconds",
            "unix-epoch-millis",
        }:
            assert required in canonicals


# ── ISO 3166: country code classification with alias forms ───────────


class TestIso3166Pack:
    def test_pack_has_at_least_240_countries(self):
        path = _seed_dir() / "iso-3166.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        kv = parsed["dimensions"]["country_code"]["known_values"]
        assert len(kv) >= 240

    def test_alpha2_enum_classifies(self):
        path = _seed_dir() / "iso-3166.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="billing_country",
            schema_type="string",
            enum=("US", "GB", "FR", "DE"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "country_code" in dims

    def test_alpha3_enum_classifies_via_aliases(self):
        path = _seed_dir() / "iso-3166.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="country_iso3",
            schema_type="string",
            enum=("USA", "GBR", "FRA", "DEU"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "country_code" in dims

    def test_numeric_enum_classifies_via_aliases(self):
        path = _seed_dir() / "iso-3166.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="country_id",
            schema_type="string",
            enum=("840", "826", "250", "276"),  # US, GB, FR, DE numeric
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "country_code" in dims


# ── ISO 639: language code classification with aliases ───────────────


class TestIso639Pack:
    def test_pack_uses_values_registry_for_full_corpus(self):
        """v0.2.0+: ISO 639 ships a curated inline seed (~35 most-
        spoken languages) plus a values_registry pointer to the
        authoritative SIL ~7700-entry table. The previous all-inline
        form was 656 KB — the exact scale problem Extension B's
        canonicalization rule was designed to prevent."""
        path = _seed_dir() / "iso-639.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        dim = parsed["dimensions"]["language_code"]
        # Inline seed bounded but non-trivial; expanded to ~50 to
        # cover the languages real software products typically
        # localize into.
        kv = dim["known_values"]
        assert 30 <= len(kv) <= 80, (
            f"inline seed should be a curated top-N, got {len(kv)} entries"
        )
        # values_registry pointer present and points at SIL.
        assert "values_registry" in dim
        assert "sil.org" in dim["values_registry"]["uri"]

    def test_pack_file_size_under_50kb(self):
        """Architectural-consistency check: the on-disk pack file
        stays small now that it's seed + registry pointer rather
        than 7700 inline rows."""
        path = _seed_dir() / "iso-639.yaml"
        size = path.stat().st_size
        assert size < 50_000, (
            f"iso-639.yaml is {size} bytes; should be <50KB after "
            f"the values_registry migration"
        )

    def test_alpha2_enum_classifies(self):
        path = _seed_dir() / "iso-639.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="preferred_language",
            schema_type="string",
            enum=("en", "fr", "ja", "de"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "language_code" in dims

    def test_alpha3_enum_classifies_via_aliases(self):
        path = _seed_dir() / "iso-639.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="lang_code",
            schema_type="string",
            enum=("eng", "fra", "jpn", "deu"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "language_code" in dims


# ── IANA MIME: values_registry + inline seed ─────────────────────────


class TestIanaMimePack:
    def test_pack_has_values_registry(self):
        path = _seed_dir() / "iana-media-types.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        refs = inspect_registries(parsed)
        assert len(refs) == 1
        assert refs[0].dimension == "media_type"
        assert refs[0].uri.startswith("https://www.iana.org/")

    def test_inline_seed_classifies(self):
        path = _seed_dir() / "iana-media-types.yaml"
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="content_type",
            schema_type="string",
            enum=(
                "application/json",
                "text/html",
                "image/png",
            ),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "media_type" in dims

    def test_inline_documentation_does_not_drift_pack_hash(self):
        """Per Extension B canonicalization: when a dimension has
        values_registry, the inline known_values are stripped from the
        pack hash. Verify by mutating the inline list and checking
        the loaded PackRef.hash is unchanged."""
        from bulla.infer.classifier import _hash_pack
        path = _seed_dir() / "iana-media-types.yaml"
        original = yaml.safe_load(path.read_text(encoding="utf-8"))
        original_hash = _hash_pack(original)

        # Mutate the inline list — add a documentation example.
        mutated = yaml.safe_load(path.read_text(encoding="utf-8"))
        kv = mutated["dimensions"]["media_type"]["known_values"]
        kv.append("application/x-future-media-type")
        mutated_hash = _hash_pack(mutated)

        assert original_hash == mutated_hash, (
            "Inline known_values on a registry-backed dimension "
            "should be stripped from the pack hash"
        )


# ── NAICS: sector seed + values_registry ─────────────────────────────


class TestNaicsPack:
    def test_pack_has_20_sector_codes_and_registry(self):
        path = _seed_dir() / "naics-2022.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        kv = parsed["dimensions"]["industry_code"]["known_values"]
        assert len(kv) == 20  # 20 NAICS sectors
        refs = inspect_registries(parsed)
        assert len(refs) == 1
        assert refs[0].dimension == "industry_code"

    def test_naics_field_classifies(self):
        path = _seed_dir() / "naics-2022.yaml"
        configure_packs(extra_paths=[path])
        # A field literally named "naics" with sector-code values.
        field = FieldInfo(
            name="naics_code",
            schema_type="string",
            enum=("11", "23", "51", "62"),  # agriculture, construction, info, healthcare
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "industry_code" in dims


# ── Bulk: hash stability across all Tier A packs ─────────────────────


class TestTierAHashStability:
    """Loading every Tier A pack and reloading must produce identical
    PackRef hashes — pins the canonicalization invariant for the whole
    Tier A set in one go."""

    def test_all_tier_a_pack_hashes_stable(self):
        # Filter to the Tier A names rather than glob-everything in the
        # seed directory — Tier B and (later) Phase 4 packs land here
        # too, and this test scopes specifically to Tier A.
        seed_dir = _seed_dir()
        tier_a_names = {
            "iso-4217.yaml",
            "iso-8601.yaml",
            "iso-3166.yaml",
            "iso-639.yaml",
            "iana-media-types.yaml",
            "naics-2022.yaml",
        }
        paths = sorted(p for p in seed_dir.glob("*.yaml") if p.name in tier_a_names)
        assert len(paths) == 6, f"expected 6 Tier A packs, got {len(paths)}"

        _reset_taxonomy_cache()
        configure_packs(extra_paths=paths)
        first = {r.name: r.hash for r in get_active_pack_refs()}

        _reset_taxonomy_cache()
        configure_packs(extra_paths=paths)
        second = {r.name: r.hash for r in get_active_pack_refs()}

        assert first == second
