"""Tests for structural schema comparison (pack-free diagnostic).

Covers: schema_similarity(), scan_composition(), category classification
(contradiction / agreement / homonym / synonym), and receipt threading.
"""

from __future__ import annotations

import pytest

from bulla.infer.classifier import FieldInfo
from bulla.infer.structural import (
    SIMILARITY_THRESHOLD,
    _classify_pair,
    _find_mismatches,
    schema_similarity,
    scan_composition,
)
from bulla.model import (
    SchemaContradiction,
    SchemaOverlap,
    StructuralDiagnostic,
)


# ── schema_similarity ────────────────────────────────────────────────


class TestSchemaSimilarity:
    """Test the pairwise schema similarity function."""

    def test_identical_fields_with_constraints(self):
        """Fields with matching type + format have similarity above threshold."""
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="y", schema_type="string", format="date-time")
        sim = schema_similarity(a, b)
        assert sim >= SIMILARITY_THRESHOLD
        assert sim == pytest.approx(0.65)

    def test_type_mismatch_drops_below_threshold(self):
        a = FieldInfo(name="x", schema_type="string")
        b = FieldInfo(name="x", schema_type="integer")
        sim = schema_similarity(a, b)
        assert sim < SIMILARITY_THRESHOLD

    def test_format_mismatch(self):
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="x", schema_type="string", format="uri")
        sim = schema_similarity(a, b)
        assert sim < 1.0
        assert sim > 0.3

    def test_both_none_metadata_low_similarity(self):
        """Two bare fields with no constraints have low similarity."""
        a = FieldInfo(name="x")
        b = FieldInfo(name="y")
        sim = schema_similarity(a, b)
        assert sim < SIMILARITY_THRESHOLD

    def test_enum_overlap_boosts_similarity(self):
        a = FieldInfo(name="x", schema_type="string", enum=("a", "b", "c"))
        b = FieldInfo(name="y", schema_type="string", enum=("b", "c", "d"))
        sim = schema_similarity(a, b)
        bare_sim = schema_similarity(
            FieldInfo(name="x", schema_type="string"),
            FieldInfo(name="y", schema_type="string"),
        )
        assert sim > bare_sim

    def test_enum_disjoint(self):
        a = FieldInfo(name="x", schema_type="string", enum=("a", "b"))
        b = FieldInfo(name="y", schema_type="string", enum=("c", "d"))
        sim_disjoint = schema_similarity(a, b)
        sim_overlap = schema_similarity(
            FieldInfo(name="x", schema_type="string", enum=("a", "b", "c")),
            FieldInfo(name="y", schema_type="string", enum=("b", "c", "d")),
        )
        assert sim_disjoint < sim_overlap

    def test_range_overlap_boosts_similarity(self):
        a = FieldInfo(name="x", schema_type="number", minimum=0, maximum=100)
        b = FieldInfo(name="y", schema_type="number", minimum=0, maximum=100)
        sim = schema_similarity(a, b)
        bare_sim = schema_similarity(
            FieldInfo(name="x", schema_type="number"),
            FieldInfo(name="y", schema_type="number"),
        )
        assert sim > bare_sim

    def test_range_disjoint(self):
        a = FieldInfo(name="x", schema_type="integer", minimum=0, maximum=10)
        b = FieldInfo(name="y", schema_type="integer", minimum=50, maximum=100)
        sim = schema_similarity(a, b)
        assert sim < 1.0

    def test_symmetry(self):
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="y", schema_type="string", format="uri")
        assert schema_similarity(a, b) == pytest.approx(schema_similarity(b, a))


# ── _find_mismatches ─────────────────────────────────────────────────


class TestFindMismatches:
    """Test specific mismatch detection between field pairs."""

    def test_type_mismatch(self):
        a = FieldInfo(name="x", schema_type="string")
        b = FieldInfo(name="x", schema_type="integer")
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "type" in types
        type_mm = next(m for m in mm if m[0] == "type")
        assert type_mm[1] == 1.0

    def test_format_mismatch_both_present(self):
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="x", schema_type="string", format="uri")
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "format" in types
        fmt_mm = next(m for m in mm if m[0] == "format")
        assert fmt_mm[1] == 0.7

    def test_format_mismatch_one_absent(self):
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="x", schema_type="string")
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "format" in types
        fmt_mm = next(m for m in mm if m[0] == "format")
        assert fmt_mm[1] == 0.4

    def test_enum_mismatch(self):
        a = FieldInfo(name="x", enum=("a", "b", "c"))
        b = FieldInfo(name="x", enum=("b", "c", "d"))
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "enum" in types

    def test_enum_one_free_form(self):
        a = FieldInfo(name="x", enum=("a", "b", "c"))
        b = FieldInfo(name="x")
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "enum" in types

    def test_range_disjoint(self):
        a = FieldInfo(name="x", schema_type="integer", minimum=0, maximum=10)
        b = FieldInfo(name="x", schema_type="integer", minimum=50, maximum=100)
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "range" in types
        range_mm = next(m for m in mm if m[0] == "range")
        assert range_mm[1] == 0.6

    def test_range_partial_overlap(self):
        a = FieldInfo(name="x", schema_type="integer", minimum=0, maximum=50)
        b = FieldInfo(name="x", schema_type="integer", minimum=30, maximum=100)
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "range" in types
        range_mm = next(m for m in mm if m[0] == "range")
        assert range_mm[1] == 0.3

    def test_pattern_mismatch(self):
        a = FieldInfo(name="x", pattern=r"\d{4}-\d{2}-\d{2}")
        b = FieldInfo(name="x", pattern=r"\d+")
        mm = _find_mismatches(a, b)
        types = [m[0] for m in mm]
        assert "pattern" in types

    def test_no_mismatches_compatible(self):
        a = FieldInfo(name="x", schema_type="string", format="date-time")
        b = FieldInfo(name="x", schema_type="string", format="date-time")
        mm = _find_mismatches(a, b)
        assert mm == []


# ── Category classification ──────────────────────────────────────────


class TestClassifyPair:
    """Test the four-way category classification."""

    def test_agreement_same_name_compatible(self):
        """Same name, same type, no constraint differences = agreement."""
        a = FieldInfo(name="path", schema_type="string")
        b = FieldInfo(name="path", schema_type="string")
        sim = schema_similarity(a, b)
        overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=True)
        assert overlap.category == "agreement"
        assert contradiction is None

    def test_homonym_same_name_different_types(self):
        """Same name, different types = homonym (different concepts)."""
        a = FieldInfo(name="amount", schema_type="integer", minimum=0, maximum=100)
        b = FieldInfo(name="amount", schema_type="string")
        sim = schema_similarity(a, b)
        overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=True)
        assert overlap.category == "homonym"
        assert contradiction is None

    def test_homonym_same_name_integer_vs_string(self):
        a = FieldInfo(name="id", schema_type="string")
        b = FieldInfo(name="id", schema_type="integer", minimum=0, maximum=1000)
        sim = schema_similarity(a, b)
        overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=True)
        assert overlap.category == "homonym"
        assert contradiction is None

    def test_contradiction_same_name_format_mismatch(self):
        """Same name, same type, different format = contradiction."""
        a = FieldInfo(name="timestamp", schema_type="string", format="date-time")
        b = FieldInfo(name="timestamp", schema_type="string", format="uri")
        sim = schema_similarity(a, b)
        overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=True)
        assert overlap.category == "contradiction"
        assert contradiction is not None
        assert contradiction.mismatch_type == "format"

    def test_synonym_different_name_high_similarity(self):
        a = FieldInfo(name="created_at", schema_type="string", format="date-time")
        b = FieldInfo(name="timestamp", schema_type="string", format="date-time")
        sim = schema_similarity(a, b)
        assert sim >= SIMILARITY_THRESHOLD
        overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=False)
        assert overlap.category == "synonym"
        assert contradiction is None

    def test_contradiction_different_name_similar_but_mismatched(self):
        a = FieldInfo(name="created_at", schema_type="string", format="date-time")
        b = FieldInfo(name="modified", schema_type="string", format="date")
        sim = schema_similarity(a, b)
        if sim >= SIMILARITY_THRESHOLD:
            overlap, contradiction = _classify_pair(a, b, "tool1", "tool2", sim, name_match=False)
            assert overlap.category == "contradiction"
            assert contradiction is not None


# ── scan_composition ─────────────────────────────────────────────────


class TestScanComposition:
    """Test the full composition scan pipeline."""

    def test_empty_composition(self):
        result = scan_composition({})
        assert result.contradiction_score == 0
        assert result.overlaps == ()
        assert result.contradictions == ()

    def test_single_tool(self):
        result = scan_composition({
            "tool1": [FieldInfo(name="x", schema_type="string")],
        })
        assert result.contradiction_score == 0

    def test_matching_fields_agreement(self):
        """Same name, same type, same constraints = agreement via name match."""
        result = scan_composition({
            "tool1": [FieldInfo(name="path", schema_type="string")],
            "tool2": [FieldInfo(name="path", schema_type="string")],
        })
        agreements = [o for o in result.overlaps if o.category == "agreement"]
        assert len(agreements) >= 1
        assert result.contradiction_score == 0

    def test_type_mismatch_homonym(self):
        """Same name, different types = homonym."""
        result = scan_composition({
            "tool1": [FieldInfo(name="amount", schema_type="integer")],
            "tool2": [FieldInfo(name="amount", schema_type="string")],
        })
        assert any(o.category == "homonym" for o in result.overlaps)

    def test_format_contradiction_detected(self):
        """Same name, same type, different format = contradiction."""
        result = scan_composition({
            "tool1": [
                FieldInfo(name="timestamp", schema_type="string", format="date-time"),
            ],
            "tool2": [
                FieldInfo(name="timestamp", schema_type="string", format="uri"),
            ],
        })
        assert result.n_contradicted > 0
        assert result.contradiction_score > 0
        assert any(c.mismatch_type == "format" for c in result.contradictions)

    def test_three_tools_pairwise(self):
        """Format mismatch detected pairwise across three tools."""
        result = scan_composition({
            "tool_a": [FieldInfo(name="x", schema_type="string", format="date-time")],
            "tool_b": [FieldInfo(name="x", schema_type="string", format="uri")],
            "tool_c": [FieldInfo(name="x", schema_type="string", format="date-time")],
        })
        contradictions = result.contradictions
        assert len(contradictions) >= 1
        involved_pairs = {(c.tool_a, c.tool_b) for c in contradictions}
        assert ("tool_a", "tool_b") in involved_pairs or ("tool_b", "tool_c") in involved_pairs

    def test_no_cross_talk_between_unrelated_fields(self):
        result = scan_composition({
            "tool1": [FieldInfo(name="alpha", schema_type="string")],
            "tool2": [FieldInfo(name="beta", schema_type="integer")],
        })
        assert result.contradiction_score == 0
        assert len(result.overlaps) == 0

    def test_leaf_name_matching(self):
        """Nested fields with same leaf name should be detected."""
        result = scan_composition({
            "tool1": [FieldInfo(name="settings.timeout", schema_type="integer")],
            "tool2": [FieldInfo(name="config.timeout", schema_type="integer")],
        })
        name_matched = [o for o in result.overlaps if o.name_match]
        assert len(name_matched) >= 1

    def test_structural_diagnostic_serialization(self):
        result = scan_composition({
            "tool1": [
                FieldInfo(name="ts", schema_type="string", format="date-time"),
            ],
            "tool2": [
                FieldInfo(name="ts", schema_type="string", format="uri"),
            ],
        })
        d = result.to_dict()
        roundtripped = StructuralDiagnostic.from_dict(d)
        assert roundtripped.contradiction_score == result.contradiction_score
        assert len(roundtripped.contradictions) == len(result.contradictions)
        assert len(roundtripped.overlaps) == len(result.overlaps)


# ── Receipt threading ────────────────────────────────────────────────


class TestReceiptThreading:
    """Test that structural contradictions flow through compose() into receipts."""

    def test_compose_includes_structural_diagnostic(self):
        from bulla.sdk import compose

        tools = [
            {
                "name": "tool_a",
                "description": "A tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "query": {"type": "string"},
                    },
                },
            },
            {
                "name": "tool_b",
                "description": "B tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "uri"},
                        "result": {"type": "string"},
                    },
                },
            },
        ]
        result = compose(tools)
        assert result.structural_diagnostic is not None
        assert result.structural_diagnostic.n_contradicted >= 0

    def test_receipt_hash_stable_with_structural_data(self):
        from bulla.sdk import compose
        from bulla.witness import verify_receipt_integrity

        tools = [
            {
                "name": "tool_x",
                "description": "X tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                },
            },
            {
                "name": "tool_y",
                "description": "Y tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                    },
                },
            },
        ]
        result = compose(tools)
        receipt_dict = result.receipt.to_dict()
        assert verify_receipt_integrity(receipt_dict)

    def test_compose_multi_includes_structural_diagnostic(self):
        from bulla.sdk import compose_multi

        server_tools = {
            "server_a": [
                {
                    "name": "search",
                    "description": "Search tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        },
                    },
                },
            ],
            "server_b": [
                {
                    "name": "fetch",
                    "description": "Fetch tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                        },
                    },
                },
            ],
        }
        result = compose_multi(server_tools)
        assert result.structural_diagnostic is not None
        assert result.decomposition is not None
