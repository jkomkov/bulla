"""End-to-end integration test for the ISO 4217 seed pack (Phase 2A).

Verifies that the pack at ``src/bulla/packs/seed/iso-4217.yaml``:
  1. Validates clean
  2. Loads via load_pack_stack
  3. Carries derives_from provenance on its PackRef
  4. Classifies a currency field whose enum is alpha-3 codes
  5. Classifies a currency field whose enum is numeric-only codes
     (this is the load-bearing Extension D demonstration: the pack
     declares aliases so downstream tools using either form are
     correctly classified as ``currency_code``)
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


def _seed_pack_path() -> Path:
    """Return absolute path to the ISO 4217 seed pack."""
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed" / "iso-4217.yaml"))


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


class TestIso4217PackOnDisk:
    def test_pack_file_exists(self):
        path = _seed_pack_path()
        assert path.exists(), f"ISO 4217 seed pack not found at {path}"

    def test_pack_validates(self):
        path = _seed_pack_path()
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = validate_pack(parsed)
        assert errors == [], f"validation errors: {errors}"

    def test_pack_has_currency_dimension(self):
        path = _seed_pack_path()
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "currency_code" in parsed["dimensions"]

    def test_pack_has_at_least_150_currencies(self):
        """Active ISO 4217 has ~178 currencies; allow margin for
        future revisions."""
        path = _seed_pack_path()
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        kv = parsed["dimensions"]["currency_code"]["known_values"]
        assert len(kv) >= 150


class TestIso4217PackLoading:
    def test_load_via_pack_stack(self):
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        refs = get_active_pack_refs()
        assert any(r.name == "iso-4217" for r in refs)

    def test_loaded_pack_carries_provenance(self):
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        refs = get_active_pack_refs()
        iso = next(r for r in refs if r.name == "iso-4217")
        assert iso.derives_from is not None
        assert iso.derives_from.standard == "ISO-4217"


class TestIso4217Classification:
    """Extension D's load-bearing demonstration: a field whose enum
    contains *only* numeric ISO 4217 codes must classify under the
    pack's ``currency_code`` dimension because the pack declares
    aliases linking each alpha-3 code to its numeric code."""

    def test_alpha3_enum_classifies(self):
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="amount_currency",
            schema_type="string",
            enum=("USD", "EUR", "JPY"),
        )
        results = classify_schema_signal(field)
        dims = {r.dimension for r in results}
        assert "currency_code" in dims, (
            f"expected currency_code in dimensions; got {dims}"
        )

    def test_numeric_enum_classifies_via_aliases(self):
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="ccy",
            schema_type="string",
            enum=("840", "978", "392"),  # USD, EUR, JPY numeric
        )
        results = classify_schema_signal(field)
        dims = {r.dimension for r in results}
        assert "currency_code" in dims, (
            f"numeric-code enum did not classify as currency_code; "
            f"got {dims}"
        )

    def test_mixed_alpha_and_numeric_enum_classifies(self):
        """A schema that lists both forms (defensive for backends that
        accept either) should also classify."""
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="currency",
            schema_type="string",
            enum=("USD", "840", "EUR", "978"),
        )
        results = classify_schema_signal(field)
        dims = {r.dimension for r in results}
        assert "currency_code" in dims

    def test_unrelated_enum_does_not_classify(self):
        path = _seed_pack_path()
        configure_packs(extra_paths=[path])
        field = FieldInfo(
            name="status",
            schema_type="string",
            enum=("ACTIVE", "PENDING", "CANCELLED"),
        )
        results = classify_schema_signal(field)
        dims = {r.dimension for r in results}
        assert "currency_code" not in dims


class TestIso4217PackHashStability:
    """The pack hash must be stable across two clean loads — proves
    the canonicalization step (Extension B) doesn't introduce drift."""

    def test_hash_stable_across_two_loads(self):
        path = _seed_pack_path()
        _reset_taxonomy_cache()
        configure_packs(extra_paths=[path])
        h1 = next(
            r.hash for r in get_active_pack_refs() if r.name == "iso-4217"
        )
        _reset_taxonomy_cache()
        configure_packs(extra_paths=[path])
        h2 = next(
            r.hash for r in get_active_pack_refs() if r.name == "iso-4217"
        )
        assert h1 == h2
