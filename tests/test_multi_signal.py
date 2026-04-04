"""Tests for multi-signal convention inference.

Covers: FieldInfo extraction, nested properties, description keyword
matching, JSON Schema structural signals, taxonomy compilation,
three-tier confidence model, signal merging, and manifest round-trip.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    FieldInfo,
    InferredDimension,
    _get_domain_map,
    _normalize_enum_value,
    classify_description,
    classify_field_by_name,
    classify_fields,
    classify_schema_signal,
    classify_tool_rich,
    _get_name_patterns,
    _merge_signals,
)
from bulla.infer.mcp import extract_field_infos, _extract_tool_fields
from bulla.manifest import generate_manifest_from_json, generate_manifest_from_tools, validate_manifest


# ── FieldInfo and extraction ───────────────────────────────────────────


class TestFieldInfoExtraction:
    def test_basic_extraction(self):
        tool = {
            "name": "test_tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "name": {"type": "string"},
                },
            },
        }
        infos = extract_field_infos(tool)
        assert len(infos) == 2
        names = {fi.name for fi in infos}
        assert "amount" in names
        assert "name" in names

    def test_schema_metadata_preserved(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "created_at": {
                        "type": "string",
                        "format": "date-time",
                        "description": "When the record was created",
                    },
                    "score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "currency": {
                        "type": "string",
                        "enum": ["USD", "EUR", "GBP"],
                    },
                    "date_str": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                    },
                },
            },
        }
        infos = extract_field_infos(tool)
        info_map = {fi.name: fi for fi in infos}

        assert info_map["created_at"].format == "date-time"
        assert info_map["created_at"].schema_type == "string"
        assert info_map["created_at"].description == "When the record was created"

        assert info_map["score"].minimum == 1.0
        assert info_map["score"].maximum == 5.0

        assert info_map["currency"].enum == ("USD", "EUR", "GBP")

        assert info_map["date_str"].pattern == r"^\d{4}-\d{2}-\d{2}$"

    def test_deduplication(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {"amount": {"type": "number"}},
            },
            "outputSchema": {
                "type": "object",
                "properties": {"amount": {"type": "number"}},
            },
        }
        infos = extract_field_infos(tool)
        assert len(infos) == 1

    def test_backward_compat_extract_tool_fields(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "date": {"type": "string"},
                },
            },
        }
        fields = _extract_tool_fields(tool)
        assert isinstance(fields, list)
        assert all(isinstance(f, str) for f in fields)
        assert "amount" in fields
        assert "date" in fields


# ── Nested property extraction ─────────────────────────────────────────


class TestNestedExtraction:
    def test_nested_properties(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "invoice": {
                        "type": "object",
                        "properties": {
                            "total_amount": {"type": "integer"},
                            "due_date": {"type": "string", "format": "date"},
                        },
                    },
                    "status": {"type": "string"},
                },
            },
        }
        infos = extract_field_infos(tool)
        names = {fi.name for fi in infos}
        assert "invoice" in names
        assert "invoice.total_amount" in names
        assert "invoice.due_date" in names
        assert "status" in names

    def test_nested_metadata_preserved(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "properties": {
                            "price": {
                                "type": "integer",
                                "description": "Price in cents",
                            },
                        },
                    },
                },
            },
        }
        infos = extract_field_infos(tool)
        info_map = {fi.name: fi for fi in infos}
        assert "data.price" in info_map
        assert info_map["data.price"].schema_type == "integer"
        assert info_map["data.price"].description == "Price in cents"

    def test_depth_limit(self):
        deep_schema = {"type": "object", "properties": {
            "a": {"type": "object", "properties": {
                "b": {"type": "object", "properties": {
                    "c": {"type": "object", "properties": {
                        "d": {"type": "string"},
                    }},
                }},
            }},
        }}
        tool = {"name": "test", "inputSchema": deep_schema}
        infos = extract_field_infos(tool)
        names = {fi.name for fi in infos}
        assert "a" in names
        assert "a.b" in names
        assert "a.b.c" in names
        assert "a.b.c.d" not in names

    def test_nested_fields_classified(self):
        tool = {
            "name": "test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payment": {
                        "type": "object",
                        "properties": {
                            "total_amount": {"type": "integer"},
                            "due_date": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = classify_field_by_name("payment.total_amount")
        assert result is not None
        assert result.dimension == "amount_unit"

        result2 = classify_field_by_name("payment.due_date")
        assert result2 is not None
        assert result2.dimension == "date_format"


# ── Description keyword matching ───────────────────────────────────────


class TestDescriptionMatching:
    def test_amount_keywords(self):
        results = classify_description("Returns amounts in cents")
        dims = {r.dimension for r in results}
        assert "amount_unit" in dims

    def test_date_keywords(self):
        results = classify_description("Timestamps are in ISO-8601 format")
        dims = {r.dimension for r in results}
        assert "date_format" in dims

    def test_rate_keywords(self):
        results = classify_description("Interest expressed as a percentage")
        dims = {r.dimension for r in results}
        assert "rate_scale" in dims

    def test_multiple_dimensions(self):
        results = classify_description(
            "Amounts in cents, dates in unix epoch, scores on a scale of 1 to 5"
        )
        dims = {r.dimension for r in results}
        assert "amount_unit" in dims
        assert "date_format" in dims
        assert "score_range" in dims

    def test_empty_description(self):
        assert classify_description("") == []
        assert classify_description(None) == []

    def test_no_match(self):
        results = classify_description("This tool processes data efficiently")
        assert len(results) == 0

    def test_case_insensitive(self):
        results = classify_description("Uses ISO-8601 for all timestamps")
        dims = {r.dimension for r in results}
        assert "date_format" in dims

    def test_all_sources_tagged(self):
        results = classify_description("Returns amounts in cents")
        for r in results:
            assert "description" in r.sources


# ── JSON Schema structural signals ─────────────────────────────────────


class TestSchemaSignals:
    def test_format_datetime(self):
        fi = FieldInfo(name="created", format="date-time")
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "date_format" in dims

    def test_format_date(self):
        fi = FieldInfo(name="start", format="date")
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "date_format" in dims

    def test_enum_currency(self):
        fi = FieldInfo(name="unit", enum=("USD", "EUR", "GBP"))
        results = classify_schema_signal(fi)
        assert len(results) > 0

    def test_range_0_to_1(self):
        fi = FieldInfo(name="prob", minimum=0.0, maximum=1.0)
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "rate_scale" in dims

    def test_range_1_to_5(self):
        fi = FieldInfo(name="stars", minimum=1.0, maximum=5.0)
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "score_range" in dims

    def test_range_0_to_100_rate_hint(self):
        """0-100 with rate-like field name → rate_scale."""
        fi = FieldInfo(name="pct", minimum=0.0, maximum=100.0)
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "rate_scale" in dims

    def test_range_0_to_100_score_default(self):
        """0-100 with neutral field name → score_range (default)."""
        fi = FieldInfo(name="quality", minimum=0.0, maximum=100.0)
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "score_range" in dims

    def test_range_0_to_100_description_hint(self):
        """0-100 with rate hint in description → rate_scale."""
        fi = FieldInfo(name="value", minimum=0.0, maximum=100.0,
                       description="Completion percentage")
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "rate_scale" in dims

    def test_integer_amount(self):
        fi = FieldInfo(name="total_amount", schema_type="integer")
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "amount_unit" in dims

    def test_date_pattern(self):
        fi = FieldInfo(name="date_str", pattern=r"^\d{4}-\d{2}-\d{2}$")
        results = classify_schema_signal(fi)
        dims = {r.dimension for r in results}
        assert "date_format" in dims

    def test_no_signals(self):
        fi = FieldInfo(name="status", schema_type="string")
        results = classify_schema_signal(fi)
        assert len(results) == 0

    def test_sources_tagged(self):
        fi = FieldInfo(name="x", format="date-time")
        results = classify_schema_signal(fi)
        for r in results:
            assert any(s.startswith("schema_") for s in r.sources)


# ── Taxonomy compilation ───────────────────────────────────────────────


class TestTaxonomyCompilation:
    def test_core_patterns_present(self):
        patterns = _get_name_patterns()
        dim_names = {name for name, _ in patterns}
        assert "date_format" in dim_names
        assert "amount_unit" in dim_names
        assert "rate_scale" in dim_names
        assert "score_range" in dim_names
        assert "id_offset" in dim_names

    def test_all_10_dimensions_covered(self):
        patterns = _get_name_patterns()
        dim_names = {name for name, _ in patterns}
        expected = {
            "date_format", "amount_unit", "rate_scale", "score_range",
            "id_offset", "precision", "encoding", "timezone",
            "null_handling", "line_ending",
        }
        assert expected.issubset(dim_names)

    def test_backward_compat_field_names(self):
        for name, expected_dim in [
            ("created_at", "date_format"),
            ("total_amount", "amount_unit"),
            ("interest_rate", "rate_scale"),
            ("quality_score", "score_range"),
            ("page_index", "id_offset"),
        ]:
            result = classify_field_by_name(name)
            assert result is not None, f"{name} should match"
            assert result.dimension == expected_dim, f"{name}: expected {expected_dim}, got {result.dimension}"


# ── Three-tier confidence model ────────────────────────────────────────


class TestConfidenceModel:
    def test_single_name_signal_is_inferred(self):
        result = classify_field_by_name("total_amount")
        assert result is not None
        assert result.confidence == "inferred"

    def test_two_signals_produce_declared(self):
        name_hits = [InferredDimension(
            field_name="amount", dimension="amount_unit",
            confidence="inferred", sources=("name",),
        )]
        desc_hits = [InferredDimension(
            field_name="_description", dimension="amount_unit",
            confidence="inferred", sources=("description",),
        )]
        merged = _merge_signals(name_hits, desc_hits, [])
        assert len(merged) == 1
        assert merged[0].dimension == "amount_unit"
        assert merged[0].confidence == "declared"
        assert "name" in merged[0].sources
        assert "description" in merged[0].sources

    def test_name_plus_schema_is_declared(self):
        name_hits = [InferredDimension(
            field_name="due_date", dimension="date_format",
            confidence="inferred", sources=("name",),
        )]
        schema_hits = [InferredDimension(
            field_name="due_date", dimension="date_format",
            confidence="inferred", sources=("schema_format",),
        )]
        merged = _merge_signals(name_hits, [], schema_hits)
        assert len(merged) == 1
        assert merged[0].confidence == "declared"

    def test_description_only_is_unknown(self):
        """Single description-keyword signal is weak → "unknown"."""
        desc_hits = [InferredDimension(
            field_name="_description", dimension="amount_unit",
            confidence="inferred", sources=("description",),
        )]
        merged = _merge_signals([], desc_hits, [])
        assert len(merged) == 1
        assert merged[0].confidence == "unknown"

    def test_multiple_dimensions_merged(self):
        name_hits = [
            InferredDimension("amount", "amount_unit", "inferred", ("name",)),
            InferredDimension("due_date", "date_format", "inferred", ("name",)),
        ]
        desc_hits = [
            InferredDimension("_description", "amount_unit", "inferred", ("description",)),
        ]
        merged = _merge_signals(name_hits, desc_hits, [])
        dim_map = {m.dimension: m for m in merged}
        assert dim_map["amount_unit"].confidence == "declared"
        assert dim_map["date_format"].confidence == "inferred"


# ── Full classify_tool_rich ────────────────────────────────────────────


class TestClassifyToolRich:
    def test_basic_tool(self):
        tool = {
            "name": "invoice_parser",
            "description": "Parse invoices. Amounts in cents.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "total_amount": {"type": "integer"},
                    "due_date": {"type": "string", "format": "date"},
                    "status": {"type": "string"},
                },
            },
        }
        results = classify_tool_rich(tool)
        dim_map = {r.dimension: r for r in results}
        assert "amount_unit" in dim_map
        assert dim_map["amount_unit"].confidence == "declared"
        assert "date_format" in dim_map

    def test_description_only_signals(self):
        tool = {
            "name": "converter",
            "description": "Converts values. Uses unix epoch for timestamps.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            },
        }
        results = classify_tool_rich(tool)
        dim_map = {r.dimension: r for r in results}
        assert "date_format" in dim_map

    def test_nested_fields_detected(self):
        tool = {
            "name": "payment_processor",
            "description": "Process payments",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payment": {
                        "type": "object",
                        "properties": {
                            "total_amount": {"type": "integer"},
                            "currency": {
                                "type": "string",
                                "enum": ["USD", "EUR", "GBP"],
                            },
                        },
                    },
                },
            },
        }
        results = classify_tool_rich(tool)
        dim_map = {r.dimension: r for r in results}
        assert "amount_unit" in dim_map

    def test_schema_format_signal(self):
        tool = {
            "name": "logger",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_time": {"type": "string", "format": "date-time"},
                },
            },
        }
        results = classify_tool_rich(tool)
        dim_map = {r.dimension: r for r in results}
        assert "date_format" in dim_map
        assert dim_map["date_format"].confidence == "declared"


# ── Manifest round-trip ────────────────────────────────────────────────


class TestManifestRoundTrip:
    def test_generated_manifests_validate(self):
        tools = [
            {
                "name": "test-tool",
                "description": "Handles financial data in cents",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "due_date": {"type": "string", "format": "date"},
                    },
                },
            },
        ]
        manifests = generate_manifest_from_tools(tools)
        assert len(manifests) == 1
        m = manifests[0]

        assert m["bulla_manifest"] == "0.1"
        assert m["tool"]["name"] == "test-tool"
        assert "conventions" in m

        for dim_name, decl in m["conventions"].items():
            assert decl["confidence"] in ("declared", "inferred", "unknown"), (
                f"Invalid confidence '{decl['confidence']}' for {dim_name}"
            )

        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as f:
            yaml.dump(m, f)
            path = Path(f.name)

        try:
            issues = validate_manifest(path)
            errors = [i for i in issues if not i.startswith("Info:")]
            assert errors == [], f"Round-trip validation failed: {errors}"
        finally:
            path.unlink()

    def test_multi_signal_manifest_has_sources(self):
        tools = [
            {
                "name": "rich-tool",
                "description": "Returns amounts in cents with ISO-8601 timestamps",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "total_amount": {"type": "integer"},
                        "created_at": {"type": "string", "format": "date-time"},
                    },
                },
            },
        ]
        manifests = generate_manifest_from_tools(tools)
        m = manifests[0]
        for dim_name, decl in m["conventions"].items():
            if decl["confidence"] == "declared":
                assert "sources" in decl
                assert len(decl["sources"]) >= 2

    def test_backward_compat_simple_tools(self):
        tools = [
            {
                "name": "simple",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                    },
                },
            },
        ]
        manifests = generate_manifest_from_tools(tools)
        assert len(manifests) == 1
        assert manifests[0]["conventions"]["amount_unit"]["confidence"] in (
            "declared", "inferred", "unknown"
        )


# ── G4: Normalization helper ──────────────────────────────────────────


class TestNormalization:
    def test_normalize_basic(self):
        assert _normalize_enum_value("USD") == "usd"
        assert _normalize_enum_value("ISO-8601") == "iso8601"
        assert _normalize_enum_value("unix_epoch") == "unixepoch"

    def test_normalize_idempotent(self):
        assert _normalize_enum_value("alreadylower") == "alreadylower"


# ── G6: Real MCP validation suite ─────────────────────────────────────

REAL_MCP_FIXTURE = Path(__file__).parent / "fixtures" / "real_mcp_tools.json"


class TestRealMCPValidation:
    """Run classifier against realistic multi-tool MCP definitions."""

    def test_stripe_tool_coverage(self):
        """Stripe charge tool: should detect amount_unit, date_format.

        String-typed *_id fields (customer_id, invoice_id) are correctly
        excluded from id_offset by the type-aware filter.
        """
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        stripe = next(t for t in tools if t["name"] == "stripe_create_charge")
        results = classify_tool_rich(stripe)
        dim_map = {r.dimension: r for r in results}

        assert "amount_unit" in dim_map
        assert dim_map["amount_unit"].confidence == "declared"  # name + description
        assert "date_format" in dim_map
        assert dim_map["date_format"].confidence == "declared"  # name + schema_format + description
        assert "id_offset" not in dim_map  # string-typed IDs correctly excluded

    def test_github_tool_coverage(self):
        """GitHub issue tool: should detect score_range, date_format, id_offset."""
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        gh = next(t for t in tools if t["name"] == "github_create_issue")
        results = classify_tool_rich(gh)
        dim_map = {r.dimension: r for r in results}

        assert "score_range" in dim_map
        assert dim_map["score_range"].confidence == "declared"  # name + schema_range + description
        assert "date_format" in dim_map
        assert "id_offset" in dim_map

    def test_datadog_tool_coverage(self):
        """Datadog tool: should detect date_format, timezone, rate_scale or score_range."""
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        dd = next(t for t in tools if t["name"] == "datadog_query_metrics")
        results = classify_tool_rich(dd)
        dim_map = {r.dimension: r for r in results}

        assert "date_format" in dim_map
        assert "timezone" in dim_map
        # confidence should detect rate_scale (0-1 range on confidence field)
        assert "rate_scale" in dim_map

    def test_slack_tool_coverage(self):
        """Slack tool: should detect encoding from description."""
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        slack = next(t for t in tools if t["name"] == "slack_post_message")
        results = classify_tool_rich(slack)
        dim_map = {r.dimension: r for r in results}

        assert "encoding" in dim_map
        assert "date_format" in dim_map  # timestamp field

    def test_ml_tool_coverage(self):
        """ML prediction tool: should detect rate_scale, score_range, precision."""
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        ml = next(t for t in tools if t["name"] == "ml_predict_score")
        results = classify_tool_rich(ml)
        dim_map = {r.dimension: r for r in results}

        assert "rate_scale" in dim_map  # 0-1 range on prediction_score
        assert "precision" in dim_map
        assert "score_range" in dim_map  # score in field name

    def test_all_five_tools_produce_results(self):
        """Every tool in the fixture should produce at least 2 dimensions."""
        tools = json.loads(REAL_MCP_FIXTURE.read_text())["tools"]
        for tool in tools:
            results = classify_tool_rich(tool)
            assert len(results) >= 2, (
                f"{tool['name']} produced only {len(results)} dimensions: "
                f"{[r.dimension for r in results]}"
            )


# ── G7: Domain-aware prioritization ───────────────────────────────────


class TestDomainPrioritization:
    def test_domain_map_loads(self):
        dm = _get_domain_map()
        assert "amount_unit" in dm
        assert "financial" in dm["amount_unit"]
        assert "universal" in dm["date_format"]

    def test_financial_domain_boosts_unknown_to_inferred(self):
        """With domain_hint='financial', weak amount_unit signal → inferred."""
        desc_hits = [InferredDimension(
            field_name="_description", dimension="amount_unit",
            confidence="inferred", sources=("description",),
        )]
        merged = _merge_signals([], desc_hits, [], domain_hint="financial")
        assert len(merged) == 1
        assert merged[0].confidence == "inferred"  # boosted from unknown

    def test_no_boost_without_domain_match(self):
        """Domain hint 'devops' should NOT boost amount_unit."""
        desc_hits = [InferredDimension(
            field_name="_description", dimension="amount_unit",
            confidence="inferred", sources=("description",),
        )]
        merged = _merge_signals([], desc_hits, [], domain_hint="devops")
        assert merged[0].confidence == "unknown"  # no boost

    def test_domain_hint_in_classify_tool_rich(self):
        tool = {
            "name": "ledger",
            "description": "Handles financial amounts in cents",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "number"}},
            },
        }
        # Without domain hint, description-only → unknown
        results_no_hint = classify_tool_rich(tool)
        amount_no = next((r for r in results_no_hint if r.dimension == "amount_unit"), None)
        assert amount_no is not None
        assert amount_no.confidence == "unknown"

        # With domain hint, description-only + domain match → inferred
        results_hint = classify_tool_rich(tool, domain_hint="financial")
        amount_hint = next((r for r in results_hint if r.dimension == "amount_unit"), None)
        assert amount_hint is not None
        assert amount_hint.confidence == "inferred"


# ── G8: End-to-end coverage test ──────────────────────────────────────


class TestEndToEnd:
    def test_real_mcp_manifest_to_validated_manifests(self):
        """Full pipeline: real MCP JSON → generate manifests → validate each → coverage."""
        manifests = generate_manifest_from_json(REAL_MCP_FIXTURE)
        assert len(manifests) == 5

        all_dims: set[str] = set()
        for m in manifests:
            # Validate
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False
            ) as f:
                yaml.dump(m, f)
                path = Path(f.name)
            try:
                issues = validate_manifest(path)
                errors = [i for i in issues if not i.startswith("Info:")]
                assert errors == [], (
                    f"Validation failed for {m['tool']['name']}: {errors}"
                )
            finally:
                path.unlink()

            # Confidence integrity
            for dim_name, decl in m["conventions"].items():
                assert decl["confidence"] in ("declared", "inferred", "unknown")
                all_dims.add(dim_name)

        # Coverage: should detect at least 6 of 10 taxonomy dimensions
        assert len(all_dims) >= 6, (
            f"Only {len(all_dims)} dimensions detected across 5 tools: {all_dims}"
        )

    def test_no_internal_grades_leak(self):
        """Confidence values must be spec-valid across the entire pipeline."""
        manifests = generate_manifest_from_json(REAL_MCP_FIXTURE)
        for m in manifests:
            for dim_name, decl in m["conventions"].items():
                assert decl["confidence"] not in ("high", "medium", "low"), (
                    f"Internal grade leaked in {m['tool']['name']}.{dim_name}"
                )
