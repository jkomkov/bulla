"""Structured prompt construction for LLM-powered dimension discovery.

Builds a prompt from tool schemas + existing pack dimensions, using rigid
delimiters (---BEGIN_PACK--- / ---END_PACK---) for reliable parsing.

Guided discovery (v0.26.0) adds batched obligation probing:
``build_guided_prompt`` / ``parse_guided_response`` evaluate multiple
obligations in a single LLM call using numbered verdict delimiters.
"""

from __future__ import annotations

import re
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


# ── Guided discovery (v0.26.0) ──────────────────────────────────────


_GUIDED_PREAMBLE = """\
You are evaluating whether specific fields are observable in MCP tool \
schemas. For each obligation below, determine whether the named field \
is present and meaningful in the tool's output/behavior (CONFIRMED), \
exists internally but is not exposed to callers (DENIED), or is not \
relevant to this tool at all (DENIED).

Reason from field names, types, descriptions, and the tool's purpose."""


_GUIDED_VERDICT_FORMAT = """\
For each obligation, respond inside numbered delimiters:
---BEGIN_VERDICT_{n}---
verdict: CONFIRMED or DENIED or UNCERTAIN
evidence: one-sentence explanation
convention_value: the value this tool uses (only if CONFIRMED, else empty)
---END_VERDICT_{n}---"""


def _format_obligation_block(
    idx: int,
    obligation_dict: dict[str, str],
    tool_schema: dict[str, Any] | None,
    known_values: list[str] | None,
) -> str:
    """Format one obligation + its target tool schema for the batched prompt."""
    lines = [f"OBLIGATION {idx}:"]
    lines.append(f"  Server group: {obligation_dict['placeholder_tool']}")
    lines.append(f"  Dimension: {obligation_dict['dimension']}")
    lines.append(f"  Field: {obligation_dict['field']}")
    if obligation_dict.get("source_edge"):
        lines.append(f"  Source edge: {obligation_dict['source_edge']}")
    if known_values:
        lines.append(f"  Known convention values: {known_values}")

    if tool_schema:
        name = tool_schema.get("name", "unknown")
        desc = tool_schema.get("description", "")
        lines.append(f"  Target tool: {name}")
        if desc:
            lines.append(f"    Description: {desc}")
        props = tool_schema.get("inputSchema", {}).get("properties", {})
        if props:
            lines.append("    Fields:")
            for fname, fdef in sorted(props.items()):
                ftype = fdef.get("type", "any")
                fdesc = fdef.get("description", "")
                if fdesc:
                    lines.append(f"      {fname} ({ftype}): {fdesc}")
                else:
                    lines.append(f"      {fname} ({ftype})")
    else:
        lines.append("  Target tool: (schema not available)")

    return "\n".join(lines)


def build_guided_prompt(
    obligations: list[dict[str, str]],
    tool_schemas: list[dict[str, Any]],
    pack_context: dict[str, Any] | None = None,
) -> str:
    """Build a batched guided discovery prompt for multiple obligations.

    Each obligation is evaluated against its target tool in a single
    LLM call.  The prompt uses numbered verdict delimiters for reliable
    multi-verdict parsing.

    Args:
        obligations: List of obligation dicts (placeholder_tool, dimension,
            field, source_edge).
        tool_schemas: All available MCP tool dicts for tool matching.
        pack_context: Merged pack dict for known_values lookup.
    """
    tool_by_name: dict[str, dict[str, Any]] = {
        t.get("name", ""): t for t in tool_schemas
    }
    dims = (pack_context or {}).get("dimensions", {})

    blocks: list[str] = []
    for idx, obl in enumerate(obligations, 1):
        group = obl["placeholder_tool"]
        dim_name = obl["dimension"]

        known_values = None
        if dim_name in dims:
            known_values = dims[dim_name].get("known_values")

        target_tool = _match_tool_for_obligation(obl, tool_by_name)
        blocks.append(_format_obligation_block(idx, obl, target_tool, known_values))

    n_obls = len(obligations)
    verdict_instructions = "\n".join(
        _GUIDED_VERDICT_FORMAT.replace("{n}", str(i))
        for i in range(1, n_obls + 1)
    )

    sections = [
        _GUIDED_PREAMBLE,
        "",
        "\n\n".join(blocks),
        "",
        verdict_instructions,
        "",
        "RULES:",
        "- CONFIRMED means the field is present and meaningful in the tool's "
        "observable output or API surface",
        "- DENIED means the field is absent, internal-only, or not relevant",
        "- UNCERTAIN means you cannot determine from the schema alone",
        "- convention_value should be a short string like 'zero_based', "
        "'one_based', 'absolute', 'relative', etc.",
        "- Do NOT output any text outside the verdict delimiters",
    ]
    return "\n".join(sections)


def _match_tool_for_obligation(
    obligation: dict[str, str],
    tool_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the best tool schema matching an obligation's target.

    Prefers exact tool name from source_edge (matching the obligation's
    server group), falls back to first prefix match on placeholder_tool.
    """
    group = obligation["placeholder_tool"]
    source_edge = obligation.get("source_edge", "")
    if source_edge:
        for part in source_edge.replace(" -> ", "\t").split("\t"):
            part = part.strip()
            if part in tool_by_name and (part.startswith(f"{group}__") or part == group):
                return tool_by_name[part]

    for name in sorted(tool_by_name):
        if name.startswith(f"{group}__") or name == group:
            return tool_by_name[name]

    return None


def parse_guided_response(
    raw: str,
    n_obligations: int,
) -> list[dict[str, str]]:
    """Extract verdict blocks from a guided discovery LLM response.

    Returns a list of dicts with keys: verdict, evidence, convention_value.
    Length always equals ``n_obligations``; missing verdicts get UNCERTAIN.
    """
    results: list[dict[str, str]] = []
    for idx in range(1, n_obligations + 1):
        begin = f"---BEGIN_VERDICT_{idx}---"
        end = f"---END_VERDICT_{idx}---"

        verdict = "UNCERTAIN"
        evidence = ""
        convention_value = ""

        if begin in raw and end in raw:
            start = raw.index(begin) + len(begin)
            stop = raw.index(end)
            block = raw[start:stop].strip()
            for line in block.splitlines():
                line = line.strip()
                if line.lower().startswith("verdict:"):
                    v = line.split(":", 1)[1].strip().upper()
                    if v in ("CONFIRMED", "DENIED", "UNCERTAIN"):
                        verdict = v
                elif line.lower().startswith("evidence:"):
                    evidence = line.split(":", 1)[1].strip()
                elif line.lower().startswith("convention_value:"):
                    cv = line.split(":", 1)[1].strip()
                    if cv:
                        convention_value = cv

        results.append({
            "verdict": verdict,
            "evidence": evidence,
            "convention_value": convention_value,
        })

    return results
