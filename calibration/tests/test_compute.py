"""Tests for calibration.compute: pairwise fee computation and DB storage."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT))

from calibration.compute import CoherenceDB, ComputeResult, diagnose_pair
from calibration.corpus import _normalize_tool_schemas
from calibration.index import _field_count


WEATHER_TOOLS = [
    {
        "name": "get_forecast",
        "description": "Get weather forecast for a location",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "start_date": {"type": "string", "description": "ISO-8601 date"},
                "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
        },
    },
]

MAPS_TOOLS = [
    {
        "name": "geocode",
        "description": "Convert address to coordinates",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "output_path": {"type": "string", "description": "File path for results"},
                "page": {"type": "integer", "description": "Page number, starting from 0"},
            },
        },
    },
]


class TestDiagnosePairBasic:
    def test_returns_compute_result(self):
        result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
        assert isinstance(result, ComputeResult)

    def test_fields_populated(self):
        result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
        assert result.name == "weather+maps"
        assert result.servers == ["weather", "maps"]
        assert result.n_tools == 2
        assert result.strategy == "pairwise"
        assert result.comp_id
        assert result.diagnostic_hash

    def test_kernel_objects_present(self):
        result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
        assert result.kernel_composition is not None
        assert result.kernel_diagnostic is not None

    def test_fee_is_nonnegative(self):
        result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
        assert result.coherence_fee >= 0
        assert result.boundary_fee >= 0
        assert result.n_blind_spots >= 0


class TestHyphenNormalization:
    """Verify the fix for the hyphen-to-underscore normalization bug.

    BullaGuard normalizes hyphens in tool names (e.g. mcp-xmind -> mcp_xmind).
    The partition matching must use the normalized names, otherwise boundary
    fee is always 0 (the bug that was fixed).
    """

    def test_hyphenated_server_has_boundary_fee(self):
        server_a_tools = [
            {
                "name": "read_file",
                "description": "Read file contents",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"},
                    },
                },
            },
        ]
        server_b_tools = [
            {
                "name": "get_contents",
                "description": "Get file from repo",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative repo path"},
                    },
                },
            },
        ]
        result = diagnose_pair(
            "mcp-filesystem", server_a_tools,
            "mcp-github", server_b_tools,
        )
        assert result.boundary_fee >= 0
        if result.coherence_fee > 0:
            assert result.boundary_fee > 0, (
                "Nonzero fee with hyphenated names should produce nonzero "
                "boundary fee — partition matching must normalize hyphens"
            )


class TestFieldCount:
    def test_standard_input_schema(self):
        tools = [
            {"name": "t1", "inputSchema": {"type": "object", "properties": {"a": {}, "b": {}}}},
            {"name": "t2", "inputSchema": {"type": "object", "properties": {"c": {}}}},
        ]
        assert _field_count(tools) == 3

    def test_underscore_input_schema(self):
        tools = [{"name": "t1", "input_schema": {"type": "object", "properties": {"a": {}, "b": {}}}}]
        assert _field_count(tools) == 2

    def test_json_string_schema(self):
        import json
        schema = json.dumps({"type": "object", "properties": {"x": {}, "y": {}}})
        tools = [{"name": "t1", "inputSchema": schema}]
        assert _field_count(tools) == 2

    def test_empty_tools(self):
        assert _field_count([]) == 0

    def test_tool_without_schema(self):
        tools = [{"name": "t1"}]
        assert _field_count(tools) == 0


class TestNormalizeToolSchemas:
    def test_underscore_to_camelcase(self):
        tools = [{"name": "t1", "input_schema": {"type": "object", "properties": {"a": {}}}}]
        result = _normalize_tool_schemas(tools)
        assert "inputSchema" in result[0]
        assert "input_schema" not in result[0]
        assert result[0]["inputSchema"]["properties"]["a"] == {}

    def test_json_string_parsed(self):
        import json
        schema_str = json.dumps({"type": "object", "properties": {"b": {"type": "string"}}})
        tools = [{"name": "t1", "input_schema": schema_str}]
        result = _normalize_tool_schemas(tools)
        assert isinstance(result[0]["inputSchema"], dict)
        assert result[0]["inputSchema"]["properties"]["b"]["type"] == "string"

    def test_invalid_json_string(self):
        tools = [{"name": "t1", "input_schema": "not valid json"}]
        result = _normalize_tool_schemas(tools)
        assert result[0]["inputSchema"] == {}

    def test_already_camelcase_unchanged(self):
        tools = [{"name": "t1", "inputSchema": {"type": "object", "properties": {"c": {}}}}]
        result = _normalize_tool_schemas(tools)
        assert result[0]["inputSchema"]["properties"]["c"] == {}

    def test_does_not_mutate_original(self):
        original = [{"name": "t1", "input_schema": {"type": "object", "properties": {"a": {}}}}]
        _normalize_tool_schemas(original)
        assert "input_schema" in original[0]


class TestCoherenceDB:
    def test_store_and_retrieve(self):
        with tempfile.TemporaryDirectory() as td:
            db = CoherenceDB(Path(td) / "test.db")
            result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
            db.store_result(result)
            assert db.has_composition(result.comp_id)
            summary = db.summary()
            assert summary["compositions"] == 1
            db.close()

    def test_idempotent_store(self):
        with tempfile.TemporaryDirectory() as td:
            db = CoherenceDB(Path(td) / "test.db")
            result = diagnose_pair("weather", WEATHER_TOOLS, "maps", MAPS_TOOLS)
            db.store_result(result)
            db.store_result(result)
            summary = db.summary()
            assert summary["compositions"] == 1
            db.close()
