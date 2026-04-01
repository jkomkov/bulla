"""Tests for the Bulla Manifest module: taxonomy, generation, validation."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.manifest import (
    generate_manifest_from_json,
    generate_manifest_from_tools,
    load_taxonomy,
    validate_manifest,
)


SAMPLE_MANIFEST = Path(__file__).parent / "fixtures" / "sample_mcp_manifest.json"


class TestTaxonomy:
    def test_loads(self):
        tax = load_taxonomy()
        assert "taxonomy_version" in tax
        assert tax["taxonomy_version"] in ("0.1", "0.2")

    def test_has_10_dimensions(self):
        tax = load_taxonomy()
        assert len(tax["dimensions"]) == 10

    def test_required_fields(self):
        tax = load_taxonomy()
        for name, dim in tax["dimensions"].items():
            assert "description" in dim, f"{name} missing description"
            assert "known_values" in dim, f"{name} missing known_values"
            assert "field_patterns" in dim, f"{name} missing field_patterns"
            assert "domains" in dim, f"{name} missing domains"

    def test_known_dimensions(self):
        tax = load_taxonomy()
        expected = {
            "date_format", "amount_unit", "rate_scale", "score_range",
            "id_offset", "precision", "encoding", "null_handling",
            "timezone", "line_ending",
        }
        assert set(tax["dimensions"].keys()) == expected


class TestManifestGeneration:
    def test_from_json(self):
        manifests = generate_manifest_from_json(SAMPLE_MANIFEST)
        assert len(manifests) == 3
        for m in manifests:
            assert m["bulla_manifest"] == "0.1"
            assert "name" in m["tool"]
            assert isinstance(m["conventions"], dict)

    def test_from_tools_list(self):
        tools = [
            {
                "name": "my-tool",
                "inputSchema": {
                    "properties": {
                        "total_amount": {"type": "number"},
                        "due_date": {"type": "string"},
                    }
                },
            }
        ]
        manifests = generate_manifest_from_tools(tools)
        assert len(manifests) == 1
        m = manifests[0]
        assert m["tool"]["name"] == "my-tool"
        dims = set(m["conventions"].keys())
        assert "amount_unit" in dims
        assert "date_format" in dims

    def test_empty_tools(self):
        manifests = generate_manifest_from_tools([])
        assert manifests == []


class TestManifestValidation:
    def _write_manifest(self, data):
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as f:
            yaml.dump(data, f)
            return Path(f.name)

    def test_valid_minimal(self):
        path = self._write_manifest({
            "seam_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "amount_unit": {"value": "dollars", "confidence": "declared"},
            },
        })
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == []
        finally:
            path.unlink()

    def test_valid_with_unknown_confidence(self):
        path = self._write_manifest({
            "seam_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "timezone": {"confidence": "unknown"},
            },
        })
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == []
        finally:
            path.unlink()

    def test_invalid_version(self):
        path = self._write_manifest({
            "seam_manifest": "0.2",
            "tool": {"name": "test"},
            "conventions": {},
        })
        try:
            issues = validate_manifest(path)
            assert any("0.1" in i for i in issues)
        finally:
            path.unlink()

    def test_missing_tool_name(self):
        path = self._write_manifest({
            "seam_manifest": "0.1",
            "tool": {},
            "conventions": {},
        })
        try:
            issues = validate_manifest(path)
            assert any("tool.name" in i for i in issues)
        finally:
            path.unlink()

    def test_invalid_confidence(self):
        path = self._write_manifest({
            "seam_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "amount_unit": {"value": "dollars", "confidence": "maybe"},
            },
        })
        try:
            issues = validate_manifest(path)
            assert any("confidence" in i for i in issues)
        finally:
            path.unlink()

    def test_custom_dimension_info(self):
        path = self._write_manifest({
            "bulla_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "my_custom_dim": {"confidence": "declared", "value": "foo"},
            },
        })
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == []
            infos = [i for i in issues if i.startswith("Info:")]
            assert len(infos) == 1
            assert "my_custom_dim" in infos[0]
        finally:
            path.unlink()

    def test_valid_bulla_manifest_key(self):
        path = self._write_manifest({
            "bulla_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "amount_unit": {"value": "dollars", "confidence": "declared"},
            },
        })
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == []
            assert not any("deprecated" in i for i in issues)
        finally:
            path.unlink()

    def test_seam_manifest_key_backward_compat(self):
        path = self._write_manifest({
            "seam_manifest": "0.1",
            "tool": {"name": "test"},
            "conventions": {
                "amount_unit": {"value": "dollars", "confidence": "declared"},
            },
        })
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == []
            assert any("deprecated" in i for i in issues)
        finally:
            path.unlink()

    def test_invalid_yaml(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as f:
            f.write("invalid: yaml: :")
            path = Path(f.name)
        try:
            issues = validate_manifest(path)
            assert len(issues) >= 1
        finally:
            path.unlink()


class TestManifestRoundTrip:
    """Generate manifests from tool defs, then validate them — the full cycle."""

    def test_roundtrip_basic(self):
        tools = [
            {
                "name": "test-tool",
                "inputSchema": {
                    "properties": {
                        "amount": {"type": "number"},
                        "date": {"type": "string", "description": "ISO 8601 date"},
                    }
                },
            }
        ]
        manifests = generate_manifest_from_tools(tools)
        assert len(manifests) == 1
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as f:
            yaml.dump(manifests[0], f)
            path = Path(f.name)
        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == [], f"Round-trip validation failed: {errors}"
        finally:
            path.unlink()

    def test_roundtrip_from_json(self):
        manifests = generate_manifest_from_json(SAMPLE_MANIFEST)
        for m in manifests:
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False
            ) as f:
                yaml.dump(m, f)
                path = Path(f.name)
            try:
                issues = validate_manifest(path)
                errors = [i for i in issues if not i.startswith("Info:")]
                assert errors == [], (
                    f"Round-trip failed for {m['tool']['name']}: {errors}"
                )
            finally:
                path.unlink()

    def test_confidence_never_leaks_internal_grades(self):
        """Classifier returns 'high'/'medium'; manifests must use 'declared'/'inferred'/'unknown'."""
        tools = [
            {
                "name": "amount-tool",
                "inputSchema": {
                    "properties": {
                        "total_amount": {"type": "number"},
                        "currency_code": {"type": "string"},
                    }
                },
            }
        ]
        manifests = generate_manifest_from_tools(tools)
        for m in manifests:
            for dim_name, decl in m.get("conventions", {}).items():
                assert decl["confidence"] in ("declared", "inferred", "unknown"), (
                    f"Internal grade leaked: {dim_name} has confidence '{decl['confidence']}'"
                )
