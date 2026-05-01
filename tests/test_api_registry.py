"""Tests for the Phase 7 API/MCP schema-capture pipeline.

The pipeline is the load-bearing asset for Phase 7. The 100 indexed
schemas come and go; the pipeline is what makes any future schema
indexable. Tests verify:

  1. **Per-kind normalization**: MCP, OpenAPI, GraphQL all reduce to
     the same internal MCP-shape that the classifier consumes.
  2. **Content-addressing**: identical input schema + identical pack
     stack produces identical schema_hash; pack-stack changes change
     ``capture_hash`` but not ``schema_hash``.
  3. **Storage idempotency**: ``capture_to_dir`` writes deterministic
     JSON for a given input.
  4. **Coverage / corpus builders**: aggregate captured records into
     the coverage map and classifier-training corpus.
  5. **Forward-compatibility**: the captured-record JSON shape is
     stable enough to feed a future equivalence detector (Part B).
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

import pytest

from bulla.api_registry import (
    SOURCE_KIND_GRAPHQL,
    SOURCE_KIND_MCP,
    SOURCE_KIND_OPENAPI,
    build_classifier_corpus,
    build_coverage_map,
    capture,
    capture_to_dir,
)
from bulla.infer.classifier import _reset_taxonomy_cache, configure_packs


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


# ── MCP normalization ────────────────────────────────────────────────


SAMPLE_MCP_SCHEMA = {
    "tools": [
        {
            "name": "create_invoice",
            "description": "Create a new invoice with currency and amount",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer", "description": "Amount in minor units"},
                    "currency": {
                        "type": "string",
                        "enum": ["USD", "EUR", "JPY"],
                        "description": "ISO 4217 currency code",
                    },
                    "country": {
                        "type": "string",
                        "enum": ["US", "GB", "FR"],
                    },
                    "due_at": {
                        "type": "string",
                        "format": "date-time",
                    },
                },
                "required": ["amount", "currency"],
            },
        },
    ],
}


class TestMcpCapture:
    def test_capture_returns_schema_capture(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="test-mcp",
        )
        assert cap.source_kind == SOURCE_KIND_MCP
        assert cap.source_id == "test-mcp"
        assert len(cap.tools) == 1
        assert cap.tools[0].tool_name == "create_invoice"

    def test_capture_classifies_currency_field(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="test-mcp",
        )
        # currency field must classify under currency_code via name OR
        # enum signal.
        currency_field = next(
            f for tool in cap.tools for f in tool.fields
            if f.field_name == "currency"
        )
        assert "currency_code" in currency_field.dimensions

    def test_aggregate_counts_match_per_field_records(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="test-mcp",
        )
        # 4 properties; tool description is its own _description signal.
        assert cap.n_fields == 4
        # Sum of declared + inferred + unknown == n_fields (every field
        # gets exactly one tier label).
        assert cap.n_declared + cap.n_inferred + cap.n_unknown == cap.n_fields


# ── OpenAPI normalization ────────────────────────────────────────────


SAMPLE_OPENAPI_SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Test API"},
    "paths": {
        "/charges": {
            "post": {
                "operationId": "createCharge",
                "summary": "Create a charge",
                "description": "Charge a customer in a currency",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "amount": {"type": "integer"},
                                    "currency": {
                                        "type": "string",
                                        "enum": ["USD", "EUR", "GBP"],
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "get": {
                "operationId": "listCharges",
                "summary": "List charges",
                "parameters": [
                    {
                        "name": "country",
                        "in": "query",
                        "schema": {
                            "type": "string",
                            "enum": ["US", "GB"],
                        },
                    },
                ],
            },
        },
    },
}


class TestOpenApiCapture:
    def test_each_operation_becomes_a_tool(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_OPENAPI_SCHEMA,
            source_kind=SOURCE_KIND_OPENAPI,
            source_id="test-openapi",
        )
        names = {t.tool_name for t in cap.tools}
        assert "createCharge" in names
        assert "listCharges" in names

    def test_request_body_properties_become_fields(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_OPENAPI_SCHEMA,
            source_kind=SOURCE_KIND_OPENAPI,
            source_id="test-openapi",
        )
        create_tool = next(t for t in cap.tools if t.tool_name == "createCharge")
        field_names = {f.field_name for f in create_tool.fields}
        assert "amount" in field_names
        assert "currency" in field_names

    def test_query_parameters_become_fields(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-3166.yaml"])
        cap = capture(
            SAMPLE_OPENAPI_SCHEMA,
            source_kind=SOURCE_KIND_OPENAPI,
            source_id="test-openapi",
        )
        list_tool = next(t for t in cap.tools if t.tool_name == "listCharges")
        field_names = {f.field_name for f in list_tool.fields}
        assert "country" in field_names


# ── GraphQL normalization ────────────────────────────────────────────


SAMPLE_GRAPHQL_SCHEMA = {
    "__schema": {
        "queryType": {"name": "Query"},
        "mutationType": {"name": "Mutation"},
        "types": [
            {
                "kind": "OBJECT",
                "name": "Query",
                "fields": [
                    {
                        "name": "currency",
                        "description": "Get a currency by ISO 4217 code",
                        "args": [
                            {
                                "name": "code",
                                "description": "Currency code",
                                "type": {"kind": "SCALAR", "name": "String"},
                            },
                        ],
                    },
                ],
            },
            {
                "kind": "OBJECT",
                "name": "Mutation",
                "fields": [
                    {
                        "name": "createInvoice",
                        "description": "Create an invoice",
                        "args": [
                            {
                                "name": "amount",
                                "type": {"kind": "SCALAR", "name": "Int"},
                            },
                            {
                                "name": "country",
                                "type": {"kind": "SCALAR", "name": "String"},
                            },
                        ],
                    },
                ],
            },
        ],
    },
}


class TestGraphQlCapture:
    def test_query_and_mutation_fields_become_tools(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_GRAPHQL_SCHEMA,
            source_kind=SOURCE_KIND_GRAPHQL,
            source_id="test-gql",
        )
        names = {t.tool_name for t in cap.tools}
        assert "currency" in names
        assert "createInvoice" in names

    def test_arguments_become_fields(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_GRAPHQL_SCHEMA,
            source_kind=SOURCE_KIND_GRAPHQL,
            source_id="test-gql",
        )
        invoice = next(
            t for t in cap.tools if t.tool_name == "createInvoice"
        )
        names = {f.field_name for f in invoice.fields}
        assert "amount" in names
        assert "country" in names


# ── Content-addressing ───────────────────────────────────────────────


class TestContentAddressing:
    def test_schema_hash_stable_across_two_captures(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        c1 = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            captured_at="2026-04-26T00:00:00Z",
        )
        c2 = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            captured_at="2026-04-26T00:00:00Z",
        )
        assert c1.schema_hash == c2.schema_hash
        assert c1.capture_hash == c2.capture_hash

    def test_different_pack_stacks_change_capture_hash_not_schema_hash(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        c1 = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            captured_at="2026-04-26T00:00:00Z",
        )
        configure_packs(extra_paths=[
            _seed_dir() / "iso-4217.yaml",
            _seed_dir() / "iso-3166.yaml",
        ])
        c2 = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            captured_at="2026-04-26T00:00:00Z",
        )
        assert c1.schema_hash == c2.schema_hash
        assert c1.capture_hash != c2.capture_hash

    def test_unknown_source_kind_raises(self):
        with pytest.raises(ValueError):
            capture({}, source_kind="rest", source_id="x")


# ── capture_to_dir storage idempotency ───────────────────────────────


class TestCaptureToDir:
    def test_writes_under_source_kind_subdir(self, tmp_path):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        path = capture_to_dir(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="my-tool",
            out_dir=tmp_path,
            captured_at="2026-04-26T00:00:00Z",
        )
        assert path.exists()
        assert path.parent.name == "mcp"
        assert path.name == "my-tool.json"

    def test_filename_safety(self, tmp_path):
        """Source IDs with slashes, spaces, etc. must be sanitized."""
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        path = capture_to_dir(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="weird/name with spaces!",
            out_dir=tmp_path,
            captured_at="2026-04-26T00:00:00Z",
        )
        assert path.exists()
        assert "/" not in path.name
        assert " " not in path.name

    def test_idempotent_writes(self, tmp_path):
        """Two captures with identical inputs produce byte-identical
        files."""
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        p1 = capture_to_dir(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            out_dir=tmp_path,
            captured_at="2026-04-26T00:00:00Z",
        )
        first = p1.read_bytes()
        p2 = capture_to_dir(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            out_dir=tmp_path,
            captured_at="2026-04-26T00:00:00Z",
        )
        second = p2.read_bytes()
        assert first == second


# ── Coverage map and classifier corpus aggregations ──────────────────


class TestAggregations:
    def _three_captures(self):
        configure_packs(extra_paths=sorted((_seed_dir()).glob("*.yaml")))
        return [
            capture(
                SAMPLE_MCP_SCHEMA,
                source_kind=SOURCE_KIND_MCP,
                source_id="mcp-a",
                captured_at="2026-04-26T00:00:00Z",
            ),
            capture(
                SAMPLE_OPENAPI_SCHEMA,
                source_kind=SOURCE_KIND_OPENAPI,
                source_id="oa-b",
                captured_at="2026-04-26T00:00:00Z",
            ),
            capture(
                SAMPLE_GRAPHQL_SCHEMA,
                source_kind=SOURCE_KIND_GRAPHQL,
                source_id="gql-c",
                captured_at="2026-04-26T00:00:00Z",
            ),
        ]

    def test_coverage_map_shape(self):
        captures = self._three_captures()
        cov = build_coverage_map(captures)
        assert "by_source" in cov
        assert "by_dimension" in cov
        assert "totals" in cov
        assert cov["totals"]["n_sources"] == 3
        ids = {row["source_id"] for row in cov["by_source"]}
        assert ids == {"mcp-a", "oa-b", "gql-c"}

    def test_classifier_corpus_rows(self):
        captures = self._three_captures()
        rows = build_classifier_corpus(captures)
        assert len(rows) > 0
        # Each row has the contract fields.
        for r in rows:
            for k in (
                "source_kind", "source_id", "tool", "field",
                "dimensions", "confidence",
            ):
                assert k in r
            # Synthetic _description records are excluded from corpus.
            assert r["field"] != "_description"

    def test_classifier_corpus_includes_currency_examples(self):
        captures = self._three_captures()
        rows = build_classifier_corpus(captures)
        currency_rows = [r for r in rows if "currency_code" in r["dimensions"]]
        assert len(currency_rows) > 0


# ── Forward-compatibility shape pinning ──────────────────────────────


class TestForwardCompatibility:
    """The capture record JSON shape is the input contract for the
    deferred Part B equivalence detector. Pin the top-level keys so
    a future Part B can rely on them without forcing a migration."""

    def test_top_level_keys_present(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
            captured_at="2026-04-26T00:00:00Z",
        )
        d = cap.to_dict()
        for k in (
            "source_kind", "source_id", "schema_hash", "captured_at",
            "active_packs", "tools", "aggregate", "capture_hash",
        ):
            assert k in d, f"missing top-level key {k!r}"

    def test_aggregate_keys_present(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
        )
        agg = cap.to_dict()["aggregate"]
        for k in (
            "n_tools", "n_fields", "n_declared", "n_inferred",
            "n_unknown", "n_dim_signals", "dim_hits",
        ):
            assert k in agg

    def test_field_record_carries_classifier_outputs(self):
        configure_packs(extra_paths=[_seed_dir() / "iso-4217.yaml"])
        cap = capture(
            SAMPLE_MCP_SCHEMA,
            source_kind=SOURCE_KIND_MCP,
            source_id="x",
        )
        d = cap.to_dict()
        any_field = d["tools"][0]["fields"][0]
        for k in ("field_name", "dimensions", "confidence"):
            assert k in any_field
