"""End-to-end integration tests for Tier B seed packs (Phase 3).

Covers:
  - ucum.yaml         (units of measure)
  - fix-4.4.yaml      (FIX 4.4 messaging)
  - fix-5.0.yaml      (FIX 5.0 SP2 messaging)
  - gs1.yaml          (GS1 General Specifications)
  - un-edifact.yaml   (UN/EDIFACT D.21B+)
  - fhir-r4.yaml      (FHIR R4 resource types)
  - fhir-r5.yaml      (FHIR R5 resource types + R4↔R5 mapping)
  - icd-10-cm.yaml    (ICD-10-CM diagnosis codes + ICD-9 GEMs mapping)

Each pack uses Extension B's ``values_registry`` pointer for the
authoritative content; the inline seed is documentation only. Tests
verify: validation, registry pointer presence, classifier signal on
inline seeds, and (where applicable) Extension E `mappings:` data.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    FieldInfo,
    _reset_taxonomy_cache,
    classify_description,
    classify_schema_signal,
    configure_packs,
    get_active_pack_refs,
)
from bulla.mappings import list_mappings, translate
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


# ── Per-pack: validation + registry presence ─────────────────────────


@pytest.mark.parametrize(
    "filename,expected_pack_name,expected_standard,expected_registries",
    [
        ("ucum.yaml",        "ucum",        "UCUM",                  1),
        ("fix-4.4.yaml",     "fix-4.4",     "FIX-4.4",               1),
        ("fix-5.0.yaml",     "fix-5.0",     "FIX-5.0",               1),
        ("gs1.yaml",         "gs1",         "GS1-General-Specifications", 1),
        ("un-edifact.yaml",  "un-edifact",  "UN-EDIFACT",            1),
        ("fhir-r4.yaml",     "fhir-r4",     "HL7-FHIR",              1),
        ("fhir-r5.yaml",     "fhir-r5",     "HL7-FHIR",              1),
        ("icd-10-cm.yaml",   "icd-10-cm",   "ICD-10-CM",             1),
    ],
)
def test_tier_b_pack_validates(
    filename: str,
    expected_pack_name: str,
    expected_standard: str,
    expected_registries: int,
):
    path = _seed_dir() / filename
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert validate_pack(parsed) == [], f"{filename} failed validation"
    assert parsed["pack_name"] == expected_pack_name
    assert parsed["derives_from"]["standard"] == expected_standard
    refs = inspect_registries(parsed)
    assert len(refs) >= expected_registries


# ── UCUM ─────────────────────────────────────────────────────────────


class TestUcumPack:
    def test_units_field_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "ucum.yaml"])
        field = FieldInfo(
            name="dose_unit",
            schema_type="string",
            enum=("mg", "g", "kg", "mcg"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "unit_of_measure" in dims

    def test_force_units_classify_via_aliases(self):
        """Mars Climate Orbiter dimension: Newton-second vs lbf-second
        both classify under ``unit_of_measure`` because the pack
        includes both canonical and alias forms."""
        configure_packs(extra_paths=[_seed_dir() / "ucum.yaml"])
        field = FieldInfo(
            name="impulse_unit",
            schema_type="string",
            enum=("N.s", "[lbf_av].s", "newton_second"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "unit_of_measure" in dims


# ── FIX ──────────────────────────────────────────────────────────────


class TestFixPacks:
    def test_fix_msgtype_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "fix-4.4.yaml"])
        field = FieldInfo(
            name="msg_type",
            schema_type="string",
            enum=("D", "8", "F", "G"),  # NewOrderSingle, Exec, Cancel, Replace
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "fix_msg_type" in dims

    def test_fix_side_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "fix-4.4.yaml"])
        field = FieldInfo(
            name="side",
            schema_type="string",
            enum=("1", "2", "5"),  # Buy, Sell, SellShort
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "fix_side" in dims

    def test_fix_4_and_5_can_coexist(self):
        configure_packs(extra_paths=[
            _seed_dir() / "fix-4.4.yaml",
            _seed_dir() / "fix-5.0.yaml",
        ])
        names = {r.name for r in get_active_pack_refs()}
        assert "fix-4.4" in names
        assert "fix-5.0" in names


# ── GS1 ──────────────────────────────────────────────────────────────


class TestGs1Pack:
    def test_gs1_ai_field_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "gs1.yaml"])
        field = FieldInfo(
            name="application_identifier",
            schema_type="string",
            enum=("01", "10", "17", "21"),  # GTIN, batch, expiry, serial
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "gs1_application_identifier" in dims

    def test_gs1_id_key_type_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "gs1.yaml"])
        field = FieldInfo(
            name="id_key_type",
            schema_type="string",
            enum=("GTIN", "GLN", "SSCC"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "gs1_id_key_type" in dims


# ── UN/EDIFACT ───────────────────────────────────────────────────────


class TestEdifactPack:
    def test_edifact_msg_type_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "un-edifact.yaml"])
        field = FieldInfo(
            name="message_type",
            schema_type="string",
            enum=("INVOIC", "ORDERS", "DESADV"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "edifact_message_type" in dims


# ── FHIR R4 / R5 ─────────────────────────────────────────────────────


class TestFhirPacks:
    def test_fhir_r4_resource_type_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "fhir-r4.yaml"])
        field = FieldInfo(
            name="resourceType",
            schema_type="string",
            enum=("Patient", "Observation", "Encounter"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "fhir_resource_type" in dims

    def test_fhir_r4_to_r5_mapping_resolves(self):
        """Extension E demonstration: R5 ImagingSelection ↔ R4
        ImagingManifest crosswalk."""
        path = _seed_dir() / "fhir-r5.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        result = translate(
            "ImagingSelection",
            from_pack=parsed,
            to_pack_name="fhir-r4",
            to_dimension="fhir_resource_type",
        )
        assert result.found
        assert "ImagingManifest" in result.values
        assert result.equivalence == "lossy_bidirectional"


# ── ICD-10-CM ────────────────────────────────────────────────────────


class TestIcd10CmPack:
    def test_icd_10_chapter_field_classifies(self):
        configure_packs(extra_paths=[_seed_dir() / "icd-10-cm.yaml"])
        field = FieldInfo(
            name="primary_diagnosis",
            schema_type="string",
            enum=("A00-B99", "I00-I99", "J00-J99"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "icd_10_cm_code" in dims

    def test_icd_10_description_keyword_classifies(self):
        """Description-keyword signal route: a tool/field description
        mentioning 'ICD-10-CM' classifies under icd_10_cm_code via
        ``classify_description`` (separate from the schema-signal
        classifier which only sees format/enum/range/pattern)."""
        configure_packs(extra_paths=[_seed_dir() / "icd-10-cm.yaml"])
        results = classify_description(
            "Returns the patient's ICD-10-CM diagnosis code"
        )
        dims = {r.dimension for r in results}
        assert "icd_10_cm_code" in dims

    def test_icd_9_to_icd_10_gems_mapping(self):
        path = _seed_dir() / "icd-10-cm.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Forward translation: ICD-9 → ICD-10 (note: the mapping table
        # uses 'from' = ICD-9 code, 'to' = ICD-10 code)
        result = translate(
            "250.00",  # legacy ICD-9 diabetes code
            from_pack=parsed,
            to_pack_name="icd-9-cm",
            to_dimension="icd_9_cm_code",
        )
        assert result.found
        # The pack stores GEMs with from=ICD-9 → to=ICD-10, so when we
        # ask for the icd-9-cm.icd_9_cm_code translation we get the
        # ICD-10 code as 'to'.
        assert "E11.9" in result.values

    def test_icd_10_cm_has_mappings_block(self):
        path = _seed_dir() / "icd-10-cm.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        summary = list_mappings(parsed)
        assert any(t == "icd-9-cm" for t, _, _ in summary)


# ── Bulk: load all 14 seed packs together (Phase 5 dress rehearsal) ──


class TestAllSeedPacksLoadTogether:
    def test_all_tier_a_and_b_seed_packs_co_load(self):
        # Scope to the 14 Tier A + Tier B names (Phase 4 restricted
        # packs are tested separately in test_seed_phase4_restricted.py
        # — they get their own all-19 co-load test there).
        seed_dir = _seed_dir()
        tier_ab_names = {
            "iso-4217.yaml", "iso-8601.yaml", "iso-3166.yaml", "iso-639.yaml",
            "iana-media-types.yaml", "naics-2022.yaml",
            "ucum.yaml", "fix-4.4.yaml", "fix-5.0.yaml", "gs1.yaml",
            "un-edifact.yaml", "fhir-r4.yaml", "fhir-r5.yaml", "icd-10-cm.yaml",
        }
        paths = sorted(p for p in seed_dir.glob("*.yaml") if p.name in tier_ab_names)
        assert len(paths) == 14, f"expected 14 Tier A+B packs, got {len(paths)}"
        configure_packs(extra_paths=paths)
        names = {r.name for r in get_active_pack_refs()}
        for required in {
            "iso-4217", "iso-8601", "iso-3166", "iso-639",
            "iana-media-types", "naics-2022",
            "ucum", "fix-4.4", "fix-5.0", "gs1", "un-edifact",
            "fhir-r4", "fhir-r5", "icd-10-cm",
        }:
            assert required in names

    def test_combined_pack_hashes_stable(self):
        seed_dir = _seed_dir()
        tier_ab_names = {
            "iso-4217.yaml", "iso-8601.yaml", "iso-3166.yaml", "iso-639.yaml",
            "iana-media-types.yaml", "naics-2022.yaml",
            "ucum.yaml", "fix-4.4.yaml", "fix-5.0.yaml", "gs1.yaml",
            "un-edifact.yaml", "fhir-r4.yaml", "fhir-r5.yaml", "icd-10-cm.yaml",
        }
        paths = sorted(p for p in seed_dir.glob("*.yaml") if p.name in tier_ab_names)
        _reset_taxonomy_cache()
        configure_packs(extra_paths=paths)
        first = {r.name: r.hash for r in get_active_pack_refs()}
        _reset_taxonomy_cache()
        configure_packs(extra_paths=paths)
        second = {r.name: r.hash for r in get_active_pack_refs()}
        assert first == second
