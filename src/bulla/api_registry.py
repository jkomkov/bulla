"""API/MCP Schema Indexing — the Phase 7 layer.

A *separate parallel registry* from the convention pack layer. The
distinction is load-bearing:

  - Convention packs are the **codomain** of δ₀ — the dimension
    vocabulary space (what dimensions exist, what values they take).
  - This API/tool registry is the **domain** of δ₀ — the tool-surface
    space (what fields exist, what data flows through them).

The schema-capture pipeline ingests an MCP/OpenAPI/GraphQL schema,
normalizes it, content-addresses the result, runs the classifier
against the active pack stack, and emits a structured record per
schema. The aggregate record set is:

  - the **classifier-training corpus** (every (field, dimension,
    confidence) triple is a labeled example);
  - the **coverage map** (which dimensions are matched per server, where
    `unknown_dimensions` cluster);
  - **forward-compatible** with the deferred Part B equivalence detector
    — captured schemas record classifier outputs in a structured way
    so the future detector can consume them directly.

The pipeline is intentionally storage-agnostic: it produces JSON
records that can be committed to disk, persisted to a database, or
published to an aggregator. ``capture_to_dir`` is the reference
implementation that writes one JSON per schema into a directory.

Phase 7 sprint scope: ship the pipeline + ~100 indexed schemas. The
coverage of *which* ~100 schemas is enumerated in
``scripts/standards-ingest/build_phase7_index.py``; the pipeline
itself is the load-bearing asset and accepts any future schema
without modification.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bulla.infer.classifier import (
    classify_description,
    classify_field_by_name,
    classify_schema_signal,
    get_active_pack_refs,
)
from bulla.infer.mcp import extract_field_infos
from bulla.model import PackRef


# ── Source kinds ──────────────────────────────────────────────────────


SOURCE_KIND_MCP = "mcp"
SOURCE_KIND_OPENAPI = "openapi"
SOURCE_KIND_GRAPHQL = "graphql"


# ── Captured-record dataclasses ──────────────────────────────────────


@dataclass(frozen=True)
class FieldRecord:
    """One field's classification under the active pack stack.

    ``confidence`` is the strongest tier across all matching signals
    (``declared`` > ``inferred`` > ``unknown``). ``dimensions`` is the
    full multi-set: a single field may classify under multiple
    dimensions if its name, schema, and description signals each
    match a different one (the equivalence detector uses these).
    """

    field_name: str
    dimensions: tuple[str, ...]
    confidence: str
    schema_type: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "field_name": self.field_name,
            "dimensions": list(self.dimensions),
            "confidence": self.confidence,
        }
        if self.schema_type:
            d["schema_type"] = self.schema_type
        if self.description:
            d["description"] = self.description
        return d


@dataclass(frozen=True)
class ToolRecord:
    """One tool's classification — name, description, plus per-field
    records."""

    tool_name: str
    description: str
    fields: tuple[FieldRecord, ...]

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "description": self.description,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class SchemaCapture:
    """A complete capture: source provenance + per-tool records +
    aggregate counts.

    ``schema_hash`` is content-addressed: SHA-256 of the
    canonicalized source dict. Two captures with byte-identical input
    schemas produce identical schema_hash regardless of when they were
    captured.

    ``active_packs`` records the pack stack under which classification
    ran. A re-classification under a different pack stack produces a
    different ``capture_hash`` (which folds in active_packs) but the
    same ``schema_hash`` (which doesn't).
    """

    source_kind: str
    source_id: str  # e.g. "airtable-mcp", "stripe-api"
    schema_hash: str
    captured_at: str
    active_packs: tuple[PackRef, ...]
    tools: tuple[ToolRecord, ...]
    n_fields: int
    n_declared: int
    n_inferred: int
    n_unknown: int
    n_dim_signals: int
    dim_hits: dict[str, int]

    @property
    def capture_hash(self) -> str:
        """Hash of the full capture record (schema + active_packs +
        classifier output). Stable across re-captures with identical
        inputs."""
        d = self.to_dict(include_capture_hash=False)
        return hashlib.sha256(
            json.dumps(d, sort_keys=True).encode()
        ).hexdigest()

    def to_dict(self, *, include_capture_hash: bool = True) -> dict:
        d: dict = {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "schema_hash": self.schema_hash,
            "captured_at": self.captured_at,
            "active_packs": [p.to_dict() for p in self.active_packs],
            "tools": [t.to_dict() for t in self.tools],
            "aggregate": {
                "n_tools": len(self.tools),
                "n_fields": self.n_fields,
                "n_declared": self.n_declared,
                "n_inferred": self.n_inferred,
                "n_unknown": self.n_unknown,
                "n_dim_signals": self.n_dim_signals,
                "dim_hits": dict(self.dim_hits),
            },
        }
        if include_capture_hash:
            d["capture_hash"] = self.capture_hash
        return d


# ── Source-kind-specific normalizers ─────────────────────────────────


def _hash_schema(schema: dict) -> str:
    """Content-address a schema dict deterministically."""
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode()
    ).hexdigest()


def _normalize_mcp(raw: dict) -> list[dict]:
    """MCP captures store `{tools: [{name, description, inputSchema}, ...]}`.
    Returns a list of tool dicts in the shape ``extract_field_infos``
    expects (Bulla's MCP convention).
    """
    tools = raw.get("tools", [])
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, dict)]


def _normalize_openapi(raw: dict) -> list[dict]:
    """Convert an OpenAPI 3.x doc into a list of MCP-shaped tool dicts.

    Each operation (path × method) becomes one "tool" with:
      - ``name`` = ``operationId`` (or ``method_path`` fallback)
      - ``description`` = ``summary`` + ``description``
      - ``inputSchema`` = a JSON Schema constructed from the operation's
        parameters + requestBody.

    This is a deliberately minimal normalization — enough to feed the
    classifier, not a faithful round-trip of the OpenAPI doc.
    """
    paths = raw.get("paths", {}) or {}
    if not isinstance(paths, dict):
        return []

    out: list[dict] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or f"{method}_{path}"
            summary = op.get("summary", "") or ""
            desc = op.get("description", "") or ""
            full_desc = f"{summary}\n\n{desc}".strip()

            properties: dict[str, dict] = {}
            required: list[str] = []
            for p in op.get("parameters", []) or []:
                if not isinstance(p, dict):
                    continue
                name = p.get("name")
                if not isinstance(name, str):
                    continue
                schema = p.get("schema") or {}
                if isinstance(schema, dict):
                    if p.get("description") and "description" not in schema:
                        schema = dict(schema)
                        schema["description"] = p["description"]
                    properties[name] = schema
                if p.get("required"):
                    required.append(name)
            body = op.get("requestBody", {})
            if isinstance(body, dict):
                content = body.get("content", {}) or {}
                for media_type, body_def in content.items():
                    if not isinstance(body_def, dict):
                        continue
                    body_schema = body_def.get("schema")
                    if isinstance(body_schema, dict):
                        body_props = body_schema.get("properties", {}) or {}
                        if isinstance(body_props, dict):
                            properties.update(body_props)
                    break  # only one body media type

            tool = {
                "name": op_id,
                "description": full_desc,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
            out.append(tool)

    return out


def _normalize_graphql(raw: dict) -> list[dict]:
    """Convert a GraphQL introspection result into a list of MCP-shaped
    tool dicts.

    Each top-level Query / Mutation field becomes one "tool"; that
    field's arguments become the inputSchema properties.

    Accepts the standard introspection-query shape:
    ``{__schema: {queryType: {...}, mutationType: {...}, types: [...]}}``.
    """
    schema = raw.get("__schema") or raw.get("data", {}).get("__schema")
    if not isinstance(schema, dict):
        return []

    types = schema.get("types") or []
    types_by_name = {t.get("name"): t for t in types if isinstance(t, dict)}

    out: list[dict] = []
    for op_kind in ("queryType", "mutationType"):
        op_type_ref = schema.get(op_kind)
        if not isinstance(op_type_ref, dict):
            continue
        op_type_name = op_type_ref.get("name")
        op_type = types_by_name.get(op_type_name)
        if not isinstance(op_type, dict):
            continue
        for fld in op_type.get("fields", []) or []:
            if not isinstance(fld, dict):
                continue
            name = fld.get("name")
            if not isinstance(name, str):
                continue
            description = fld.get("description") or ""
            properties: dict[str, dict] = {}
            for arg in fld.get("args", []) or []:
                if not isinstance(arg, dict):
                    continue
                arg_name = arg.get("name")
                if not isinstance(arg_name, str):
                    continue
                arg_type = arg.get("type", {}) or {}
                # Walk the GraphQL type wrapper (NON_NULL, LIST, etc.)
                # down to the named type for a coarse `schema_type` hint.
                inner = arg_type
                while isinstance(inner, dict) and inner.get("ofType"):
                    inner = inner["ofType"]
                gql_kind = inner.get("kind") if isinstance(inner, dict) else ""
                gql_type_name = inner.get("name") if isinstance(inner, dict) else ""
                schema_type = "string"
                if gql_kind == "SCALAR":
                    if gql_type_name in {"Int", "Float"}:
                        schema_type = "number"
                    elif gql_type_name == "Boolean":
                        schema_type = "boolean"
                properties[arg_name] = {
                    "type": schema_type,
                    "description": arg.get("description") or "",
                }
            tool = {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": [],
                },
            }
            out.append(tool)
    return out


_NORMALIZERS = {
    SOURCE_KIND_MCP: _normalize_mcp,
    SOURCE_KIND_OPENAPI: _normalize_openapi,
    SOURCE_KIND_GRAPHQL: _normalize_graphql,
}


# ── Capture pipeline ─────────────────────────────────────────────────


def _classify_tool(tool: dict) -> ToolRecord:
    """Run the full classifier pipeline against a single MCP-shaped
    tool dict and return a ``ToolRecord``."""
    field_infos = extract_field_infos(tool)
    desc_results = classify_description(tool.get("description") or "")

    field_records: list[FieldRecord] = []
    for fi in field_infos:
        results: list = []
        name_match = classify_field_by_name(
            fi.name, schema_type=fi.schema_type
        )
        if name_match is not None:
            results.append(name_match)
        results.extend(classify_schema_signal(fi))
        if fi.description:
            results.extend(classify_description(fi.description))
        confidences = {r.confidence for r in results}
        if "declared" in confidences:
            conf = "declared"
        elif "inferred" in confidences:
            conf = "inferred"
        elif results:
            conf = "unknown"
        else:
            conf = "unknown"
        dims = tuple(sorted({r.dimension for r in results}))
        field_records.append(FieldRecord(
            field_name=fi.name,
            dimensions=dims,
            confidence=conf,
            schema_type=fi.schema_type or "",
            description=fi.description or "",
        ))

    # Tool-level description signals are recorded as a synthetic
    # ``_description`` field so the equivalence detector can see them
    # without conflating with real fields.
    if desc_results:
        dims = tuple(sorted({r.dimension for r in desc_results}))
        field_records.append(FieldRecord(
            field_name="_description",
            dimensions=dims,
            confidence="inferred",
            description=tool.get("description") or "",
        ))

    return ToolRecord(
        tool_name=tool.get("name", ""),
        description=tool.get("description", "") or "",
        fields=tuple(field_records),
    )


def capture(
    raw_schema: dict,
    *,
    source_kind: str,
    source_id: str,
    captured_at: str | None = None,
) -> SchemaCapture:
    """Full pipeline: raw schema → classified ``SchemaCapture``.

    Pure: depends only on the active pack stack (configured via
    ``configure_packs`` or default-loaded by the classifier on first
    use).

    Args:
        raw_schema: The source schema dict (an MCP manifest, an
            OpenAPI 3.x doc, or a GraphQL introspection result).
        source_kind: One of ``mcp``, ``openapi``, ``graphql``.
        source_id: Identifying name for the source (e.g.
            ``"stripe-openapi"``, ``"airtable-mcp"``).
        captured_at: ISO-8601 UTC timestamp; defaults to ``now()``.

    Returns:
        A frozen ``SchemaCapture`` record with per-tool, per-field
        classification + aggregate counts.
    """
    if source_kind not in _NORMALIZERS:
        raise ValueError(
            f"Unknown source_kind {source_kind!r}; expected one of "
            f"{sorted(_NORMALIZERS.keys())}"
        )
    if captured_at is None:
        captured_at = datetime.now(timezone.utc).isoformat()

    schema_hash = _hash_schema(raw_schema)

    tools_raw = _NORMALIZERS[source_kind](raw_schema)
    tool_records = tuple(_classify_tool(t) for t in tools_raw)

    # Aggregate counts.
    n_fields = 0
    n_declared = 0
    n_inferred = 0
    n_unknown = 0
    n_dim_signals = 0
    dim_hits: dict[str, int] = {}
    for tr in tool_records:
        for fr in tr.fields:
            if fr.field_name == "_description":
                # Tool-level signal — don't count as a field, but count
                # its dimension hits.
                for d in fr.dimensions:
                    n_dim_signals += 1
                    dim_hits[d] = dim_hits.get(d, 0) + 1
                continue
            n_fields += 1
            if fr.confidence == "declared":
                n_declared += 1
            elif fr.confidence == "inferred":
                n_inferred += 1
            else:
                n_unknown += 1
            for d in fr.dimensions:
                n_dim_signals += 1
                dim_hits[d] = dim_hits.get(d, 0) + 1

    return SchemaCapture(
        source_kind=source_kind,
        source_id=source_id,
        schema_hash=schema_hash,
        captured_at=captured_at,
        active_packs=get_active_pack_refs(),
        tools=tool_records,
        n_fields=n_fields,
        n_declared=n_declared,
        n_inferred=n_inferred,
        n_unknown=n_unknown,
        n_dim_signals=n_dim_signals,
        dim_hits=dim_hits,
    )


# ── Storage helpers ──────────────────────────────────────────────────


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(s: str) -> str:
    return _SAFE_ID_RE.sub("_", s).strip("_") or "unnamed"


def capture_to_dir(
    raw_schema: dict,
    *,
    source_kind: str,
    source_id: str,
    out_dir: Path,
    captured_at: str | None = None,
) -> Path:
    """Capture + write to ``<out_dir>/<source_kind>/<source_id>.json``.

    Returns the written path. Idempotent: re-running with the same
    raw_schema and pack stack produces a byte-identical file because
    every component is content-addressed.
    """
    rec = capture(
        raw_schema,
        source_kind=source_kind,
        source_id=source_id,
        captured_at=captured_at,
    )
    target_dir = out_dir / source_kind
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_filename(source_id)}.json"
    target.write_text(
        json.dumps(rec.to_dict(), indent=2),
        encoding="utf-8",
    )
    return target


def build_coverage_map(captures: list[SchemaCapture]) -> dict:
    """Aggregate per-source dimension hits into a coverage map.

    Output shape::

        {
            "by_source": [
                {
                    "source_kind": "mcp",
                    "source_id": "airtable-mcp",
                    "n_tools": 7,
                    "n_fields": 15,
                    "n_unknown": 12,
                    "dim_hits": {"language_code": 1}
                },
                ...
            ],
            "by_dimension": [
                {
                    "dimension": "currency_code",
                    "total_hits": 5,
                    "sources": ["stripe-openapi", "shopify-graphql", ...]
                },
                ...
            ],
            "totals": {
                "n_sources": 100,
                "n_tools": 1234,
                "n_fields": 9876,
                "n_unknown": 4321
            }
        }
    """
    by_source: list[dict] = []
    dim_to_sources: dict[str, set[str]] = {}
    dim_total: dict[str, int] = {}
    n_tools = 0
    n_fields = 0
    n_unknown = 0

    for cap in captures:
        by_source.append({
            "source_kind": cap.source_kind,
            "source_id": cap.source_id,
            "n_tools": len(cap.tools),
            "n_fields": cap.n_fields,
            "n_unknown": cap.n_unknown,
            "dim_hits": dict(cap.dim_hits),
        })
        n_tools += len(cap.tools)
        n_fields += cap.n_fields
        n_unknown += cap.n_unknown
        for dim, count in cap.dim_hits.items():
            dim_to_sources.setdefault(dim, set()).add(cap.source_id)
            dim_total[dim] = dim_total.get(dim, 0) + count

    by_dimension = [
        {
            "dimension": dim,
            "total_hits": dim_total[dim],
            "sources": sorted(dim_to_sources[dim]),
        }
        for dim in sorted(dim_total.keys(), key=lambda d: -dim_total[d])
    ]

    return {
        "by_source": sorted(
            by_source,
            key=lambda r: (r["source_kind"], r["source_id"]),
        ),
        "by_dimension": by_dimension,
        "totals": {
            "n_sources": len(captures),
            "n_tools": n_tools,
            "n_fields": n_fields,
            "n_unknown": n_unknown,
        },
    }


def build_classifier_corpus(captures: list[SchemaCapture]) -> list[dict]:
    """Flatten every (field, dimension, confidence) triple into the
    classifier-training corpus.

    Each row::

        {
            "source_kind": "openapi",
            "source_id": "stripe-openapi",
            "tool": "create_charge",
            "field": "currency",
            "schema_type": "string",
            "description": "Three-letter ISO currency code",
            "dimensions": ["currency_code"],
            "confidence": "declared"
        }

    The corpus is the load-bearing input for the deferred Part B
    equivalence detector — each row is a labeled training example.
    """
    rows: list[dict] = []
    for cap in captures:
        for tool in cap.tools:
            for fr in tool.fields:
                if fr.field_name == "_description":
                    continue
                rows.append({
                    "source_kind": cap.source_kind,
                    "source_id": cap.source_id,
                    "tool": tool.tool_name,
                    "field": fr.field_name,
                    "schema_type": fr.schema_type,
                    "description": fr.description,
                    "dimensions": list(fr.dimensions),
                    "confidence": fr.confidence,
                })
    return rows
