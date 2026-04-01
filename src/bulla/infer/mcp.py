"""Infer a proto-composition YAML from an MCP manifest JSON.

Reads the `tools` array from an MCP server's `list_tools` response and
produces a YAML composition with inferred semantic dimensions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bulla.infer.classifier import (
    FieldInfo,
    InferredDimension,
    classify_fields,
    classify_tool_rich,
)


# ── Field extraction ───────────────────────────────────────────────────

MAX_NESTING_DEPTH = 3


def _extract_field_infos_from_schema(
    schema: dict[str, Any],
    prefix: str = "",
    depth: int = 0,
) -> list[FieldInfo]:
    """Recursively extract FieldInfo objects from a JSON Schema."""
    if depth >= MAX_NESTING_DEPTH or not isinstance(schema, dict):
        return []
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return []

    results: list[FieldInfo] = []
    for name, prop_schema in props.items():
        full_name = f"{prefix}.{name}" if prefix else name
        if not isinstance(prop_schema, dict):
            results.append(FieldInfo(name=full_name))
            continue

        enum_raw = prop_schema.get("enum")
        enum_val = None
        if isinstance(enum_raw, list):
            enum_val = tuple(str(v) for v in enum_raw)

        minimum = prop_schema.get("minimum")
        maximum = prop_schema.get("maximum")

        results.append(FieldInfo(
            name=full_name,
            schema_type=prop_schema.get("type"),
            format=prop_schema.get("format"),
            enum=enum_val,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
            pattern=prop_schema.get("pattern"),
            description=prop_schema.get("description"),
        ))

        if prop_schema.get("type") == "object":
            results.extend(_extract_field_infos_from_schema(
                prop_schema, full_name, depth + 1,
            ))

    return results


def extract_field_infos(tool: dict[str, Any]) -> list[FieldInfo]:
    """Extract FieldInfo objects from a tool's input/output schemas."""
    infos: list[FieldInfo] = []
    input_schema = tool.get("inputSchema", {})
    infos.extend(_extract_field_infos_from_schema(input_schema))
    output_schema = tool.get("outputSchema", {})
    infos.extend(_extract_field_infos_from_schema(output_schema))

    seen: set[str] = set()
    deduped: list[FieldInfo] = []
    for fi in infos:
        if fi.name not in seen:
            seen.add(fi.name)
            deduped.append(fi)
    return deduped


def _extract_tool_fields(tool: dict[str, Any]) -> list[str]:
    """Extract all field names from a tool's input/output schemas.

    Backward-compatible: returns flat list of field name strings.
    Includes nested fields as dot-path names.
    """
    infos = extract_field_infos(tool)
    return [fi.name for fi in infos]


# ── Shared dimension detection ─────────────────────────────────────────


def _find_shared_dimensions(
    tools_dims: dict[str, list[InferredDimension]],
) -> list[dict[str, Any]]:
    """Find edges: tools sharing inferred dimensions of the same type."""
    edges: list[dict[str, Any]] = []
    tool_names = list(tools_dims.keys())
    for i, t1 in enumerate(tool_names):
        for t2 in tool_names[i + 1:]:
            shared: list[dict[str, str]] = []
            dims_1 = {d.dimension: d for d in tools_dims[t1]}
            dims_2 = {d.dimension: d for d in tools_dims[t2]}
            for dim_name in dims_1:
                if dim_name in dims_2:
                    shared.append({
                        "name": f"{dim_name}_match",
                        "from_field": dims_1[dim_name].field_name,
                        "to_field": dims_2[dim_name].field_name,
                    })
            if shared:
                edges.append({
                    "from": t1,
                    "to": t2,
                    "dimensions": shared,
                })
    return edges


# ── Composition inference ──────────────────────────────────────────────


def infer_from_manifest(manifest_path: Path) -> str:
    """Read an MCP manifest JSON and produce a proto-composition YAML."""
    data = json.loads(manifest_path.read_text())

    tools_list: list[dict[str, Any]]
    if isinstance(data, list):
        tools_list = data
    elif isinstance(data, dict) and "tools" in data:
        tools_list = data["tools"]
    else:
        raise ValueError(
            f"Expected a JSON object with a 'tools' array or a plain array "
            f"of tool objects in {manifest_path}"
        )

    composition: dict[str, Any] = {
        "name": f"inferred-from-{manifest_path.stem}",
        "tools": {},
        "edges": [],
    }

    tools_dims: dict[str, list[InferredDimension]] = {}

    for tool in tools_list:
        name = tool.get("name", "unknown_tool")
        safe_name = name.replace("-", "_").replace(" ", "_")

        field_infos = extract_field_infos(tool)
        fields = [fi.name for fi in field_infos]
        inferred = classify_tool_rich(tool, field_infos=field_infos)

        inferred_field_names = {d.field_name for d in inferred}
        observable = [f for f in fields if f not in inferred_field_names]

        composition["tools"][safe_name] = {
            "internal_state": fields if fields else ["_placeholder"],
            "observable_schema": observable if observable else fields[:1] if fields else ["_placeholder"],
        }
        tools_dims[safe_name] = inferred

    composition["edges"] = _find_shared_dimensions(tools_dims)

    lines: list[str] = []
    lines.append(f"# Proto-composition inferred from {manifest_path.name}")
    lines.append("# REVIEW all fields and edges before running bulla diagnose")
    lines.append("")
    lines.append(yaml.dump(composition, default_flow_style=False, sort_keys=False))

    for tool_name, dims in tools_dims.items():
        if dims:
            lines.append(f"# REVIEW: {tool_name} inferred dimensions:")
            for d in dims:
                sources_str = "+".join(d.sources) if d.sources else "name"
                lines.append(
                    f"#   {d.field_name} -> {d.dimension} "
                    f"(confidence: {d.confidence}, sources: {sources_str})"
                )
            lines.append("")

    return "\n".join(lines)
