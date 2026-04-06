"""Tests for micro-pack format: validation, refines, provenance, pack loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    _reset_taxonomy_cache,
    configure_packs,
    load_pack_stack,
)
from bulla.packs.validate import validate_pack


SAMPLE_MICROPACK = {
    "pack_name": "discovered_test",
    "pack_version": "0.1.0",
    "dimensions": {
        "coordinate_datum": {
            "description": "Geographic coordinate reference system",
            "known_values": ["WGS84", "NAD83", "ETRS89"],
            "field_patterns": ["*_lat", "*_lon", "*_coordinates"],
            "description_keywords": ["coordinate", "latitude", "longitude"],
            "refines": "reference_frame",
            "provenance": {
                "source": "bulla-discover-v0.1",
                "confidence": 0.85,
                "source_tools": ["maps__geocode", "weather__forecast"],
                "boundary": True,
            },
        },
    },
}


class TestValidatePack:
    def test_valid_micropack(self):
        errors = validate_pack(SAMPLE_MICROPACK)
        assert errors == []

    def test_missing_pack_name(self):
        pack = {"dimensions": {"d": {"description": "x", "field_patterns": ["*_x"]}}}
        errors = validate_pack(pack)
        assert any("pack_name" in e for e in errors)

    def test_missing_dimensions(self):
        pack = {"pack_name": "test"}
        errors = validate_pack(pack)
        assert any("dimensions" in e for e in errors)

    def test_empty_dimensions(self):
        pack = {"pack_name": "test", "dimensions": {}}
        errors = validate_pack(pack)
        assert any("at least one dimension" in e for e in errors)

    def test_missing_description(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"field_patterns": ["*_x"]}},
        }
        errors = validate_pack(pack)
        assert any("description" in e for e in errors)

    def test_missing_patterns_and_keywords(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x"}},
        }
        errors = validate_pack(pack)
        assert any("field_patterns" in e and "description_keywords" in e for e in errors)

    def test_only_field_patterns_valid(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*_x"]}},
        }
        assert validate_pack(pack) == []

    def test_only_description_keywords_valid(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "description_keywords": ["kw"]}},
        }
        assert validate_pack(pack) == []

    def test_refines_must_be_string(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*_x"], "refines": 42}},
        }
        errors = validate_pack(pack)
        assert any("refines" in e and "string" in e for e in errors)

    def test_provenance_must_be_dict(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*_x"], "provenance": "bad"}},
        }
        errors = validate_pack(pack)
        assert any("provenance" in e and "mapping" in e for e in errors)

    def test_known_values_must_be_list(self):
        pack = {
            "pack_name": "test",
            "dimensions": {"d": {"description": "x", "field_patterns": ["*_x"], "known_values": "bad"}},
        }
        errors = validate_pack(pack)
        assert any("known_values" in e and "list" in e for e in errors)

    def test_non_dict_input(self):
        errors = validate_pack("not a dict")
        assert any("mapping" in e for e in errors)


class TestMicropackLoading:
    """Test that micro-packs with refines/provenance load and merge correctly."""

    def setup_method(self):
        _reset_taxonomy_cache()

    def _write_micropack(self, pack_dict: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        )
        yaml.dump(pack_dict, tmp, default_flow_style=False)
        tmp.flush()
        return Path(tmp.name)

    def test_micropack_loads_and_merges(self):
        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            merged, refs = load_pack_stack(extra_paths=[path])
            dims = merged["dimensions"]
            assert "coordinate_datum" in dims
            assert "date_format" in dims  # base still present
            assert refs[-1].name == "discovered_test"
            assert any(r.name == "base" for r in refs)
        finally:
            path.unlink()

    def test_refines_preserved(self):
        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            merged, _ = load_pack_stack(extra_paths=[path])
            dim = merged["dimensions"]["coordinate_datum"]
            assert dim["refines"] == "reference_frame"
        finally:
            path.unlink()

    def test_provenance_preserved(self):
        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            merged, _ = load_pack_stack(extra_paths=[path])
            dim = merged["dimensions"]["coordinate_datum"]
            assert dim["provenance"]["source"] == "bulla-discover-v0.1"
            assert dim["provenance"]["confidence"] == 0.85
        finally:
            path.unlink()

    def test_packref_hash_stable(self):
        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            _, refs1 = load_pack_stack(extra_paths=[path])
            _reset_taxonomy_cache()
            _, refs2 = load_pack_stack(extra_paths=[path])
            assert refs1[1].hash == refs2[1].hash
        finally:
            path.unlink()

    def test_micropack_field_patterns_used_by_classifier(self):
        """Micro-pack field_patterns compile into name patterns for classification."""
        from bulla.infer.classifier import classify_field_by_name

        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            configure_packs(extra_paths=[path])
            result = classify_field_by_name("gps_lat")
            assert result is not None
            assert result.dimension == "coordinate_datum"
        finally:
            path.unlink()

    def test_micropack_description_keywords_used(self):
        """Micro-pack description_keywords fire in description matching."""
        from bulla.infer.classifier import classify_description

        path = self._write_micropack(SAMPLE_MICROPACK)
        try:
            configure_packs(extra_paths=[path])
            hits = classify_description("Uses latitude coordinate system")
            dims = {h.dimension for h in hits}
            assert "coordinate_datum" in dims
        finally:
            path.unlink()

    def teardown_method(self):
        _reset_taxonomy_cache()


class TestPackValidateCLI:
    """Test bulla pack validate CLI subcommand."""

    def _write_yaml(self, data: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        )
        yaml.dump(data, tmp, default_flow_style=False)
        tmp.flush()
        return Path(tmp.name)

    def test_valid_pack_exits_zero(self):
        import subprocess
        import sys

        path = self._write_yaml(SAMPLE_MICROPACK)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "bulla", "pack", "validate", str(path)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "VALID" in result.stdout
        finally:
            path.unlink()

    def test_invalid_pack_exits_nonzero(self):
        import subprocess
        import sys

        path = self._write_yaml({"pack_name": "bad"})
        try:
            result = subprocess.run(
                [sys.executable, "-m", "bulla", "pack", "validate", str(path)],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0
            assert "error" in result.stdout.lower()
        finally:
            path.unlink()
