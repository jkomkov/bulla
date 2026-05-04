"""Shared helpers for the runtime adapters.

Both ``langgraph_runtime`` and ``crewai_runtime`` need the same three
operations: walk a Pydantic args_schema (or fallback ``args`` dict)
into a flat field-name list, walk a user-supplied output JSONSchema,
and infer ``SemanticDimension`` entries on an edge from the shared
field-name set on its endpoints. Centralizing them here is anti-bloat:
no logic in the adapter files drifts ahead of the other.

Module is private (``_runtime_common``); each adapter re-uses it via
direct import. Not part of the bulla public API.
"""

from __future__ import annotations

from typing import Any

from bulla.infer.mcp import extract_field_infos
from bulla.model import SemanticDimension, ToolSpec


def tool_input_fields(tool_obj: Any) -> list[str]:
    """Extract input field names from a tool object.

    Tries ``args_schema`` (raw Pydantic class) first, falls back to
    ``args`` (resolved JSONSchema dict on newer LangChain / CrewAI
    versions). Returns a flat list of field names; nested fields are
    expanded via ``bulla.infer.mcp.extract_field_infos``.

    Returns ``[]`` when the tool exposes neither attribute or the
    Pydantic model rejects ``model_json_schema()``.
    """
    schema_dict: dict[str, Any] | None = None
    args_schema = getattr(tool_obj, "args_schema", None)
    if args_schema is not None:
        try:
            schema_dict = args_schema.model_json_schema()
        except Exception:
            schema_dict = None
    if schema_dict is None:
        args = getattr(tool_obj, "args", None)
        if isinstance(args, dict):
            schema_dict = {"type": "object", "properties": args}
    if not schema_dict:
        return []
    pseudo_tool = {"inputSchema": schema_dict}
    infos = extract_field_infos(pseudo_tool)
    return [info.name for info in infos]


def output_fields_from_kwarg(
    tool_key: str, output_schemas: dict[str, dict]
) -> list[str]:
    """Extract output field names from a user-supplied
    ``output_schemas[tool_key]`` JSONSchema.

    Returns ``[]`` when the key is missing or the value isn't a dict.
    """
    schema = output_schemas.get(tool_key)
    if not isinstance(schema, dict):
        return []
    pseudo_tool = {"outputSchema": schema}
    infos = extract_field_infos(pseudo_tool)
    return [info.name for info in infos]


def shared_field_dimensions(
    from_spec: ToolSpec, to_spec: ToolSpec
) -> tuple[SemanticDimension, ...]:
    """Build ``SemanticDimension`` entries for fields appearing on
    both sides of an edge.

    The convention name is ``{field}_match``. ``from_field`` and
    ``to_field`` are equal because the runtime adapters infer edges
    from same-named fields (cross-named edges require an explicit
    framework annotation, which neither LangGraph nor CrewAI exposes
    in the tool schema today).
    """
    from_fields = set(from_spec.internal_state)
    to_fields = set(to_spec.internal_state)
    shared = sorted(from_fields & to_fields)
    return tuple(
        SemanticDimension(name=f"{f}_match", from_field=f, to_field=f)
        for f in shared
    )


__all__ = [
    "output_fields_from_kwarg",
    "shared_field_dimensions",
    "tool_input_fields",
]
