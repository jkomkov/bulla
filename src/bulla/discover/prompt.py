"""Structured prompt construction for LLM-powered dimension discovery.

Builds a prompt from tool schemas + existing pack dimensions, using rigid
delimiters (---BEGIN_PACK--- / ---END_PACK---) for reliable parsing.
"""

from __future__ import annotations

import uuid
from typing import Any

_SYSTEM_PREAMBLE = """\
You are analyzing MCP (Model Context Protocol) tool schemas for implicit \
convention assumptions that could cause silent failures when tools are \
composed by an AI agent.

These schemas may have sparse or empty descriptions. Reason from field \
names, types, and cross-tool patterns when descriptions are unavailable."""

_TASK_INSTRUCTION = """\
TASK: Identify fields across tools where the same semantic concept has an \
undeclared interpretation convention. A convention ambiguity exists when two \
tools could reasonably disagree on how to interpret a shared field — e.g., \
one tool expects zero-based page indices while another expects one-based, \
or one uses absolute file paths while another uses repository-relative paths.

For each genuine convention ambiguity you find, output a dimension definition."""

_OUTPUT_FORMAT = """\
OUTPUT FORMAT (strict YAML, no commentary outside the delimiters):
---BEGIN_PACK---
pack_name: "discovered_{session_id}"
pack_version: "0.1.0"
dimensions:
  dimension_name:
    description: "What convention this dimension captures"
    known_values: ["value1", "value2"]
    field_patterns: ["*_suffix", "prefix_*", "exact_name"]
    description_keywords: ["keyword1", "keyword2"]
    refines: "parent_dimension_or_null"
---END_PACK---"""

_RULES = """\
RULES:
- Only propose dimensions for genuine convention ambiguities where two \
tools could silently disagree
- You may refine existing dimensions (use the refines field to point to \
the parent dimension name)
- field_patterns use glob syntax: *_suffix matches any field ending in \
_suffix, prefix_* matches any field starting with prefix_, exact_name \
matches exactly
- Minimum per dimension: description + at least one of field_patterns \
or description_keywords
- If you find no genuine convention ambiguities, output an empty \
dimensions block
- Do NOT output any text outside the ---BEGIN_PACK--- / ---END_PACK--- \
delimiters"""


def _format_existing_dimensions(dims: dict[str, Any]) -> str:
    """Format existing dimensions for the prompt context."""
    if not dims:
        return "(none)"
    lines: list[str] = []
    for name, defn in sorted(dims.items()):
        desc = defn.get("description", "")
        lines.append(f"  {name}: {desc}")
    return "\n".join(lines)


def _format_tool_schemas(tools: list[dict[str, Any]]) -> str:
    """Format tool schemas for the prompt, showing fields with types and descriptions."""
    lines: list[str] = []
    for tool in tools:
        name = tool.get("name", "unknown")
        desc = tool.get("description", "")
        lines.append(f"  {name}: {desc}")

        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        if props:
            lines.append("    Fields:")
            for field_name, field_def in sorted(props.items()):
                ftype = field_def.get("type", "any")
                fdesc = field_def.get("description", "")
                if fdesc:
                    lines.append(f"      {field_name} ({ftype}): {fdesc}")
                else:
                    lines.append(f"      {field_name} ({ftype})")
        else:
            lines.append("    Fields: (none)")
        lines.append("")
    return "\n".join(lines)


def build_prompt(
    tools: list[dict[str, Any]],
    existing_dimensions: dict[str, Any],
    session_id: str | None = None,
) -> str:
    """Build the discovery prompt from tool schemas and existing dimensions.

    Args:
        tools: List of MCP tool dicts (name, description, inputSchema).
        existing_dimensions: Merged dimension dict from active pack stack.
        session_id: Optional session identifier for the pack name. Auto-generated if None.

    Returns:
        The complete prompt string.
    """
    if session_id is None:
        session_id = uuid.uuid4().hex[:8]

    sections = [
        _SYSTEM_PREAMBLE,
        "",
        "EXISTING DIMENSIONS (do not duplicate, but you MAY propose refinements):",
        _format_existing_dimensions(existing_dimensions),
        "",
        "TOOL SCHEMAS:",
        _format_tool_schemas(tools),
        _TASK_INSTRUCTION,
        "",
        _OUTPUT_FORMAT.replace("{session_id}", session_id),
        "",
        _RULES,
    ]
    return "\n".join(sections)


def parse_response(raw: str) -> str | None:
    """Extract the YAML block from an LLM response.

    Tries ---BEGIN_PACK--- / ---END_PACK--- delimiters first,
    falls back to markdown fenced code blocks.

    Returns the extracted YAML string, or None if no block found.
    """
    begin = "---BEGIN_PACK---"
    end = "---END_PACK---"

    if begin in raw and end in raw:
        start_idx = raw.index(begin) + len(begin)
        end_idx = raw.index(end)
        return raw[start_idx:end_idx].strip()

    for fence in ("```yaml", "```yml", "```"):
        if fence in raw:
            start_idx = raw.index(fence) + len(fence)
            rest = raw[start_idx:]
            if "```" in rest:
                end_idx = rest.index("```")
                return rest[:end_idx].strip()

    return None
