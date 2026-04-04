"""Tests for Sprint 20 classifier precision upgrades.

Tests are organized by phase to match the test-interleaved execution order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bulla.infer.classifier import (
    FieldInfo,
    InferredDimension,
    _classify_field_descriptions,
    _get_description_keywords,
    _reset_taxonomy_cache,
    classify_description,
    classify_field_by_name,
    classify_tool_rich,
    configure_packs,
)


FINANCIAL_PACK = Path(__file__).parent.parent / "src" / "bulla" / "packs" / "financial.yaml"


# ── Phase 0a: Pack-driven description keywords ──────────────────────


class TestPackDrivenKeywords:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_base_pack_keywords_loaded(self):
        kw = _get_description_keywords()
        assert "date_format" in kw
        assert "iso-8601" in kw["date_format"]
        assert "encoding" in kw
        assert "utf-8" in kw["encoding"]

    def test_financial_pack_keywords_active_when_loaded(self):
        configure_packs(extra_paths=[FINANCIAL_PACK])
        kw = _get_description_keywords()
        assert "day_count_convention" in kw
        assert "day count" in kw["day_count_convention"]
        assert "settlement_cycle" in kw
        assert "settlement cycle" in kw["settlement_cycle"]

    def test_financial_description_keyword_fires(self):
        configure_packs(extra_paths=[FINANCIAL_PACK])
        hits = classify_description("Uses ACT/360 day count convention")
        dims = {h.dimension for h in hits}
        assert "day_count_convention" in dims

    def test_financial_keywords_absent_without_pack(self):
        configure_packs()
        kw = _get_description_keywords()
        assert "day_count_convention" not in kw

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── Phase 0b: Per-field description scanning ─────────────────────────


class TestPerFieldDescriptions:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_iso_8601_in_field_description_triggers_date_format(self):
        fields = [
            FieldInfo(name="since", description="ISO 8601 formatted timestamp"),
        ]
        hits = _classify_field_descriptions(fields)
        dims = {h.dimension for h in hits}
        assert "date_format" in dims
        match = [h for h in hits if h.dimension == "date_format"][0]
        assert match.field_name == "since"
        assert "field_description" in match.sources

    def test_empty_description_produces_no_hits(self):
        fields = [FieldInfo(name="page", description="")]
        hits = _classify_field_descriptions(fields)
        assert hits == []

    def test_none_description_produces_no_hits(self):
        fields = [FieldInfo(name="page")]
        hits = _classify_field_descriptions(fields)
        assert hits == []

    def test_field_description_corroborates_name_match(self):
        """A field_description hit + name hit -> 'declared' confidence."""
        tool = {
            "name": "get_payment",
            "description": "Get a payment record",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "total_amount": {
                        "type": "integer",
                        "description": "The total amount in cents",
                    },
                },
            },
        }
        hits = classify_tool_rich(tool)
        amount_hits = [h for h in hits if h.dimension == "amount_unit"]
        assert len(amount_hits) >= 1
        assert amount_hits[0].confidence == "declared"

    def test_multiple_fields_yield_independent_hits(self):
        fields = [
            FieldInfo(name="start", description="ISO 8601 start time"),
            FieldInfo(name="encoding", description="Character encoding UTF-8"),
        ]
        hits = _classify_field_descriptions(fields)
        dims = {h.dimension for h in hits}
        assert "date_format" in dims
        assert "encoding" in dims

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── Phase 0c: Negative patterns and type-aware exclusions ────────────


class TestNegativePatterns:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_per_page_excluded_from_id_offset(self):
        assert classify_field_by_name("per_page") is None

    def test_page_size_excluded_from_id_offset(self):
        assert classify_field_by_name("page_size") is None

    def test_limit_excluded_from_id_offset(self):
        assert classify_field_by_name("limit") is None

    def test_page_count_excluded_from_id_offset(self):
        assert classify_field_by_name("page_count") is None

    def test_max_results_excluded_from_id_offset(self):
        assert classify_field_by_name("max_results") is None

    def test_batch_size_excluded_from_id_offset(self):
        assert classify_field_by_name("batch_size") is None

    def test_page_still_matches_id_offset(self):
        r = classify_field_by_name("page")
        assert r is not None
        assert r.dimension == "id_offset"

    def test_index_still_matches_id_offset(self):
        r = classify_field_by_name("index")
        assert r is not None
        assert r.dimension == "id_offset"

    def test_issue_number_still_matches(self):
        r = classify_field_by_name("issue_number")
        assert r is not None
        assert r.dimension == "id_offset"

    def teardown_method(self):
        _reset_taxonomy_cache()


class TestTypeAwareExclusion:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_string_typed_id_excluded(self):
        assert classify_field_by_name("customer_id", schema_type="string") is None

    def test_string_typed_commit_id_excluded(self):
        assert classify_field_by_name("commit_id", schema_type="string") is None

    def test_integer_typed_id_still_matches(self):
        r = classify_field_by_name("issue_id", schema_type="integer")
        assert r is not None
        assert r.dimension == "id_offset"

    def test_no_type_info_id_still_matches(self):
        r = classify_field_by_name("user_id")
        assert r is not None
        assert r.dimension == "id_offset"

    def test_string_number_not_excluded(self):
        """Fields matching id_offset via 'number' token but typed string
        are still matched since only *_id pattern is type-filtered."""
        r = classify_field_by_name("issue_number", schema_type="string")
        assert r is not None
        assert r.dimension == "id_offset"

    def test_classify_tool_rich_passes_type_through(self):
        """Verify classify_tool_rich passes schema_type for exclusion."""
        tool = {
            "name": "get_commit",
            "description": "Get a commit",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "commit_id": {"type": "string", "description": "SHA hash"},
                },
            },
        }
        hits = classify_tool_rich(tool)
        dims = {h.dimension for h in hits}
        assert "id_offset" not in dims

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── Phase 1a: path_convention dimension ──────────────────────────────


class TestPathConvention:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_path_field_gets_path_convention(self):
        r = classify_field_by_name("path")
        assert r is not None
        assert r.dimension == "path_convention"

    def test_filepath_gets_path_convention(self):
        r = classify_field_by_name("filepath")
        assert r is not None
        assert r.dimension == "path_convention"

    def test_directory_gets_path_convention(self):
        r = classify_field_by_name("directory")
        assert r is not None
        assert r.dimension == "path_convention"

    def test_no_false_positive_on_xpath(self):
        r = classify_field_by_name("xpath")
        assert r is None or r.dimension != "path_convention"

    def test_no_false_positive_on_classpath(self):
        r = classify_field_by_name("classpath")
        assert r is None or r.dimension != "path_convention"

    def test_description_keywords_loaded(self):
        kw = _get_description_keywords()
        assert "path_convention" in kw
        assert "file path" in kw["path_convention"]

    def test_path_creates_edges_across_servers(self):
        """Tools from different servers sharing path fields produce blind spots.

        Each tool needs at least one non-classified field so that `path`
        is properly projected away (hidden from observable_schema).
        """
        from bulla.guard import BullaGuard

        tools = [
            {"name": "fs__read_file", "description": "Read a file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}}}},
            {"name": "fs__write_file", "description": "Write a file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}}}},
            {"name": "fs__delete_file", "description": "Delete a file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "recursive": {"type": "boolean"}}}},
            {"name": "gh__get_file", "description": "Get repo file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "owner": {"type": "string"}}}},
            {"name": "gh__create_file", "description": "Create repo file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}}}},
        ]
        guard = BullaGuard.from_tools_list(tools, name="test")
        diag = guard.diagnose()
        assert diag.coherence_fee > 0
        dims = {bs.dimension for bs in diag.blind_spots}
        assert "path_convention_match" in dims

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── Phase 1b: Temporal field patterns ────────────────────────────────


class TestTemporalPatterns:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_since_matches_date_format(self):
        r = classify_field_by_name("since")
        assert r is not None
        assert r.dimension == "date_format"

    def test_after_matches_date_format(self):
        r = classify_field_by_name("after")
        assert r is not None
        assert r.dimension == "date_format"

    def test_before_matches_date_format(self):
        r = classify_field_by_name("before")
        assert r is not None
        assert r.dimension == "date_format"

    def test_until_matches_date_format(self):
        r = classify_field_by_name("until")
        assert r is not None
        assert r.dimension == "date_format"

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── Phase 1a continued: boundary fee test ────────────────────────────


class TestBoundaryFee:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_boundary_fee_positive_on_partition(self):
        """Partitioning servers with path fields should give boundary_fee > 0."""
        from bulla.diagnostic import decompose_fee
        from bulla.guard import BullaGuard

        tools = [
            {"name": "fs__read", "description": "Read",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}}}},
            {"name": "fs__write", "description": "Write",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "data": {"type": "string"}}}},
            {"name": "fs__delete", "description": "Delete",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "force": {"type": "boolean"}}}},
            {"name": "gh__get_file", "description": "Get",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "owner": {"type": "string"}}}},
            {"name": "gh__create_file", "description": "Create",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}}}},
        ]
        guard = BullaGuard.from_tools_list(tools, name="test")
        comp = guard.composition
        partition = [
            frozenset({t.name for t in comp.tools if t.name.startswith("fs__")}),
            frozenset({t.name for t in comp.tools if t.name.startswith("gh__")}),
        ]
        decomp = decompose_fee(comp, partition)
        assert decomp.boundary_fee > 0

    def teardown_method(self):
        _reset_taxonomy_cache()
